from fastapi import APIRouter, Depends, HTTPException
from typing import List

from app.core.security import get_current_user, require_module_write
from app.models.orm import User
from app.models.schemas import RelationCreate, RelationOut
from app.services import person_service
from app.services.audit_service import log_action

router = APIRouter(prefix="/relations", tags=["关系"])


@router.get("", response_model=List[RelationOut])
async def list_relations(_: User = Depends(get_current_user)):
    return await person_service.list_relations()


@router.post("", response_model=RelationOut, status_code=201)
async def create_relation(
    data: RelationCreate,
    current_user: User = Depends(require_module_write("lineages", "tree")),
):
    rel = await person_service.create_relation(
        data.type, data.from_person_id, data.to_person_id, data.marriage_date
    )
    if not rel:
        raise HTTPException(status_code=404, detail="人物不存在，无法建立关系")
    log_action(
        current_user.id, "create_relation", "relation", rel["rel_id"],
        detail=f"{rel['from_name']} → {rel['to_name']} ({rel['type']})",
    )
    return rel


@router.delete("/{rel_id}", status_code=204)
async def delete_relation(
    rel_id: str,
    current_user: User = Depends(require_module_write("lineages", "tree")),
):
    ok = await person_service.delete_relation(rel_id)
    if not ok:
        raise HTTPException(status_code=404, detail="关系不存在")
    log_action(current_user.id, "delete_relation", "relation", rel_id)
