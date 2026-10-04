"""谱书文字内容条目（源流/迁徙/家规家训/传记/艺文等）管理接口。

AI 识别结果在任务「写入图谱」时聚合到 content_entries（按谱系），
管理员在谱系管理页对照原图逐条校对；后续供访客图谱问答/展示使用。
"""
import logging
import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_module_write
from app.models.orm import ContentEntry, ImportTask, User
from app.models.schemas import ContentEntryCreate, ContentEntryOut, ContentEntryUpdate
from app.services import vision_service
from app.services.audit_service import log_action

router = APIRouter(prefix="/content-entries", tags=["谱书内容"])

logger = logging.getLogger("genealogy.content")


def _to_out(e: ContentEntry, task_name: Optional[str] = None) -> ContentEntryOut:
    out = ContentEntryOut.model_validate(e)
    out.task_name = task_name
    return out


@router.get("", response_model=List[ContentEntryOut])
async def list_content_entries(
    lineage_id: Optional[str] = Query(default=None),
    task_id: Optional[str] = Query(default=None),
    type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """按谱系/任务/类型/状态查询内容条目（登录即可读，供谱系管理/访客等页面浏览）。"""
    q = db.query(ContentEntry)
    if lineage_id:
        q = q.filter(ContentEntry.lineage_id == lineage_id)
    if task_id:
        q = q.filter(ContentEntry.task_id == task_id)
    if type:
        q = q.filter(ContentEntry.type == type)
    if status:
        q = q.filter(ContentEntry.status == status)
    rows = q.order_by(
        ContentEntry.type, ContentEntry.page_no, ContentEntry.id
    ).all()

    # 补充来源任务文件名
    task_names = {}
    tids = {r.task_id for r in rows if r.task_id}
    if tids:
        for t in (
            db.query(ImportTask)
            .filter(ImportTask.task_id.in_(tids))
            .all()
        ):
            task_names[t.task_id] = os.path.basename(t.file_path or "")
    return [_to_out(r, task_names.get(r.task_id)) for r in rows]


@router.post("", response_model=ContentEntryOut, status_code=201)
async def create_content_entry(
    data: ContentEntryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("lineages")),
):
    """人工补录谱书内容条目（source=manual，直接归属谱系/房支）。"""
    if data.type not in vision_service.ENTRY_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"类型不合法，可选：{'、'.join(vision_service.ENTRY_TYPES)}",
        )
    text = data.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="正文不能为空")
    row = ContentEntry(
        entry_id=uuid.uuid4().hex[:24],
        lineage_id=data.lineage_id or None,
        branch_id=data.branch_id or None,
        type=data.type,
        title=(data.title or "").strip()[:200],
        text=text[:4000],
        source="manual",
        status="active",
        created_by=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log_action(
        current_user.id,
        "create_content_entry",
        "content_entry",
        row.entry_id,
        detail=f"人工补录谱书内容[{row.type}] {row.title or '(无标题)'}",
    )
    return _to_out(row)


@router.put("/{entry_id}", response_model=ContentEntryOut)
async def update_content_entry(
    entry_id: str,
    data: ContentEntryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("lineages")),
):
    """校对条目：修改类型/标题/正文，或归档/恢复。"""
    row = (
        db.query(ContentEntry).filter(ContentEntry.entry_id == entry_id).first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="内容条目不存在")

    if data.type is not None:
        if data.type not in vision_service.ENTRY_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"类型不合法，可选：{'、'.join(vision_service.ENTRY_TYPES)}",
            )
        row.type = data.type
    if data.title is not None:
        row.title = data.title.strip()[:200]
    if data.text is not None:
        if not data.text.strip():
            raise HTTPException(status_code=400, detail="正文不能为空")
        row.text = data.text.strip()[:4000]
    if data.status is not None:
        if data.status not in ("active", "archived"):
            raise HTTPException(status_code=400, detail="状态不合法（active/archived）")
        row.status = data.status
    db.commit()
    db.refresh(row)
    log_action(
        current_user.id,
        "update_content_entry",
        "content_entry",
        row.entry_id,
        detail=f"校对谱书内容[{row.type}] {row.title or '(无标题)'} → status={row.status}",
    )
    return _to_out(row)


@router.delete("/{entry_id}", status_code=204)
async def delete_content_entry(
    entry_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("lineages")),
):
    """删除条目（误识别/废弃内容直接移除）。"""
    row = (
        db.query(ContentEntry).filter(ContentEntry.entry_id == entry_id).first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="内容条目不存在")
    db.delete(row)
    db.commit()
    log_action(
        current_user.id,
        "delete_content_entry",
        "content_entry",
        entry_id,
        detail=f"删除谱书内容[{row.type}] {row.title or '(无标题)'}",
    )
