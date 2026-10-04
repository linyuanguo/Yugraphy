"""谱书内容 RAG 语义检索（登录端，任务 4）。

树页/档案文件页搜索"族谱内容"与人物抽屉的 AI 展示文字都走这里；
访客侧对称接口在 visit.py（/visit/v/rag-ask、/visit/v/person-intro）。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.orm import User
from app.models.schemas import (
    PersonIntroOut,
    PersonIntroRequest,
    RagAskRequest,
    RagAskResponse,
    RagStatusOut,
    RagTestRequest,
)
from app.services import rag_service

router = APIRouter(prefix="/rag", tags=["谱书内容检索"])


@router.get("/status", response_model=RagStatusOut)
def rag_status(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """谱书知识索引状态（未就绪时前端提示"正在建立"，构建完成自动可用）。"""
    rag_service.load_rag_runtime(db)
    return rag_service.rag_status(db)


@router.post("/rebuild", response_model=dict)
async def rebuild_rag_index(
    _: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """清空并全量重建谱书向量索引（管理员修改 embedding 模型/切片参数后调用）。"""
    rag_service.load_rag_runtime(db)
    synced, deleted = await rag_service.rebuild_index(db)
    return {"synced": synced, "deleted": deleted, **rag_service.rag_status(db)}


@router.post("/ask", response_model=RagAskResponse)
async def rag_ask(
    req: RagAskRequest,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """自然语言检索谱书内容：AI 展示文字 + 命中原文（供跳转/查看）。"""
    rag_service.load_rag_runtime(db)
    return await rag_service.rag_search(db, req.question, req.lineage_id)


@router.post("/test-search", response_model=dict)
async def rag_test_search(
    req: RagTestRequest,
    _: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """检索链路测试：Embedding 余弦召回 vs Rerank 精排（管理员在系统设置页验证/调参用，不调 LLM）。"""
    rag_service.load_rag_runtime(db)
    return await rag_service.test_search(db, req.question, req.lineage_id)


@router.post("/person-intro", response_model=PersonIntroOut)
async def rag_person_intro(
    req: PersonIntroRequest,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """人物的 AI 展示文字（依据谱书记载生成；无材料返回 404）。"""
    rag_service.load_rag_runtime(db)
    payload = await rag_service.person_intro(db, req.person_id)
    if not payload:
        raise HTTPException(status_code=404, detail="未找到该人物，或暂无可用的谱书记载")
    return payload
