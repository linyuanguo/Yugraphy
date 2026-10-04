"""Neo4j + PostgreSQL 连接与初始化。"""
import logging

from neo4j import AsyncGraphDatabase
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

logger = logging.getLogger("genealogy.database")

Base = declarative_base()

engine = create_engine(
    settings.sqlalchemy_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Neo4j 异步驱动（懒连接，服务起来后自动重连）
driver = AsyncGraphDatabase.driver(
    settings.NEO4J_URI,
    auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    max_connection_pool_size=10,
)

# Neo4j 约束 / 索引（启动时自动创建）
NEO4J_CONSTRAINTS = [
    "CREATE CONSTRAINT person_id IF NOT EXISTS FOR (p:Person) REQUIRE p.person_id IS UNIQUE",
    "CREATE INDEX person_name_idx IF NOT EXISTS FOR (p:Person) ON (p.name)",
    "CREATE INDEX person_birth_idx IF NOT EXISTS FOR (p:Person) ON (p.birth_year)",
    "CREATE CONSTRAINT place_id IF NOT EXISTS FOR (pl:Place) REQUIRE pl.place_id IS UNIQUE",
    "CREATE CONSTRAINT doc_id IF NOT EXISTS FOR (d:Document) REQUIRE d.doc_id IS UNIQUE",
    "CREATE CONSTRAINT lineage_id IF NOT EXISTS FOR (l:Lineage) REQUIRE l.lineage_id IS UNIQUE",
    "CREATE CONSTRAINT lineage_code IF NOT EXISTS FOR (l:Lineage) REQUIRE l.code IS UNIQUE",
    "CREATE CONSTRAINT branch_id IF NOT EXISTS FOR (b:Branch) REQUIRE b.branch_id IS UNIQUE",
    "CREATE INDEX lineage_name_idx IF NOT EXISTS FOR (l:Lineage) ON (l.name)",
]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def init_neo4j() -> None:
    """创建 Neo4j 约束/索引，失败仅告警不阻断启动。"""
    try:
        async with driver.session() as session:
            for cypher in NEO4J_CONSTRAINTS:
                await session.run(cypher)
        logger.info("Neo4j constraints/indexes ensured")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j init failed (will retry on demand): %s", exc)


def init_db() -> None:
    """确保 PG 表存在 + 初始化 admin 用户（PG 未就绪时最多重试 120s，避免启动崩溃循环）。"""
    import time

    from sqlalchemy.exc import SQLAlchemyError

    import app.models.orm  # noqa: F401  注册 ORM 模型

    last_exc = None
    for attempt in range(24):
        try:
            Base.metadata.create_all(bind=engine)
            break
        except SQLAlchemyError as exc:
            last_exc = exc
            logger.warning("PostgreSQL 未就绪（第 %d/24 次，5s 后重试）: %s", attempt + 1, exc)
            time.sleep(5)
    else:
        raise last_exc
    # create_all 不会给已存在表加新列，手动补齐
    from sqlalchemy import text

    with engine.begin() as conn:
        for stmt in (
            "ALTER TABLE import_tasks ADD COLUMN IF NOT EXISTS lineage_id VARCHAR(50)",
            "ALTER TABLE import_tasks ADD COLUMN IF NOT EXISTS branch_id VARCHAR(50)",
            "ALTER TABLE import_tasks ADD COLUMN IF NOT EXISTS stage VARCHAR(20) DEFAULT ''",
            "ALTER TABLE import_tasks ADD COLUMN IF NOT EXISTS file_size BIGINT",
            "ALTER TABLE import_tasks ADD COLUMN IF NOT EXISTS file_sha256 VARCHAR(64)",
            "ALTER TABLE import_tasks ADD COLUMN IF NOT EXISTS applied_at TIMESTAMP",
            "ALTER TABLE import_tasks ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP",
            "ALTER TABLE import_tasks ADD COLUMN IF NOT EXISTS archived_by INTEGER",
            "ALTER TABLE visit_shares ADD COLUMN IF NOT EXISTS chat_model VARCHAR(100)",
            # 访问短链码（全站唯一入口 <根>/<share_code>，历史记录 NULL 时懒补齐）
            "ALTER TABLE visit_shares ADD COLUMN IF NOT EXISTS share_code VARCHAR(12)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_visit_shares_share_code "
            "ON visit_shares (share_code)",
            # 用户模块权限：操作员可访问的模块清单（JSON 数组，NULL=全部）
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS permissions JSONB",
            # 登录态版本号：每次登录自增，旧 token 随之失效（同账号只允许一处在线）
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0",
            # 页级扁平化 JSON 镜像表 task_pages_json（GIN jsonb_path_ops，见 models.orm.TaskPageJson）
            "CREATE INDEX IF NOT EXISTS ix_task_pages_json_data_gin "
            "ON task_pages_json USING gin (data jsonb_path_ops)",
            # 删除审批 delete_requests（见 models.orm.DeleteRequest）：按目标与状态快速检索
            "CREATE INDEX IF NOT EXISTS ix_delete_requests_lookup "
            "ON delete_requests (target_type, target_id, status)",
            "CREATE INDEX IF NOT EXISTS ix_delete_requests_submitted "
            "ON delete_requests (submitted_by, status)",
        ):
            try:
                conn.execute(text(stmt))
            except Exception as exc:  # noqa: BLE001
                logger.warning("迁移语句失败（可忽略）: %s — %s", stmt, exc)
    logger.info("PostgreSQL tables ensured")

    from app.core.security import hash_password, verify_password

    from app.models.orm import User

    with SessionLocal() as db:
        admin = (
            db.query(User).filter(User.username == "admin").first()
        )
        if admin is None:
            db.add(
                User(
                    username="admin",
                    password=hash_password("admin123"),
                    full_name="系统管理员",
                    role="admin",
                    is_active=True,
                )
            )
            db.commit()
            logger.warning("创建默认管理员 admin/admin123，请部署后立即修改密码！")
        else:
            # 若 init.sql 中的 hash 与 admin123 不匹配则修正
            try:
                ok = verify_password("admin123", admin.password)
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                admin.password = hash_password("admin123")
                db.commit()
                logger.warning("管理员 admin 密码被重置为 admin123，请部署后立即修改！")


def reset_stale_tasks() -> list:
    """服务重启后处理中断的任务。

    返回需要重新执行的 task_id 列表（running / 滞留 pending 且源文件仍在 → 重置为
    pending 重新排队；源文件已按清理策略删除 → 标记 failed，页面图若已生成仍可逐页
    审核/重识别）：
    - running：上次进程被打断，需重跑
    - pending：可能因创建后未获调度（如进程在启动任务前崩溃/重启）而滞留，源文件
      仍在也应重跑；无源文件的 pending 说明任务未真正开始且文件异常缺失，标记失败
    """
    import os

    from app.models.orm import ImportTask

    from sqlalchemy import func as _func

    from app.models.orm import TaskPageJson

    resumed: list = []
    with SessionLocal() as db:
        stale = db.query(ImportTask).filter(
            ImportTask.status.in_(["running", "pending"])
        ).all()
        # 页面镜像（task_pages_json）里已有的页面数：>0 说明页面图已落 MinIO，
        # 即使原始扫描件已按清理策略删除，也能「断点续跑」，不该判死要重新上传
        page_counts = dict(
            db.query(TaskPageJson.task_id, _func.count(TaskPageJson.id))
            .filter(TaskPageJson.task_id.in_([t.task_id for t in stale]))
            .group_by(TaskPageJson.task_id)
            .all()
        ) if stale else {}
        for task in stale:
            orig = task.status
            has_pages = (page_counts.get(task.task_id) or 0) > 0
            # 卷级整理（consolidating）阶段中断：页面已全部识别，重启只续整理、不重跑识别
            if task.stage == "consolidating":
                task.status = "pending"
                task.pause_reason = None
                resumed.append(task.task_id)
                continue
            # 转图/预处理已完成、正等待识别闸的 ready 任务：页面图已全量在 MinIO，
            # 重启后无需重转图——置 paused 引导用户点「断点续跑」直接从已有页面开始识别
            # （旧版置 failed 会误导成「任务失败」，实际一页未丢、可原样续跑）
            if task.stage == "ready":
                task.status = "paused"
                task.pause_reason = "manual"
                task.error_msg = (
                    "服务重启打断：页面已转换就绪但尚未开始 AI 识别，"
                    "点「继续」即可从已有页面开始识别（识别完成会自动整卷整理）"
                )
                continue
            if task.file_path and os.path.exists(task.file_path):
                task.status = "pending"
                task.stage = ""
                task.pause_reason = None
                task.done_pages = 0
                resumed.append(task.task_id)
            elif has_pages:
                # 原件已清理但页面图齐全：绝不判 failed「请重新上传」——置 paused，
                # 人工点「继续」走断点续跑（已识别页不重跑，缺失页补识别即可）
                task.status = "paused"
                task.pause_reason = "manual"
                task.error_msg = (
                    "服务重启打断：页面图已完整保存，无需重新上传，"
                    "点「继续」即可从断点续跑（已识别页不会重跑）"
                )
            else:
                task.status = "failed"
                task.stage = "failed"
                task.pause_reason = None
                task.error_msg = (
                    "服务重启导致任务中断，且原始扫描件已清理，请重新上传"
                    if orig == "running"
                    else "任务未能正常启动且源文件缺失，请重新上传"
                )
        if stale:
            db.commit()
            logger.warning(
                "重启恢复：%d 个中断/滞留任务，其中 %d 个重新排队", len(stale), len(resumed)
            )
    return resumed


async def close_neo4j() -> None:
    await driver.close()


def close_db() -> None:
    engine.dispose()
