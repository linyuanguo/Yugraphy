"""FastAPI 应用入口。"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.client_ip import current_client_ip, get_client_ip
from app.core.config import settings
from app.core.database import (
    close_db,
    close_neo4j,
    init_db,
    init_neo4j,
    reset_stale_tasks,
)
from app.services import file_service, ip_whitelist_service

logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("genealogy")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    # 数据库初始化（失败不阻断启动，服务起来后自动重连）
    init_db()
    await init_neo4j()
    # 载入 HTTPS IP 白名单到进程内缓存（保存时会热更新，无需重启）
    try:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            ip_whitelist_service.load(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("加载 HTTPS IP 白名单失败: %s", exc)
    # 服务重启后，把上次中断的任务重新排队（源文件已清理的标记为失败）
    try:
        from app.services import consolidate_service, import_service

        for task_id in reset_stale_tasks():
            # 中断在卷级整理阶段的任务：页面已识别完，重启后只续整理
            from app.core.database import SessionLocal
            from app.models.orm import ImportTask

            with SessionLocal() as db:
                t = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
                resume_consolidate = bool(t and t.stage == "consolidating")
            if resume_consolidate:
                asyncio.create_task(consolidate_service.run_consolidation(task_id))
            else:
                asyncio.create_task(import_service.run_import_task(task_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("恢复中断任务失败: %s", exc)
    try:
        file_service._ensure_bucket(file_service._client())  # noqa: SLF001
        # Nginx 直连 MinIO 的 /files/ 需匿名只读（原图对照/文件预览），幂等设置
        file_service.ensure_public_read()
    except Exception as exc:  # noqa: BLE001
        logger.warning("MinIO bucket 初始化失败（稍后自动重试）: %s", exc)
    # 操作日志按保留策略自动清理（启动即执行一次，之后每 6 小时执行一次）
    async def _audit_retention_loop() -> None:
        while True:
            try:
                from app.services.audit_service import prune_audit_logs

                n = prune_audit_logs()
                if n:
                    logger.info("自动清理过期操作日志 %d 条", n)
            except Exception as exc:  # noqa: BLE001
                logger.warning("自动清理操作日志失败: %s", exc)
            await asyncio.sleep(6 * 3600)

    retention_task = asyncio.create_task(_audit_retention_loop())

    # 🔴 自愈巡检：无人推动时自己把「让位的整卷整理」拉起来（修复 09-17 全线停摆）。
    # 事故复盘：A 卷整理刚启动就让位给 B 卷，B 卷却因"无缺失页"自我否决并还原 paused
    # → 双方互等、唤醒链断掉 → 12 个卷静默冻结一夜（详见 import_service
    # .self_heal_yielded_consolidation 的 docstring）。此巡检是最后一道保险：
    # 每 5 分钟检查一次「没有识别任务在跑 + 存在 priority_ai 整理」→ 放行一个（链式）。
    async def _consolidation_self_heal_loop() -> None:
        await asyncio.sleep(60)  # 启动后稍等，让 reset_stale_tasks 的恢复先排布完
        while True:
            try:
                from app.services import import_service

                await import_service.self_heal_yielded_consolidation()
            except Exception as exc:  # noqa: BLE001
                logger.warning("整卷整理自愈巡检失败: %s", exc)
            await asyncio.sleep(5 * 60)

    self_heal_task = asyncio.create_task(_consolidation_self_heal_loop())

    # 存储孤儿数据每日自动扫描一次（结果缓存供「系统设置 → 存储清理」展示，
    # 由管理员人工勾选确认后才删除，绝不自动删）
    async def _storage_scan_loop() -> None:
        while True:
            try:
                from app.services import storage_service

                await asyncio.to_thread(storage_service.scan_orphans)
                logger.info("存储孤儿数据扫描完成")
            except Exception as exc:  # noqa: BLE001
                logger.warning("存储孤儿数据扫描失败: %s", exc)
            await asyncio.sleep(24 * 3600)

    storage_scan_task = asyncio.create_task(_storage_scan_loop())

    # 谱书内容 RAG 向量索引预热（后台增量构建；构建期间检索自动走关键词兜底）
    rag_warm_task: "asyncio.Task | None" = None
    try:
        from app.core.database import SessionLocal
        from app.services import rag_service

        async def _warm_rag() -> None:
            try:
                with SessionLocal() as db:
                    rag_service.load_rag_runtime(db)
                    await rag_service._ensure_index(db, wait=True)  # noqa: SLF001
                    st = rag_service.rag_status(db)
                logger.info("谱书内容 RAG 索引已就绪（%s 条）", st["total"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("谱书内容 RAG 索引预热失败（检索自动降级关键词）: %s", exc)

        rag_warm_task = asyncio.create_task(_warm_rag())
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAG 预热任务创建失败: %s", exc)

    logger.info("%s 已启动", settings.APP_NAME)
    yield
    if rag_warm_task:
        rag_warm_task.cancel()
    retention_task.cancel()
    storage_scan_task.cancel()
    self_heal_task.cancel()
    await close_neo4j()
    close_db()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 内网环境 + Nginx 同源部署，开发期放开
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ClientIPMiddleware:
    """纯 ASGI 中间件：每个 HTTP 请求把客户端 IP 存入 contextvar，
    供 service 层 log_action 自动填充（后台任务继承发起请求的 IP）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            from starlette.requests import Request

            current_client_ip.set(get_client_ip(Request(scope)))
        await self.app(scope, receive, send)


app.add_middleware(ClientIPMiddleware)


class IPWhitelistMiddleware:
    """HTTPS IP 白名单门禁：启用后仅命中 IP 段可访问 /api（含登录与全部数据接口）。

    - 白名单未启用（空）→ 一律放行（默认不限制 IP）。
    - 命中判定基于真实客户端 IP（Nginx 注入 X-Real-IP，仅经 Nginx 这一入口，可信）。
    - 保存白名单时由 settings 接口热更新进程内缓存，无需重启即生效。
    - 放行 /api/health（供监控探活）与 OPTIONS（CORS 预检）。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and ip_whitelist_service.is_enabled()
            and scope.get("method", "").upper() != "OPTIONS"
        ):
            path = scope.get("path", "")
            if path.startswith("/api/") and path != "/api/health":
                from starlette.requests import Request

                if not ip_whitelist_service.is_allowed(get_client_ip(Request(scope))):
                    resp = JSONResponse(
                        status_code=403,
                        content={"detail": "当前 IP 不在允许访问的白名单内"},
                    )
                    return await resp(scope, receive, send)
        await self.app(scope, receive, send)


app.add_middleware(IPWhitelistMiddleware)
app.include_router(api_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}


@app.get("/files/{object_name:path}")
async def serve_file(object_name: str):
    """开发模式直接透传 MinIO 对象（生产环境由 Nginx /files/ 代理，不会走到这里）。"""
    from fastapi.responses import StreamingResponse

    from minio.error import S3Error

    try:
        resp = file_service._client().get_object(  # noqa: SLF001
            settings.MINIO_BUCKET, object_name
        )
    except S3Error:
        return JSONResponse(status_code=404, content={"detail": "文件不存在"})
    content_type = resp.headers.get("Content-Type", "application/octet-stream")

    def iter_body():
        try:
            for chunk in resp.stream(64 * 1024):
                yield chunk
        finally:
            resp.close()
            resp.release_conn()

    return StreamingResponse(iter_body(), media_type=content_type)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):  # noqa: ANN001
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})
