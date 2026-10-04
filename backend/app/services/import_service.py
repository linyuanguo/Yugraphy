"""扫描件 AI 导入任务：转图 → 预处理 → 调 206 VLM → 进度入库 → 审核后写入图谱。"""
import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, defer

from app.core.config import settings
from app.core.database import SessionLocal, driver
from app.models.orm import ContentEntry, ImportTask, TaskPageJson
from app.services import (
    file_service,
    grid_extract,
    person_service,
    task_registry,
    vision_service,
)
from app.services.audit_service import log_task_action
from app.utils import image_utils

logger = logging.getLogger("genealogy.import")

# 串行并发闸：逐页调用，避免 27B 单卡并发 OOM/排队
_vision_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_VISION_CALLS)
# 任务级并发闸：同时只处理 N 个导入任务的「AI 识别+整卷整理」（调 206），其余等待；
# 转图/预处理不占用本闸（见 _convert_semaphore），页面先就绪者先识别
_task_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_IMPORT_TASKS)
# 转图并发闸：同时「转图+预处理」的任务数（本机 CPU/IO，与 206 识别解耦、不共用名额）
_convert_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_CONVERT)

# 优先级 A（上传 > 转图）：正在接收上传文件时的活跃计数。转图是 CPU 密集
# （各自 CONVERT_CONCURRENCY 个线程），批量上传时若同时开转图会拖慢收文件、
# 甚至触发客户端超时。故有上传在进行时，新的转图任务先等上传收完再开始。
_uploading = 0


def note_upload_start() -> None:
    """上传开始（由 /tasks/import 端点调用）。"""
    global _uploading
    _uploading += 1


def note_upload_end() -> None:
    """上传结束（无论成功失败都要调用，见端点 try/finally）。"""
    global _uploading
    _uploading = max(0, _uploading - 1)


def _has_pages_to_extract(task: "ImportTask") -> bool:
    """该任务是否**确有页面待识别**（避免"假 pending"骗停整理）。

    09-17 事故暴露的漏洞：`has_pending_extraction` 只按状态判定，于是一个
    「页镜像已齐全、实际无页可识别」的任务（如 523 页全识别完的 1cc778a3e37c
    被人为置成 running/extracting）会**永远**被算作"在等识别"，
    使整理在 0 块处反复让位 → 全局静止。

    判定依据（任一成立即视为"确有待识别页"）：
    - result.pages 里存在 failed 占位，或 failed_pages 非空；
    - 已识别页数（pages 长度） < total_pages（仍缺页）。
    取不到 total_pages（0）时宽松放行，避免误判导致抢跑。
    """
    total = task.total_pages or 0
    res = task.result or {}
    pages = res.get("pages") or []
    if res.get("failed_pages"):
        return True
    if any(p.get("failed") for p in pages if isinstance(p, dict)):
        return True
    if total <= 0:
        return True  # 无从判断，保守放行
    return len(pages) < total


def has_pending_extraction(exclude_task_id: str = "") -> bool:
    """是否存在「等待 AI 识别」的其它任务（优先级 B：识别 > 整理）。

    判定：状态 pending（已入队等识别闸），或 running 且阶段为 ready/extracting
    （已转好图、准备/正在逐页识别）。自己除外（调用方通常是正在整理的任务）。
    ⚠ 必须再过一道 `_has_pages_to_extract`：仅状态匹配但**无页可识别**的
    "假 pending" 不算数，否则整理会被它反复骗停（见该函数 docstring）。
    """
    with SessionLocal() as db:
        rows = (
            db.query(ImportTask)
            .filter(
                ImportTask.task_id != exclude_task_id,
                ImportTask.archived_at.is_(None),
                or_(
                    ImportTask.status == "pending",
                    and_(
                        ImportTask.status == "running",
                        ImportTask.stage.in_(["ready", "extracting"]),
                    ),
                ),
            )
            .all()
        )
    return any(_has_pages_to_extract(t) for t in rows)


def _next_yielded_consolidation(exclude_task_id: str = "") -> Optional[str]:
    """取一个「为让位识别而暂停」的整理任务。

    只认 pause_reason='priority_ai'（整理为给 AI 识别让位而自动暂停）；
    人工暂停的整理任务（pause_reason='manual'）不在此列，绝不会被自动续跑。
    """
    with SessionLocal() as db:
        row = (
            db.query(ImportTask.task_id)
            .filter(
                ImportTask.status == "paused",
                ImportTask.stage == "consolidating",
                ImportTask.pause_reason == "priority_ai",
                ImportTask.task_id != exclude_task_id,
            )
            .order_by(ImportTask.updated_at.asc())
            .first()
        )
        return row[0] if row else None


async def auto_resume_yielded_consolidation(current_task_id: str = "") -> None:
    """本任务跑完释放识别闸后，自动续跑此前让位的整卷整理（一次一个，链式）。"""
    tid = _next_yielded_consolidation(current_task_id)
    if not tid:
        return
    logger.info("任务 %s：识别名额已空出，自动续跑此前让位的整卷整理", tid)
    try:
        await resume_task(tid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("自动续跑整卷整理失败 %s: %s", tid, exc)


def _any_extraction_active() -> bool:
    """是否**确有**任务在跑/在等识别（自愈巡检的判据）。

    只有在系统里没有任何活跃识别任务时，才允许把让位的整理拉起来，
    否则会跟真正在跑的识别抢 206 名额。
    """
    with SessionLocal() as db:
        rows = (
            db.query(ImportTask)
            .filter(
                ImportTask.archived_at.is_(None),
                ImportTask.status.in_(["pending", "running"]),
            )
            .all()
        )
    for t in rows:
        # running 但处于整理阶段的，不算"识别中"（正是我们要唤醒的那类）
        if t.status == "running" and t.stage == "consolidating":
            continue
        if _has_pages_to_extract(t):
            return True
    return False


async def self_heal_yielded_consolidation() -> None:
    """自愈巡检：无任何识别任务在跑，却存在「让位的整理」→ 自动唤醒一个（链式）。

    🔴 修复 09-17 全线停摆事故的最后一道保险。
    此前唤醒让位整理的触发点只有两处（任务正常跑完的 finally、续跑早退的兜底），
    一旦「A 卷整理让位 / B 卷识别自我否决」互等，链就断了 —— 让位的整理变成永久
    孤儿，整台机器静默到下次重启（实测冻了一夜，12 个卷全卡住）。

    本函数由启动时 + 周期巡检调用，保证**没人推的时候自己会推**：
    - 有识别任务在跑/在等 → 不动（整理本就该让位）；
    - 没有识别任务、但有 priority_ai 的整理 → 续跑**一个**（跑完其 finally 链式续下一个）。
    ⚠ 只放一个，绝不批量：大卷整理 result 达数十 MB，同时开多个会打爆内存与 206 并发。
    """
    try:
        if _any_extraction_active():
            return
        tid = _next_yielded_consolidation()
        if not tid:
            return
        logger.warning(
            "自愈巡检：无识别任务在跑，但存在让位的整卷整理，自动唤醒 %s", tid
        )
        await resume_task(tid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("自愈巡检唤醒整理失败（下次巡检重试）: %s", exc)


def _task_dir(task_id: str) -> str:
    return os.path.join(settings.UPLOAD_DIR, "tasks", task_id)


# ==================== 页级扁平化 JSON（ai_meta/notes/persons/content） ====================
# 底层识别结果取消「篇目分类嵌套」：每页顶层只保留 ai_meta（统计）、notes（备注）、
# persons（人物数组）、content（纯文本段落数组，条目不带 type/title/类别）。
# ai_meta 由后端按页面计算（耗时取本页 AI 调用实际耗时），模型自身不输出。


def _ai_meta_of(
    persons: List[dict], content: List[dict], duration_s: float = 0.0
) -> dict:
    """由页面 persons/content 计算 AI 检测统计（人数/段落数/字数/耗时/低置信数）。"""
    paras = [
        str(c.get("text") if isinstance(c, dict) else c or "").strip()
        for c in content or []
    ]
    paras = [t for t in paras if t]
    low = sum(
        1
        for p in persons or []
        if (p.get("confidence") if p.get("confidence") is not None else 1.0) < 0.6
    )
    return {
        "person_count": len(persons or []),
        "entry_count": len(paras),
        "char_count": sum(len(t) for t in paras),
        "duration_s": round(max(0.0, float(duration_s or 0)), 1),
        "low_conf_count": low,
    }


def _flat_content(content_raw) -> List[dict]:
    """规整 content 段落：list[str] 或 list[{"text":..}] → [{"text":.., "manual":False}]。"""
    out: List[dict] = []
    for c in content_raw or []:
        if isinstance(c, dict):
            text = str(c.get("text") or "").strip()
            manual = bool(c.get("manual", False))
        else:
            text = str(c or "").strip()
            manual = False
        if text:
            out.append({"text": text, "manual": manual})
    return out


def _flat_page(
    page_no: int,
    image_url: str,
    norm: Dict,
    duration_s: float = 0.0,
    reviewed: bool = False,
    **extra,
) -> dict:
    """由识别结果 norm 组装扁平化页 dict（供落库 import_tasks.result / task_pages_json）。"""
    content = _flat_content(norm.get("content"))
    persons = list(norm.get("persons") or [])
    page = {
        "page_no": page_no,
        "image_url": image_url,
        "ai_meta": _ai_meta_of(persons, content, duration_s),
        "notes": str(norm.get("notes") or ""),
        "persons": persons,
        "content": content,
        "reviewed": bool(reviewed),
    }
    page.update(extra)
    return page


async def cleanup_task_pages(task_id: str) -> int:
    """清理该任务在 MinIO 的页面图（释放空间），任务记录与提取结果保留。"""
    removed = await file_service.remove_prefix(f"scans/{task_id}/")
    logger.info("任务 %s：已清理 %d 个页面图", task_id, removed)
    return removed


async def cleanup_task_local(task_id: str) -> None:
    """删除任务本地中间目录（raw/work/resume 及未转完的原扫描件等残留）。"""
    task_dir = _task_dir(task_id)
    if os.path.isdir(task_dir):
        await run_in_threadpool(shutil.rmtree, task_dir, True)
        logger.info("任务 %s：已清理本地任务目录 %s", task_id, task_dir)


def _page_json_data(page: dict) -> dict:
    """单页镜像数据（task_pages_json.data）：只保留扁平化可检索字段。"""
    return {
        "ai_meta": page.get("ai_meta") or {},
        "notes": str(page.get("notes") or ""),
        "persons": page.get("persons") or [],
        "content": page.get("content") or [],
        "reviewed": bool(page.get("reviewed")),
        "failed": bool(page.get("failed")),
        "illustrations": page.get("illustrations") or [],
    }


def _mirror_task_pages_json(db: Session, task_id: str, pages) -> None:
    """把 task.result.pages 整表重建镜像到 task_pages_json（一页一行）。

    页面识别/审核/插图/重识别都经 _update_task(result=…) 落盘，故在此统一收口：
    凡 result 带 pages 即重建（量级小：单页一行 jsonb，整卷也仅数百行）。
    """
    if pages is None:
        return
    db.query(TaskPageJson).filter(TaskPageJson.task_id == task_id).delete(
        synchronize_session=False
    )
    now = datetime.now()
    rows = []
    for p in pages:
        if not isinstance(p, dict) or p.get("page_no") is None:
            continue
        rows.append(
            TaskPageJson(
                task_id=task_id,
                page_no=int(p["page_no"]),
                data=_page_json_data(p),
                updated_at=now,
            )
        )
    if rows:
        db.add_all(rows)


def _update_task(
    task_id: str, skip_pages_mirror: bool = False, keep_paused: bool = True, **kwargs
) -> None:
    """更新任务字段并落盘。

    skip_pages_mirror=True：跳过 task_pages_json 镜像重建。仅用于「只更新 result
    尾部（如卷级整理的 consolidated）而 pages 未变」的场景——镜像在建页时已写好，
    重复 delete+insert 数百行纯属浪费，且是 500+ 页大卷整理 OOM 的元凶之一。

    keep_paused=True（默认）：任务处于 paused 时，后台协程例行的状态写入
    （running / pending）一律丢弃，不得把 paused 覆盖回运行态。否则「暂停」会被
    协程的下一个写入点悄悄改回运行态 —— 表现为：刚暂停的任务又自己跑起来，且
    重启时会被 reset_stale_tasks 当成中断任务重排/因源文件已删而判 failed。
    显式恢复（断点续跑 / 重新识别等用户动作）必须传 keep_paused=False。
    """
    with SessionLocal() as db:
        # 只更新标量进度字段（不传 result）时不要加载巨大的 result JSONB：
        # 大卷整卷 result 达数十 MB，每块/每页读回一次是内存峰值的主要来源
        q = db.query(ImportTask)
        if "result" not in kwargs:
            q = q.options(defer(ImportTask.result))
        task = q.filter(ImportTask.task_id == task_id).first()
        if not task:
            return
        # 暂停保护：已暂停的任务不被后台例行写入改回运行态（详见 docstring）
        if (
            keep_paused
            and task.status == "paused"
            and kwargs.get("status") in ("running", "pending")
        ):
            logger.info(
                "任务 %s 已暂停：忽略后台状态写入 %s（保留 paused）",
                task_id, kwargs.get("status"),
            )
            kwargs.pop("status")
        # 离开暂停态时自动清空暂停原因（调用方显式传 pause_reason 则以显式值为准）
        if "status" in kwargs and kwargs["status"] != "paused" and "pause_reason" not in kwargs:
            kwargs["pause_reason"] = None
        pages = None
        for k, v in kwargs.items():
            if k == "result" and isinstance(v, dict):
                pgs = v.get("pages")
                if isinstance(pgs, list):
                    pages = pgs
            setattr(task, k, v)
        if not skip_pages_mirror:
            _mirror_task_pages_json(db, task_id, pages)
        task.updated_at = datetime.now()
        db.commit()
        # 尽早释放本次会话持有的 ORM 实例与属性历史（含整卷 result 大 dict 的旧值
        # 快照），降低大卷整理期间的内存峰值
        db.expunge_all()


def _get_task(task_id: str) -> Optional[ImportTask]:
    with SessionLocal() as db:
        return db.query(ImportTask).filter(ImportTask.task_id == task_id).first()


def _task_paused(task_id: str) -> bool:
    """任务是否处于暂停态（前端 Pause 只写 DB 状态，后台协程在安全点自查）。"""
    with SessionLocal() as db:
        row = (
            db.query(ImportTask.status)
            .filter(ImportTask.task_id == task_id)
            .first()
        )
        return bool(row) and row[0] == "paused"


def task_to_dict(task: ImportTask) -> dict:
    return {
        "task_id": task.task_id,
        "file_path": task.file_path,
        "file_type": task.file_type,
        "total_pages": task.total_pages or 0,
        "done_pages": task.done_pages or 0,
        "status": task.status,
        "error_msg": task.error_msg,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


async def run_import_task(task_id: str) -> None:
    """上传任务的后台流程（登记到任务注册表，删除任务时 DELETE 接口会取消本协程）。

    并发拆成两段、使用两个独立的闸：
    - A 段（转图 + 页面预处理，本机 CPU/IO）：只受 MAX_CONCURRENT_CONVERT 转图闸
      限制，不占 AI 识别名额——后上传的书无需等前面的书识别完即可先完成转图，
      页面全部就绪后置 stage='ready' 等待识别；
    - B 段（逐页 AI 识别 + 卷级整理，调 206）：才受 MAX_CONCURRENT_IMPORT_TASKS
      限制，同时只识别一本，其余在 stage='ready' 排队（等待识别）。
    """
    task_registry.register(task_id)
    try:
        if _task_paused(task_id):
            # 排队刚被暂停（尚未开始转图）：直接收尾，恢复时走断点续跑
            log_task_action(task_id, "pause", "排队中被暂停，任务未开始转图")
            return
        prep = await _convert_and_prepare(task_id)
        if not prep:
            return  # 失败（原件缺失/转图异常等）已按 failed 落库
        total, prepared = prep
        if _task_paused(task_id):
            # 转图/页面准备期间收到暂停：页面已全部就绪（MinIO 有副本），
            # 不再排队等 AI 识别名额，直接收尾（恢复时补齐识别即可）
            log_task_action(task_id, "pause", f"页面准备完成后暂停（{total} 页待识别）")
            return
        async with _task_semaphore:
            await _extract_and_consolidate(task_id, total, prepared)
    finally:
        task_registry.unregister(task_id)
        # 优先级 B：本任务跑完（识别闸已释放），后台自动续跑曾让位的整卷整理
        try:
            asyncio.create_task(auto_resume_yielded_consolidation(task_id))
        except RuntimeError:
            pass  # 事件循环正关闭（服务退出）时忽略
        # 任务结束：清理本地中间图（MinIO 已有副本，raw/work 属冗余）
        if not settings.KEEP_LOCAL_PAGE_IMAGES:
            shutil.rmtree(os.path.join(_task_dir(task_id), "raw"), ignore_errors=True)
            shutil.rmtree(os.path.join(_task_dir(task_id), "work"), ignore_errors=True)


async def _missing_minio_pages(task_id: str, total: int) -> List[int]:
    """核对 MinIO 页面图是否齐全，返回缺失页号列表（全在则返回空列表）。"""
    missing: List[int] = []
    for i in range(1, total + 1):
        ok = await file_service.object_exists(f"scans/{task_id}/page_{i:03d}.png")
        if not ok:
            missing.append(i)
    return missing


async def _convert_and_prepare(
    task_id: str,
) -> Optional[Tuple[int, List[Tuple[int, str, str]]]]:
    """A 段：转图 → 删原件 → 并发预处理并上传 MinIO（本机 CPU/IO，不排队等识别）。

    成功返回 (total, prepared)；失败已置 failed 并返回 None。
    """
    # 优先级 A：有文件正在上传时先让位，等上传收完再开始转图
    waited = 0
    while _uploading > 0:
        if waited == 0:
            logger.info("任务 %s：检测到文件正在上传，转图让位等待", task_id)
        waited += 1
        await asyncio.sleep(1)
    async with _convert_semaphore:
        return await _convert_and_prepare_locked(task_id)


async def _convert_and_prepare_locked(
    task_id: str,
) -> Optional[Tuple[int, List[Tuple[int, str, str]]]]:
    task = _get_task(task_id)
    if not task:
        return None
    src_path = task.file_path
    task_dir = _task_dir(task_id)
    raw_dir = os.path.join(task_dir, "raw")
    work_dir = os.path.join(task_dir, "work")
    # 清空上次残留（重启恢复等场景），避免旧文件干扰转图进度统计
    shutil.rmtree(raw_dir, ignore_errors=True)
    shutil.rmtree(work_dir, ignore_errors=True)
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    try:
        if not os.path.exists(src_path):
            _update_task(
                task_id,
                status="failed",
                stage="failed",
                error_msg="源文件不存在（原始扫描件可能已按清理策略删除，请重新上传）",
            )
            return None

        _update_task(task_id, status="running", stage="converting", done_pages=0)

        # ① 先取总页数（只读元数据，毫秒级），让前端立刻有进度分母
        total = await run_in_threadpool(image_utils.count_pages, src_path)
        _update_task(task_id, total_pages=total, done_pages=0)

        # ② PDF/TIF → PNG（CPU 操作放线程池），期间轮询已生成页数回写进度
        convert_future = asyncio.create_task(
            run_in_threadpool(
                image_utils.convert_to_images,
                src_path,
                raw_dir,
                settings.MAX_IMAGE_LONG_EDGE,
                settings.CONVERT_CONCURRENCY,
            )
        )
        while not convert_future.done():
            await asyncio.sleep(2)
            try:
                done = len([f for f in os.listdir(raw_dir) if f.endswith(".png")])
            except OSError:
                done = 0
            _update_task(task_id, done_pages=min(done, total) if total else done)
        page_files = await convert_future
        total = len(page_files)
        # 转图完成：进度顶到总页数（stage 仍为 converting = 页面准备中），
        # 页面全部就绪后再置 ready 等待 AI 识别
        _update_task(task_id, total_pages=total, done_pages=total)

        # ③ 并发预处理 + 上传处理图到 MinIO（供审核界面查看）
        #    注意：原件必须等页面图全部上传 MinIO 成功后再删（见下方 ⑤）——
        #    旧实现在这里就删原件，一旦预处理/上传 MinIO 期间被暂停、重启或 OOM，
        #    会出现「原件已删 + MinIO 无图」的双空状态，任务永久不可恢复。
        #    预处理（纠偏/增强）逐页约 1~3s，批量并发后不再叠加进 VLM 串行时间
        #    预处理（纠偏/增强）逐页约 1~3s，批量并发后不再叠加进 VLM 串行时间
        _prep_sem = asyncio.Semaphore(settings.PREPROCESS_CONCURRENCY)

        async def _prepare_page(idx: int, raw_path: str) -> Tuple[int, str, str]:
            async with _prep_sem:
                work_path = os.path.join(work_dir, f"page_{idx:03d}_work.png")
                await run_in_threadpool(
                    image_utils.preprocess_image,
                    raw_path,
                    work_path,
                    settings.PREPROCESS_DESKEW,
                    settings.PREPROCESS_DENOISE,
                    settings.PREPROCESS_ENHANCE,
                    settings.PREPROCESS_BINARIZE,
                )
                object_name = f"scans/{task_id}/page_{idx:03d}.png"
                await file_service.upload_file(object_name, work_path, "image/png")
                # 审核页小图条缩略图（~10KB）：前端按 /files/thumbs/{task}/page_NNN.jpg
                # 规则引用；老任务缺缩略图时自动回退原图（可用
                # scripts/backfill_thumbs.py 一次性补齐历史任务）。
                thumb_path = os.path.join(work_dir, f"page_{idx:03d}_thumb.jpg")
                await run_in_threadpool(image_utils.make_thumbnail, work_path, thumb_path)
                await file_service.upload_file(
                    f"thumbs/{task_id}/page_{idx:03d}.jpg", thumb_path, "image/jpeg"
                )
                return idx, work_path, object_name

        prepared = await asyncio.gather(
            *(_prepare_page(i, p) for i, p in enumerate(page_files, start=1))
        )
        logger.info("任务 %s：全部页面预处理完成（%d 页），等待 AI 识别", task_id, total)

        # ⑤ 页面图已全部落 MinIO → 逐个 stat 核对对象确实存在，核对通过才删原件。
        #    只数 prepared 条数不够：上传调用返回成功也可能没真正落盘（中断窗口期），
        #    一旦误删原件就是「原件没了 + MinIO 没图」的双空，任务永久不可恢复。
        if not settings.KEEP_ORIGINAL_SCAN and len(prepared) == total:
            missing = await _missing_minio_pages(task_id, total)
            if missing:
                logger.warning(
                    "任务 %s：MinIO 仍缺 %d 页（如 %s），保留原始扫描件以便续跑",
                    task_id, len(missing), missing[:5],
                )
            else:
                try:
                    os.remove(src_path)
                    logger.info(
                        "任务 %s：%d 页已全部核对存在于 MinIO，已清理原始扫描件 %s",
                        task_id, total, src_path,
                    )
                except OSError as exc:
                    logger.warning("任务 %s：清理原始扫描件失败 %s", task_id, exc)
        elif len(prepared) != total:
            logger.warning(
                "任务 %s：页面图上传不完整（%d/%d），保留原始扫描件以便续跑",
                task_id, len(prepared), total,
            )
        # 页面已全部就绪（MinIO 有副本）：置 ready——若识别闸忙，任务将在此等待；
        # 重启恢复特判见 database.reset_stale_tasks（stage=='ready' → 引导断点续跑）
        _update_task(task_id, stage="ready", done_pages=total)
        log_task_action(task_id, "convert_images", f"转换图片完成（{total} 页）")
        return total, prepared
    except Exception as exc:  # noqa: BLE001
        logger.exception("任务 %s 转图/准备阶段失败", task_id)
        _update_task(
            task_id,
            status="failed",
            stage="failed",
            error_msg=str(exc)[:2000],
        )
        return None

async def _extract_and_consolidate(
    task_id: str, total: int, prepared: List[Tuple[int, str, str]]
) -> None:
    """B 段：逐页 AI 识别（206）+ 自动卷级整理。仅在持有 AI 识别任务闸时调用（一次一本）。

    stage 流转：extracting（AI 识别）→ consolidating（整卷整理）→ done。
    """
    pages_result: List[dict] = []
    try:
        # 开始识别：识别进度从 0 计数（done_pages 复用作「已识别页数」）
        _update_task(task_id, total_pages=total, done_pages=0, stage="extracting")

        # ⑤ 并发调 206 多模态，采用「滑动窗口」调度（窗口 = MAX_CONCURRENT_VISION_CALLS）。
        #    不可一次性把全部页面协程丢进队列：多任务共享 VLM 槽时，先完成任务的大量
        #    排队协程会占满信号量 FIFO，后完成任务被饿死（表现为 0/N 长时间不动）。
        #    改为「完成一页补一页」，多任务公平轮转共享 VLM 槽，进度实时可感知。
        WINDOW = max(1, settings.MAX_CONCURRENT_VISION_CALLS)

        async def _extract_page(item: Tuple[int, str, str]) -> Tuple[int, dict]:
            idx, work_path, object_name = item
            tiled = False
            t0 = time.monotonic()
            async with _vision_semaphore:
                try:
                    # 页型分流：格线页（世系格/多格登记页）走「切格识别」——
                    # 两轮 6 页实测：格线页切格后内容 +36%~118%、人名不减反增
                    # （p255 7→17 人、p235 9→10 人），而正文页切格有害 →
                    # 非格线页返回 None 直接走原整页链路，切格异常也自动回退。
                    raw_result = None
                    if settings.OCR_GRID_ENABLED:
                        try:
                            raw_result = await grid_extract.extract_page_grid_norm(
                                work_path
                            )
                        except Exception as grid_err:  # noqa: BLE001
                            logger.warning(
                                "任务 %s 第 %s 页切格识别失败，回退整页链路: %s",
                                task_id, idx, grid_err,
                            )
                            raw_result = None
                    if raw_result is None:
                        raw_result = await vision_service.extract_page(work_path)
                except Exception as first_err:  # noqa: BLE001
                    # 整页判失败（多为透印死循环/慢生成/坏 JSON）→ 直接走审核端同款兜底：
                    # 更长硬超时重跑整页，仍失败则横向分块。只让个别顽固页多等几分钟，
                    # 换「新导入任务几乎零失效页」，免去事后逐页手工补扫。
                    if not settings.IMPORT_REDO_FALLBACK:
                        raise
                    logger.warning(
                        "任务 %s 第 %s 页整页识别失败，转重识别兜底（长超时+分块）: %s",
                        task_id, idx, first_err,
                    )
                    raw_result = await vision_service.redo_extract_page(
                        work_path, hard_timeout=settings.REDO_PAGE_TIMEOUT
                    )
                    tiled = bool(raw_result.pop("_redo_tiled", False))
            duration_s = time.monotonic() - t0
            norm = vision_service.normalize_extraction(raw_result)
            if tiled:
                prev = norm.get("notes") or ""
                norm["notes"] = (
                    (prev + "；" if prev else "") + "分块识别（整页识别失败后自动兜底）"
                )
            return idx, _flat_page(
                idx,
                file_service.public_url(object_name),
                norm,
                duration_s=duration_s,
            )

        def _failed_page_entry(idx: int, object_name: str, err: str) -> dict:
            """单页识别失败的占位（保留图与页码，可后续重试）。"""
            return _flat_page(
                idx,
                file_service.public_url(object_name),
                {"persons": [], "content": [], "notes": ""},
                duration_s=0.0,
                failed=True,
                error=str(err)[:500],
            )

        done_count = 0
        failed_count = 0
        failed_pages: List[int] = []
        checkpoint = settings.RESULT_CHECKPOINT_PAGES
        # 大卷放宽落盘频率：整卷 result 已数十 MB，每 10 页整体序列化一次代价高
        if len(prepared) > 300:
            checkpoint = max(checkpoint, len(prepared) // 20)
        fut_to_idx: Dict[asyncio.Task, int] = {}
        inflight: set = set()
        pos = 0
        total_n = len(prepared)
        paused_flag = False  # 用户点「暂停」：本页完成后干净收尾，不进入卷级整理
        try:
            # 预填窗口（同时在飞页数不超过 WINDOW）
            while len(inflight) < WINDOW and pos < total_n:
                t = asyncio.create_task(_extract_page(prepared[pos]))
                fut_to_idx[t] = prepared[pos][0]
                inflight.add(t)
                pos += 1
            while inflight and not paused_flag:
                done, pending = await asyncio.wait(
                    inflight, return_when=asyncio.FIRST_COMPLETED
                )
                inflight = pending
                for fut in done:
                    idx = fut_to_idx.pop(fut, None)
                    try:
                        got_idx, page_entry = await fut
                        pages_result.append(page_entry)
                        done_count += 1
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        # 单页连续失败（含空响应/坏 JSON）只跳过该页，不拖垮整个任务；
                        # 页面留占位标记，识别完成后可在审核界面重试或一键续跑
                        failed_count += 1
                        logger.warning(
                            "任务 %s 第 %s 页识别失败（已跳过，可后续重试）: %s",
                            task_id, idx, exc,
                        )
                        if idx is not None:
                            failed_pages.append(idx)
                            pages_result.append(
                                _failed_page_entry(
                                    idx, prepared[idx - 1][2], str(exc)
                                )
                            )
                    if idx is not None:
                        _update_task(task_id, done_pages=done_count)
                        if (done_count + failed_count) % checkpoint == 0 or (
                            done_count + failed_count
                        ) == total_n:
                            pages_result.sort(key=lambda x: x["page_no"])
                            _update_task(
                                task_id,
                                done_pages=done_count,
                                result={"pages": pages_result},
                            )
                    logger.info(
                        "任务 %s 进度 %d/%d（失败 %d）",
                        task_id, done_count + failed_count, total_n, failed_count,
                    )
                    # 用户点「暂停」：本页成果已落库，停在滑窗边界（在飞页交 finally 取消）
                    if _task_paused(task_id):
                        paused_flag = True
                        break
                    # 完成一页补一页，维持窗口大小
                    if pos < total_n:
                        t = asyncio.create_task(_extract_page(prepared[pos]))
                        fut_to_idx[t] = prepared[pos][0]
                        inflight.add(t)
                        pos += 1
        finally:
            for f in list(inflight):
                if not f.done():
                    f.cancel()
        pages_result.sort(key=lambda x: x["page_no"])
        # 暂停收尾：保留已识别页（缺失/失败页留给「继续」补识别），不进入卷级整理
        if paused_flag:
            pause_result: dict = {"pages": pages_result}
            if failed_count:
                pause_result["failed_pages"] = failed_pages
            _update_task(
                task_id,
                status="paused",
                pause_reason="manual",
                done_pages=done_count,
                error_msg="已暂停（已识别页保留；点「继续」补齐剩余/失败页并自动整卷整理）",
                result=pause_result,
            )
            log_task_action(
                task_id, "pause",
                f"AI 识别中已暂停（已识别 {done_count}/{total_n} 页，失败 {failed_count} 页）",
            )
            return

        # 单页失败不影响任务完成；失败页可在审核界面重试，或点「重试失败页」续跑。
        # 提取（第一段）结束不直接 done：自动进入卷级整理（第二段，人物归并 + 关系推断），
        # 整理完成（status=done）后才开放审核。
        result: dict = {"pages": pages_result}
        if failed_count:
            result["failed_pages"] = failed_pages
        _update_task(
            task_id,
            status="running",
            stage="consolidating",
            done_pages=done_count,
            error_msg=None,
            result=result,
        )
        log_task_action(
            task_id, "ai_extract",
            f"AI 识别完成（{done_count}/{total} 页，失败 {failed_count} 页）",
        )
        try:
            from app.services import consolidate_service

            await consolidate_service.run_consolidation(task_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("任务 %s 自动卷级整理调度失败", task_id)
            _update_task(
                task_id,
                status="done",
                stage="done",
                done_pages=done_count,
                error_msg=f"卷级整理调度失败：{exc}；可在任务列表点「重新整理」重试",
                result=result,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("任务 %s 识别/整理失败", task_id)
        _update_task(
            task_id,
            status="failed",
            stage="failed",
            error_msg=str(exc)[:2000],
            result={"pages": pages_result} if pages_result else None,
        )


async def resume_task(task_id: str, force_all: bool = False) -> dict:
    """断点续跑入口（登记后台任务，删除时可真正取消）。

    force_all=True 时表示「整卷重新识别」：所有页面（含失败页、含已成功页）
    全部重新调用 AI 识别，结束后自动重跑卷级整理（内容全变，旧整卷结果作废）。
    """
    task_registry.register(task_id)
    try:
        result = await _resume_impl(task_id, force_all=force_all)
        if not result.get("ok"):
            logger.warning(
                "任务 %s 续跑未执行: %s", task_id, result.get("error")
            )
        return result
    finally:
        task_registry.unregister(task_id)
        # ⚠⚠ 兜底唤醒（修复 09-17 全线停摆事故）：
        # 此前「让位的整卷整理（paused/priority_ai）」只在 run_import_task 正常跑完时
        # 被唤醒；而 _resume_impl 的所有早退路径（无缺失页 / 无需续跑 / 状态不符 /
        # 并发双点让路 / 异常）都不触发唤醒 → 每次早退都把让位的整理任务变成**永久孤儿**。
        # 事故链路：A 卷整理刚启动(0 块)发现 B 卷在等识别 → 让位；B 卷拿到名额后发现
        # 「没有缺失/失败的页面」→ 自我否决并还原成 paused → 此时已无 pending/running
        # → 双方互等，全局静止（09-17 15:32 起 12 个卷冻了一夜）。
        # 故无论 _resume_impl 走哪条路径返回，都必须尝试把让位的整理推起来（幂等）。
        _wake_yielded_consolidation_bg(task_id)


def _wake_yielded_consolidation_bg(current_task_id: str = "") -> None:
    """后台尝试唤醒一个「让位的整卷整理」（幂等；无候选则什么都不做）。

    与 run_import_task.finally 里同源，但抽成函数供 _resume_impl 早退路径复用。
    仅当确实存在优先级更高的识别任务在跑时才会被再次让位，故不会与识别抢资源。
    """
    try:
        asyncio.create_task(auto_resume_yielded_consolidation(current_task_id))
    except RuntimeError:
        pass  # 事件循环正关闭（服务退出）时忽略


async def _resume_impl(task_id: str, force_all: bool = False) -> dict:
    """断点续跑/重试失败页/整卷重新识别，直接用 MinIO 已转页面识别。

    - 续跑（force_all=False）：无需重新上传，已识别的页保留，只补识别缺失/失败的页
      （单页失败同样容错跳过）。
    - 整卷重识别（force_all=True）：全部页重新跑 AI；个别页再次失败时保留其旧识别
      内容（不丢数据），其余页用新结果，结束后一律自动重跑卷级整理。
    """
    # ===== 排队可见性：进识别队列前先预校验，并立即标记 pending（排队中） =====
    # 修复：若等 _task_semaphore（识别并发名额）时才改状态，第二个及之后的任务会
    # 阻塞在信号量上、DB 状态一直不变 → 列表看不到「排队中」，用户只收到一句
    # "已开始续跑"提示却毫无动静，误以为没生效。
    pre = _get_task(task_id)
    if not pre:
        return {"ok": False, "error": "任务不存在"}
    orig_status = pre.status
    orig_stage = pre.stage
    if pre.status in ("pending", "running"):
        return {"ok": False, "error": "任务已在排队或处理中，请勿重复提交"}
    if pre.status not in ("failed", "done", "paused"):
        return {"ok": False, "error": f"任务状态 {pre.status}，无法重识别"}
    if not force_all and pre.status == "done":
        # 需要补识别的信号：pages 里的 failed 占位 / failed_pages 数组 / 已识别页数 < 总页数
        # ⚠ 判据以 **pages 实际条数** 为准，不用 done_pages（该字段实测会失真：
        # 事故现场 1cc778a3e37c 报 13/523 而 pages 已有完整 523 行；若按 done_pages
        # 判断会误判"还差 510 页"从而白跑一轮重识别）。
        pre_pages = (pre.result or {}).get("pages", [])
        pre_failed = (pre.result or {}).get("failed_pages") or []
        pre_total = pre.total_pages or 0
        pre_have = len(pre_pages)
        if (
            not any(p.get("failed") for p in pre_pages)
            and not pre_failed
            and (pre_total <= 0 or pre_have >= pre_total)
        ):
            return {"ok": False, "error": "任务已全部识别完成，无需续跑"}
    # 预校验通过 → 先入队（列表立即显示「⏳ 排队中」），拿到并发名额后转 running
    # keep_paused=False：断点续跑是用户显式恢复动作，允许把 paused 改成 pending
    _update_task(
        task_id, status="pending", stage="extracting", error_msg=None, keep_paused=False
    )

    orig_done = pre.done_pages or 0

    def _restore() -> None:
        """排队中未真正开始就被拒绝时，恢复进入前的状态，避免任务卡在 pending。

        ⚠ 必须连 done_pages 一起还原（orig_done）：进入本函数时已把状态写成
        pending/extracting，若此处只还原 status/stage 而不还原 done_pages，
        会把进入前的进度留在"被覆盖后"的值上 —— 事故现场 `1cc778a3e37c`
        报 13/523 而实际 523 页全部识别完，即此类写入把 done_pages 拍扁所致
        （其 result.pages 与 task_pages_json 均为完整 523 行）。
        """
        _update_task(
            task_id, status=orig_status, stage=orig_stage, done_pages=orig_done
        )

    async with _task_semaphore:
        task = _get_task(task_id)
        if not task:
            return {"ok": False, "error": "任务不存在"}
        # 补漏：历史任务在「转图完成 → 上传 MinIO」之间被中断（旧版此时已把原件删了）。
        # 若原始扫描件侥幸还在而 MinIO 页面图不齐，先用原件补转图（同名覆盖、幂等），
        # 再继续识别——这样「暂停/中断」不会让任务作废。
        if task.file_path and os.path.exists(task.file_path):
            have = await file_service.count_objects(f"scans/{task_id}/")
            if 0 <= have < (task.total_pages or 0):
                logger.info(
                    "任务 %s：原件仍在而 MinIO 仅 %d/%s 页，先补转图再续识别",
                    task_id, have, task.total_pages,
                )
                _update_task(task_id, status="running", stage="converting")
                prep = await _convert_and_prepare_locked(task_id)
                if not prep:
                    return {"ok": False, "error": "补转图失败，请重新上传该卷"}
        if task.status == "running":
            _restore()  # 并发双点同一任务（前一个请求已拿到名额开跑），本次让路
            return {"ok": False, "error": "任务正在处理中，请稍候再试"}
        if task.status not in ("pending", "failed", "done"):
            _restore()  # 排队期间状态被改动（含被暂停），无法按计划续跑
            return {"ok": False, "error": f"任务状态 {task.status}，无法重识别"}

        all_pages = (task.result or {}).get("pages", [])
        if not force_all and task.status == "done":
            # 排队等名额期间任务被其他路径补齐：复查，无需再跑
            # ⚠ 同样以 pages 实际条数为准（done_pages 会失真，理由见上方预校验处）
            failed_arr = (task.result or {}).get("failed_pages") or []
            t_total = task.total_pages or 0
            all_ok = (
                not any(p.get("failed") for p in all_pages)
                and not failed_arr
                and (t_total <= 0 or len(all_pages) >= t_total)
            )
            if all_ok:
                _restore()
                return {"ok": False, "error": "任务已全部识别完成，无需续跑"}

        # 保留全部页（含失败占位）：重识别成功则替换，失败则保留/新建占位，
        # 确保 pages 与 failed_pages 始终一致（修复此前续跑失败页从 pages 消失、
        # 导致状态列与失效页列表数量对不上的 bug）
        pages_now = list(all_pages)
        old_by_no = {p["page_no"]: p for p in all_pages}
        # 成功页（非 failed 占位）：续跑只补识别这些之外的页（失败占位 + 缺失页）
        good_nos = {p["page_no"] for p in all_pages if not p.get("failed")}

        # 从 MinIO 列出该任务全部页面图
        client = file_service._client()  # noqa: SLF001
        try:
            objs = [
                o.object_name
                for o in client.list_objects(
                    settings.MINIO_BUCKET, prefix=f"scans/{task_id}/", recursive=True
                )
            ]
        except Exception as exc:  # noqa: BLE001
            _restore()
            return {"ok": False, "error": f"无法访问页面图存储：{exc}"}
        target: Dict[int, str] = {}
        for n in objs:
            base = os.path.basename(n)
            if not base.startswith("page_") or not base.endswith(".png"):
                continue
            try:
                no = int(base[5:-4])
            except ValueError:
                continue
            target[no] = n
        need = (
            [no for no in sorted(target) if no not in good_nos]
            if not force_all
            else sorted(target)
        )
        if not need:
            # ⚠ 页已齐全（无缺失/失败页）→ 不该原地退回，而应推进到「整合」：
            # 这正是 09-17 停摆事故的关键分支。当时只判 orig_stage=='consolidating'
            # 才续整理，本任务处于 extracting 时直接 _restore() 退回 paused，
            # 与该任务自己让位出去的整理形成**双方互等**（整理等识别、识别说没我事），
            # 全局静止。修正为：只要页齐全，就把任务推进到卷级整理（幂等、可重复执行），
            # 并顺手把失真的 done_pages 校正为实际页数。
            _update_task(
                task_id,
                status="running",
                stage="consolidating",
                error_msg=None,
                done_pages=len(pages_now),
                result={"pages": pages_now},
            )
            log_task_action(
                task_id, "consolidate",
                f"页面已齐全（{len(pages_now)} 页），无需补识别，直接进入卷级整理",
            )
            try:
                from app.services import consolidate_service

                await consolidate_service.run_consolidation(task_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("任务 %s 无需补识别但整卷整理失败", task_id)
                _update_task(
                    task_id,
                    status="done",
                    stage="done",
                    done_pages=len(pages_now),
                    error_msg=f"整卷整理失败：{exc}；可在任务列表点「重新整理」重试",
                    result={"pages": pages_now},
                )
            return {"ok": True, "no_missing": True}

        tmp_dir = os.path.join(_task_dir(task_id), "resume")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        os.makedirs(tmp_dir, exist_ok=True)
        _update_task(
            task_id,
            status="running",
            stage="extracting",
            error_msg=None,
            done_pages=len(good_nos),
        )
        try:
            WINDOW = max(1, settings.MAX_CONCURRENT_VISION_CALLS)
            checkpoint = settings.RESULT_CHECKPOINT_PAGES
            # 同上：大卷放宽 result 落盘频率，降低内存峰值
            if len(good_nos) > 300:
                checkpoint = max(checkpoint, len(good_nos) // 20)

            async def _ex(no: int, obj: str) -> Tuple[int, dict]:
                tmp = os.path.join(tmp_dir, f"p{no}.png")
                await run_in_threadpool(
                    client.fget_object, settings.MINIO_BUCKET, obj, tmp
                )
                t0 = time.monotonic()
                # 主动补识别：放宽单页硬超时；整页仍失败时自动横向分块兜底
                async with _vision_semaphore:
                    norm = await vision_service.redo_extract_page(
                        tmp, hard_timeout=settings.REDO_PAGE_TIMEOUT
                    )
                duration_s = time.monotonic() - t0
                tiled = bool(norm.pop("_redo_tiled", False))
                # 整卷重识别（force_all）新旧版合并：新版为主；旧版该页独有人员补齐并
                # 标低置信——防新版对个别页漏读导致旧识别出的人被整页覆盖弄丢。
                # content 一律以新版为准。续跑（force_all=False）补的失败页旧结果为空，跳过。
                if force_all:
                    prev_p = old_by_no.get(no) or {}
                    merged_persons, added_old = _merge_old_persons(
                        norm["persons"], prev_p.get("persons") or []
                    )
                    if added_old:
                        norm["persons"] = merged_persons
                        note = norm.get("notes") or ""
                        norm["notes"] = (
                            (note + "；" if note else "")
                            + f"保留旧版独有人名 {added_old} 人（低置信，待核）"
                        )
                return no, _flat_page(
                    no,
                    file_service.public_url(obj),
                    norm,
                    duration_s=duration_s,
                    re_extract_tiled=tiled,
                )

            fut_map: Dict[asyncio.Task, int] = {}
            inflight: set = set()
            pos = 0
            new_ok = 0
            failed_pages: List[int] = []
            processed = 0
            paused_flag = False  # 续跑中收到暂停：停在滑窗边界，进度保留
            try:
                while len(inflight) < WINDOW and pos < len(need):
                    no = need[pos]
                    t = asyncio.create_task(_ex(no, target[no]))
                    fut_map[t] = no
                    inflight.add(t)
                    pos += 1
                while inflight and not paused_flag:
                    done, pending = await asyncio.wait(
                        inflight, return_when=asyncio.FIRST_COMPLETED
                    )
                    inflight = pending
                    for fut in done:
                        no = fut_map.pop(fut, None)
                        try:
                            _, entry = await fut
                            pages_now = [p for p in pages_now if p["page_no"] != no]
                            pages_now.append(entry)
                            new_ok += 1
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "任务 %s 第 %s 页重识别失败（%s）: %s",
                                task_id, no, "整卷重识别，保留旧内容" if force_all else "跳过，可再次续跑", exc,
                            )
                            if no is not None:
                                failed_pages.append(no)
                                existing = next(
                                    (x for x in pages_now if x["page_no"] == no), None
                                )
                                if existing is None:
                                    # 缺失页：新建 failed 占位（保证 pages 与 failed_pages 一致）
                                    pages_now.append(
                                        _flat_page(
                                            no,
                                            file_service.public_url(target[no]),
                                            {"persons": [], "content": [], "notes": ""},
                                            duration_s=0.0,
                                            failed=True,
                                            error=str(exc)[:200],
                                        )
                                    )
                                elif not existing.get("failed"):
                                    # 成功页重识别失败：保留旧内容（不标 failed，旧结果仍可用）
                                    pass
                                else:
                                    # 失败占位页重识别失败：保留占位，更新错误信息
                                    existing["error"] = str(exc)[:200]
                        processed += 1
                        _update_task(task_id, done_pages=len(pages_now))
                        if processed % checkpoint == 0 or processed == len(need):
                            pages_now.sort(key=lambda x: x["page_no"])
                            _update_task(task_id, result={"pages": pages_now})
                        logger.info(
                            "任务 %s 续跑进度 %d/%d（成功 %d）",
                            task_id, processed, len(need), new_ok,
                        )
                        # 续跑中收到暂停：停在滑窗边界，剩余缺页留待下次「继续」
                        if _task_paused(task_id):
                            paused_flag = True
                            break
                        if pos < len(need):
                            n2 = need[pos]
                            t = asyncio.create_task(_ex(n2, target[n2]))
                            fut_map[t] = n2
                            inflight.add(t)
                            pos += 1
            finally:
                for f in list(inflight):
                    if not f.done():
                        f.cancel()
            pages_now.sort(key=lambda x: x["page_no"])
            if paused_flag:
                # 续跑中暂停：进度落库，保持 paused（不进入整理/不置 done）
                _update_task(
                    task_id,
                    status="paused",
                    pause_reason="manual",
                    done_pages=len(pages_now),
                    error_msg="已暂停（识别进度保留；点「继续」补齐剩余/失败页并自动整卷整理）",
                    result={"pages": pages_now},
                )
                log_task_action(task_id, "pause", "续跑识别中已暂停（识别进度已保留）")
                return {"ok": True}
            result: dict = {"pages": pages_now}
            # failed_pages 数组与 pages 里的 failed 占位保持一致（修复两来源不一致）
            result["failed_pages"] = [p["page_no"] for p in pages_now if p.get("failed")]
            if result["failed_pages"] and not force_all:
                # 仍有失败页：数据不全不整理，保持 done 供逐页重试/查看
                _update_task(
                    task_id,
                    status="done",
                    stage="done",
                    done_pages=len(pages_now),
                    error_msg=(
                        f"{len(failed_pages)} 页重试后仍失败（第 "
                        f"{'、'.join(map(str, failed_pages))[:400]} 页），可逐页重试；"
                        "失败页补齐后请在任务列表点「重新整理」更新整卷关系"
                    ),
                    result=result,
                )
            else:
                # 页已补齐 / 整卷重识别完成（失败页保留了旧内容，页仍齐全）：
                # 旧卷级整理结果已过时，自动重跑卷级整理
                _update_task(
                    task_id,
                    status="running",
                    stage="consolidating",
                    done_pages=len(pages_now),
                    error_msg=None,
                    result=result,
                )
                try:
                    from app.services import consolidate_service

                    await consolidate_service.run_consolidation(task_id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("任务 %s 识别后重新整理失败", task_id)
                    _update_task(
                        task_id,
                        status="done",
                        stage="done",
                        done_pages=len(pages_now),
                        error_msg=f"识别完成，但卷级整理失败：{exc}；可点「重新整理」重试",
                        result=result,
                    )
            return {"ok": True, "new_pages": new_ok, "failed": len(failed_pages)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("任务 %s 续跑失败", task_id)
            try:
                _update_task(
                    task_id,
                    status="failed",
                    stage="failed",
                    error_msg=f"续跑中断：{exc}"[:1000],
                    result={"pages": pages_now} if pages_now else None,
                )
            except Exception:  # noqa: BLE001
                pass
            return {"ok": False, "error": str(exc)}
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _merge_old_persons(
    new_persons: List[dict], old_persons: List[dict]
) -> Tuple[List[dict], int]:
    """新旧版结果合并（规则15）：以新版（本次识别结果）为主；
    旧版（改动前/上次的识别结果）里独有的姓名补进 persons 尾部，仅补人名（不覆盖新版
    entries），confidence 定为 0.6，并标记 _source="old_only" 供审核区分来源。

    去重键＝去掉空格/全角空格的姓名；(二)(三) 后缀保留视为不同人（各成独立条目）。
    仅合并人员；entries 一律以新版为准（新版结构拆分更优，不混入旧版）。
    """

    def _key(p: dict) -> str:
        return (p.get("name") or "").strip().replace(" ", "").replace("\u3000", "")

    merged = list(new_persons)
    seen = {_key(p) for p in merged if _key(p)}
    added = 0
    for p in old_persons:
        if not isinstance(p, dict):
            continue
        key = _key(p)
        if not key or key in seen:
            continue
        np_ = {**p}
        np_["confidence"] = 0.6
        np_["_source"] = "old_only"
        merged.append(np_)
        seen.add(key)
        added += 1
    return merged, added


async def re_extract_page(task_id: str, page_no: int) -> Optional[dict]:
    """对某页单独重新识别（审核界面"重识别"）。"""
    task = _get_task(task_id)
    if not task or not task.result:
        return None
    pages = task.result.get("pages", [])
    page = next((p for p in pages if p["page_no"] == page_no), None)
    if not page:
        return None
    image_url = page["image_url"]
    object_name = image_url.removeprefix("/files/")
    # 从 MinIO 拉回原图临时文件
    client = file_service._client()  # noqa: SLF001
    tmp = os.path.join(_task_dir(task_id), f"retry_page_{page_no}.png")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    client.fget_object(settings.MINIO_BUCKET, object_name, tmp)

    t0 = time.monotonic()
    # 单页主动重试：放宽单页硬超时；整页失败时自动横向分块兜底
    async with _vision_semaphore:
        norm = await vision_service.redo_extract_page(
            tmp, hard_timeout=settings.REDO_PAGE_TIMEOUT
        )
    duration_s = time.monotonic() - t0
    tiled = bool(norm.pop("_redo_tiled", False))

    # 新旧版合并（规则4）：新版（本次重识别结果）为主；旧版（上次识别结果）独有的
    # 姓名补齐回 persons 并标低置信——防新版漏读导致旧版已识别出的人被整页覆盖弄丢。
    old_persons = page.get("persons") or []
    merged_persons, added_old = _merge_old_persons(norm["persons"], old_persons)

    # 保留人工审核成果（审核界面「+ 人工添加原文段落」的 manual 段落 / 人工改写过的段落）：
    # 重识别是重新跑 AI，若整页覆盖会把这些人工转录内容冲掉。因此取出旧版里 manual=true 的
    # 段落暂存，重识别后再并入新版结果，保证人工内容在重识别后仍在。
    old_content = _flat_content(page.get("content"))
    manual_paras = [c for c in old_content if c.get("manual")]

    fresh_content = _flat_content(norm.get("content"))

    # 并入人工段落：与新版已识别段落去重（按文本规整后比较），避免重复；其余追到末尾保留
    fresh_keys = {c["text"] for c in fresh_content}
    kept_manual: List[dict] = []
    for c in manual_paras:
        if c["text"] in fresh_keys:
            continue  # 新版已识别出同段，避免重复
        fresh_content.append(c)
        kept_manual.append(c)

    page.update(
        {
            "ai_meta": _ai_meta_of(
                merged_persons, fresh_content, duration_s
            ),
            "persons": merged_persons,
            "content": fresh_content,
            "notes": str(norm.get("notes") or ""),
            "reviewed": False,
            "re_extract_tiled": tiled,
            "failed": False,  # 重识别成功：清除失败标记（否则前端一直显示「识别失败」）
        }
    )
    page.pop("relations", None)
    page.pop("entries", None)
    if kept_manual:
        prev = (page.get("notes") or "").strip()
        page["notes"] = (
            (prev + "；" if prev else "")
            + f"重识别已保留 {len(kept_manual)} 段人工添加内容"
        )
    if added_old:
        prev = (page.get("notes") or "").strip()
        page["notes"] = (
            (prev + "；" if prev else "")
            + f"保留旧版独有人名 {added_old} 人（低置信，待核）"
        )
    page.pop("error", None)
    # 单页内容变化 → 卷级整理结果失效（删除并标记，前端提示「重新整理」）
    page.pop("paddle_draft", None)  # 重识别成功：旧的失败页参考草稿不再需要
    result = dict(task.result or {})
    if page_no in (result.get("failed_pages") or []):
        result["failed_pages"] = [n for n in result["failed_pages"] if n != page_no]
    if result.get("consolidated"):
        result.pop("consolidated", None)
        result["consolidation_stale"] = True
    _update_task(task_id, result=result)
    return page


def save_page_review(task_id: str, page_no: int, payload: dict) -> Optional[dict]:
    """暂存某页审核结果（不写图谱）。"""
    task = _get_task(task_id)
    if not task or not task.result:
        return None
    pages = task.result.get("pages", [])
    page = next((p for p in pages if p["page_no"] == page_no), None)
    if not page:
        return None
    # 变更前后签名：页内容（人物/正文）被人工改动过 → 整卷整理结果过期，
    # 点「写入图谱并归档」时会先自动重新整理（见 tasks.py _need_consolidate）。
    _sig = lambda: json.dumps(  # noqa: E731
        [page.get("persons") or [], page.get("content") or []],
        ensure_ascii=False, sort_keys=True, default=str,
    )
    old_sig = _sig()
    page["persons"] = [
        {**p, "confirmed": bool(p.get("confirmed", True))} for p in payload.get("persons", [])
    ]
    # 兼容旧前端 payload 里带 relations/entries 的情况（新结构已不再使用，忽略之）
    if "content" in payload:
        page["content"] = _flat_content(payload.get("content"))
    page["notes"] = str(payload.get("notes", page.get("notes") or ""))
    page["reviewed"] = bool(payload.get("reviewed", True))
    page["ai_meta"] = _ai_meta_of(
        page.get("persons") or [],
        page.get("content") or [],
        (page.get("ai_meta") or {}).get("duration_s", 0),
    )
    result = dict(task.result or {})
    result["pages"] = pages
    # 人工改过页内容 → 标记整卷结果过期：写入前自动重新整理（不删除现有结果，
    # 整卷人工校对仍可查看；整理完成后由新 consolidated 覆盖并需重新标记审核）
    if _sig() != old_sig and result.get("consolidated"):
        result["consolidation_stale"] = True
    _update_task(task_id, result=result)
    return page


def review_status(task) -> dict:
    """统计任务的人工审核状态（写入图谱 / 归档的前置闸）。

    判定规则：
    - 存在卷级整理结果（consolidated）：以 cons.reviewed 为准（人工在整卷人物/关系
      抽屉里点过「保存」才算已审核）；重新整理会重建 consolidated → 自动失效。
    - 老任务 / 无整卷结果：非失败页全部暂存过（page.reviewed）才算已审核。
    返回 {reviewed, reviewed_at, reviewed_by_name, reviewed_pages, total_pages}。
    """
    result = getattr(task, "result", None) or {}
    pages = result.get("pages") or []
    cons = result.get("consolidated")
    need = [p for p in pages if not p.get("failed")]
    reviewed_pages = sum(1 for p in pages if p.get("reviewed"))
    if cons:
        return {
            "reviewed": bool(cons.get("reviewed")),
            "reviewed_at": cons.get("reviewed_at"),
            "reviewed_by_name": cons.get("reviewed_by_name"),
            "reviewed_pages": reviewed_pages,
            "total_pages": len(pages),
        }
    return {
        "reviewed": bool(need) and all(p.get("reviewed") for p in need),
        "reviewed_at": None,
        "reviewed_by_name": None,
        "reviewed_pages": reviewed_pages,
        "total_pages": len(pages),
    }


def save_consolidated_review(
    task_id: str,
    persons: List[dict],
    relations: List[dict],
    reviewer: Optional[dict] = None,
    mark_reviewed: Optional[bool] = None,
) -> Optional[dict]:
    """暂存卷级整理结果的审核（覆盖人物/关系的 confirmed 等编辑）。

    仅当任务存在卷级整理结果（result.consolidated）时可用；老任务（逐页关系）走
    save_page_review。

    reviewer={"id","username"} 时配合 mark_reviewed 维护人工审核标记
    （reviewed/reviewed_at/reviewed_by*），作为「写入图谱 / 归档」的前置闸；
    重新识别、重新整理后该标记自动失效。

    mark_reviewed 三态语义：
      - True  = 显式「本卷已人工校对完成」→ 落审核标记（并记录审核人）
      - False = 显式「取消已审核」→ 清除审核标记（误标/发现差错后改回未审核）
      - None  = 普通暂存（不清除也不落标），默认
    """
    task = _get_task(task_id)
    if not task or not task.result or not task.result.get("consolidated"):
        return None
    result = dict(task.result)
    cons = dict(result["consolidated"])
    # 保留原条目页码信息（前端 ReviewItem 不含 pages 字段）
    p_pages = {
        str(op.get("name", "")).strip(): (op.get("pages") or [])
        for op in (cons.get("persons") or [])
    }
    r_pages = {
        (
            str(or_.get("type", "")),
            str(or_.get("from_name", "")).strip(),
            str(or_.get("to_name", "")).strip(),
        ): (or_.get("pages") or [])
        for or_ in (cons.get("relations") or [])
    }
    new_persons = []
    for p in persons:
        pp = {**p, "confirmed": bool(p.get("confirmed", True))}
        if not pp.get("pages"):
            pp["pages"] = p_pages.get(str(p.get("name", "")).strip(), [])
        new_persons.append(pp)
    new_relations = []
    for r in relations:
        rr = {**r, "confirmed": bool(r.get("confirmed", True))}
        if not rr.get("pages"):
            rr["pages"] = r_pages.get(
                (
                    str(r.get("type", "")),
                    str(r.get("from_name", "")).strip(),
                    str(r.get("to_name", "")).strip(),
                ),
                [],
            )
        new_relations.append(rr)
    cons["persons"] = new_persons
    cons["relations"] = new_relations
    # 人工审核标记（mark_reviewed 三态，见函数 docstring）：True 落标并记审核人；
    # False 显式清除（取消已审核）；None 普通暂存不动。重新整理会重建 consolidated
    # （新 dict 不含本标记）→ 自动失效。
    if mark_reviewed is True:
        cons["reviewed"] = True
        cons["reviewed_at"] = datetime.now().isoformat(timespec="seconds")
        if reviewer:
            cons["reviewed_by"] = reviewer.get("id")
            cons["reviewed_by_name"] = reviewer.get("username")
    elif mark_reviewed is False:
        cons["reviewed"] = False
        cons.pop("reviewed_at", None)
        cons.pop("reviewed_by", None)
        cons.pop("reviewed_by_name", None)
    result["consolidated"] = cons
    _update_task(task_id, result=result)
    return cons


# ============ 审核写入图谱 ============
# 识别可提供的 Person 属性（写入时：新建直接带全量；复用已有节点时只补缺失字段，不覆盖人工数据）
PERSON_RECOGNIZED_FIELDS = ("birth_year", "death_year", "birth_place", "biography")


async def _match_or_create_person(
    name: str, gender: str = "unknown", attrs: Optional[Dict] = None
) -> str:
    """按姓名匹配已有人物（宽松：忽略空格、大小写），不存在则新建。返回 person_id。

    attrs: 本页识别出的人物属性（生卒/籍贯/简介等）。新建时写入；若已存在同名节点，
    仅补空缺字段（coalesce），避免覆盖手工核对过的数据。
    """
    attrs = {k: (attrs or {}).get(k) for k in PERSON_RECOGNIZED_FIELDS}
    async with driver.session() as session:
        rec = await session.run(
            # 去空白须与卷级整理的 _norm_name（去所有空白）一致，否则「张　三」这类
            # 含全角空格的姓名在卷内已合并、入库时却匹配不到 → 重复建出第二个 Person
            "MATCH (p:Person) WHERE "
            "toLower(replace(replace(replace(p.name,' ',''),'　',''),char(9),'')) = "
            "toLower(replace(replace(replace($name,' ',''),'　',''),char(9),'')) "
            "RETURN p.person_id AS pid ORDER BY p.created_at LIMIT 1",
            name=name,
        )
        rec_ = await rec.single()
        if rec_:
            pid = rec_["pid"]
            await session.run(
                "MATCH (p:Person {person_id: $pid}) SET "
                "p.birth_year = coalesce(p.birth_year, $birth_year), "
                "p.death_year = coalesce(p.death_year, $death_year), "
                "p.birth_place = coalesce(p.birth_place, $birth_place), "
                "p.biography = coalesce(p.biography, $biography) ",
                pid=pid, **attrs,
            )
            return pid
        pid = uuid.uuid4().hex[:24]
        cyph = (
            "CREATE (p:Person {person_id: $pid, name: $name, gender: $gender, "
            "created_at: datetime(), birth_year: $birth_year, death_year: $death_year, "
            "birth_place: $birth_place, biography: $biography}) "
            "RETURN p.person_id AS pid"
        )
        rec = await session.run(cyph, pid=pid, name=name, gender=gender, **attrs)
        row = await rec.single()
        return row["pid"]


async def _ensure_document(doc_id: str, title: str) -> str:
    async with driver.session() as session:
        rec = await session.run(
            "MERGE (d:Document {doc_id: $doc_id}) SET d.title = $title RETURN d.doc_id AS doc_id",
            doc_id=doc_id,
            title=title,
        )
        row = await rec.single()
        return row["doc_id"]


async def _attach_lineage(
    pid: str, lineage_id: Optional[str], branch_id: Optional[str]
) -> None:
    """给人物挂谱系/房支归属（重建关系，幂等）。"""
    if not lineage_id:
        return
    async with driver.session() as session:
        await session.run(
            "MATCH (p:Person {person_id: $pid}) "
            "OPTIONAL MATCH (p)-[r1:BELONGS_TO]->(:Lineage) DELETE r1 "
            "WITH p "
            "OPTIONAL MATCH (p)-[r2:IN_BRANCH]->(:Branch) DELETE r2 "
            "WITH p MATCH (l:Lineage {lineage_id: $lid}) "
            "CREATE (p)-[:BELONGS_TO]->(l)",
            pid=pid, lid=lineage_id,
        )
        if branch_id:
            await session.run(
                "MATCH (p:Person {person_id: $pid}), (b:Branch {branch_id: $bid, lineage_id: $lid}) "
                "CREATE (p)-[:IN_BRANCH]->(b)",
                pid=pid, bid=branch_id, lid=lineage_id,
            )


async def apply_review_to_graph(
    persons: List[dict],
    relations: List[dict],
    doc_id: Optional[str] = None,
    doc_title: Optional[str] = None,
    lineage_id: Optional[str] = None,
    branch_id: Optional[str] = None,
) -> dict:
    """把确认后的结果批量写入 Neo4j：按姓名匹配/新建人物，再建关系。

    任务创建时若指定了谱系/房支，写入的人物自动挂上归属。
    """
    name_to_pid: Dict[str, str] = {}
    created_persons = 0
    linked_persons = 0
    created_relations = 0
    skipped_relations = 0

    for p in persons:
        if not p.get("confirmed", True):
            continue
        name = str(p.get("name", "")).strip()
        if not name:
            continue
        gender = str(p.get("gender", "unknown"))
        if gender not in ("male", "female", "unknown"):
            gender = "unknown"
        p_attrs = {k: p.get(k) for k in PERSON_RECOGNIZED_FIELDS}
        pid = await _match_or_create_person(name, gender, p_attrs)
        if lineage_id:
            await _attach_lineage(pid, lineage_id, branch_id)
        if pid in name_to_pid.values() and pid not in name_to_pid:
            linked_persons += 1
        name_to_pid[name] = pid

    # 关系（先按名字，再补建缺失人物）
    for r in relations:
        if not r.get("confirmed", True):
            continue
        rtype = str(r.get("type", "")).strip()
        from_name = str(r.get("from_name", "")).strip()
        to_name = str(r.get("to_name", "")).strip()
        if rtype not in ("parent_child", "spouse") or not from_name or not to_name:
            skipped_relations += 1
            continue
        from_pid = name_to_pid.get(from_name)
        if not from_pid:
            from_pid = await _match_or_create_person(from_name)
            # 关系里补建/新匹配的人物同样挂谱系归属，避免游离（否则人物链在谱系视图里断）
            if lineage_id:
                await _attach_lineage(from_pid, lineage_id, branch_id)
        to_pid = name_to_pid.get(to_name)
        if not to_pid:
            to_pid = await _match_or_create_person(to_name)
            if lineage_id:
                await _attach_lineage(to_pid, lineage_id, branch_id)
        name_to_pid.setdefault(from_name, from_pid)
        name_to_pid.setdefault(to_name, to_pid)
        rel = await person_service.create_relation(
            rtype, from_pid, to_pid, r.get("marriage_date")
        )
        if rel:
            created_relations += 1

    # 关联文档（可追溯来源）
    if doc_id:
        await _ensure_document(doc_id, doc_title or doc_id)
        async with driver.session() as session:
            for pid in set(name_to_pid.values()):
                await session.run(
                    "MATCH (p:Person {person_id: $pid}), (d:Document {doc_id: $doc_id}) "
                    "MERGE (p)-[:HAS_DOCUMENT]->(d)",
                    pid=pid,
                    doc_id=doc_id,
                )

    created_persons = len(
        {v for v in name_to_pid.values()}
    )
    return {
        "created_persons": created_persons,
        "linked_persons": linked_persons,
        "created_relations": created_relations,
        "skipped": skipped_relations,
        "mapping": name_to_pid,
    }


def sync_task_entries(task_id: str) -> dict:
    """把任务已识别页的谱书内容（扁平 content 段落）幂等聚合写入 content_entries。

    扁平化规则（用户决策）：AI 识别输出不再区分 世系/功德/… type → 每页正文（含页内人工
    补充段落）合并为一条 type=「其他」的条目（title=「第N页」），同 task_id+页码+type+title
    幂等更新，避免重复应用产生重复行；content_entries.type 列保留，供谱书内容页分类浏览与
    人工补录时自选类型使用。

    插图页补充识别条目（type=插图）随本页正文逐条幂等入库；谱系/房支随任务记录，
    供谱系管理页按谱系聚合展示与校对。
    """
    task = _get_task(task_id)
    if not task:
        return {"added": 0, "updated": 0}
    pages = (task.result or {}).get("pages", [])
    inserted = updated = 0
    with SessionLocal() as db:
        def _upsert(e: dict, page_no: int, source: str) -> None:
            nonlocal inserted, updated
            if not isinstance(e, dict):
                return
            etype = str(e.get("type", "")).strip()
            text = str(e.get("text", "")).strip()
            if not etype or not text:
                return
            title = str(e.get("title") or "").strip()[:200]
            row = (
                db.query(ContentEntry)
                .filter(
                    ContentEntry.task_id == task_id,
                    ContentEntry.page_no == page_no,
                    ContentEntry.type == etype,
                    ContentEntry.title == title,
                )
                .first()
            )
            if row:
                row.text = text
                row.status = "active"
                # 任务谱系可能已被重建（apply 前置 ensure 谱系），同步修正过期归属，
                # 避免 content_entries 挂在已删除谱系下导致"人→内容"关联断
                if row.lineage_id != task.lineage_id:
                    row.lineage_id = task.lineage_id
                if row.branch_id != task.branch_id:
                    row.branch_id = task.branch_id
                updated += 1
            else:
                db.add(
                    ContentEntry(
                        entry_id=uuid.uuid4().hex[:24],
                        task_id=task_id,
                        lineage_id=task.lineage_id,
                        branch_id=task.branch_id,
                        type=etype,
                        title=title,
                        text=text,
                        page_no=page_no,
                        source=source,
                        status="active",
                    )
                )
                inserted += 1

        for p in pages:
            page_no = p.get("page_no")
            # 扁平正文：整页 content 段落合并为一条 type=其他（AI 输出不再分类）
            paras = []
            for c in p.get("content") or []:
                t = str(c.get("text") if isinstance(c, dict) else c or "").strip()
                if t:
                    paras.append(t)
            if paras:
                _upsert(
                    {
                        "type": "其他",
                        "title": f"第{page_no}页",
                        "text": "\n\n".join(paras),
                    },
                    page_no,
                    "ai",
                )
            # 插图页补充识别（type=插图）：apply 时随谱书内容一并入库，供 RAG 检索图注
            for e in p.get("illustrations") or []:
                _upsert(e, page_no, "ai")
        db.commit()
    logger.info("任务 %s：谱书内容聚合完成 +%d / ~%d", task_id, inserted, updated)
    return {"added": inserted, "updated": updated}





# ============ 插图页补充识别（正文极少页的图注条目） ============
ILLUSTRATION_TEXT_THRESHOLD = 120  # 页 content 正文低于此字符数视为「疑似插图/封面页」候选


def _page_body_chars(page: dict) -> int:
    """估算一页正文量（content 段落文本字符总数），用于插图页候选判定。"""
    total = 0
    for c in page.get("content") or []:
        total += len(str(c.get("text") if isinstance(c, dict) else c or ""))
    return total


async def ensure_illustration_pass(task_id: str) -> None:
    """插图页补充识别（幂等）：对识别成功但正文很少的页做一次图注生成。

    动机：纯照片/图画/封面/地图页几乎无正文，正文检索搜不到；谱书中这类页常含祠堂、
    祖宅、坟山、地图、人物像等珍贵图像。本函数在卷级整理前对候选页调用 VLM 描述图，
    产出与 entries 同构的 {type:插图,...} 存 pages[i].illustrations，审核「写入图谱」
    （apply）时随 sync_task_entries 入库，进而被 RAG 检索到（如搜「宗祠/祖宅/地图」）。

    结果标记存 task.result.illustration_pass_done，已补过不重复跑。单页失败只跳过该页，
    不影响识别/整理主流程。
    """
    try:
        task = _get_task(task_id)
        if not task or not task.result:
            return
        result = task.result
        if result.get("illustration_pass_done"):
            return
        pages = result.get("pages") or []
        if not pages:
            return
        candidates = [
            p
            for p in pages
            if not p.get("failed")
            and p.get("image_url")
            and "illustrations" not in p
            and _page_body_chars(p) < ILLUSTRATION_TEXT_THRESHOLD
        ]
        if not candidates:
            result["illustration_pass_done"] = True
            _update_task(task_id, result=dict(result))
            return
        client = file_service._client()  # noqa: SLF001
        tmp_dir = os.path.join(_task_dir(task_id), "illus")
        os.makedirs(tmp_dir, exist_ok=True)
        try:
            for page in candidates:
                no = page.get("page_no")
                object_name = page["image_url"].removeprefix("/files/")
                tmp = os.path.join(tmp_dir, f"i{no}.png")
                try:
                    await run_in_threadpool(
                        client.fget_object, settings.MINIO_BUCKET, object_name, tmp
                    )
                    async with _vision_semaphore:
                        items = await vision_service.describe_illustrations(tmp)
                    page["illustrations"] = (items or [])[:4]
                    result["pages"] = pages
                    _update_task(task_id, result=dict(result))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "任务 %s 第 %s 页插图识别失败（跳过）: %s", task_id, no, exc
                    )
                    page.setdefault("illustrations", [])
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        result["illustration_pass_done"] = True
        _update_task(task_id, result=dict(result))
        logger.info("任务 %s：插图页补充识别完成", task_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("任务 %s 插图页补充识别异常（忽略，不影响主流程）", task_id)
