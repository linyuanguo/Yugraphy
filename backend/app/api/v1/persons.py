import os
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_module_write
from app.models.orm import User
from app.models.schemas import (
    AiDupReviewRequest,
    AiDupReviewResult,
    PersonBatchDeleteRequest,
    PersonCreate,
    PersonListResponse,
    PersonMaterialsOut,
    PersonMergeRequest,
    PersonOut,
    PersonUpdate,
)
from app.services import file_service, material_service, person_service, visit_service
from app.services.audit_service import log_action
from app.utils.image_utils import sanitize_filename

router = APIRouter(prefix="/persons", tags=["人物"])


@router.get("", response_model=PersonListResponse)
async def list_persons(
    search: str | None = Query(default=None),
    gender: str | None = Query(default=None),
    generation: int | None = Query(default=None),
    lineage_id: str | None = Query(default=None),
    branch_id: str | None = Query(default=None),
    limit: int = Query(default=1000, le=5000),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_user),
):
    items, total = await person_service.list_persons(
        search=search, gender=gender, generation=generation,
        lineage_id=lineage_id, branch_id=branch_id,
        limit=limit, offset=offset,
    )
    return {"total": total, "items": items}


@router.get("/fuzzy-search", response_model=List[PersonOut])
async def fuzzy_search_persons(
    q: str = Query(..., min_length=1, max_length=50),
    _: User = Depends(get_current_user),
):
    """姓名联想搜索（与访客分享页一致：子串 / 同音 / 拼音缩写 / 错别字容错）。

    注意：需定义在 /{person_id} 之前，避免被当作 person_id 路由。
    """
    return await visit_service.search_persons(q)


@router.get("/{person_id}", response_model=PersonOut)
async def get_person(person_id: str, _: User = Depends(get_current_user)):
    person = await person_service.get_person(person_id)
    if not person:
        raise HTTPException(status_code=404, detail="人物不存在")
    return person


@router.get("/{person_id}/materials", response_model=PersonMaterialsOut)
async def get_person_materials(
    person_id: str, _: User = Depends(get_current_user)
):
    """人物"家族信息"材料:姓名命中的传记篇目 + 谱系/房支背景内容。

    数据来自该人写入图谱(apply)时聚合到 content_entries 的谱书内容;
    阶段 2 将在此基础上叠加向量检索的语义召回。
    """
    person = await person_service.get_person(person_id)
    if not person:
        raise HTTPException(status_code=404, detail="人物不存在")
    rows = await run_in_threadpool(
        material_service.query_person_materials,
        person["name"],
        person.get("lineage_id"),
        person.get("branch_id"),
    )
    person_entries = [r for r in rows if r["scope"] == "person"]
    background_entries = [r for r in rows if r["scope"] != "person"]
    return PersonMaterialsOut(
        person_id=person["person_id"],
        name=person["name"],
        lineage_id=person.get("lineage_id"),
        lineage_name=person.get("lineage_name"),
        branch_id=person.get("branch_id"),
        branch_name=person.get("branch_name"),
        person_entries=person_entries,
        background_entries=background_entries,
        total=len(rows),
    )


@router.post("", response_model=PersonOut, status_code=201)
async def create_person(
    data: PersonCreate,
    current_user: User = Depends(require_module_write("lineages", "tree")),
):
    person = await person_service.create_person(data)
    log_action(
        current_user.id, "create_person", "person", person["person_id"],
        detail=f"新建人物 {person['name']}",
    )
    return person


@router.put("/{person_id}", response_model=PersonOut)
async def update_person(
    person_id: str,
    data: PersonUpdate,
    current_user: User = Depends(require_module_write("lineages", "tree")),
):
    person = await person_service.update_person(person_id, data)
    if not person:
        raise HTTPException(status_code=404, detail="人物不存在")
    log_action(
        current_user.id, "update_person", "person", person_id,
        detail=f"更新人物 {person['name']}",
    )
    return person


@router.delete("/{person_id}", status_code=204)
async def delete_person(
    person_id: str,
    current_user: User = Depends(require_module_write("lineages", "tree")),
):
    ok = await person_service.delete_person(person_id)
    if not ok:
        raise HTTPException(status_code=404, detail="人物不存在")
    log_action(current_user.id, "delete_person", "person", person_id, "删除人物")


@router.post("/batch-delete")
async def batch_delete_persons(
    data: PersonBatchDeleteRequest,
    current_user: User = Depends(require_module_write("lineages", "tree")),
):
    if data.lineage_id or data.branch_id:
        if not data.lineage_id:
            raise HTTPException(status_code=400, detail="按房支删除需同时提供 lineage_id")
        n = await person_service.delete_persons_by_scope(data.lineage_id, data.branch_id)
        scope = f"lineage={data.lineage_id}" + (f", branch={data.branch_id}" if data.branch_id else "")
        detail = f"删除分类全部人物 {n} 位（{scope}）"
    else:
        n = await person_service.batch_delete(data.person_ids)
        detail = f"批量删除 {n} 位人物"
    log_action(
        current_user.id, "batch_delete_persons", "person", "",
        detail=detail,
    )
    return {"deleted": n}


@router.post("/merge")
async def merge_persons(
    data: PersonMergeRequest,
    current_user: User = Depends(require_module_write("lineages", "tree")),
):
    """把重复/疑似同名的若干人物合并进主节点（同谱系跨卷人工归并）。"""
    try:
        result = await person_service.merge_persons(data.primary_id, data.secondary_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    log_action(
        current_user.id, "merge_persons", "person", data.primary_id,
        detail=(
            f"合并 {data.primary_id} ← {len(data.secondary_ids)} 位重复人物"
            f"（成功 {result['merged']} 位）"
        ),
    )
    return result


@router.post("/ai-dup-review", response_model=AiDupReviewResult)
async def ai_dup_review(
    data: AiDupReviewRequest,
    current_user: User = Depends(require_module_write("lineages", "tree")),
):
    """谱系跨卷「疑似同名」AI 裁定：候选组由服务端按归一化名聚簇后送 LLM。

    只返回是否同一人/建议保留谁的判断，不自动合并；人工在弹窗里确认后仍走 /merge。
    """
    return await person_service.ai_review_duplicate_groups(data.lineage_id)


@router.post("/{person_id}/photo", response_model=PersonOut)
async def upload_photo(
    person_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(require_module_write("lineages", "tree")),
):
    person = await person_service.get_person(person_id)
    if not person:
        raise HTTPException(status_code=404, detail="人物不存在")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    ext = os.path.splitext(file.filename or "photo.jpg")[1].lower() or ".jpg"
    object_name = f"photos/{person_id}/photo{uuid.uuid4().hex[:8]}{ext}"
    await file_service.upload_bytes(object_name, data, file.content_type or "image/jpeg")
    updated = await person_service.update_person(
        person_id, PersonUpdate(photo_url=file_service.public_url(object_name))
    )
    log_action(current_user.id, "upload_photo", "person", person_id)
    return updated
