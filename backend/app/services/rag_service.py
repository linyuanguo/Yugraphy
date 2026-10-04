"""谱书内容 RAG 检索 + AI 展示文字服务（任务 4）。

数据源：content_entries（AI 识别聚合的谱书原文，PG）。向量索引维护在
rag_vectors 表（JSONB 存归一化向量，增量同步；量级小用全量余弦，未来量大换 pgvector）。

能力：
1. rag_search / ask：自然语言检索谱书内容 → LLM 生成"展示文字"（答案）+ 命中原文
   （含谱系/房支/页码，供前端跳转定位）。
   检索链路：query embedding(206:30010) → 全量余弦 top 60 → rerank(206:30011) 精排
   top N → LLM(206:30000，关思考) 生成。索引未就绪/embedding 失败时降级关键词召回，
   保证随时可用。
2. person_intro：某个人物的 AI 展示文字（依据姓名命中传记 + 谱系/房支背景原文）。

所有外部调用都有超时与降级：任意一步失败不影响返回原文命中与基础拼装回答。
"""
import asyncio
import json
import logging
import math
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import driver
from app.models.orm import AppSetting, ContentEntry, ImportTask, RagVector
from app.services import material_service, person_service, qa_service

logger = logging.getLogger("genealogy.rag")

# ============ 配置（默认取 .env；系统设置页可经 AppSetting("rag_config") 覆盖） ============
RAG_CONFIG_KEY = "rag_config"
# 206 实测：匿名 401 时带此密钥可用；若服务器后续放开匿名自动跳过
_FALLBACK_KEY = "sk-qwen3vl-2026-local-001"

# 默认值（与 .env 一致）。键名与系统设置页「族谱内容检索（RAG）」表单一致。
_RT_DEFAULTS: Dict[str, object] = {
    "embedding_url": settings.EMBEDDING_URL,          # embedding 服务地址（OpenAI 兼容 /v1/embeddings）
    "embedding_model": settings.EMBEDDING_MODEL,      # embedding 模型名
    "embedding_api_key": settings.EMBEDDING_API_KEY or "",  # 空 = 匿名 + _FALLBACK_KEY 兜底
    "rerank_url": settings.RERANK_URL,                # rerank 服务地址（/v1/rerank）
    "rerank_model": settings.RERANK_MODEL,            # reranker 模型名
    "embedding_batch": 24,      # 单次批量嵌入条数（服务排队，分批可减少超时）
    "max_emb_chars": 1200,      # 每条参与向量化的文本最大长度（"切片"上限）
    "top_candidates": 60,       # 余弦召回候选数（供 rerank 精排）
    "search_limit": 12,         # 默认返回命中数
    "hit_text_chars": 1200,     # 命中原文返回/展示截断长度
}
_rt: Dict[str, object] = {}  # 运行态覆盖值（load_rag_runtime / apply_rag_config 装载）


def default_rag_config() -> Dict[str, object]:
    """服务端默认配置（.env/内置）。系统设置页未自定义时回退这些值。"""
    return dict(_RT_DEFAULTS)


def _rc(key: str) -> object:
    v = _rt.get(key)
    return v if v is not None else _RT_DEFAULTS.get(key)


def _coerce_int(v: object, dft: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return dft


def _emb_base() -> str:
    return str(_rc("embedding_url") or settings.EMBEDDING_URL).rstrip("/")


def _emb_model() -> str:
    return str(_rc("embedding_model") or settings.EMBEDDING_MODEL)


def _rerank_base() -> str:
    return str(_rc("rerank_url") or settings.RERANK_URL).rstrip("/")


def _rerank_model() -> str:
    return str(_rc("rerank_model") or settings.RERANK_MODEL)


def _emb_key() -> str:
    return str(_rc("embedding_api_key") or "").strip()


def _emb_batch() -> int:
    return _coerce_int(_rc("embedding_batch"), 24, 1, 64)


def _max_emb_chars() -> int:
    return _coerce_int(_rc("max_emb_chars"), 1200, 50, 5000)


def _top_candidates() -> int:
    return _coerce_int(_rc("top_candidates"), 60, 5, 200)


def _search_limit() -> int:
    return _coerce_int(_rc("search_limit"), 12, 1, 50)


def _hit_chars() -> int:
    return _coerce_int(_rc("hit_text_chars"), 1200, 50, 5000)


def apply_rag_config(cfg: Dict[str, object]) -> None:
    """写入运行态配置（系统设置保存后即时生效，下次检索/索引同步使用）。"""
    global _rt
    _rt = {k: cfg.get(k) for k in _RT_DEFAULTS}
    _emb_model_cache[0] = 0.0  # embedding 模型名可能变更，强制重新探测


def load_rag_runtime(db: Session) -> Dict[str, object]:
    """从 AppSetting(rag_config) 装载生效配置并返回快照（无自定义 = .env 默认）。

    在 rag 检索 / person-intro / 状态 / 索引预热等入口调用，保证 DB 改动即时生效。
    """
    cfg = default_rag_config()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == RAG_CONFIG_KEY).first()
        if row and row.value:
            data = json.loads(row.value)
            if isinstance(data, dict):
                for k in list(_RT_DEFAULTS):
                    v = data.get(k)
                    if v is not None and str(v).strip() != "":
                        cfg[k] = v
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 RAG 配置失败（使用默认）: %s", exc)
    apply_rag_config(cfg)
    return cfg


def effective_rag_config() -> Dict[str, object]:
    """当前生效配置快照（从未装载 = 默认值）。"""
    return dict(_rt or _RT_DEFAULTS)

# ============ 索引构建状态（进程内） ============
_build_lock: Optional[asyncio.Lock] = None
_building = False
_built_once = False  # 标记曾完成一次全量（供状态展示）

# ============ 模块级小缓存 ============
_emb_model_cache: List = [0.0, ""]  # [ts, model_name]
_name_map_cache: List = [0.0, {}]  # [ts, {lineage_id:name, "branch:"+bid:name}]
_intro_cache: Dict[str, Tuple[float, Optional[dict]]] = {}  # person_id -> (ts, payload)


def _get_build_lock() -> asyncio.Lock:
    global _build_lock
    if _build_lock is None:
        _build_lock = asyncio.Lock()
    return _build_lock


def _get_session() -> Session:
    from app.core.database import SessionLocal

    return SessionLocal()


# ============ HTTP 工具（匿名优先，401 回退密钥） ============
async def _post_json(url: str, payload: dict, timeout: float = 120) -> dict:
    """POST JSON。匿名 401 时用已知密钥重试一次。"""
    key = _emb_key() or _FALLBACK_KEY
    headers_list = [{}, {"Authorization": f"Bearer {key}"}]
    last_err: Optional[Exception] = None
    for headers in headers_list:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 401:
                    last_err = RuntimeError("401 unauthorized")
                    continue
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if not headers:  # 匿名失败 → 立即试带密钥
                continue
            break
    raise RuntimeError(f"AI 服务请求失败 {url}: {last_err}")


async def _resolve_embedding_model() -> str:
    """优先用服务 /v1/models 返回的模型名（模型名可能被部署者改动）。"""
    now = time.time()
    if _emb_model_cache[0] and now - _emb_model_cache[0] < 600:
        return _emb_model_cache[1]
    name = _emb_model()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_emb_base()}/v1/models")
            if resp.status_code == 200:
                arr = resp.json().get("data") or []
                if arr:
                    name = str(arr[0].get("id") or name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("获取 embedding 模型列表失败，使用默认: %s", exc)
    _emb_model_cache[0], _emb_model_cache[1] = now, name
    return name


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """批量文本 → 向量。单条失败返回空列表（调用方降级）。"""
    if not texts:
        return []
    model = await _resolve_embedding_model()
    results: List[List[float]] = []
    for i in range(0, len(texts), _emb_batch()):
        batch = texts[i : i + _emb_batch()]
        try:
            data = await _post_json(
                f"{_emb_base()}/v1/embeddings",
                {"model": model, "input": batch},
            )
            rows = sorted((data.get("data") or []), key=lambda d: int(d.get("index", 0)))
            results.extend([list(r["embedding"]) for r in rows])
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding 批量失败（第 %d 批）: %s", i // _emb_batch(), exc)
            if not results:
                raise
            # 部分成功：丢弃不完整批次，交由调用方以已有结果继续
            logger.warning("embedding 部分成功，放弃第 %d 批后续", i // _emb_batch())
            break
    if len(results) != len(texts):
        logger.warning("embedding 返回条数不符: got=%d want=%d", len(results), len(texts))
    return results


async def rerank(query: str, docs: List[str], top_n: int) -> Optional[List[int]]:
    """返回精排后的索引（升序相关性前 top_n）。失败返回 None（调用方用原序）。"""
    if not docs:
        return None
    try:
        data = await _post_json(
            f"{_rerank_base()}/v1/rerank",
            {
                "model": _rerank_model(),
                "query": query[:200],
                "documents": [d[:300] for d in docs],
                "top_n": min(top_n, len(docs)),
            },
        )
        results = data.get("results") or []
        if not results:
            return None
        ordered = sorted(
            results,
            key=lambda r: float(r.get("relevance_score", 0)),
            reverse=True,
        )
        return [int(r.get("index", 0)) for r in ordered[:top_n]]
    except Exception as exc:  # noqa: BLE001
        logger.warning("rerank 失败（使用余弦序）: %s", exc)
        return None


# ============ LLM 生成（关思考：快而稳，content 不空） ============
async def _llm_text(db: Session, prompt: str, timeout: float = 90, max_tokens: int = 800) -> Optional[str]:
    try:
        qa = qa_service.get_qa_settings(db)
        base = (qa.get("llm_api_base") or settings.SGLANG_URL).rstrip("/")
        key = qa.get("llm_api_key") or settings.SGLANG_API_KEY
        payload = {
            "model": qa["qa_llm_model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base}/v1/chat/completions", json=payload, headers=headers
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        return (content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAG LLM 生成失败: %s", exc)
        return None


# ============ 谱系/房支名称缓存（Neo4j 只读一次） ============
async def _lineage_name_map() -> Dict[str, str]:
    now = time.time()
    if _name_map_cache[0] and now - _name_map_cache[0] < 600:
        return _name_map_cache[1]
    names: Dict[str, str] = {}
    try:
        async with driver.session() as session:
            rec = await session.run("MATCH (l:Lineage) RETURN l.lineage_id AS id, l.name AS name")
            async for r in rec:
                if r["id"]:
                    names[str(r["id"])] = str(r["name"] or "")
            rec = await session.run("MATCH (b:Branch) RETURN b.branch_id AS id, b.name AS name")
            async for r in rec:
                if r["id"]:
                    names["branch:" + str(r["id"])] = str(r["name"] or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取谱系/房支名称失败（命中不带名称）: %s", exc)
    _name_map_cache[0], _name_map_cache[1] = now, names
    return names


def _hit_base(e) -> dict:
    return {
        "entry_id": e.entry_id,
        "type": e.type,
        "title": e.title or "",
        "text": e.text,
        "page_no": e.page_no,
        "task_id": e.task_id,
        "lineage_id": e.lineage_id,
        "branch_id": e.branch_id,
        "score": 0.0,
    }


async def _attach_names(hits: List[dict]) -> List[dict]:
    names = await _lineage_name_map()
    for h in hits:
        h["lineage_name"] = names.get(h.get("lineage_id") or "", "")
        h["branch_name"] = names.get("branch:" + (h.get("branch_id") or ""), "")
    return hits


def _attach_page_images(hits: List[dict], db: Session) -> List[dict]:
    """RAG 命中关联扫描件页面图：任务在存时命中条目带原图/缩略图 URL。

    图片 URL 规则与审核页一致：/files/{scans|thumbs}/{task_id}/page_NNN.{png|jpg}。
    任务已删除、条目不来自扫描页（人工补录/无页码）时保持纯文字展示（无图）。
    """
    tids = {h.get("task_id") for h in hits if h.get("task_id")}
    names: Dict[str, str] = {}
    if tids:
        rows = db.query(ImportTask).filter(ImportTask.task_id.in_(tids)).all()
        names = {t.task_id: (t.file_path or "").rsplit("/", 1)[-1] for t in rows}
    for h in hits:
        tid = h.get("task_id")
        no = h.get("page_no")
        if tid and no and names.get(tid):
            h["task_name"] = names[tid]
            h["image_url"] = f"/files/scans/{tid}/page_{int(no):03d}.png"
            h["thumb_url"] = f"/files/thumbs/{tid}/page_{int(no):03d}.jpg"
    return hits


def _norm_vec(v: List[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


# ============ 索引增量同步 ============
def _active_entries(db: Session) -> List[ContentEntry]:
    return (
        db.query(ContentEntry)
        .filter(ContentEntry.status == "active", ContentEntry.text.isnot(None))
        .all()
    )


def _vec_rows(db: Session) -> List[RagVector]:
    return db.query(RagVector).all()


async def _sync_index(db: Session, session_factory=None) -> Tuple[int, int]:
    """把 content_entries(active) 增量同步到 rag_vectors。返回 (新增/更新数, 删除数)。

    session_factory：自行管理会话时传入；为 None 时用传入的 db（调用方负责 commit/close）。
    分批 embedding；部分失败允许保留旧向量（本函数只 commit 成功的）。
    """
    active = _active_entries(db)
    vec_map = {v.entry_id: v for v in _vec_rows(db)}

    # 需要入库/更新的条目（含内容变化）
    to_sync: List[ContentEntry] = []
    for ent in active:
        old = vec_map.get(ent.entry_id)
        if old is None:
            to_sync.append(ent)
        elif (ent.updated_at or ent.created_at) and (old.updated_at or old.created_at):
            if ent.updated_at and old.updated_at and ent.updated_at > old.updated_at:
                to_sync.append(ent)
    del_ids = [eid for eid in vec_map if eid not in {e.entry_id for e in active}]

    texts = [f"{e.title}\n{e.text[: _max_emb_chars()]}" for e in to_sync]
    vectors: List[List[float]] = []
    if texts:
        try:
            vectors = await embed_texts(texts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("谱书向量化失败（本次跳过）: %s", exc)
            vectors = []
    # 部分向量（embedding 返回数量少于输入时，只入库成功的条目）
    synced = 0
    for ent, vec in zip(to_sync, vectors):
        old = vec_map.get(ent.entry_id)
        row = old or RagVector(entry_id=ent.entry_id)
        row.lineage_id = ent.lineage_id
        row.branch_id = ent.branch_id
        row.type = ent.type
        row.title = ent.title or ""
        row.text_head = ent.text[:500]
        row.dim = len(vec)
        row.vector = _norm_vec(vec)
        row.updated_at = datetime.now()
        if old is None:
            db.add(row)
        synced += 1
    if del_ids:
        db.query(RagVector).filter(RagVector.entry_id.in_(del_ids)).delete(
            synchronize_session=False
        )
    db.commit()
    return synced, len(del_ids)


async def _ensure_index(db: Session, wait: bool = False) -> None:
    """确保索引就绪：数据变化/首次时后台增量同步。wait=False 不阻塞调用方。"""
    global _building, _built_once
    active_n = db.query(ContentEntry).filter(ContentEntry.status == "active").count()
    vec_n = db.query(RagVector).count()
    if active_n and vec_n == active_n and _built_once:
        return

    lock = _get_build_lock()
    async def _do() -> None:
        global _building, _built_once
        if _building:
            return
        _building = True
        try:
            if wait:
                await _sync_index(db)
            else:
                own = _get_session()
                try:
                    await _sync_index(own)
                finally:
                    own.close()
            _built_once = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("谱书内容索引构建失败: %s", exc)
        finally:
            _building = False

    async with lock:
        if wait:
            await _do()
        else:
            asyncio.create_task(_do())


def rag_status(db: Session) -> dict:
    """索引状态（前端提示"正在建立知识索引"用）。"""
    active_n = (
        db.query(ContentEntry)
        .filter(ContentEntry.status == "active", ContentEntry.text.isnot(None))
        .count()
    )
    vec_n = db.query(RagVector).count()
    return {
        "ready": bool(active_n and vec_n == active_n),
        "building": _building,
        "indexed": vec_n,
        "total": active_n,
    }


async def rebuild_index(db: Session) -> Tuple[int, int]:
    """清空向量表并全量重建（管理员修改 embedding 模型/切片参数后调用）。返回 (入库数, 删除数)。"""
    global _built_once
    n = db.query(RagVector).count()
    if n:
        db.query(RagVector).delete(synchronize_session=False)
        db.commit()
    try:
        synced, deleted = await _sync_index(db)
    finally:
        _built_once = True
    return synced, deleted


# ============ 关键词降级检索 ============
_STOP_TOKENS = {
    "请问", "帮我", "搜索", "检索", "一下", "介绍", "如何", "怎样", "怎么样", "何处",
    "哪里", "谁", "什么", "怎么", "为什么", "吗", "呢", "的", "是", "了", "关于",
    "家谱", "族谱", "谱书", "内容", "全文", "故事", "简介", "历史", "在", "里",
    "有", "没有", "找", "查", "看", "第", "几", "代", "位", "中", "和", "与",
    "及", "为", "从", "到", "其", "该", "这", "那", "一", "不", "也", "就", "都",
}


def _split_tokens(q: str) -> List[str]:
    chunks = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", q)
    tokens = []
    for c in chunks:
        low = c.lower()
        if low in _STOP_TOKENS:
            continue
        # 中文整块 >6 字按 2 字窗口细分（避免整句子串过于苛刻）
        if len(re.sub(r"[A-Za-z0-9]", "", c)) > 6:
            han = re.findall(r"[\u4e00-\u9fff]", c)
            tokens.extend("".join(han[i : i + 2]) for i in range(0, max(1, len(han) - 1), 2))
        else:
            tokens.append(c)
    # 去重保序
    seen = set()
    out = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:12]


def keyword_search(
    db: Session, question: str, lineage_id: Optional[str] = None, limit: Optional[int] = None
) -> List[dict]:
    """ILike/子串关键词召回（冷启动与 embedding 失败时的降级路径）。"""
    if limit is None:
        limit = _search_limit()
    tokens = _split_tokens(question)
    entries = _active_entries(db)
    scored: List[Tuple[int, int, int, ContentEntry]] = []
    for e in entries:
        if lineage_id and e.lineage_id != lineage_id:
            continue
        title = e.title or ""
        text = e.text or ""
        hit_n = 0
        for t in tokens:
            if t in title or t in (e.type or ""):
                hit_n += 2
            elif t in text:
                hit_n += 1
        if hit_n:
            # (命中权重, 类型优先级, 页码) 排序
            scored.append((hit_n, e.page_no or 0, e.id, e))
    scored.sort(key=lambda x: (x[0], -x[1], x[2]), reverse=True)
    hits = []
    for _, _, _, e in scored[: max(limit * 2, 20)]:
        h = _hit_base(e)
        hits.append(h)
    return hits[:limit]


# ============ 语义检索 ============
async def _load_vectors(db: Session, lineage_id: Optional[str]) -> List[Tuple[dict, List[float]]]:
    """读取 rag_vectors → (行数据, 归一化向量)。"""
    q = db.query(RagVector)
    if lineage_id:
        q = q.filter(RagVector.lineage_id == lineage_id)
    out = []
    for v in q.all():
        vec = v.vector or []
        if not vec:
            continue
        out.append(({"entry_id": v.entry_id}, vec))
    return out


async def semantic_search(
    db: Session, question: str, lineage_id: Optional[str] = None, limit: Optional[int] = None
) -> List[dict]:
    """query embed → 余弦 top candidates → rerank 精排 → 命中（原文从 content_entries 取）。"""
    if limit is None:
        limit = _search_limit()
    qv_raw = await embed_texts([question])
    if not qv_raw:
        return []
    qv = _norm_vec(qv_raw[0])
    pairs = await _load_vectors(db, lineage_id)
    if not pairs:
        return []
    scored: List[Tuple[float, str]] = []
    for meta, vec in pairs:
        # 向量均已在入库时归一化，dot 即余弦
        dot = sum(a * b for a, b in zip(qv, vec))
        scored.append((dot, meta["entry_id"]))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:_top_candidates()]
    ordered = [eid for _, eid in top]

    # rerank 精排（失败保留余弦序）
    try:
        rows = db.query(ContentEntry).filter(
            ContentEntry.entry_id.in_(ordered)
        ).all()
        text_map = {e.entry_id: e.text or "" for e in rows}
        docs = [text_map.get(eid, "")[:300] for eid in ordered]
        rr = await rerank(question, docs, limit)
        if rr:
            ordered = [ordered[i] for i in rr]
    except Exception as exc:  # noqa: BLE001
        logger.warning("rerank 前准备失败: %s", exc)

    entries = {
        e.entry_id: e
        for e in db.query(ContentEntry).filter(ContentEntry.entry_id.in_(ordered[: limit * 2])).all()
    }
    hits = []
    rank_score = {eid: sc for sc, eid in scored}
    for i, eid in enumerate(ordered[:limit]):
        e = entries.get(eid)
        if not e:
            continue
        h = _hit_base(e)
        h["score"] = round(1.0 - i / (limit + 1), 3) if limit else 0.0
        hits.append(h)
    return hits


def _text_by_id(db: Session, entry_id: str) -> str:
    e = db.query(ContentEntry).filter(ContentEntry.entry_id == entry_id).first()
    return (e.text if e else "")[:300]


# ============ 检索链路测试（系统设置页「测试向量检索与 Rerank」用） ============
async def test_search(
    db: Session, question: str, lineage_id: Optional[str] = None, top_n: Optional[int] = None
) -> dict:
    """分阶段返回「Embedding 余弦召回」与「Rerank 精排」两组命中（不调 LLM 生成文案）。

    供管理员在系统设置页直观对比当前 embedding/reranker 配置的实际召回与精排效果；
    rerank 失败时 reranked 按余弦序返回并置 rerank_used=False。
    """
    question = (question or "").strip()
    status = rag_status(db)
    base: dict = {
        "question": question,
        "ready": status["ready"],
        "building": status["building"],
        "total": 0,
        "embedding_model": _emb_model(),
        "rerank_model": _rerank_model(),
        "vector_dim": None,
        "rerank_used": False,
        "error": None,
        "recall": [],
        "ranked": [],
    }
    if not question:
        base["error"] = "问题为空"
        return base
    if not status["ready"]:
        base["error"] = (
            f"谱书向量索引尚未就绪（{status['indexed']}/{status['total']} 条）。"
            "请先在上方点「重建向量索引」完成后再测试。"
        )
        return base
    try:
        qv_raw = await embed_texts([question])
        if not qv_raw:
            base["error"] = "Embedding 服务不可用或返回空（请检查模型/地址配置并「保存 RAG 设置」后再试）"
            return base
    except Exception as exc:  # noqa: BLE001
        logger.warning("测试向量化失败: %s", exc)
        base["error"] = f"向量化失败：{exc}"
        return base
    try:
        model = await _resolve_embedding_model()
        if model:
            base["embedding_model"] = model
    except Exception as exc:  # noqa: BLE001
        logger.warning("获取 embedding 模型名失败: %s", exc)
    qv = _norm_vec(qv_raw[0])
    pairs = await _load_vectors(db, lineage_id)
    if not pairs:
        base["error"] = "所选范围内暂无谱书向量（请确认已写入谱书内容并完成索引）"
        return base

    scored: List[Tuple[float, str]] = []
    for meta, vec in pairs:
        dot = sum(a * b for a, b in zip(qv, vec))
        scored.append((dot, meta["entry_id"]))
    scored.sort(key=lambda x: x[0], reverse=True)
    if top_n is None:
        top_n = _top_candidates()
    top = scored[:top_n]
    order_ids = [eid for _, eid in top]
    rows = db.query(ContentEntry).filter(ContentEntry.entry_id.in_(order_ids)).all()
    emap = {e.entry_id: e for e in rows}

    recall: List[dict] = []
    for pos, (dot, eid) in enumerate(top):
        e = emap.get(eid)
        if not e:
            continue
        recall.append(
            {
                "rank": pos + 1,
                "entry_id": eid,
                "type": e.type,
                "title": e.title or "",
                "text": (e.text or "")[:300],
                "page_no": e.page_no,
                "lineage_id": e.lineage_id,
                "branch_id": e.branch_id,
                "sim": round(float(dot), 4),
            }
        )
    await _attach_names(recall)
    base["vector_dim"] = len(pairs[0][1]) if pairs else None

    # Rerank 精排（尽量保留原始分数展示；失败按余弦序）
    rerank_used = False
    scores: Dict[int, float] = {}
    try:
        data = await _post_json(
            f"{_rerank_base()}/v1/rerank",
            {
                "model": _rerank_model(),
                "query": question[:200],
                "documents": [
                    (emap.get(eid).text if emap.get(eid) else "")[:300] for eid in order_ids
                ],
                "top_n": len(order_ids),
            },
        )
        for r in data.get("results") or []:
            scores[int(r.get("index", 0))] = float(r.get("relevance_score", 0.0))
        rerank_used = bool(scores)
    except Exception as exc:  # noqa: BLE001
        logger.warning("测试 rerank 失败（按余弦序展示）: %s", exc)

    order = (
        sorted(range(len(order_ids)), key=lambda i: -scores.get(i, 0.0))
        if rerank_used
        else list(range(len(order_ids)))
    )
    from_rank_by_eid = {h["entry_id"]: h["rank"] for h in recall}
    ranked: List[dict] = []
    for new_pos, i in enumerate(order):
        eid = order_ids[i]
        item = next((h for h in recall if h["entry_id"] == eid), None)
        if item is None:
            continue
        ranked.append(
            {
                **item,
                "rank": new_pos + 1,
                "from_rank": from_rank_by_eid.get(eid, new_pos + 1),
                "rerank_score": round(scores.get(i, 0.0), 4) if rerank_used else None,
            }
        )
    base.update(
        {
            "total": len(recall),
            "rerank_used": rerank_used,
            "recall": recall,
            "ranked": ranked,
        }
    )
    return base


# ============ 对外主入口 ============
def _plain_answer(question: str, hits: List[dict]) -> str:
    """无 LLM 时的可读降级回答（按命中原文拼一段引导性展示文字）。"""
    if not hits:
        return f"在谱书原文中暂未检索到与「{question}」相关的内容。"
    lines = []
    seen = set()
    for h in hits[:5]:
        head = (h["text"] or "").strip()[:120]
        if not head or head in seen:
            continue
        seen.add(head)
        tag = " / ".join(x for x in [h.get("lineage_name") or "", h.get("type") or ""] if x)
        lines.append(f"〔{tag or '谱书'}〕{head}…")
    return "检索到以下谱书原文片段（点击右侧条目可查看全文）：\n" + "\n".join(lines)


async def rag_search(
    db: Session, question: str, lineage_id: Optional[str] = None, limit: Optional[int] = None
) -> dict:
    """自然语言检索谱书内容：向量主路径，关键词兜底；LLM 生成展示文字失败则拼装降级。"""
    if limit is None:
        limit = _search_limit()
    question = (question or "").strip()
    await _ensure_index(db, wait=False)

    status = rag_status(db)
    source = "keyword"
    hits: List[dict] = []
    if status["ready"]:
        try:
            hits = await semantic_search(db, question, lineage_id, limit=limit)
            if hits:
                source = "vector"
        except Exception as exc:  # noqa: BLE001
            logger.warning("向量检索失败，降级关键词: %s", exc)
            hits = []
    if not hits:
        hits = keyword_search(db, question, lineage_id, limit=limit)
        source = "keyword" if hits else "none"

    hits = await _attach_names(hits)
    _attach_page_images(hits, db)

    answer = None
    if hits:
        snippet = "\n\n".join(
            f"〔{(h.get('type') or '')}{(('·' + h.get('lineage_name', '')) if h.get('lineage_name') else '')}〕"
            f"{h['title']}：{h['text'][:600]}"
            for h in hits[:6]
        )
        prompt = (
            "你是家谱管理系统的讲解员。用户问：{q}\n\n"
            "下面是族谱识别原文中检索到的相关资料（OCR 可能有繁体与错别字）：\n{ctx}\n\n"
            "请据此写一段 150-280 字的「展示文字」回答用户，要求：\n"
            "1. 客观平实、通顺连贯，适合放到族谱浏览页面向参观者介绍；\n"
            "2. 只使用原文信息，不要编造原文没有的事实，不确定处用『可能』等措辞；\n"
            "3. 如原文为繁体可转简体；回答中不要出现『根据资料显示』等套话开头；\n"
            "4. 输出纯文本，不要 markdown 标题、列表或引用。"
        ).format(q=question, ctx=snippet)
        try:
            answer = await _llm_text(db, prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RAG 回答生成异常: %s", exc)
    if not answer:
        answer = _plain_answer(question, hits)

    # 命中原文保留前 1200 字（前端可"展开全文"阅读）
    return {
        "question": question,
        "answer": answer,
        "hits": [
            {
                **h,
                "text": (h.get("text") or "")[:_hit_chars()],
            }
            for h in hits
        ],
        "source": source,
        "ready": status["ready"],
        "building": status["building"],
        "total": len(hits),
    }


# ============ 人物 AI 展示文字 ============
_INTRO_CACHE_TTL = 600.0  # 秒：同一人物 10 分钟内复用（避免重复打开反复生成）


async def person_intro(db: Session, person_id: str) -> Optional[dict]:
    """人物 AI 展示文字：姓名命中传记/谱系背景原文 → LLM 提炼为通顺介绍。

    无可用谱书材料时返回 None（前端不展示该卡，而非编造）。
    """
    cached = _intro_cache.get(person_id)
    if cached and time.time() - cached[0] < _INTRO_CACHE_TTL:
        return cached[1]

    person = await person_service.get_person(person_id)
    if not person:
        return None

    rows = await asyncio.to_thread(
        material_service.query_person_materials,
        person["name"],
        person.get("lineage_id"),
        person.get("branch_id"),
    )
    rows = [r for r in rows if (r.get("text") or "").strip()][:8]
    if not rows:
        # 兜底：向量/关键词再找一次（姓名可能以繁体/别字出现）
        try:
            hits = await rag_search(db, person["name"], person.get("lineage_id"), limit=6)
            rows = [
                {
                    "type": h.get("type") or "谱书",
                    "title": h.get("title") or "",
                    "text": (h.get("text") or "")[:400],
                    "scope": "lineage",
                }
                for h in hits.get("hits", [])
            ][:6]
        except Exception as exc:  # noqa: BLE001
            logger.warning("人物兜底检索失败 %s: %s", person_id, exc)
    if not rows:
        return None

    facts = []
    if person.get("birth_year") or person.get("death_year"):
        facts.append(f"生卒 {person.get('birth_year') or '?'}—{person.get('death_year') or '?'}")
    if person.get("birth_place"):
        facts.append(f"籍贯 {person['birth_place']}")
    if person.get("generation") is not None:
        facts.append(f"第{person['generation']}代")
    ctx = "\n\n".join(
        f"〔{r.get('scope', '谱书')}·{r.get('type', '')}〕{r.get('title', '')}\n{(r.get('text') or '')[:400]}"
        for r in rows
    )
    prompt = (
        "你是家谱讲解员，请为人物「{name}」写一段 150-280 字的「人物展示文字」。\n"
        "可用的事实信息：{facts}\n\n"
        "谱书原文资料（OCR 可能含繁体/错别字）：\n{ctx}\n\n"
        "要求：\n"
        "1. 语言典雅平实，适合放在家谱人物页面向参观者介绍，段落式纯文本；\n"
        "2. 依据原文事实组织（生平、世系归属、谱书记载事迹等），不编造，不确定用『疑为/或』；\n"
        "3. 如与祖先/家族迁徙有关可自然带出，但以该人物为主；\n"
        "4. 繁体可转简体；不要以『根据』『资料显示』开头；不用 markdown 符号。"
    ).format(name=person["name"], facts=("；".join(facts) or "暂无额外结构化信息"), ctx=ctx)
    text = await _llm_text(db, prompt, max_tokens=700)
    if not text:
        # LLM 失败仍给出基于原文片段的降级文案
        heads = [r.get("text", "").strip()[:80] for r in rows if r.get("text", "").strip()]
        text = "暂无 AI 文案（模型暂不可用）。以下为谱书记载原文片段：\n" + "\n".join(heads[:3])
    ln = person.get("lineage_name")
    if not ln and rows:
        ln = rows[0].get("lineage_name")
    payload = {
        "person_id": person_id,
        "name": person["name"],
        "text": text,
        "sources": len(rows),
        "lineage_name": ln,
    }
    _intro_cache[person_id] = (time.time(), payload)
    # 简单防膨胀
    if len(_intro_cache) > 500:
        now = time.time()
        for k in [k for k, (ts, _) in _intro_cache.items() if now - ts > _INTRO_CACHE_TTL]:
            _intro_cache.pop(k, None)
    return payload
