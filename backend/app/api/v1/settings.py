"""系统设置聚合接口（admin）：访客问答参数 + 模型列表配置。

模型列表存 AppSetting.llm_models（JSON 数组），供前端「图谱问答-启用 AI
增强解析」的模型下拉使用；qa_* 设置沿用 qa_service 的 AppSetting 读写。
"""
import json
import logging
import re
import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.core.database import get_db
from app.core.security import require_role
from app.models.orm import AppSetting, AuditLog, User
from app.models.schemas import (
    AuditLogOut,
    AuditLogPage,
    LlmModelItem,
    StorageCleanupOut,
    StorageCleanupRequest,
    StorageItemOut,
    StorageScanOut,
    SystemSettingsOut,
    SystemSettingsUpdate,
)
from app.services import (
    consolidate_service,
    ip_whitelist_service,
    qa_service,
    rag_service,
    storage_service,
    vision_service,
)
from app.services.audit_service import RETENTION_KEY, log_action, prune_audit_logs

logger = logging.getLogger("genealogy.settings")

router = APIRouter(prefix="/settings", tags=["系统设置"])

MODELS_KEY = "llm_models"
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,100}$")
SCAN_PROMPT_KEY = vision_service.VISION_PROMPT_KEY
# 整卷整理（AI 第二段卷级归并）提示词；留空 = 用 consolidate_service 内置默认模板
CONSOLIDATE_PROMPT_KEY = consolidate_service.CONSOLIDATE_PROMPT_KEY

# ============ RAG / 向量库配置（存 AppSetting；运行时装载见 rag_service.load_rag_runtime） ============
VECTOR_DBS_KEY = "vector_dbs"  # JSON 数组：外部向量库登记（预留接入）
VECTOR_DB_ACTIVE_KEY = "vector_db_active"
BUILTIN_VDB_ID = "builtin_pg"  # 当前默认引擎：内置 PostgreSQL 向量表

# rag_config 的字符串键（空 = 回退 .env/内置）与数值键合法范围（键名与 rag_service._RT_DEFAULTS 一致）
_RAG_STR_KEYS = {
    "embedding_url",
    "embedding_model",
    "embedding_api_key",
    "rerank_url",
    "rerank_model",
}
_RAG_INT_RANGES = {
    "embedding_batch": (1, 64),
    "max_emb_chars": (50, 5000),
    "top_candidates": (5, 200),
    "search_limit": (1, 50),
    "hit_text_chars": (50, 5000),
}


def _builtin_vdb() -> dict:
    return {
        "id": BUILTIN_VDB_ID,
        "name": "内置向量库（PostgreSQL rag_vectors）",
        "kind": "builtin",
        "type": "内置引擎",
        "endpoint": "",
        "api_key": "",
        "note": "当前默认检索引擎：谱书原文向量存 PostgreSQL 的 rag_vectors 表（JSONB + 全量余弦召回），随系统数据持久化，无需额外配置。",
        "builtin": True,
    }


def _get_rag_config(db: Session) -> dict:
    """读取 RAG 配置（未自定义 = .env/内置默认），数值规整为 int 供表单回显。"""
    cfg = rag_service.default_rag_config()
    row = db.query(AppSetting).filter(AppSetting.key == rag_service.RAG_CONFIG_KEY).first()
    if row and row.value:
        try:
            data = json.loads(row.value)
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in cfg and v is not None})
        except (TypeError, ValueError):
            pass
    for k in _RAG_INT_RANGES:
        try:
            cfg[k] = int(cfg[k])
        except (TypeError, ValueError):
            cfg[k] = rag_service.default_rag_config()[k]
    return cfg


def _save_rag_config(db: Session, raw: Any) -> dict:
    """校验并保存 RAG 配置（字符串留空 = 回退 .env 默认）。保存后写入运行态即时生效。"""
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="RAG 配置格式不正确")
    defaults = rag_service.default_rag_config()
    out = dict(defaults)
    for k in _RAG_STR_KEYS:
        v = raw.get(k)
        out[k] = v.strip() if isinstance(v, str) and v.strip() else defaults[k]
    for k, (lo, hi) in _RAG_INT_RANGES.items():
        v = raw.get(k)
        try:
            n = int(v)
        except (TypeError, ValueError):
            n = defaults[k]
        out[k] = max(lo, min(hi, n))
    row = db.query(AppSetting).filter(AppSetting.key == rag_service.RAG_CONFIG_KEY).first()
    value = json.dumps(out, ensure_ascii=False)
    if row is None:
        db.add(AppSetting(key=rag_service.RAG_CONFIG_KEY, value=value))
    else:
        row.value = value
    db.commit()
    rag_service.apply_rag_config(out)  # 进程内即时生效（embedding 缓存同时失效）
    return out


def _vdb_from_row(it: Any) -> Optional[dict]:
    """外部向量库登记条目规整。名称必填；内置行/非法行返回 None。"""
    if not isinstance(it, dict):
        return None
    name = str(it.get("name") or "").strip()
    if not name:
        return None
    iid = str(it.get("id") or "").strip() or ("vdb_" + uuid.uuid4().hex[:10])
    return {
        "id": iid,
        "name": name[:100],
        "kind": "external",
        "type": str(it.get("type") or "其它").strip()[:50] or "其它",
        "endpoint": str(it.get("endpoint") or "").strip()[:500] or "",
        "api_key": str(it.get("api_key") or "").strip()[:500] or "",
        "note": str(it.get("note") or "").strip()[:300] or "",
        "builtin": False,
    }


def _get_vector_dbs(db: Session) -> List[dict]:
    """内置引擎恒在首条，其后为登记的（预留接入的）外部向量库。"""
    out: List[dict] = [_builtin_vdb()]
    row = db.query(AppSetting).filter(AppSetting.key == VECTOR_DBS_KEY).first()
    if row and row.value:
        try:
            parsed = json.loads(row.value)
            if isinstance(parsed, list):
                for it in parsed:
                    item = _vdb_from_row(it)
                    if item and item["id"] != BUILTIN_VDB_ID:
                        out.append(item)
        except (TypeError, ValueError):
            pass
    return out


def _save_vector_dbs(db: Session, items: Any) -> None:
    """保存外部向量库登记列表（内置行忽略；重复 id 报错）。"""
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="向量库列表格式不正确")
    exts: List[dict] = []
    seen: set = set()
    for it in items:
        item = _vdb_from_row(it)
        if item is None or item["id"] == BUILTIN_VDB_ID:
            continue
        if item["id"] in seen:
            raise HTTPException(status_code=400, detail=f"向量库「{item['name']}」重复")
        seen.add(item["id"])
        exts.append(item)
    row = db.query(AppSetting).filter(AppSetting.key == VECTOR_DBS_KEY).first()
    value = json.dumps(exts, ensure_ascii=False)
    if row is None:
        db.add(AppSetting(key=VECTOR_DBS_KEY, value=value))
    else:
        row.value = value
    db.commit()


def _get_vector_db_active(db: Session) -> str:
    row = db.query(AppSetting).filter(AppSetting.key == VECTOR_DB_ACTIVE_KEY).first()
    active = (row.value or "").strip() if row and row.value else ""
    ids = {i["id"] for i in _get_vector_dbs(db)}
    return active if active in ids else BUILTIN_VDB_ID


def _save_vector_db_active(db: Session, active: str) -> None:
    """设置当前默认向量库。当前引擎仅内置 PostgreSQL 向量表可生效；
    外部库为接入登记（预留），服务端尚未接入其引擎，不允许设为默认。"""
    ids = {i["id"] for i in _get_vector_dbs(db)}
    v = (active or "").strip()
    if not v or v not in ids:
        raise HTTPException(status_code=400, detail="要设为默认的向量库不存在")
    if v != BUILTIN_VDB_ID:
        raise HTTPException(
            status_code=400,
            detail="该向量库引擎尚未接入服务端，仅「内置向量库（PostgreSQL）」可作为当前默认",
        )
    row = db.query(AppSetting).filter(AppSetting.key == VECTOR_DB_ACTIVE_KEY).first()
    if row is None:
        db.add(AppSetting(key=VECTOR_DB_ACTIVE_KEY, value=v))
    else:
        row.value = v
    db.commit()


def _default_models() -> List[dict]:
    """未配置模型列表时，默认提供服务器部署的识别/问答模型。"""
    name = app_settings.QWEN_MODEL_NAME
    return [
        {
            "name": name,
            "label": name,
            "note": "当前部署默认模型（识别与问答共用），API 由服务器环境变量配置",
            "builtin": True,
        }
    ]


def _get_models(db: Session) -> List[LlmModelItem]:
    row = db.query(AppSetting).filter(AppSetting.key == MODELS_KEY).first()
    raw: list = []
    if row and row.value:
        try:
            parsed = json.loads(row.value)
            if isinstance(parsed, list):
                raw = parsed
        except (TypeError, ValueError):
            raw = []
    models = _sanitize(raw)
    return models or [LlmModelItem(**m) for m in _default_models()]


def _sanitize(raw: list) -> List[LlmModelItem]:
    """规整模型条目：去空行、校验名称、去重保序。

    内置部署模型（name 等于 QWEN_MODEL_NAME）锁定：api_base/api_key 一律清空，
    由服务器环境变量提供，不允许在页面配置。
    """
    seen: set = set()
    out: List[LlmModelItem] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or not MODEL_NAME_RE.match(name):
            raise HTTPException(
                status_code=400,
                detail=f"模型名非法：{name or '(空)'}，仅允许字母/数字/._-",
            )
        if name in seen:
            continue
        seen.add(name)
        builtin = name == app_settings.QWEN_MODEL_NAME
        api_base = api_key = None
        if not builtin:
            api_base = str(item.get("api_base") or "").strip() or None
            api_key = str(item.get("api_key") or "").strip() or None
        label = str(item.get("label") or "").strip() or name
        note = str(item.get("note") or "").strip()
        out.append(
            LlmModelItem(
                name=name,
                label=label,
                note=note or None,
                api_base=api_base,
                api_key=api_key,
                builtin=builtin,
            )
        )
    return out


def _save_models(db: Session, data: List[LlmModelItem]) -> List[LlmModelItem]:
    if not data:
        raise HTTPException(status_code=400, detail="模型列表不能为空，请至少保留一个模型")
    models = _sanitize([m.model_dump() for m in data])
    row = db.query(AppSetting).filter(AppSetting.key == MODELS_KEY).first()
    # 持久化不含 builtin 标志（读取时按名称动态判定）
    value = json.dumps(
        [
            {
                "name": m.name,
                "label": m.label,
                "note": m.note,
                "api_base": m.api_base,
                "api_key": m.api_key,
            }
            for m in models
        ],
        ensure_ascii=False,
    )
    if row is None:
        db.add(AppSetting(key=MODELS_KEY, value=value))
    else:
        row.value = value
    db.commit()
    return models


@router.get("", response_model=SystemSettingsOut)
def get_system_settings(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    qa = qa_service.get_qa_settings(db)
    row = db.query(AppSetting).filter(AppSetting.key == RETENTION_KEY).first()
    scan_row = db.query(AppSetting).filter(AppSetting.key == SCAN_PROMPT_KEY).first()
    cons_row = (
        db.query(AppSetting).filter(AppSetting.key == CONSOLIDATE_PROMPT_KEY).first()
    )
    return SystemSettingsOut(
        qa_enable_llm=qa["qa_enable_llm"],
        qa_llm_prompt=qa["qa_llm_prompt"],
        qa_llm_model=qa["qa_llm_model"],
        qa_welcome=qa["qa_welcome"],
        llm_models=_get_models(db),
        audit_retention_days=(row.value or "").strip() if row and row.value else "",
        # 未自定义时返回代码默认模板，便于管理员查看/基于默认修改
        scan_prompt=(scan_row.value or "").strip() if scan_row and scan_row.value
        else vision_service.VISION_PROMPT,
        # 未自定义时返回代码默认模板，便于管理员查看/基于默认修改
        consolidate_prompt=(cons_row.value or "").strip() if cons_row and cons_row.value
        else consolidate_service.CONSOLIDATE_PROMPT,
        rag_config=_get_rag_config(db),
        vector_dbs=_get_vector_dbs(db),
        vector_db_active=_get_vector_db_active(db),
        https_ip_whitelist=_get_ip_whitelist(db),
        https_ip_whitelist_enabled=bool(_get_ip_whitelist(db)),
    )


@router.put("", response_model=SystemSettingsOut)
def update_system_settings(
    data: SystemSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    if data.qa_welcome is not None:
        qa_service.set_qa_setting(db, "qa_welcome", data.qa_welcome.strip())
    if data.qa_enable_llm is not None:
        qa_service.set_qa_setting(db, "qa_enable_llm", "1" if data.qa_enable_llm else "0")
    if data.qa_llm_prompt is not None:
        qa_service.set_qa_setting(db, "qa_llm_prompt", data.qa_llm_prompt.strip())
    if data.qa_llm_model is not None:
        qa_service.set_qa_setting(db, "qa_llm_model", data.qa_llm_model.strip())
    if data.llm_models is not None:
        _save_models(db, data.llm_models)
    if data.audit_retention_days is not None:
        _save_retention(db, data.audit_retention_days)
    if data.scan_prompt is not None:
        _save_scan_prompt(db, data.scan_prompt)
    if data.consolidate_prompt is not None:
        _save_consolidate_prompt(db, data.consolidate_prompt)
    if data.rag_config is not None:
        _save_rag_config(db, data.rag_config)
    if data.vector_dbs is not None:
        _save_vector_dbs(db, data.vector_dbs)
    if data.vector_db_active is not None:
        _save_vector_db_active(db, data.vector_db_active)
    if data.https_ip_whitelist is not None:
        _save_ip_whitelist(db, data.https_ip_whitelist)
    log_action(current_user.id, "update_system_settings", detail="更新系统设置")
    return get_system_settings(db)


def _get_ip_whitelist(db: Session) -> List[str]:
    row = db.query(AppSetting).filter(AppSetting.key == ip_whitelist_service.KEY).first()
    if not row or not row.value:
        return []
    try:
        parsed = json.loads(row.value)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except (TypeError, ValueError):
        return []
    return []


def _save_ip_whitelist(db: Session, raw: Any) -> List[str]:
    """保存 HTTPS IP 白名单并热加载。空列表 = 禁用（不限制 IP）；保存后即时生效、无需重启。"""
    try:
        normalized = ip_whitelist_service.apply(raw)  # 内部校验 + 更新进程内缓存
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    row = db.query(AppSetting).filter(AppSetting.key == ip_whitelist_service.KEY).first()
    if row is None:
        if normalized:
            db.add(
                AppSetting(
                    key=ip_whitelist_service.KEY,
                    value=json.dumps(normalized, ensure_ascii=False),
                )
            )
    elif normalized:
        row.value = json.dumps(normalized, ensure_ascii=False)
    else:
        db.delete(row)
    db.commit()
    return normalized


def _save_scan_prompt(db: Session, value: str) -> None:
    """保存识别提示词：留空 = 恢复服务端代码默认模板。"""
    val = (value or "").strip()
    row = db.query(AppSetting).filter(AppSetting.key == SCAN_PROMPT_KEY).first()
    if row is None:
        if val:
            db.add(AppSetting(key=SCAN_PROMPT_KEY, value=val))
    elif val:
        row.value = val
    else:
        db.delete(row)


def _save_consolidate_prompt(db: Session, value: str) -> None:
    """保存整卷整理提示词：留空 = 恢复服务端代码默认模板。"""
    val = (value or "").strip()
    row = db.query(AppSetting).filter(AppSetting.key == CONSOLIDATE_PROMPT_KEY).first()
    if row is None:
        if val:
            db.add(AppSetting(key=CONSOLIDATE_PROMPT_KEY, value=val))
    elif val:
        row.value = val
    else:
        db.delete(row)
    db.commit()
    db.commit()


def _save_retention(db: Session, value: str) -> None:
    """保存操作日志保留策略：空 = 永久；否则 1~3650 天整数。"""
    days_raw = (value or "").strip()
    if days_raw:
        try:
            days_n = int(days_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="保留天数必须是整数")
        if days_n < 1 or days_n > 3650:
            raise HTTPException(status_code=400, detail="保留天数须在 1~3650 之间（永久请留空）")
    row = db.query(AppSetting).filter(AppSetting.key == RETENTION_KEY).first()
    if row is None:
        db.add(AppSetting(key=RETENTION_KEY, value=days_raw))
    else:
        row.value = days_raw
    db.commit()


# ============ 操作日志管理（audit_logs，仅管理员） ============
@router.get("/audit-logs", response_model=AuditLogPage)
def list_audit_logs(
    action: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    query = db.query(AuditLog).order_by(AuditLog.id.desc())
    if action:
        query = query.filter(AuditLog.action == action)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                AuditLog.detail.ilike(like),
                AuditLog.target_id.ilike(like),
                AuditLog.action.ilike(like),
            )
        )
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    uids = {r.user_id for r in rows if r.user_id}
    user_map = {
        u.id: u.username
        for u in db.query(User).filter(User.id.in_(uids)) if uids
    }
    items = []
    for r in rows:
        items.append(
            AuditLogOut(
                id=r.id,
                user_id=r.user_id,
                username=user_map.get(r.user_id),
                action=r.action,
                target_type=r.target_type,
                target_id=r.target_id,
                detail=r.detail,
                ip_address=r.ip_address,
                created_at=r.created_at,
            )
        )
    return AuditLogPage(items=items, total=total)


@router.delete("/audit-logs/{log_id}", status_code=204)
def delete_audit_log(
    log_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    row = db.query(AuditLog).filter(AuditLog.id == log_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="日志不存在")
    db.delete(row)
    db.commit()
    log_action(current_user.id, "delete_audit_log", detail=f"删除操作日志 #{log_id}")


@router.delete("/audit-logs", status_code=204)
def clear_audit_logs(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """一键清空全部操作日志（会先记录本次清空行为）。"""
    n = db.query(AuditLog).count()
    db.query(AuditLog).delete()
    db.commit()
    log_action(current_user.id, "clear_audit_logs", detail=f"清空操作日志（共 {n} 条）")


@router.post("/audit-logs/prune")
def prune_audit_logs_now(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """按保留策略立即清理过期操作日志（保留设为永久时删除 0 条）。"""
    n = prune_audit_logs()
    if n:
        log_action(current_user.id, "prune_audit_logs", detail=f"按保留策略清理过期操作日志（共 {n} 条）")
    return {"deleted": n}


# ============ 存储清理（孤儿数据扫描 / 人工确认删除） ============
@router.get("/storage-scan", response_model=StorageScanOut)
async def scan_storage(
    refresh: bool = Query(default=False, description="true=立即重扫；否则返回每日定时扫描的缓存结果"),
    _: User = Depends(require_role("admin")),
):
    """孤儿数据扫描结果：孤儿任务目录 / 孤儿页面图(MinIO) / 上传根目录散落文件。

    运行中的任务在库中有记录，不会被判为孤儿，因此清单里不会出现正在使用的任务。
    """
    result = None
    if not refresh:
        result = storage_service.load_result()
    if not result:
        result = await run_in_threadpool(storage_service.scan_orphans)
    return StorageScanOut(
        items=[StorageItemOut(**i) for i in (result.get("items") or [])],
        total_size=int(result.get("total_size") or 0),
        total_items=int(result.get("total_items") or 0),
        scanned_at=result.get("scanned_at"),
    )


@router.post("/storage-cleanup", response_model=StorageCleanupOut)
async def cleanup_storage(
    data: StorageCleanupRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """按勾选的 key 删除孤儿数据（删除前再次校验：在库任务的数据绝不删）。"""
    res = await storage_service.remove_items(data.keys or [])
    if res["removed"]:
        mb = res["freed"] / 1024 / 1024
        log_action(
            current_user.id,
            "cleanup_storage",
            detail=f"清理孤儿数据 {len(res['removed'])} 项，释放 {mb:.1f} MB",
        )
        # 删除后立刻刷新缓存，页面无需再手动重扫
        await run_in_threadpool(storage_service.scan_orphans)
    return StorageCleanupOut(**res)
