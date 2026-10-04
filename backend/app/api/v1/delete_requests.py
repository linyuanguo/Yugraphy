"""删除审批接口（09-07 审批流）。

操作员删除「扫描件任务 / 谱系」时在删除接口里落 pending 申请（见 tasks.py /
lineages.py），管理员在此处集中处理：列表查看 / 通过（真正执行删除） / 驳回。
编辑器只能看到自己提交的申请；admin 可见全部。列表只读接口对所有登录用户开放
（编辑器需要拿自己申请的状态在源页面展示「删除审批中」标记）。
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.orm import DeleteRequest, User
from app.services import delete_ops
from app.services.audit_service import log_action

logger = logging.getLogger("genealogy.delete_requests")

router = APIRouter(prefix="/delete-requests", tags=["删除审批"])

_ALLOWED_STATUS = {"pending", "approved", "rejected"}


class _ReviewIn(BaseModel):
    force: bool = Field(default=False, description="谱系当前人物数>0 时，管理员确认连同人物一并删除")
    note: Optional[str] = Field(default=None, max_length=500, description="审批意见")


def _get_request_or_404(db: Session, request_id: int) -> DeleteRequest:
    req = db.query(DeleteRequest).filter(DeleteRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="删除申请不存在")
    return req


def _ensure_visible(req: DeleteRequest, user: User) -> None:
    if user.role != "admin" and req.submitted_by != user.id:
        raise HTTPException(status_code=403, detail="无权查看该申请")


@router.get("")
async def list_requests(
    status: Optional[str] = Query(default=None, description="pending/approved/rejected，空=全部"),
    target_type: Optional[str] = Query(default=None, description="task/lineage，空=全部"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """申请列表：admin 见全部；操作员只见自己提交的（源页面用来展示删除审批状态）。"""
    q = db.query(DeleteRequest)
    if current_user.role != "admin":
        q = q.filter(DeleteRequest.submitted_by == current_user.id)
    if status:
        if status not in _ALLOWED_STATUS:
            raise HTTPException(status_code=400, detail="status 取值：pending/approved/rejected")
        q = q.filter(DeleteRequest.status == status)
    if target_type:
        if target_type not in delete_ops.TARGET_TYPES:
            raise HTTPException(status_code=400, detail="target_type 取值：task/lineage")
        q = q.filter(DeleteRequest.target_type == target_type)
    rows = q.order_by(DeleteRequest.id.desc()).limit(500).all()
    out = delete_ops.decorate_names(db, [delete_ops.request_to_dict(r) for r in rows])
    return out


@router.get("/pending-count")
async def pending_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """待审批数量：admin=全局待审总数（顶部铃铛/菜单徽标）；操作员=自己待审数。"""
    q = db.query(DeleteRequest).filter(DeleteRequest.status == "pending")
    if current_user.role != "admin":
        q = q.filter(DeleteRequest.submitted_by == current_user.id)
    return {"pending": q.count()}


@router.get("/{request_id}")
async def get_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """申请详情 + 目标当前状态（审批时实时核对，防提交后变化）。"""
    req = _get_request_or_404(db, request_id)
    _ensure_visible(req, current_user)
    row = delete_ops.decorate_names(db, [delete_ops.request_to_dict(req)])[0]
    row["current"] = await delete_ops.current_target_state(db, req)
    return row


@router.post("/{request_id}/approve")
async def approve_request(
    request_id: int,
    body: _ReviewIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """审批通过并真正执行删除；若审批时目标已不存在则自动按「已达成」归档。"""
    req = _get_request_or_404(db, request_id)
    if req.status != "pending":
        raise HTTPException(status_code=409, detail="该申请已处理，请勿重复审批")
    note = (body.note or "").strip()
    try:
        if req.target_type == delete_ops.TASK:
            info = await delete_ops.perform_delete_task(
                db, req.target_id, actor_id=current_user.id
            )
        else:
            info = await delete_ops.perform_delete_lineage(
                db, req.target_id, force=body.force, actor_id=current_user.id
            )
    except delete_ops.DeleteTargetGone:
        req.status = "approved"
        req.reviewed_by = current_user.id
        req.reviewed_at = datetime.now()
        req.review_note = (note + "；" if note else "") + "审批时目标已不存在（可能已被其它渠道删除），按已达成删除目的归档"
        db.commit()
        log_action(
            current_user.id, "delete_approve", req.target_type, req.target_id,
            detail=f"审批通过删除申请 #{request_id}（{req.target_name}）：目标已不存在，自动归档",
        )
        return {"ok": True, "already_gone": True, "request_id": req.id, "target_type": req.target_type}
    except delete_ops.DeleteNeedsForce as exc:
        raise HTTPException(
            status_code=400,
            detail=f"该谱系当前仍归属 {exc.count} 位人物，须勾选「连同人物一并删除（强制）」后才能通过。",
        ) from exc
    except delete_ops.DeleteArchived as exc:
        raise HTTPException(
            status_code=400,
            detail="该任务已归档为只读档案，无法删除；请先在「AI 识别归档」中取回后再审批。",
        ) from exc
    req.status = "approved"
    req.reviewed_by = current_user.id
    req.reviewed_at = datetime.now()
    req.review_note = note or None
    db.commit()
    removed_parts = []
    if info.get("removed_pages"):
        removed_parts.append(f"清理页面图 {info['removed_pages']}")
    if info.get("removed_persons"):
        removed_parts.append(f"连带删除人物 {info['removed_persons']}")
    if info.get("removed_entries"):
        removed_parts.append(f"清除内容条目 {info['removed_entries']}")
    if info.get("removed_vectors"):
        removed_parts.append(f"清除检索向量 {info['removed_vectors']}")
    detail = f"审批通过删除申请 #{request_id}（{req.target_name}）：已删除"
    if removed_parts:
        detail += "（" + "、".join(removed_parts) + "）"
    log_action(
        current_user.id, "delete_approve", req.target_type, req.target_id, detail=detail
    )
    return {"ok": True, "deleted": True, "already_gone": False, "request_id": req.id, **info}


@router.post("/{request_id}/reject")
async def reject_request(
    request_id: int,
    body: _ReviewIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """驳回删除申请：目标保留，填写原因供操作员在原页面看到。"""
    req = _get_request_or_404(db, request_id)
    if req.status != "pending":
        raise HTTPException(status_code=409, detail="该申请已处理")
    req.status = "rejected"
    req.reviewed_by = current_user.id
    req.reviewed_at = datetime.now()
    req.review_note = (body.note or "").strip() or None
    db.commit()
    log_action(
        current_user.id, "delete_reject", req.target_type, req.target_id,
        detail=f"驳回删除申请 #{request_id}（{req.target_name}）"
        + (f"：{req.review_note}" if req.review_note else ""),
    )
    return {"ok": True, "status": "rejected", "request_id": req.id}
