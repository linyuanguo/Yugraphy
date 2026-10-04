"""图谱树分享管理 + 访客只读公开接口。

- 管理端（admin/操作员）：/shares 创建/列表/撤销/编辑分享，/settings 问答设置
- 公开端（?code=<share_code>）：/v/* 只读接口（树/子树/人物/搜索/问答），短链码校验。
  访问链接仅一种形态：<站点根>/<share_code>；旧 /s/<code> 与 /visit?token= 形态已下线失效。
"""
import random
import secrets
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_module_write, require_role
from app.models.orm import User, VisitShare
from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    LineageOut,
    PersonDetailOut,
    PersonIntroOut,
    PersonIntroRequest,
    PersonMaterialsOut,
    PersonOut,
    RagAskRequest,
    RagAskResponse,
    RagStatusOut,
    TreeData,
    VisitInfo,
    VisitSettingsOut,
    VisitSettingsUpdate,
    VisitShareCreate,
    VisitShareOut,
    VisitShareUpdate,
)
from app.services import (
    lineage_service,
    material_service,
    person_service,
    qa_service,
    rag_service,
    tree_service,
    visit_service,
)
from app.services.audit_service import log_action

router = APIRouter(prefix="/visit", tags=["访客"])


# ============ 短链码生成 / 校验 ============
_SHORT_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # 去掉易混淆 0/O/1/I
_SHORT_LEN = 6


def _generate_share_code(db: Session) -> str:
    """生成不与现存记录冲突的 6 位短链码。"""
    for _ in range(12):
        code = "".join(random.SystemRandom().choice(_SHORT_ALPHABET) for _ in range(_SHORT_LEN))
        exists = (
            db.query(VisitShare.id).filter(VisitShare.share_code == code).first()
        )
        if not exists:
            return code
    raise HTTPException(status_code=500, detail="短链码生成失败，请重试")


def _ensure_share_codes(db: Session) -> None:
    """历史分享记录懒补齐短链码（幂等）；补齐后即用新根路径短链访问。"""
    rows = db.query(VisitShare).filter(VisitShare.share_code.is_(None)).limit(200).all()
    if not rows:
        return
    for share in rows:
        share.share_code = _generate_share_code(db)
    db.commit()


def _check_code(code: str, db: Session) -> VisitShare:
    """按短链码校验分享有效性（访问链接 <站点根>/<share_code> 的唯一入口）。"""
    if not code:
        raise HTTPException(status_code=401, detail="分享链接无效")
    share = (
        db.query(VisitShare)
        .filter(VisitShare.share_code == code.upper(), VisitShare.revoked == False)  # noqa: E712
        .first()
    )
    if not share:
        raise HTTPException(status_code=401, detail="分享链接无效")
    if share.expires_at and share.expires_at < datetime.now():
        raise HTTPException(status_code=401, detail="分享链接已过期")
    return share


# ============ 管理端 ============
@router.get("/shares", response_model=List[VisitShareOut])
def list_shares(
    db: Session = Depends(get_db),
    _: User = Depends(require_module_write("visit", "visit_share")),
):
    _ensure_share_codes(db)
    return (
        db.query(VisitShare)
        .order_by(VisitShare.created_at.desc())
        .all()
    )


@router.post("/shares", response_model=VisitShareOut, status_code=201)
def create_share(
    data: VisitShareCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("visit", "visit_share")),
):
    # token 仅作库内唯一凭据保留，对外不再提供长链形态；访问入口统一为 <根>/<share_code>
    if data.expires_at is not None:
        exp = data.expires_at
        if exp.tzinfo is not None:
            exp = exp.astimezone().replace(tzinfo=None)
        if exp <= datetime.now():
            raise HTTPException(status_code=400, detail="到期时间需晚于当前时间")
    else:
        exp = (
            datetime.now() + timedelta(days=data.expires_days)
            if data.expires_days
            else None
        )
    share = VisitShare(
        token=secrets.token_urlsafe(32),
        share_code=_generate_share_code(db),
        name=data.name,
        note=data.note,
        allow_search=data.allow_search,
        allow_chat=data.allow_chat,
        chat_model=data.chat_model or None,
        expires_at=exp,
        revoked=False,
        created_by=current_user.id,
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    log_action(current_user.id, "create_visit_share", "visit_share", str(share.id), f"创建访客分享 {share.name}")
    return share


@router.put("/shares/{share_id}", response_model=VisitShareOut)
def update_share(
    share_id: int,
    data: VisitShareUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("visit", "visit_share")),
):
    share = db.query(VisitShare).filter(VisitShare.id == share_id).first()
    if not share:
        raise HTTPException(status_code=404, detail="分享不存在")
    if share.revoked:
        raise HTTPException(status_code=400, detail="已撤销的分享不可编辑")
    if data.name is not None:
        share.name = data.name
    if data.note is not None:
        share.note = data.note
    if data.allow_search is not None:
        share.allow_search = data.allow_search
    if data.allow_chat is not None:
        share.allow_chat = data.allow_chat
    if data.chat_model is not None:
        share.chat_model = data.chat_model or None
    if "expires_at" in data.model_fields_set:
        exp = data.expires_at
        if exp is not None:
            if exp.tzinfo is not None:
                exp = exp.astimezone().replace(tzinfo=None)
            if exp <= datetime.now():
                raise HTTPException(status_code=400, detail="到期时间需晚于当前时间")
        share.expires_at = exp
    db.commit()
    db.refresh(share)
    log_action(current_user.id, "update_visit_share", "visit_share", str(share_id), f"更新访客分享 {share.name}")
    return share


@router.delete("/shares/{share_id}", status_code=204)
def revoke_share(
    share_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("visit", "visit_share")),
):
    share = db.query(VisitShare).filter(VisitShare.id == share_id).first()
    if not share:
        raise HTTPException(status_code=404, detail="分享不存在")
    share.revoked = True
    db.commit()
    log_action(current_user.id, "revoke_visit_share", "visit_share", str(share_id), f"撤销访客分享 {share.name}")


@router.delete("/shares/{share_id}/record", status_code=204)
def delete_share_record(
    share_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("visit", "visit_share")),
):
    """彻底删除分享记录（用于清理已撤销/废弃的测试记录；链接随之永久失效）。"""
    share = db.query(VisitShare).filter(VisitShare.id == share_id).first()
    if not share:
        raise HTTPException(status_code=404, detail="分享不存在")
    name = share.name
    db.delete(share)
    db.commit()
    log_action(current_user.id, "delete_visit_share", "visit_share", str(share_id), f"删除访客分享 {name}")


@router.get("/settings", response_model=VisitSettingsOut)
def get_settings(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    return qa_service.get_qa_settings(db)


@router.put("/settings", response_model=VisitSettingsOut)
def update_settings(
    data: VisitSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    mapping = {
        "qa_enable_llm": "1" if data.qa_enable_llm else "0",
        "qa_llm_prompt": data.qa_llm_prompt,
        "qa_llm_model": data.qa_llm_model,
        "qa_welcome": data.qa_welcome,
    }
    for key, value in mapping.items():
        if value is not None:
            qa_service.set_qa_setting(db, key, str(value))
    log_action(current_user.id, "update_visit_settings", detail="更新访客问答设置")
    return qa_service.get_qa_settings(db)


# ============ 公开接口（短链码校验） ============
@router.get("/v/info", response_model=VisitInfo)
def visit_info(
    code: str = Query(...),
    db: Session = Depends(get_db),
):
    share = _check_code(code, db)
    qa = qa_service.get_qa_settings(db)
    return VisitInfo(
        name=share.name,
        allow_search=share.allow_search,
        allow_chat=share.allow_chat,
        welcome=qa["qa_welcome"],
    )


@router.get("/v/lineages", response_model=List[LineageOut])
async def visit_lineages(
    code: str = Query(...),
    _db: Session = Depends(get_db),
):
    """访客只读：谱系列表（含房支与人数），供 3D 谱系画布一级呈现。"""
    _check_code(code, _db)
    return await lineage_service.list_lineages()


@router.get("/v/tree", response_model=TreeData)
async def visit_tree(
    code: str = Query(...),
    lineage_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    _check_code(code, db)
    return await tree_service.get_tree(lineage_id=lineage_id)


@router.get("/v/subtree", response_model=TreeData)
async def visit_subtree(
    code: str = Query(...),
    person_id: str = Query(...),
    depth: int = Query(default=4, ge=1, le=8),
    db: Session = Depends(get_db),
):
    _check_code(code, db)
    data = await tree_service.get_person_subtree(person_id, depth)
    if not data["nodes"]:
        raise HTTPException(status_code=404, detail="人物不存在")
    return data


@router.get("/v/person/{person_id}", response_model=PersonDetailOut)
async def visit_person(
    person_id: str,
    code: str = Query(...),
    db: Session = Depends(get_db),
):
    _check_code(code, db)
    person = await visit_service.get_person_detail(person_id)
    if not person:
        raise HTTPException(status_code=404, detail="人物不存在")
    return person


@router.get("/v/person/{person_id}/materials", response_model=PersonMaterialsOut)
async def visit_person_materials(
    person_id: str,
    code: str = Query(...),
    db: Session = Depends(get_db),
):
    """访客只读:人物的"家族信息"材料(姓名命中传记 + 谱系/房支背景)。

    材料正文含谱书原文,与搜索同权限(allow_search 开放时才可见)。
    """
    share = _check_code(code, db)
    if not share.allow_search:
        raise HTTPException(status_code=403, detail="该分享未开放搜索")
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


@router.get("/v/search", response_model=List[PersonOut])
async def visit_search(
    q: str = Query(..., min_length=1, max_length=50),
    code: str = Query(...),
    db: Session = Depends(get_db),
):
    share = _check_code(code, db)
    if not share.allow_search:
        raise HTTPException(status_code=403, detail="该分享未开放搜索")
    return await visit_service.search_persons(q)


@router.post("/v/chat", response_model=ChatResponse)
async def visit_chat(
    payload: ChatRequest,
    code: str = Query(...),
    db: Session = Depends(get_db),
):
    share = _check_code(code, db)
    if not share.allow_chat:
        raise HTTPException(status_code=403, detail="该分享未开放图谱问答")
    return await qa_service.answer_question(
        payload.question, db, model_override=share.chat_model or None
    )


@router.get("/v/rag-status", response_model=RagStatusOut)
def visit_rag_status(
    code: str = Query(...),
    db: Session = Depends(get_db),
):
    """访客:谱书内容索引状态（搜索框提示"正在建立知识索引"用）。"""
    _check_code(code, db)
    rag_service.load_rag_runtime(db)
    return rag_service.rag_status(db)


@router.post("/v/rag-ask", response_model=RagAskResponse)
async def visit_rag_ask(
    payload: RagAskRequest,
    code: str = Query(...),
    db: Session = Depends(get_db),
):
    """访客:自然语言检索谱书内容(树页搜索框升级入口),同搜索权限。"""
    share = _check_code(code, db)
    if not share.allow_search:
        raise HTTPException(status_code=403, detail="该分享未开放搜索")
    rag_service.load_rag_runtime(db)
    return await rag_service.rag_search(db, payload.question, payload.lineage_id)


@router.post("/v/person-intro", response_model=PersonIntroOut)
async def visit_person_intro(
    payload: PersonIntroRequest,
    code: str = Query(...),
    db: Session = Depends(get_db),
):
    """访客:人物的 AI 展示文字(谱书记载生成),材料可见性与搜索同权。"""
    share = _check_code(code, db)
    if not share.allow_search:
        raise HTTPException(status_code=403, detail="该分享未开放搜索")
    rag_service.load_rag_runtime(db)
    out = await rag_service.person_intro(db, payload.person_id)
    if not out:
        raise HTTPException(status_code=404, detail="未找到该人物，或暂无可用的谱书记载")
    return out
