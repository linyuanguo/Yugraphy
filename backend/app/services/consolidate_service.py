"""卷级整理（第二段）：识别全部页完成后，对整卷文本做人物归并与世系关系推断。

两段式识别：
- 第一段（vision_service.extract_page，关闭思考）：逐页只做"读字提人"——输出该页
  persons（人物+本页明载属性）与 entries（谱书正文摘录），**不推断亲属关系**。
- 第二段（本文件，开启思考）：整卷所有页识别完成后自动执行。输入为纯文本（各页
  persons + entries，无需再读图），按页序滑窗分块，每块调用 206 纯文本对话做
  人物跨页归并 + 父子/配偶推断；块结果按规范化姓名合并，写入 result.consolidated。

写入后任务才算 done（审核看到的是整理后的全卷人物与关系）。
"""
import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import defer

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.orm import AppSetting, ImportTask
from app.services import task_registry
from app.services.audit_service import log_task_action
from app.services.vision_service import parse_json_from_llm

logger = logging.getLogger("genealogy.consolidate")

CHUNK_CHARS: int = settings.CONSOLIDATE_CHUNK_CHARS
OVERLAP: int = settings.CONSOLIDATE_OVERLAP_PAGES
RETRIES: int = settings.CONSOLIDATE_MAX_RETRIES
# 半成品落盘节流（大卷 OOM 防护）：每 N 块 / 每 X 秒才写一次完整 result，
# 进度 done_pages 仍每块轻量更新（不写 result、不重建页镜像）
SAVE_EVERY_BLOCKS: int = max(1, settings.CONSOLIDATE_SAVE_EVERY_BLOCKS)
SAVE_EVERY_SECONDS: int = max(0, settings.CONSOLIDATE_SAVE_EVERY_SECONDS)

CONSOLIDATE_PROMPT = """你是族谱世系整理专家。下面给你某族谱「第 {start}~{end} 页」的初步识别材料：每一页列出该页出现的「人物」（姓名、性别、生卒/籍贯/简介等，由手写扫描件 AI 转写，可能有少量错字）以及「谱文」摘录。

请基于这些材料做两件事：
1. 人物归并：同一人物若跨页重复出现，合并为一条（补充出现过的属性）；仅当同页明确是两个同名之人时才分开（可写为「姓名(二)」等加以区分）。每个人的姓名保持材料中的原字符。
2. 世系关系推断：只根据明确线索推断两类关系，宁缺毋滥，没有依据绝不编造：
   - parent_child：谱文明写「某之子/女」「某公生子」等时，from_name=父母名，to_name=子女名；
   - spouse：谱文明写「配某氏」「妣某氏」「聘某氏」等婚姻记载时，from_name=夫名，to_name=妻名。
   人名必须与人物名单写法一致；跨页同名一律视为同一人（不要重复建关系）。

注意事项：
- 这是同族谱的连续页面，人物只应属于本谱系；序言/源流里与世系无关的远古传说人物（如炎帝、黄帝、姜嫄等）不要列入，除非材料明确属于本谱世系行。
- 生卒年、出生地、简介只保留材料中明确写出的，绝不编造；拿不准就省略。
- 只输出材料中出现过的人物。

请严格输出如下 JSON（不要输出任何其他文字）：
{{
  "persons": [
    {{"name": "姓名", "gender": "male/female/unknown", "birth_year": null,
      "death_year": null, "birth_place": "", "biography": "", "confidence": 0.9}}
  ],
  "relations": [
    {{"type": "parent_child", "from_name": "父名", "to_name": "子名", "confidence": 0.9}},
    {{"type": "spouse", "from_name": "夫名", "to_name": "妻名", "confidence": 0.9}}
  ]
}}

要求：
- 不要输出 "relations" 以外的字段；没有关系的段落可以给空数组。
- confidence 0-1 表示对该条目的把握程度（辨认困难、疑似错字给低分）。
- biography 一句话不超过 20 字；没有明确记载就省略该字段。

以下是识别材料：
{chunk}"""

CONSOLIDATE_PROMPT_KEY = "consolidate_prompt"


def _prompt_template() -> str:
    """取生效的整卷整理提示词：系统设置自定义（AppSetting）优先，否则用代码内置模板。

    每次调用读一次库（块级调用，开销可忽略），保证管理员在「系统设置 → 模型配置」
    改完立即对下一次整理生效，无需重启服务。
    """
    try:
        with SessionLocal() as db:
            row = (
                db.query(AppSetting)
                .filter(AppSetting.key == CONSOLIDATE_PROMPT_KEY)
                .first()
            )
            if row and (row.value or "").strip():
                return row.value.strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取自定义整理提示词失败，回退默认模板: %s", exc)
    return CONSOLIDATE_PROMPT


def _get_task(task_id: str) -> Optional[ImportTask]:
    with SessionLocal() as db:
        return db.query(ImportTask).filter(ImportTask.task_id == task_id).first()


def _update_task(
    task_id: str, skip_pages_mirror: bool = False, keep_paused: bool = True, **kwargs
) -> None:
    """更新任务字段（本文件专用轻量版：不重建 task_pages_json 页镜像）。

    skip_pages_mirror 仅为与 import_service._update_task 保持签名一致而显式接收
    （整理阶段 pages 未变，本实现本就不做镜像重建），不参与 setattr。

    keep_paused=True（默认）：任务处于 paused 时丢弃后台例行的 running/pending
    写入，避免整理协程把人工暂停的任务改回运行态（详见 import_service._update_task）。
    """
    with SessionLocal() as db:
        # 只更新标量进度字段（不传 result）时不要加载巨大的 result JSONB：
        # 大卷整卷 result 达数十 MB，每块读回一次是内存峰值的主要来源
        q = db.query(ImportTask)
        if "result" not in kwargs:
            q = q.options(defer(ImportTask.result))
        task = q.filter(ImportTask.task_id == task_id).first()
        if not task:
            return
        # 暂停保护：已暂停的任务不被整理协程的例行写入改回运行态
        if (
            keep_paused
            and task.status == "paused"
            and kwargs.get("status") in ("running", "pending")
        ):
            logger.info(
                "任务 %s 已暂停：整理协程忽略状态写入 %s（保留 paused）",
                task_id, kwargs.get("status"),
            )
            kwargs.pop("status")
        if "status" in kwargs and kwargs["status"] != "paused" and "pause_reason" not in kwargs:
            kwargs["pause_reason"] = None
        for k, v in kwargs.items():
            setattr(task, k, v)
        task.updated_at = datetime.now()
        db.commit()
        # 尽早释放会话持有的 ORM 实例与属性历史（含整卷 result 旧值快照），
        # 降低大卷整理反复落盘时的内存峰值
        db.expunge_all()


def _norm_name(name: str) -> str:
    """规范化姓名作为合并键：去空白（保留括号内区分标记）。"""
    return re.sub(r"\s+", "", name or "")


def _page_text(page: dict) -> str:
    """把一页的识别结果压缩成给整理模型的文本（控制单页体积）。"""
    no = page.get("page_no", 0)
    parts: List[str] = [f"第{no}页"]
    persons = page.get("persons") or []
    if persons:
        rows = []
        for p in persons[:40]:
            bits = [str(p.get("name", "")).strip()]
            g = p.get("gender")
            if g and g in ("male", "female"):
                bits.append("男" if g == "male" else "女")
            for k, label in (("birth_year", "生"), ("death_year", "卒")):
                if p.get(k):
                    bits.append(f"{label}{p[k]}")
            bp = p.get("birth_place")
            if bp:
                bits.append(f"籍{bp}")
            bio = p.get("biography")
            if bio:
                bits.append(f"迹:{bio}")
            rows.append("、".join(bits))
        parts.append("人物:" + "；".join(rows))
    # 扁平结构：正文以纯文本段落 content 存储（不再分 type/标题）
    content = page.get("content") or []
    cnt = 0
    for c in content[:8]:
        text = str(c.get("text") if isinstance(c, dict) else c or "").strip()
        if not text:
            continue
        cnt += 1
        parts.append(f"谱文({cnt}):{text[:300]}")
    notes = str(page.get("notes") or "")
    if notes:
        parts.append(f"备注:{notes[:80]}")
    return "\n".join(parts)


def _split_chunks(seq: List[Tuple[int, str]]) -> List[List[Tuple[int, str]]]:
    """按目标字符数贪心分块；相邻块重叠 OVERLAP 页（防父子被切在边界）。

    防死循环：原实现每块结束无条件把下一块起点回退 OVERLAP 页。当某块只推进了
    ≤ OVERLAP 页就超字符上限（相邻长文本页聚集，如登记/世系页每页千余字，2 页即超
    CHUNK_CHARS），回退会把起点拉回本块起点 → 下一轮生成完全相同分块 → 无限循环、
    chunks 无限增长直至进程被 OOM 杀（实测 523 页卷卡死并占满 31G 内存）。修复为
    「本块净推进页数 > OVERLAP 时才回退重叠」：推进不足时强制前进、放弃本次重叠，
    既保证起点单调前进（不死循环），又对正常文本保留原有的跨块重叠。
    """
    chunks: List[List[Tuple[int, str]]] = []
    i = 0
    n = len(seq)
    while i < n:
        block: List[Tuple[int, str]] = []
        size = 0
        j = i
        while j < n and (not block or size + len(seq[j][1]) <= CHUNK_CHARS):
            block.append(seq[j])
            size += len(seq[j][1])
            j += 1
        # 单页超长也必须进块，避免内层空转
        if not block:
            block = [seq[i]]
            j = i + 1
        chunks.append(block)
        advanced = j - i
        i = j
        if i >= n:
            break
        # 仅当净推进 > OVERLAP 时才回退重叠；推进不足时直接前进，防止起点回卷死循环
        if advanced > OVERLAP:
            i -= OVERLAP
    return chunks


async def _chat(
    prompt: str, timeout: float | None = None, enable_thinking: bool = False
) -> str:
    """纯文本对话调用（无图）。

    enable_thinking 默认关思考——实测(qwen3-vl-30b, SGLang)开思考时模型 reasoning
    会占满 max_tokens 把 content 挤空(返回空串致解析失败)，且慢 5 倍(1 块 5.5min 空返回)；
    关思考 1.1min 稳定产出 JSON。timeout 默认逐页识别 VLM_TIMEOUT(60s)，
    卷级整理单独放宽到 CONSOLIDATE_TIMEOUT。
    """
    payload = {
        "model": settings.QWEN_MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": settings.VISION_MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }
    headers = {"Authorization": f"Bearer {settings.SGLANG_API_KEY}"}
    async with httpx.AsyncClient(timeout=timeout or settings.VLM_TIMEOUT) as client:
        resp = await client.post(
            f"{settings.SGLANG_URL}/v1/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


async def _chat_with_retry(
    prompt: str, timeout: float | None = None, enable_thinking: bool = False
) -> str:
    last_exc: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            return await _chat(prompt, timeout=timeout, enable_thinking=enable_thinking)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("卷级整理调用第 %d 次失败: %s", attempt + 1, exc)
            if attempt < RETRIES:
                await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(f"卷级整理 LLM 调用失败: {last_exc}")


def _normalize_block(raw: dict, page_nos: List[int]) -> Tuple[List[dict], List[dict]]:
    persons: List[dict] = []
    for p in raw.get("persons") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "")).strip()
        if not name:
            continue
        gender = str(p.get("gender", "unknown")).lower()
        if gender not in ("male", "female", "unknown"):
            gender = "unknown"
        conf = p.get("confidence")
        try:
            conf = float(conf) if conf is not None else 0.5
        except (TypeError, ValueError):
            conf = 0.5
        persons.append(
            {
                "name": name,
                "gender": gender,
                "birth_year": p.get("birth_year"),
                "death_year": p.get("death_year"),
                "birth_place": p.get("birth_place") or None,
                "biography": p.get("biography") or None,
                "confidence": round(min(max(conf, 0.0), 1.0), 2),
                "pages": list(page_nos),
            }
        )
    relations: List[dict] = []
    for r in raw.get("relations") or []:
        if not isinstance(r, dict):
            continue
        rtype = str(r.get("type", "")).strip()
        from_name = str(r.get("from_name", "")).strip()
        to_name = str(r.get("to_name", "")).strip()
        if rtype not in ("parent_child", "spouse") or not from_name or not to_name:
            continue
        conf = r.get("confidence")
        try:
            conf = float(conf) if conf is not None else 0.5
        except (TypeError, ValueError):
            conf = 0.5
        relations.append(
            {
                "type": rtype,
                "from_name": from_name,
                "to_name": to_name,
                "confidence": round(min(max(conf, 0.0), 1.0), 2),
                "pages": list(page_nos),
            }
        )
    return persons, relations


def _merge_into(
    p_by_key: Dict[str, dict],
    r_by_key: Dict[Tuple[str, str, str], dict],
    persons: List[dict],
    relations: List[dict],
) -> None:
    """把一批结果并入累积字典（增量归并：每块只处理该块，避免全量重算）。"""
    for p in persons:
        key = _norm_name(p["name"])
        old = p_by_key.get(key)
        if old is None:
            p_by_key[key] = p
            continue
        # 属性合并：优先取非空/置信更高的
        for f in ("birth_year", "death_year", "birth_place", "biography"):
            if not old.get(f) and p.get(f):
                old[f] = p[f]
        old["pages"] = sorted(set((old.get("pages") or []) + (p.get("pages") or [])))
        old["confidence"] = round(
            min(max(max(old.get("confidence") or 0, p.get("confidence") or 0), 0.0), 1.0), 2
        )
        if old["gender"] == "unknown" and p["gender"] != "unknown":
            old["gender"] = p["gender"]
    for r in relations:
        key = (r["type"], _norm_name(r["from_name"]), _norm_name(r["to_name"]))
        old = r_by_key.get(key)
        if old is None:
            r_by_key[key] = r
            continue
        old["pages"] = sorted(set((old.get("pages") or []) + (r.get("pages") or [])))
        old["confidence"] = round(
            min(max(max(old.get("confidence") or 0, r.get("confidence") or 0), 0.0), 1.0), 2
        )


def _merge(
    persons: List[dict], relations: List[dict]
) -> Tuple[List[dict], List[dict]]:
    """按规范化姓名合并各块结果（跨块同名视为同一人），保持先见顺序。"""
    p_by_key: Dict[str, dict] = {}
    r_by_key: Dict[Tuple[str, str, str], dict] = {}
    _merge_into(p_by_key, r_by_key, persons, relations)
    return list(p_by_key.values()), list(r_by_key.values())


# 卷级整理的块级全局并发闸：与逐页识别共享 206（max_running_requests=4）。
# 多任务同时整理时，块在飞总数不超过该值；块内滑窗并发，避免把 206 排队打满。
_consolidate_semaphore = asyncio.Semaphore(settings.CONSOLIDATE_MAX_CONCURRENCY)


# ============ 卷内 AI 全局消歧 pass（块间归并收口，人工审核前） ============
# 块间按「去空格规范化名」合键，写法不同但实为同一人的跨块重复（前块「志远」、后块
# 「王志远」）键不同会残留为两条。本 pass 在块级滑窗全部归并后，把整卷人物总表分批送
# LLM 做一次全表消歧：仅当 AI 给出高置信「同一人」判定才自动合并，宁缺毋滥；低于置信
# 阈值 / 任何异常都不自动执行，留给人工审核页整卷人物列表把关（误判可在审核页改/删）。
DISAMBIG_MIN: int = max(0, settings.CONSOLIDATE_DISAMBIG_MIN_PERSONS)
DISAMBIG_BATCH: int = max(1000, settings.CONSOLIDATE_DISAMBIG_BATCH_CHARS)
DISAMBIG_CONF: float = min(max(settings.CONSOLIDATE_DISAMBIG_MIN_CONF, 0.0), 1.0)

_DISAMBIG_PROMPT = """你是族谱人物消歧专家。以下是同一部族谱「整卷整理后」的人物名单：每个条目一行，格式「编号|姓名|性别|生卒|籍贯|所在页|事迹」。由于整理分多个段落进行，同一人在不同段落可能被写成不同称呼（正名与表字、带姓与省姓、排行与名等），形成名字不同、实为同一人的两条（名字完全相同的已提前合并，无需处理）。

请只找出「写法不同但几乎可以肯定实为同一人」的条目对。判据优先级：生卒（含月份）一致 > 事迹/配偶子女线索一致 > 一名含于另一名。两人生卒、房支、事迹相互矛盾，或疑似同页并列的两人（名字带「(二)」等区分标记），视为不同人，绝不合并。

宁缺毋滥：只报非常有把握的，拿不准就不报。严格输出 JSON（不要任何其他文字）：
{{"merges":[{{"duplicate_index":3,"keep_index":1,"confidence":0.95,"reason":"生卒一致且名含于另一名"}}]}}
- duplicate_index=被并入方条目编号，keep_index=保留方条目编号；
- confidence 0~1，低于 0.9 的建议不会被采纳；
- 没有可报的重复输出 {{"merges":[]}}

名单：
{rows}"""


def _person_brief(idx: int, p: dict) -> str:
    pages = p.get("pages") or []
    loc = f"页{min(pages)}" if pages else ""
    by, dy = p.get("birth_year"), p.get("death_year")
    bd = (f"{by or ''}~{dy or ''}").strip("~")
    g = {"male": "男", "female": "女"}.get(p.get("gender") or "", "")
    bio = str(p.get("biography") or "").replace("\n", "")[:40]
    bp = str(p.get("birth_place") or "")[:16]
    return f"{idx}|{p.get('name') or ''}|{g}|{bd}|{bp}|{loc}|{bio}"


def _apply_disambig_merges(
    persons: List[dict], relations: List[dict], merges: List[dict]
) -> Tuple[List[dict], List[dict]]:
    """按消歧建议执行合并：dup 并入 keep（属性互补规则同块归并），关系两端同步改名。

    返回新 persons/relations（persons 保持原顺序、去掉被并入项）。每条建议至多执行一次；
    索引越界 / 名字相同 / keep 已被并走等异常情况自动跳过该条。
    """
    # 先做属性并入与“被并入”标记（标记暂存于 dict，重建列表时剔除）
    dropped_norm: Dict[str, str] = {}  # 被并入名字(norm) -> 保留名字原文
    for m in merges:
        try:
            dup_i = int(m.get("duplicate", -1))
            keep_i = int(m.get("keep", -1))
        except (TypeError, ValueError):
            continue
        if dup_i == keep_i or dup_i < 0 or keep_i < 0:
            continue
        if dup_i >= len(persons) or keep_i >= len(persons):
            continue
        dup = persons[dup_i]
        keep = persons[keep_i]
        if dup is None or keep is None:
            continue
        dup_name = str(dup.get("name") or "").strip()
        keep_name = str(keep.get("name") or "").strip()
        if not dup_name or not keep_name or dup_name == keep_name:
            continue
        if dup.get("_merged") or keep.get("_merged"):
            continue  # keep 自身也已被并入 / dup 已被并走 → 跳过，防连环
        for f in ("birth_year", "death_year", "birth_place", "biography"):
            if not keep.get(f) and dup.get(f):
                keep[f] = dup[f]
        keep["pages"] = sorted(
            set((keep.get("pages") or []) + (dup.get("pages") or []))
        )
        keep["confidence"] = round(
            min(max(max(keep.get("confidence") or 0, dup.get("confidence") or 0), 0.0), 1.0), 2
        )
        if keep.get("gender") in ("", "unknown") and dup.get("gender") not in ("", "unknown"):
            keep["gender"] = dup["gender"]
        dup["_merged"] = True
        dropped_norm[_norm_name(dup_name)] = keep_name

    if not dropped_norm:
        return persons, relations

    # 重建人物列表：剔除被并入项、保留 keep（其余字段原样）
    new_persons: List[dict] = []
    for p in persons:
        if p.get("_merged"):
            continue
        new_persons.append({k: v for k, v in p.items() if k != "_merged"})

    # 关系改写：被并入的名字替换为保留名；自环（合并后 from==to）删除；按三元组去重
    new_relations: List[dict] = []
    seen: set = set()
    for r in relations:
        rtype = r.get("type")
        f = str(r.get("from_name") or "").strip()
        t = str(r.get("to_name") or "").strip()
        nf, nt = _norm_name(f), _norm_name(t)
        if nf in dropped_norm:
            f = dropped_norm[nf]
        if nt in dropped_norm:
            t = dropped_norm[nt]
        nf, nt = _norm_name(f), _norm_name(t)
        if not nf or not nt or nf == nt:
            continue  # 无意义/自环
        key = (rtype, nf, nt)
        if key in seen:
            continue
        seen.add(key)
        nr = dict(r)
        nr["from_name"] = f
        nr["to_name"] = t
        new_relations.append(nr)

    return new_persons, new_relations


async def _run_disambiguation_pass(
    persons: List[dict], relations: List[dict]
) -> Tuple[List[dict], List[dict], int]:
    """卷内全局消歧（整卷人物总表分批送 LLM 找「写法不同实为同一人」的重复对）。

    块间代码归并只认去空格同名；写法不一致的同人（如正名/表字/省姓）会残留。本函数
    在全部分块归并后调用，AI 高置信建议才自动合并。任何失败/异常由调用方兜底忽略。
    返回 (persons, relations, 实际合并条数)。
    """
    if len(persons) < DISAMBIG_MIN or not persons:
        return persons, relations, 0

    rows = [_person_brief(i, p) for i, p in enumerate(persons)]
    batches: List[List[str]] = []
    cur: List[str] = []
    cur_chars = 0
    for row in rows:
        if cur and cur_chars + len(row) > DISAMBIG_BATCH:
            batches.append(cur)
            cur, cur_chars = [], 0
        cur.append(row)
        cur_chars += len(row)
    if cur:
        batches.append(cur)

    merges: List[dict] = []
    for bi, rows_b in enumerate(batches, start=1):
        prompt = _DISAMBIG_PROMPT.format(rows="\n".join(rows_b))
        # 持全局并发闸：与其它任务的在飞整理块公平共享 206，避免挤压/被挤压
        async with _consolidate_semaphore:
            raw = await _chat_with_retry(prompt, timeout=settings.CONSOLIDATE_TIMEOUT)
        data = parse_json_from_llm(raw) or {}
        for m in data.get("merges") or []:
            if not isinstance(m, dict):
                continue
            try:
                dup_i = int(m.get("duplicate_index", -1))
                keep_i = int(m.get("keep_index", -1))
                conf = float(m.get("confidence") or 0)
            except (TypeError, ValueError):
                continue
            if conf < DISAMBIG_CONF or dup_i == keep_i:
                continue
            if dup_i < 0 or keep_i < 0 or dup_i >= len(persons) or keep_i >= len(persons):
                continue
            merges.append(
                {
                    "duplicate": dup_i,
                    "keep": keep_i,
                    "confidence": round(min(max(conf, 0.0), 1.0), 2),
                    "reason": str(m.get("reason") or "")[:80],
                }
            )
    if not merges:
        return persons, relations, 0

    new_persons, new_relations = _apply_disambig_merges(persons, relations, merges)
    applied = len(persons) - len(new_persons)
    logger.info(
        "卷内消歧 pass：AI 建议 %d 组合并，实际合并 %d 组（人物 %d → %d，关系 %d → %d）",
        len(merges), applied, len(persons), len(new_persons),
        len(relations), len(new_relations),
    )
    return new_persons, new_relations, applied


def _other_extraction_waiting(task_id: str) -> bool:
    """是否有其它任务正在等待 AI 识别（优先级 B：AI 识别 > 整卷整理）。

    整理持有着「识别+整理」共用的任务闸，若此时别卷已转好图在排队等识别，
    整理应让位：落盘半成品 → 置 paused(consolidating) → 释放闸；识别跑完后由
    import_service.auto_resume_yielded_consolidation 自动续跑整理。
    """
    try:
        from app.services.import_service import has_pending_extraction

        return has_pending_extraction(task_id)
    except Exception:  # noqa: BLE001
        return False


def _task_paused(task_id: str) -> bool:
    """任务是否处于暂停态（前端 Pause 写 DB 状态，整理协程在块边界自查后停下）。"""
    with SessionLocal() as db:
        row = (
            db.query(ImportTask.status)
            .filter(ImportTask.task_id == task_id)
            .first()
        )
        return bool(row) and row[0] == "paused"


async def run_consolidation(task_id: str) -> None:
    """卷级整理入口：登记后台任务（删除时可真正取消），块并发由全局闸 + 滑动窗口控制。"""
    task_registry.register(task_id)
    try:
        await _consolidate_impl(task_id)
    finally:
        task_registry.unregister(task_id)


async def _consolidate_impl(task_id: str) -> None:
    """卷级整理主流程：读任务已识别页 → 分块调 LLM → 归并 → 写 result.consolidated。

    任务进入本函数时应已具备逐页提取结果。结束后统一置 status=done/stage=done：
    - 成功：error_msg 保留原失败页提示（若有）；
    - 某块失败：置 error_msg 提示可「重新整理」。
    """
    task = _get_task(task_id)
    if not task or not task.result:
        return
    # 插图页补充识别：对正文极少（疑似照片/图画/地图/封面）页生成图注条目，apply 时随
    # 篇目一并入库、可被 RAG 检索到。幂等（result.illustration_pass_done），函数内 import
    # 避免与 import_service 循环引用；失败不阻断整理主流程。
    try:
        from app.services.import_service import ensure_illustration_pass

        await ensure_illustration_pass(task_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("任务 %s 插图页补充识别异常（忽略）: %s", task_id, exc)
    # ensure_illustration_pass 会更新 result.pages（illustrations）与 illustration_pass_done；
    # 必须重新读取任务：沿用旧 task 引用时，下方落盘会用不含 illustrations 的旧 pages
    # 整体覆盖 result，把刚生成的插图成果与完成标记一起冲掉（illustration_pass_done 被覆盖后
    # 下次整理又会重跑插图识别，白白耗费整卷级 VLM 时长）。
    task = _get_task(task_id)
    if not task or not task.result:
        return
    pages_all = task.result.get("pages") or []
    pages_ok = [p for p in pages_all if not p.get("failed")]
    all_nos = [p.get("page_no", 0) for p in pages_all]
    if not pages_ok:
        # 没有可整理的数据：直接结束（任务本身可能整本失败）
        _update_task(
            task_id,
            status="done",
            stage="done",
            done_pages=len(pages_all),
            error_msg="全部页面识别失败，无可整理内容，请重试失败页或重新上传",
        )
        return

    prev_error = task.error_msg or None
    ordered = sorted(pages_ok, key=lambda p: p.get("page_no", 0))
    seq = [(p.get("page_no", 0), _page_text(p)) for p in ordered]
    chunks = _split_chunks(seq)
    total_no = len(pages_all)

    _update_task(task_id, status="running", stage="consolidating", done_pages=0, error_msg=None)
    logger.info("任务 %s：开始卷级整理，共 %d 块（%d 页有效）", task_id, len(chunks), len(ordered))

    failed_chunks = 0
    done_pages = 0
    done_blocks = 0
    # 累积归并状态：每完成一块只并入该块（增量），不再缓存全部块原始结果——
    # 大卷（500+ 页上百块）全量缓存 + 每块全量重归并是内存暴涨的主因
    acc_p: Dict[str, dict] = {}
    acc_r: Dict[Tuple[str, str, str], dict] = {}
    total_chunks = len(chunks)
    WINDOW = max(1, settings.CONSOLIDATE_MAX_CONCURRENCY)

    async def _do_block(
        ci: int, chunk: List[Tuple[int, str]]
    ) -> Tuple[int, List[dict], List[dict]]:
        """整理单块：持全局块闸调 206；失败异常上抛，由调度处计 failed_chunks。"""
        page_nos = [no for no, _ in chunk]
        chunk_text = "\n\n".join(t for _, t in chunk)
        start_no, end_no = page_nos[0], page_nos[-1]
        prompt = _prompt_template().format(
            start=start_no, end=end_no, chunk=chunk_text
        )
        async with _consolidate_semaphore:
            raw = await _chat_with_retry(
                prompt, timeout=settings.CONSOLIDATE_TIMEOUT
            )
        data = parse_json_from_llm(raw)
        persons, relations = _normalize_block(data, page_nos)
        return ci, persons, relations

    def _acc() -> Tuple[List[dict], List[dict]]:
        """取当前累计归并结果（半成品落盘与最终合并共用）。

        并入顺序严格按块号（=页序），与改动前「按块号升序全量合并」完全等价：
        同一人若多块给出不同非空属性，取页序靠前那块的值，不随块完成先后漂移。
        """
        return list(acc_p.values()), list(acc_r.values())

    def _save_progress() -> None:
        """落一次完整 result 半成品（含 consolidated 尾部）。"""
        acc_p_s, acc_r_s = _acc()
        _update_task(
            task_id,
            done_pages=done_pages,
            result=_result_with_consolidated(
                task, pages_all, acc_p_s, acc_r_s, failed_chunks
            ),
            # 整理阶段 pages 未变（镜像在建页时已写好）：跳过数百行镜像重建
            skip_pages_mirror=True,
        )

    # 乱序完成块的暂存区（滑窗并发下完成顺序≠块号序）。只暂存窗口内未轮到的块
    # （≤ WINDOW 个），不缓存全部块结果，内存仍远低于旧实现；并入严格按块号升序，
    # 保证归并结果与改动前完全一致。
    pending_blocks: Dict[int, Tuple[List[dict], List[dict]]] = {}
    next_ci = 1

    def _drain() -> None:
        """把暂存区中紧接 next_ci 的连续块按块号升序并入累计结果。"""
        nonlocal next_ci, done_blocks
        while next_ci in pending_blocks:
            bp, br = pending_blocks.pop(next_ci)
            if bp or br:
                _merge_into(acc_p, acc_r, bp, br)
                done_blocks += 1
            next_ci += 1

    inflight: set = set()
    fut_map: Dict[asyncio.Task, Tuple[int, List[int]]] = {}
    pos = 0
    last_save = time.monotonic()
    try:
        # 预填窗口（同时在飞块数 ≤ WINDOW），完成一块补一块：
        # 避免把全部块一次性丢进队列占满全局闸 FIFO，多任务公平共享 206
        while len(inflight) < WINDOW and pos < total_chunks:
            t = asyncio.create_task(_do_block(pos + 1, chunks[pos]))
            fut_map[t] = (pos + 1, [no for no, _ in chunks[pos]])
            inflight.add(t)
            pos += 1
        try:
            while inflight:
                done, pending = await asyncio.wait(
                    inflight, return_when=asyncio.FIRST_COMPLETED
                )
                inflight = pending
                for fut in done:
                    ci, page_nos = fut_map.pop(fut, (None, []))
                    try:
                        got_ci, b_persons, b_relations = await fut
                        pending_blocks[got_ci] = (b_persons, b_relations)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        failed_chunks += 1
                        logger.warning(
                            "任务 %s 第 %d 块（第 %d~%d 页）整理失败: %s",
                            task_id, ci,
                            page_nos[0] if page_nos else 0,
                            page_nos[-1] if page_nos else 0, exc,
                        )
                        # 失败块也要占位：否则按块号推进会卡在这一块
                        if ci is not None:
                            pending_blocks[ci] = ([], [])
                    # 按块号升序并入已到齐的连续块（与旧实现等价）
                    _drain()
                    if ci is not None:
                        done_pages = min(total_no, done_pages + len(page_nos))
                        # 落盘节流（大卷 OOM 防护）：每完成一块就把整卷 result 整体序列化
                        # 写库，500+ 页大卷会反复造上百 MB 临时对象直至被 OOM 杀；
                        # 改为每 N 块 / 每 X 秒 / 最后一块落一次，其余只轻量更新进度
                        now = time.monotonic()
                        need_save = (
                            not inflight
                            or done_blocks % SAVE_EVERY_BLOCKS == 0
                            or (
                                SAVE_EVERY_SECONDS > 0
                                and now - last_save >= SAVE_EVERY_SECONDS
                            )
                        )
                        if need_save:
                            last_save = now
                            _save_progress()
                        else:
                            # 只更新进度：不写 result、不重建页镜像
                            _update_task(task_id, done_pages=done_pages)
                        # 用户点「暂停」：停在块边界（半成品已落盘）。恢复时点「继续」会
                        # 整卷重跑整理（幂等），或点「重新整理」直接重跑
                        if _task_paused(task_id):
                            if not need_save:
                                _save_progress()
                            log_task_action(
                                task_id, "pause",
                                f"整卷整理中已暂停（已完成 {done_blocks}/{total_chunks} 块）",
                            )
                            _update_task(
                                task_id,
                                status="paused",
                                pause_reason="manual",
                                error_msg=(
                                    "已暂停（整卷整理未完成；点「继续」或「重新整理」会整卷重跑）"
                                ),
                            )
                            return
                        # 优先级 B：有其它任务在等 AI 识别名额 → 整理让位
                        # （半成品已落盘；识别跑完后自动续跑整理，无需人工点「继续」）
                        if _other_extraction_waiting(task_id):
                            _save_progress()
                            log_task_action(
                                task_id, "pause",
                                f"整卷整理让位给 AI 识别（已完成 {done_blocks}/{total_chunks} 块）",
                            )
                            _update_task(
                                task_id,
                                status="paused",
                                stage="consolidating",
                                pause_reason="priority_ai",
                                error_msg=(
                                    "整卷整理已让位给 AI 识别，识别完成后会自动续跑整理"
                                ),
                            )
                            logger.info(
                                "任务 %s：整卷整理让位（%d/%d 块），等待中的识别任务先跑",
                                task_id, done_blocks, total_chunks,
                            )
                            return
                    # 完成一块补一块，维持窗口大小
                    if pos < total_chunks:
                        t = asyncio.create_task(_do_block(pos + 1, chunks[pos]))
                        fut_map[t] = (pos + 1, [no for no, _ in chunks[pos]])
                        inflight.add(t)
                        pos += 1
        finally:
            for f in list(inflight):
                if not f.done():
                    f.cancel()
    except Exception as exc:  # noqa: BLE001
        logger.exception("任务 %s 卷级整理异常", task_id)
        persons, relations = [], []
        failed_chunks += 1
        _update_task(
            task_id,
            status="done",
            stage="done",
            done_pages=total_no,
            error_msg=f"卷级整理异常中断：{exc}；可在任务列表点「重新整理」重试",
            result=_result_with_consolidated(task, pages_all, [], [], 1),
            skip_pages_mirror=True,
        )
        return
    persons, relations = _acc()

    # 卷内 AI 全局消歧收口（人工审核之前）：块间代码归并后，写法不同实为同一人的残留
    # 重复（正名/表字/省姓等称呼差异）在整卷人物表上再消一次歧。高置信才自动合并；
    # 本 pass 任何失败都不阻断主流程（保留原归并结果）。
    disambig_applied = 0
    if not failed_chunks:
        try:
            persons, relations, disambig_applied = await _run_disambiguation_pass(
                persons, relations
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("任务 %s 卷内消歧 pass 失败（忽略，保留原归并结果）: %s", task_id, exc)

    final_error = None
    if failed_chunks:
        final_error = (
            f"卷级整理有 {failed_chunks} 个片段失败（已保留其余结果），"
            "可在任务列表点「重新整理」重试"
        )
        if prev_error:
            final_error += f"。{prev_error}"
    elif prev_error:
        final_error = prev_error
    logger.info("任务 %s：卷级整理完成，人物 %d 人、关系 %d 条（失败块 %d%s）",
                task_id, len(persons), len(relations), failed_chunks,
                f"，卷内消歧合并 {disambig_applied} 组" if disambig_applied else "")
    _update_task(
        task_id,
        status="done",
        stage="done",
        done_pages=total_no,
        error_msg=final_error,
        result=_result_with_consolidated(
            task, pages_all, persons, relations, failed_chunks, final=True
        ),
    )
    log_task_action(
        task_id, "ai_consolidate",
        f"AI 卷级整理完成（人物 {len(persons)}、关系 {len(relations)}、失败块 {failed_chunks}）",
    )


def _result_with_consolidated(
    task: ImportTask,
    pages_all: List[dict],
    persons: List[dict],
    relations: List[dict],
    failed_chunks: int,
    final: bool = False,
) -> dict:
    """构造新的 result（pages + consolidated 尾部）。中途每块也调用，persons/relations 为当前累计。"""
    result = dict(task.result or {})
    result["pages"] = pages_all
    result["consolidated"] = {
        "persons": persons,
        "relations": relations,
        "failed_chunks": failed_chunks,
        "done_at": datetime.now().isoformat(timespec="seconds"),
        "final": final,
    }
    if final:
        # 整卷整理成功：清除「单页重识别后需重新整理」的过期提示
        result.pop("consolidation_stale", None)
    return result
