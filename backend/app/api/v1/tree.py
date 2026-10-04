from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.security import require_module_read
from app.models.orm import User
from app.models.schemas import TreeData
from app.services import tree_service

router = APIRouter(prefix="/tree", tags=["家谱树"])


@router.get("", response_model=TreeData)
async def get_tree(
    lineage_id: str | None = Query(default=None),
    _: User = Depends(require_module_read("tree", "lineages")),
):
    return await tree_service.get_tree(lineage_id=lineage_id)


@router.get("/subtree", response_model=TreeData)
async def get_subtree(
    person_id: str = Query(...),
    depth: int = Query(default=6, ge=1, le=12),
    _: User = Depends(require_module_read("tree", "lineages")),
):
    data = await tree_service.get_person_subtree(person_id, depth)
    if not data["nodes"]:
        raise HTTPException(status_code=404, detail="人物不存在")
    return data
