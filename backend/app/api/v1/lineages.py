"""谱系（宗谱）与房支管理接口。"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_module_write
from app.models.orm import User
from app.models.schemas import (
    BranchCreate,
    BranchOut,
    BranchUpdate,
    LineageCreate,
    LineageOut,
    LineageUpdate,
)
from app.services import delete_ops, lineage_service
from app.services.audit_service import log_action

router = APIRouter(prefix="/lineages", tags=["谱系"])


def _branch_out(b: dict, person_count: int = 0) -> dict:
    return {**b, "person_count": person_count}


@router.get("", response_model=List[LineageOut])
async def list_lineages(_: User = Depends(get_current_user)):
    return await lineage_service.list_lineages()


@router.post("", response_model=LineageOut, status_code=201)
async def create_lineage(
    data: LineageCreate,
    # 扫描件导入流程里也要能新建谱系，故授权给 lineages 或 tasks
    current_user: User = Depends(require_module_write("lineages", "tasks")),
):
    try:
        lineage = await lineage_service.create_lineage(data.name, data.note, data.code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(
        current_user.id, "create_lineage", "lineage", lineage["lineage_id"],
        detail=f"新建谱系 {lineage['name']}（档案编号 {lineage.get('code') or '无'}）",
    )
    return {**lineage, "person_count": 0, "branches": []}


@router.put("/{lineage_id}", response_model=LineageOut)
async def update_lineage(
    lineage_id: str,
    data: LineageUpdate,
    current_user: User = Depends(require_module_write("lineages")),
):
    try:
        lineage = await lineage_service.update_lineage(
            lineage_id, data.name, data.note, data.code
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not lineage:
        raise HTTPException(status_code=404, detail="谱系不存在")
    log_action(
        current_user.id, "update_lineage", "lineage", lineage_id,
        detail=f"更新谱系 {lineage['name']}（档案编号 {lineage.get('code') or '无'}）",
    )
    items = await lineage_service.list_lineages()
    return next((it for it in items if it["lineage_id"] == lineage_id), lineage)


@router.delete("/{lineage_id}")
async def delete_lineage(
    lineage_id: str,
    force: bool = Query(default=False, description="谱系归属人物数>0 时须强制删除（连同人物）"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("lineages")),
):
    """删除谱系（09-07 审批流）。

    - 管理员：直接真正删除（谱系/房支/人物/内容条目/检索向量一并清除；
      人物数 > 0 且未勾选 force 时拒绝）。
    - 操作员：不真正删除，生成待审批的「删除申请」；待管理员在
      「系统设置 → 删除审批」通过后才真正删除。
    """
    items = await lineage_service.list_lineages()
    lineage = next((it for it in items if it["lineage_id"] == lineage_id), None)
    if not lineage:
        raise HTTPException(status_code=404, detail="谱系不存在")
    if current_user.role == "admin":
        count = lineage["person_count"]
        if count > 0 and not force:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"该谱系下还有 {count} 位人物。强制删除将连同这些人物"
                    "及其全部关联关系一并删除，并清除该谱系已入库的文字条目与"
                    "检索向量（不可恢复），请确认后再次执行。"
                ),
            )
        info = await delete_ops.perform_delete_lineage(
            db, lineage_id, force=force, actor_id=current_user.id
        )
        return {
            "deleted": True,
            "removed_persons": info["removed_persons"],
            "removed_entries": info["removed_entries"],
            "removed_vectors": info["removed_vectors"],
        }
    # 操作员：提交删除申请（force 只记录其意图，最终由管理员在审批时决定）
    try:
        req = delete_ops.request_delete_lineage(db, current_user, lineage, force=force)
    except delete_ops.PendingDeleteExists as exc:
        submitter = exc.submitter_name or "其他操作员"
        raise HTTPException(
            status_code=409,
            detail=f"该谱系已有一条待审核的删除申请（提交人：{submitter}），请勿重复提交。",
        ) from exc
    return {
        "deleted": False,
        "requested": True,
        "request_id": req.id,
        "target_id": lineage_id,
        "target_name": lineage["name"] or lineage_id,
        "person_count": lineage["person_count"] or 0,
    }


@router.post("/{lineage_id}/branches", response_model=BranchOut, status_code=201)
async def create_branch(
    lineage_id: str,
    data: BranchCreate,
    current_user: User = Depends(require_module_write("lineages")),
):
    branch = await lineage_service.create_branch(lineage_id, data.name, data.note)
    if not branch:
        raise HTTPException(status_code=404, detail="谱系不存在")
    log_action(
        current_user.id, "create_branch", "branch", branch["branch_id"],
        detail=f"谱系 {lineage_id} 新建房支 {branch['name']}",
    )
    return _branch_out(branch)


@router.put("/branches/{branch_id}", response_model=BranchOut)
async def update_branch(
    branch_id: str,
    data: BranchUpdate,
    current_user: User = Depends(require_module_write("lineages")),
):
    branch = await lineage_service.update_branch(branch_id, data.name, data.note)
    if not branch:
        raise HTTPException(status_code=404, detail="房支不存在")
    log_action(
        current_user.id, "update_branch", "branch", branch_id,
        detail=f"更新房支 {branch['name']}",
    )
    return _branch_out(branch)


@router.delete("/branches/{branch_id}", status_code=204)
async def delete_branch(
    branch_id: str,
    current_user: User = Depends(require_module_write("lineages")),
):
    branch = await lineage_service.get_branch(branch_id)
    if not branch:
        raise HTTPException(status_code=404, detail="房支不存在")
    ok = await lineage_service.delete_branch(branch_id)
    if not ok:
        raise HTTPException(status_code=404, detail="房支不存在")
    log_action(current_user.id, "delete_branch", "branch", branch_id, f"删除房支 {branch['name']}")
