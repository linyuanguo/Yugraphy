import asyncio
import hashlib
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Set

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, driver, get_db
from app.core.security import get_current_user, require_module_write
from app.models.orm import ImportTask, TaskPageJson, User
from app.models.schemas import (
    ApplyRequest,
    ApplyResponse,
    ImportTaskDetail,
    ImportTaskOut,
    PageReviewData,
    ReviewItem,
    ReviewRelationItem,
    ReviewSaveRequest,
)
from app.services import consolidate_service, delete_ops, import_service, lineage_service, task_registry
from app.services.audit_service import log_action
from app.utils.image_utils import sanitize_filename

router = APIRouter(prefix="/tasks", tags=["导入任务"])

ALLOWED_EXTS = {".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}

logger = logging.getLogger("genealogy.tasks")

# 自动创建的谱系占位名称与说明（用户可去谱系管理改名完善）
AUTO_LINEAGE_NOTE = "由文件名自动创建的谱系，请核对修改名称与信息"


async def _lineage_names(
    lineage_ids: set, branch_ids: set
) -> tuple[dict, dict]:
    """批量查询谱系/房支 id -> name。"""
    lmap: dict = {}
    bmap: dict = {}
    if lineage_ids:
        async with driver.session() as session:
            rec = await session.run(
                "MATCH (l:Lineage) WHERE l.lineage_id IN $ids RETURN l.lineage_id AS id, l.name AS name",
                ids=list(lineage_ids),
            )
            lmap = {r["id"]: r["name"] async for r in rec}
    if branch_ids:
        async with driver.session() as session:
            rec = await session.run(
                "MATCH (b:Branch) WHERE b.branch_id IN $ids RETURN b.branch_id AS id, b.name AS name",
                ids=list(branch_ids),
            )
            bmap = {r["id"]: r["name"] async for r in rec}
    return lmap, bmap


def _to_out(
    task: ImportTask,
    lmap: Optional[dict] = None,
    bmap: Optional[dict] = None,
    doc_ids: Optional[Set[str]] = None,
    user_map: Optional[dict] = None,
    pages_ready: Optional[Set[str]] = None,
) -> ImportTaskOut:
    lmap = lmap or {}
    bmap = bmap or {}
    user_map = user_map or {}
    # 能否断点续跑：已有识别结果，或页镜像里还有已转好的页面图（原件可不在）
    can_resume = bool((task.result or {}).get("pages")) or (
        pages_ready is not None and task.task_id in pages_ready
    )
    failed_pages = 0
    if task.result:
        fp = task.result.get("failed_pages")
        if isinstance(fp, list):
            failed_pages = len(fp)
        else:
            failed_pages = sum(
                1 for p in task.result.get("pages", []) if p.get("failed")
            )
    # 老任务（无 applied_at 标记）以 Neo4j Document 节点存在性补判是否已写入图谱
    applied = bool(
        task.applied_at or (doc_ids and task.task_id in doc_ids)
    )
    # 人工审核状态：写入图谱 / 归档的前置闸（整卷保存过 = 已审核；老任务按逐页暂存判定）
    try:
        rev = import_service.review_status(task)
    except Exception:  # noqa: BLE001
        rev = {
            "reviewed": False, "reviewed_at": None, "reviewed_by_name": None,
            "reviewed_pages": 0, "total_pages": 0,
        }
    return ImportTaskOut(
        task_id=task.task_id,
        file_path=os.path.basename(task.file_path or ""),
        file_type=task.file_type,
        file_size=task.file_size,
        total_pages=task.total_pages or 0,
        done_pages=task.done_pages or 0,
        failed_pages=failed_pages,
        status=task.status,
        stage=task.stage or "",
        pause_reason=task.pause_reason,
        error_msg=task.error_msg,
        lineage_id=task.lineage_id,
        lineage_name=lmap.get(task.lineage_id) if task.lineage_id else None,
        branch_id=task.branch_id,
        branch_name=bmap.get(task.branch_id) if task.branch_id else None,
        applied=applied,
        applied_at=task.applied_at,
        reviewed=bool(rev.get("reviewed")),
        reviewed_at=rev.get("reviewed_at"),
        reviewed_by_name=rev.get("reviewed_by_name"),
        reviewed_pages=int(rev.get("reviewed_pages") or 0),
        review_total_pages=int(rev.get("total_pages") or 0),
        # 新管线任务缺整卷结果/失效：前端据此对未审核 done 卷显示「重新整理」
        needs_reconsolidate=_need_consolidate(task),
        can_resume=can_resume,
        archived=bool(task.archived_at),
        archived_at=task.archived_at,
        archived_by=task.archived_by,
        archived_by_name=user_map.get(task.archived_by) if task.archived_by else None,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _ensure_not_archived(task: ImportTask) -> None:
    """归档任务 = 只读档案：图片保留，禁止任何写操作（审核/重识别/整理/删除/清理页图）。"""
    if task.archived_at:
        raise HTTPException(
            status_code=403,
            detail="该任务已归档为只读档案（页面图片与记录受保护）。"
            "如需修改或删除，请先在「档案文件 → AI 识别归档」中「取回」。",
        )


async def _task_applied_to_graph(task: ImportTask) -> bool:
    """任务是否已写入图谱：新任务看 applied_at 标记；老任务按 Neo4j Document 节点补判。"""
    if task.applied_at:
        return True
    try:
        async with driver.session() as session:
            rec = await session.run(
                "MATCH (d:Document {doc_id: $id}) RETURN d.doc_id AS id LIMIT 1",
                id=task.task_id,
            )
            async for _r in rec:
                return True
            return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("查询任务 %s 图谱 Document 失败: %s", task.task_id, exc)
        return False


async def _validate_lineage(lineage_id: Optional[str], branch_id: Optional[str]) -> None:
    """校验谱系/房支存在（不存在则 400）。"""
    if not lineage_id and not branch_id:
        return
    try:
        await lineage_service.lineage_branch_names(lineage_id, branch_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class _CheckNamesIn(BaseModel):
    names: List[str] = Field(min_length=1, max_length=200)


class _ConsolSaveIn(BaseModel):
    """卷级整理（第二段）审核暂存 payload。"""

    persons: List[ReviewItem] = []
    relations: List[ReviewRelationItem] = []
    # reviewed 三态：true=落「已人工审核」标记；false=取消标记；不传(None)=普通暂存不动
    reviewed: Optional[bool] = None


@router.post("/check-names")
def check_import_names(
    data: _CheckNamesIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_module_write("tasks")),
):
    """上传前同名预检：返回每个文件名是否已存在非 failed 的导入任务。

    由前端在真正上传大文件前调用，用于提醒用户同名文件此前已导入过，
    避免重复上传后才在内容判重(sha256)环节被拦。失败(failed)任务允许重传，不计入。
    """
    seen: set = set()
    unique: List[str] = []
    for n in data.names:
        s = sanitize_filename(n)
        if s and s not in seen:
            seen.add(s)
            unique.append(s)
    tasks = (
        db.query(ImportTask)
        .filter(ImportTask.status.in_(["pending", "running", "done", "paused"]))
        .order_by(ImportTask.created_at.desc())
        .limit(1000)
        .all()
    )
    by_name: Dict[str, List[dict]] = {}
    for t in tasks:
        bn = os.path.basename(t.file_path or "")
        if bn in unique:
            by_name.setdefault(bn, []).append(
                {
                    "task_id": t.task_id,
                    "status": t.status,
                    "file_size": t.file_size,
                    "created_at": t.created_at,
                }
            )
    return [{"name": n, "existing": by_name.get(n, [])} for n in unique]


@router.post("/import", response_model=ImportTaskOut, status_code=201)
async def create_import_task(
    file: UploadFile = File(...),
    lineage_id: str | None = Form(default=None),
    branch_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks")),
):
    await _validate_lineage(lineage_id, branch_id)
    filename = sanitize_filename(file.filename or "scan")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=400, detail=f"不支持的文件类型 {ext}，仅支持 {sorted(ALLOWED_EXTS)}"
        )

    # 未手动指定谱系时，任务不归属任何谱系；真正创建谱系放到「审核写入图谱」
    # （apply_task）时按文件名档案编号自动创建（J148-001-001-001.pdf → 谱系 J148-001-001）。
    # 目的：谱系管理只展示"已审核写入图谱"的谱系，AI 识别但未审核的任务不产生谱系记录。
    task_id = uuid.uuid4().hex[:12]
    save_dir = os.path.join(settings.UPLOAD_DIR, "tasks", task_id)
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, filename)

    # 流式落盘：扫描件可达数百 MB，避免一次性载入内存；边写边算 sha256 供内容判重
    # 期间标记「上传中」：新转图任务会让位（优先级 A：上传 > 转图），
    # 避免批量上传时转图抢 CPU 拖慢收文件
    total = 0
    hasher = hashlib.sha256()
    import_service.note_upload_start()
    try:
        with open(save_path, "wb") as f:
            while True:
                chunk = await file.read(8 * 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                f.write(chunk)
                hasher.update(chunk)
    finally:
        import_service.note_upload_end()
    if total == 0:
        try:
            os.remove(save_path)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="空文件")
    digest = hasher.hexdigest()

    # 重复文件检测：按内容 sha256 判重（改名/复制粘贴的副本都能查出）；
    # 老任务没有哈希时退回「同名 + 同字节数」兜底；failed 一律允许重传
    dup = None
    row = (
        db.query(ImportTask)
        .filter(
            ImportTask.file_sha256 == digest,
            ImportTask.status.in_(["pending", "running", "done", "paused"]),
        )
        .order_by(ImportTask.created_at.desc())
        .first()
    )
    if row:
        dup = row
    if not dup:
        dup = next(
            (
                t
                for t in db.query(ImportTask)
                .filter(
                    ImportTask.file_size == total,
                    ImportTask.file_sha256.is_(None),
                    ImportTask.status.in_(["pending", "running", "done", "paused"]),
                )
                .order_by(ImportTask.created_at.desc())
                .all()
                if t.file_path and os.path.basename(t.file_path) == filename
            ),
            None,
        )
    if dup:
        try:
            os.remove(save_path)
        except OSError:
            pass
        dup_time = dup.created_at.strftime("%Y-%m-%d %H:%M") if dup.created_at else "未知时间"
        log_action(
            current_user.id,
            "duplicate_file",
            "task",
            dup.task_id,
            detail=(
                f"重复文件拦截：上传 {filename}（{total} 字节）与既有任务内容相同"
                f"（{os.path.basename(dup.file_path)}，{dup_time}）"
            ),
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"检测到相同内容的文件已导入过（任务状态：{dup.status}，"
                f"{dup_time}，原文件名：{os.path.basename(dup.file_path)}）。"
                "如确需重新导入，请先在任务列表中删除原任务。"
            ),
        )

    task = ImportTask(
        task_id=task_id,
        file_path=save_path,
        file_type=ext.lstrip("."),
        file_size=total,
        file_sha256=digest,
        status="pending",
        lineage_id=lineage_id,
        branch_id=branch_id,
        created_by=current_user.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    log_action(
        current_user.id, "create_import_task", "task", task_id,
        detail=f"发起 AI 导入 {filename}（谱系 {lineage_id or '未指定'}）",
    )

    # 同一事件循环内异步执行（避免跨 loop 使用 Neo4j 驱动）
    asyncio.create_task(import_service.run_import_task(task_id))
    lmap, bmap = await _lineage_names({lineage_id} if lineage_id else set(), {branch_id} if branch_id else set())
    return _to_out(task, lmap, bmap)


@router.get("", response_model=List[ImportTaskOut])
async def list_tasks(
    db: Session = Depends(get_db),
    archived: Optional[bool] = None,
    _: User = Depends(get_current_user),
):
    query = db.query(ImportTask)
    if archived is not None:
        query = query.filter(
            ImportTask.archived_at.isnot(None)
            if archived
            else ImportTask.archived_at.is_(None)
        )
    tasks = query.order_by(ImportTask.created_at.desc()).limit(100).all()
    # 页镜像里仍有已转页面图的任务 = 可断点续跑（与原始扫描件是否已清理无关）
    pages_ready: Set[str] = set()
    if tasks:
        try:
            rows = (
                db.query(TaskPageJson.task_id, func.count(TaskPageJson.id))
                .filter(TaskPageJson.task_id.in_([t.task_id for t in tasks]))
                .group_by(TaskPageJson.task_id)
                .all()
            )
            pages_ready = {tid for tid, cnt in rows if (cnt or 0) > 0}
        except Exception as exc:  # noqa: BLE001
            logger.warning("查询页镜像计数失败: %s", exc)
    lmap, bmap = await _lineage_names(
        {t.lineage_id for t in tasks if t.lineage_id},
        {t.branch_id for t in tasks if t.branch_id},
    )
    # 老任务无 applied_at 标记：一次查出所有已入库 Document，补判「已写入图谱」
    doc_ids: Set[str] = set()
    try:
        async with driver.session() as session:
            rec = await session.run("MATCH (d:Document) RETURN d.doc_id AS id")
            doc_ids = {r["id"] async for r in rec if r["id"]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("查询图谱 Document 集合失败: %s", exc)
    # 归档操作人 id -> 用户名（展示用）
    archiver_ids = {t.archived_by for t in tasks if t.archived_by}
    user_map = {
        u.id: u.username
        for u in db.query(User).filter(User.id.in_(archiver_ids))
        if archiver_ids
    }
    return [
        _to_out(t, lmap, bmap, doc_ids, user_map, pages_ready=pages_ready)
        for t in tasks
    ]


@router.get("/{task_id}", response_model=ImportTaskDetail)
async def get_task(
    task_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    lmap, bmap = await _lineage_names(
        {task.lineage_id} if task.lineage_id else set(),
        {task.branch_id} if task.branch_id else set(),
    )
    detail = ImportTaskDetail(**dict(_to_out(task, lmap, bmap)))
    pages: List[PageReviewData] = []
    if task.result and task.result.get("pages"):
        for p in task.result["pages"]:
            pages.append(
                PageReviewData(
                    page_no=p["page_no"],
                    image_url=p["image_url"],
                    ai_meta=p.get("ai_meta"),
                    notes=p.get("notes"),
                    persons=p.get("persons", []),
                    content=p.get("content", []),
                    relations=p.get("relations", []),  # 兼容旧任务；新任务恒空
                    page_notes=p.get("page_notes"),  # 兼容旧任务；新任务改 notes
                    reviewed=bool(p.get("reviewed", False)),
                    failed=bool(p.get("failed", False)),
                    error=p.get("error"),
                )
            )
    detail.pages = pages
    if task.result:
        detail.consolidated = task.result.get("consolidated")
        detail.consolidation_stale = bool(task.result.get("consolidation_stale"))
    return detail


@router.post("/{task_id}/archive")
async def archive_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks")),
):
    """把已写入图谱的完成任务归档为只读档案：页面图片保留不删、禁止删除/清理/改写，
    供「档案文件 → AI 识别归档」只读查阅；发现问题可先「取回」再继续审核/重识别。
    """
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.archived_at:
        return {"ok": True}
    if task.status != "done":
        raise HTTPException(status_code=400, detail="任务尚未完成 AI 识别，暂不能归档")
    if not await _task_applied_to_graph(task):
        raise HTTPException(
            status_code=400,
            detail="该任务尚未写入图谱。请在审核页点「✔ 写入图谱」，成功后再归档到档案文件。",
        )
    now = datetime.now()
    task.applied_at = task.applied_at or now
    task.archived_at = now
    task.archived_by = current_user.id
    db.commit()
    log_action(
        current_user.id,
        "archive_import_task",
        "task",
        task_id,
        detail=f"归档到档案文件（只读）：{os.path.basename(task.file_path or '')}",
    )
    return {"ok": True}


@router.post("/{task_id}/unarchive")
async def unarchive_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks", "documents")),
):
    """从档案库取回任务：解除只读，可继续审核/重识别/删除（页面图片全程保留）。

    取回时同步清理该任务此前「写入图谱」产生的谱系数据与检索向量
    （content_entries + rag_vectors + 仅本任务贡献的人物），使重新整理/再写入
    不会与旧数据冲突或重复；操作员取回属任务编辑操作，无需申请管理员审批删除谱系数据。
    """
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not task.archived_at:
        raise HTTPException(status_code=400, detail="该任务未归档，无需取回")
    task.archived_at = None
    db.commit()
    # 清理该任务此前写入的谱系数据与向量（含仅本任务贡献的 Neo4j 人物）；
    # 该任务归属的卷号谱系若已「空且独占」，也从谱系管理中一并移除。
    try:
        purge = await delete_ops.purge_task_written(db, task_id)
    except Exception as exc:  # 清理失败不应阻塞取回
        purge = {
            "removed_entries": -1,
            "removed_vectors": -1,
            "removed_persons": -1,
            "removed_lineages": -1,
        }
        logger.exception("取回任务清理写入数据失败: %s", exc)
    lineage_txt = (
        f"、谱系 {purge['removed_lineages']} 个" if purge.get("removed_lineages") else ""
    )
    log_action(
        current_user.id,
        "unarchive_import_task",
        "task",
        task_id,
        detail=(
            f"从档案文件取回（恢复可编辑）：{os.path.basename(task.file_path or '')}"
            f"，已清理该任务写入数据：内容条目 {purge['removed_entries']}、"
            f"检索向量 {purge['removed_vectors']}、专属人物 {purge['removed_persons']}"
            f"{lineage_txt}"
        ),
    )
    return {"ok": True, "purged": purge}


@router.post("/{task_id}/pages/{page_no}/re-extract")
async def re_extract_page(
    task_id: str,
    page_no: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks")),
):
    """对某一页单独重新识别（失败页/效果不佳时使用）。

    识别参数与导入流水线不同：单页硬超时放宽到 REDO_PAGE_TIMEOUT；整页仍失败时自动
    横向分块兜底（notes 会注明「分块识别」）。请求可能耗时较长，前端已放宽超时。
    """
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _ensure_not_archived(task)
    page = await import_service.re_extract_page(task_id, page_no)
    if not page:
        raise HTTPException(status_code=404, detail="页面不存在")
    log_action(current_user.id, "re_extract_page", "task", task_id, f"重识别第 {page_no} 页")
    return page


@router.delete("/{task_id}/pages")
async def cleanup_task_pages(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks")),
):
    """清理该任务的页面图（MinIO），释放存储空间。

    任务记录与已提取的数据保留；清理后仍可查看已识别的文字结果，
    但无法再对照原图校对或对单页重新识别。
    """
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _ensure_not_archived(task)
    removed = await import_service.cleanup_task_pages(task_id)
    log_action(
        current_user.id, "cleanup_task_pages", "task", task_id, f"清理 {removed} 个页面图"
    )
    return {"removed": removed}


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks")),
):
    """删除任务（09-07 审批流）。

    - 管理员：直接真正删除（记录、页面图、本地中间文件一并清除；正在后台运行
      的任务会先取消对应协程，避免协程白跑占槽堵住新任务）。
    - 操作员：不真正删除，生成一条待审批的「删除申请」；待管理员在
      「系统设置 → 删除审批」通过后才真正删除。

    若该任务已写入图谱，已写入的人物/关系不受影响（仅删除导入记录与识别缓存）。
    """
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _ensure_not_archived(task)
    if current_user.role == "admin":
        info = await delete_ops.perform_delete_task(db, task_id, actor_id=current_user.id)
        return {
            "deleted": True,
            "removed_pages": info["removed_pages"],
            "interrupted": info["interrupted"],
        }
    try:
        req = delete_ops.request_delete_task(db, current_user, task)
    except delete_ops.PendingDeleteExists as exc:
        submitter = exc.submitter_name or "其他操作员"
        raise HTTPException(
            status_code=409,
            detail=f"该任务已有一条待审核的删除申请（提交人：{submitter}），请勿重复提交。",
        ) from exc
    return {
        "deleted": False,
        "requested": True,
        "request_id": req.id,
        "target_id": task_id,
        "target_name": os.path.basename(task.file_path or "") or task_id,
    }


@router.post("/{task_id}/resume")
async def resume_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks")),
):
    """断点续跑：对失败/个别页失败的任务，直接用已转好的页面图继续 AI 识别。

    不需要重新上传；任务进入 running，页面刷新即见进度。
    """
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _ensure_not_archived(task)
    # 同步前置校验：不满足立即报错（避免 create_task 后台静默失败、前端误以为已续跑）
    if task.status in ("pending", "running"):
        raise HTTPException(status_code=400, detail="任务已在排队或处理中，请勿重复提交")
    if task.status not in ("failed", "done", "paused"):
        raise HTTPException(
            status_code=400, detail=f"任务状态 {task.status}，无法续跑"
        )
    if task.status == "done":
        pages = (task.result or {}).get("pages", [])
        failed_arr = (task.result or {}).get("failed_pages") or []
        all_ok = (
            not any(p.get("failed") for p in pages)
            and not failed_arr
            and (task.done_pages or 0) >= (task.total_pages or 0)
        )
        if all_ok:
            raise HTTPException(
                status_code=400, detail="任务已全部识别完成，无需续跑"
            )
    asyncio.create_task(import_service.resume_task(task_id))
    log_action(
        current_user.id, "resume_import_task", "task", task_id,
        detail=(
            f"续跑识别（已识别 {task.done_pages or 0}/{task.total_pages or 0} 页，"
            "补齐剩余/失败页面，无需重新上传）"
        ),
    )
    return {"ok": True, "queued": True}


@router.post("/{task_id}/pause")
async def pause_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks")),
):
    """暂停扫描件导入任务（处理中/排队中）。

    只把 DB 状态置为 paused（后台协程在安全点自查后干净收尾：正在识别的当前页 /
    整理中的当前片段完成后停下，已识别页保留）。恢复=点「继续」（走断点续跑，
    补齐剩余页并自动整卷整理）或对整卷整理中断的任务点「重新整理」。
    """
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _ensure_not_archived(task)
    if task.status not in ("pending", "running"):
        raise HTTPException(
            status_code=400, detail=f"任务状态 {task.status}，无法暂停"
        )
    task.status = "paused"
    task.pause_reason = "manual"  # 人工暂停（区别于整卷整理为给识别让位的自动暂停）
    db.commit()
    log_action(
        current_user.id, "pause_import_task", "task", task_id,
        detail=(
            f"暂停导入任务（已识别 {task.done_pages or 0}/{task.total_pages or 0} 页，"
            "将在当前页/片段完成后停下，可点「继续」恢复）"
        ),
    )
    return {"ok": True, "paused": True}


@router.post("/{task_id}/consolidate")
async def reconsolidate_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks")),
):
    """手动重新执行卷级整理（第二段：人物归并 + 世系关系推断）。

    逐页重识别、失败页补齐后内容变化，点此重跑以更新整卷人物/关系。
    """
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _ensure_not_archived(task)
    if task.status == "running":
        raise HTTPException(status_code=400, detail="任务正在处理中，请稍候再试")
    if not task.result or not task.result.get("pages"):
        raise HTTPException(status_code=400, detail="任务尚无已识别页面数据，无法整理")
    asyncio.create_task(consolidate_service.run_consolidation(task_id))
    log_action(
        current_user.id, "reconsolidate_task", "task", task_id,
        detail="重新执行卷级整理（人物归并 + 世系关系推断）",
    )
    return {"ok": True, "queued": True}


@router.post("/{task_id}/re-extract-all")
async def re_extract_all_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks")),
):
    """整卷重新 AI 识别：对本任务所有页面（含失败页与已成功页）全部重新调用识别。

    与「重新整理」（只重跑第二段人物归并/关系推断）不同，这里整卷第一段逐页提取
    全部重跑；完成后自动重新整理整卷。批量识别质量不理想时可整卷重跑一次。
    已写入图谱的数据不受影响；审核页尚未暂存的人工校对会被新识别覆盖。
    """
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _ensure_not_archived(task)
    if task.status == "running":
        raise HTTPException(status_code=400, detail="任务正在处理中，请稍候再试")
    if not task.result or not task.result.get("pages"):
        raise HTTPException(status_code=400, detail="任务尚无已识别页面数据，无法整卷重识别")
    asyncio.create_task(import_service.resume_task(task_id, force_all=True))
    log_action(
        current_user.id, "re_extract_all", "task", task_id,
        detail=f"整卷重新 AI 识别（{task.total_pages or 0} 页全部重跑，完成后自动重新整理整卷）",
    )
    return {"ok": True, "queued": True}


@router.post("/{task_id}/consolidated-review")
async def save_consolidated_review(
    task_id: str,
    payload: _ConsolSaveIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks")),
):
    """暂存卷级整理结果的审核（人物/关系的勾选与编辑，断点续审）。

    payload.reviewed 三态：true=人工确认「本卷已校对完成」，落审核标记
    （reviewed/reviewed_at/reviewed_by*），之后才允许「写入图谱并归档」；
    false=显式取消已审核标记（误标/发现差错后可改回未审核）；不传=普通暂存不动。
    """
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _ensure_not_archived(task)
    cons = import_service.save_consolidated_review(
        task_id,
        [p.model_dump() for p in payload.persons],
        [r.model_dump() for r in payload.relations],
        reviewer={"id": current_user.id, "username": current_user.username},
        mark_reviewed=payload.reviewed,
    )
    if not cons:
        raise HTTPException(
            status_code=404,
            detail="该任务没有卷级整理结果（请先点「重新整理」生成，或旧任务走逐页审核）",
        )
    log_action(
        current_user.id, "save_consolidated_review", "task", task_id,
        detail=f"暂存卷级整理审核：人物 {len(cons.get('persons') or [])}、关系 {len(cons.get('relations') or [])}",
    )
    return {"ok": True}


@router.post("/{task_id}/pages/{page_no}/review")
async def save_page_review(
    task_id: str,
    page_no: int,
    payload: ReviewSaveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks")),
):
    """暂存某页审核结果（断点续审）。"""
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _ensure_not_archived(task)
    page = import_service.save_page_review(
        task_id,
        page_no,
        {
            "persons": [p.model_dump() for p in payload.persons],
            "relations": [r.model_dump() for r in payload.relations],
            "content": [c for c in payload.content],
            "notes": payload.notes,
            "page_notes": payload.page_notes,
            "reviewed": payload.reviewed,
        },
    )
    if not page:
        raise HTTPException(status_code=404, detail="页面不存在")
    return {"ok": True, "page_no": page_no}


def _need_consolidate(task: ImportTask) -> bool:
    """写入前是否需要先跑「卷级整理」。

    判定：整卷结果已过期（单页重识别、人工改动页内容等置了 consolidation_stale），
    或新管线任务压根没有 consolidated（逐页不再产关系）。整理由全局并发闸排队，
    与其它 AI 识别/整理共存时不抢占、自动排队（consolidate_service）。
    """
    result = task.result or {}
    if bool(result.get("consolidation_stale")):
        return True
    pages = result.get("pages") or []
    if (
        not result.get("consolidated")
        and bool(pages)
        and all(not (p.get("relations") or []) for p in pages)
        and task.status == "done"
    ):
        return True
    return False


def _review_gate_error(task: ImportTask, force: bool) -> Optional[str]:
    """人工审核闸：通过返回 None；未通过返回拒绝理由（写入图谱/归档的前置条件）。"""
    if force:
        return None
    rev = import_service.review_status(task)
    if rev.get("reviewed"):
        return None
    result = task.result or {}
    total = int(rev.get("total_pages") or 0)
    done = int(rev.get("reviewed_pages") or 0)
    if result.get("consolidated"):
        hint = (
            "整卷人物/关系尚未人工审核：请先在审核页打开「🧑 整卷人物／🕸 整卷关系」"
            "校对后点「✅ 标记已审核」，再执行写入图谱。"
        )
    else:
        hint = (
            f"本卷仍有 {max(0, total - done)}/{total} 页未人工审核："
            "请在审核页逐页核对并点「💾 保存」。"
        )
    return (
        f"该卷尚未完成人工审核，已拒绝写入图谱。{hint}"
        "如确需直接写入，请在提示框中选择「仍要写入」（会记入审计日志）。"
    )


async def _write_task_to_graph(
    db: Session,
    task: ImportTask,
    persons: List[dict],
    relations: List[dict],
    user_id: int,
    username: str,
    force: bool = False,
    page_no: Optional[int] = None,
) -> dict:
    """把任务结果写入 Neo4j 图谱 + 聚合谱书内容（写入/归档一键流程与 /apply 共用）。"""
    task_id = task.task_id
    # 未显式传内容时，优先取卷级整理结果（第二段人物/关系）；老任务退回逐页结果
    if not persons and not relations and task.result:
        cons = task.result.get("consolidated")
        if cons:
            persons = [
                {
                    k: p.get(k)
                    for k in (
                        "name", "gender", "birth_year", "death_year",
                        "birth_place", "biography", "confidence", "confirmed",
                    )
                    if k in p
                }
                for p in (cons.get("persons") or [])
            ]
            relations = [
                {
                    k: r.get(k)
                    for k in (
                        "type", "from_name", "to_name",
                        "marriage_date", "confidence", "confirmed",
                    )
                    if k in r
                }
                for r in (cons.get("relations") or [])
            ]
        if not persons and not relations:
            pages = task.result.get("pages", [])
            selected = (
                [p for p in pages if p["page_no"] == page_no] if page_no else pages
            )
            for p in selected:
                persons.extend(p.get("persons", []))
                relations.extend(p.get("relations", []))

    # 谱系归属修正（防"任务引用了已删除的谱系"导致人物静默写入却无归属）
    lineage_id = task.lineage_id
    if lineage_id and not await lineage_service.get_lineage(lineage_id):
        logger.warning("任务 %s：谱系引用 %s 已不存在，本次写入将重建/清空归属", task_id, lineage_id)
        lineage_id = None
    if not lineage_id and settings.AUTO_ATTACH_LINEAGE_BY_FILENAME:
        auto_code = lineage_service.infer_lineage_key_from_filename(
            os.path.basename(task.file_path or "")
        )
        if auto_code:
            try:
                lineage = await lineage_service.ensure_lineage_by_code(
                    auto_code, name=auto_code, note=AUTO_LINEAGE_NOTE,
                )
                lineage_id = lineage["lineage_id"]
            except Exception as exc:  # noqa: BLE001
                logger.warning("任务 %s：写入前自动创建谱系失败: %s", task_id, exc)
    if lineage_id != task.lineage_id:
        task.lineage_id = lineage_id
        db.commit()

    result = await import_service.apply_review_to_graph(
        persons,
        relations,
        doc_id=task.task_id,
        doc_title=os.path.basename(task.file_path or ""),
        lineage_id=task.lineage_id,
        branch_id=task.branch_id,
    )
    entries_stats = {"added": 0, "updated": 0}
    try:
        entries_stats = import_service.sync_task_entries(task_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("任务 %s：谱书内容聚合失败（不影响图谱写入）: %s", task_id, exc)
    forced = force and not import_service.review_status(task).get("reviewed")
    log_action(
        user_id, "apply_review", "task", task_id,
        detail=(
            f"写入图谱: {result}; 谱书内容聚合: {entries_stats}"
            + ("；⚠ 强制写入未经人工审核的卷" if forced else "")
        ),
    )
    task.applied_at = datetime.now()
    db.commit()
    return result


@router.post("/{task_id}/apply", response_model=ApplyResponse)
async def apply_task(
    task_id: str,
    payload: ApplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks")),
):
    """把（该页/全部）确认结果写入 Neo4j 图谱（不归档）。

    人工审核闸：本卷尚未人工审核 → 409 拒绝；前端二次确认后带 force=true 放行并记
    审计日志。单页写入（带 page_no）不校验整卷审核状态。
    需要「先整理再写入 + 归档」的一键流程走 /{task_id}/apply-archive。
    """
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _ensure_not_archived(task)

    # 人工审核闸（未审核 → 拒绝；前端二次确认后带 force=true 放行，记审计日志）
    if not payload.page_no:
        err = _review_gate_error(task, payload.force)
        if err:
            raise HTTPException(status_code=409, detail=err)

    persons = [p.model_dump() for p in payload.persons]
    relations = [r.model_dump() for r in payload.relations]

    return await _write_task_to_graph(
        db, task, persons, relations,
        current_user.id, current_user.username,
        force=payload.force, page_no=payload.page_no,
    )


class _ApplyArchiveIn(BaseModel):
    force: bool = False  # 跳过「尚未人工审核」闸（前端二次确认后带；记审计日志）
    archive: bool = True  # 写入成功后立即归档为只读档案


async def _apply_archive_bg(
    task_id: str, force: bool, archive: bool, user_id: int, username: str
) -> None:
    """一键「写入图谱并归档」后台流程：需整理则先整理（全局并发闸自动排队）
    → 写入图谱 → 归档。服务端全程执行，前端可离开页面，轮询任务状态即可。
    """
    try:
        with SessionLocal() as db:
            task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
            need = _need_consolidate(task) if task else False
        if need:
            logger.info("任务 %s：写入前先执行卷级整理（内容有变动/结果缺失）", task_id)
            await consolidate_service.run_consolidation(task_id)
        with SessionLocal() as db:
            task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
            if not task or task.archived_at:
                return
            if task.status != "done":
                logger.warning("任务 %s：状态 %s，非 done，跳过写入", task_id, task.status)
                return
            # 未人工审核 → 拒绝：前端二次确认后带 force 放行。但刚由本流程自动重新整理
            # （内容有改动/结果缺失）产生的新整卷结果必然无 reviewed 标记，视为已确认，
            # 直接按 force 语义写入并记审计（用户已接受整理后异常关系在谱系管理中修正）。
            bg_force = force or need
            err = _review_gate_error(task, bg_force)
            if err:
                logger.warning("任务 %s：写入前审核闸未通过 → %s", task_id, err)
                return
            await _write_task_to_graph(db, task, [], [], user_id, username, force=bg_force)
            if archive:
                now = datetime.now()
                task.applied_at = task.applied_at or now
                task.archived_at = now
                task.archived_by = user_id
                db.commit()
                log_action(
                    user_id, "archive_import_task", "task", task_id,
                    detail=f"写入图谱后自动归档（只读）：{os.path.basename(task.file_path or '')}",
                )
        logger.info("任务 %s：一键「写入图谱并归档」完成（先整理=%s）", task_id, need)
    except Exception as exc:  # noqa: BLE001
        logger.exception("任务 %s：一键写入并归档失败: %s", task_id, exc)


@router.post("/{task_id}/apply-archive")
async def apply_and_archive_task(
    task_id: str,
    payload: _ApplyArchiveIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("tasks")),
):
    """一键「写入图谱并归档」：有改动/结果缺失 → 先排队做卷级整理，再写入图谱并归档。

    对应流程「识别 → 自动整理 → 人工审核 → 写入图谱 → 归档」的收口动作：
    - 需整理（单页重识别、人工改页内容、整卷结果缺失）→ 先跑卷级整理（与其它 AI
      识别/整理共享并发闸，自动排队、不抢占），整理完立即写入并归档；
    - 无改动 → 直接写入图谱并归档。
    后台执行，端点立即返回 {queued:true, consolidate:是否需先整理}，前端轮询任务状态。
    """
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _ensure_not_archived(task)
    if task.status != "done":
        raise HTTPException(
            status_code=400, detail=f"任务状态 {task.status}，需「已完成」后才能写入图谱"
        )
    err = _review_gate_error(task, payload.force)
    if err:
        raise HTTPException(status_code=409, detail=err)
    need = _need_consolidate(task)
    asyncio.create_task(
        _apply_archive_bg(
            task_id, payload.force, payload.archive,
            current_user.id, current_user.username,
        )
    )
    log_action(
        current_user.id, "apply_archive_task", "task", task_id,
        detail=(
            f"一键写入图谱并归档（{'先整理再写入' if need else '直接写入'}；"
            f"归档={'是' if payload.archive else '否'}）"
        ),
    )
    return {"ok": True, "queued": True, "consolidate": need}
