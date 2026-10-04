"""人物与关系的 Neo4j 读写服务。"""
import asyncio
import logging
import re
import uuid
from typing import Dict, List, Optional, Tuple

import httpx

from app.core.config import settings
from app.core.database import driver
from app.services import person_index

logger = logging.getLogger("genealogy.person")

PERSON_FIELDS = [
    "person_id", "name", "gender", "birth_year", "birth_date",
    "death_year", "death_date", "birth_place", "death_place",
    "photo_url", "biography", "notes", "generation", "is_alive",
]


def node_to_dict(node) -> dict:
    d = dict(node)
    return {k: d.get(k) for k in PERSON_FIELDS}


def person_params(data) -> dict:
    """把 Pydantic 模型转成 Neo4j 属性（忽略 None；谱系归属走关系）。"""
    params = {}
    for k in PERSON_FIELDS:
        v = getattr(data, k, None)
        if v is not None and k != "person_id":
            params[k] = v
    return params


async def _attach_lineage_rels(
    person_id: str,
    lineage_id: Optional[str] = None,
    branch_id: Optional[str] = None,
) -> None:
    """设置人物谱系/房支归属关系（lineage_id/branch_id 为空字符串时移除）。"""
    async with driver.session() as session:
        await session.run(
            "MATCH (p:Person {person_id: $person_id}) "
            "OPTIONAL MATCH (p)-[r1:BELONGS_TO]->(:Lineage) DELETE r1 "
            "WITH p "
            "OPTIONAL MATCH (p)-[r2:IN_BRANCH]->(:Branch) DELETE r2",
            person_id=person_id,
        )
        if lineage_id:
            await session.run(
                "MATCH (p:Person {person_id: $person_id}), (l:Lineage {lineage_id: $lineage_id}) "
                "CREATE (p)-[:BELONGS_TO]->(l)",
                person_id=person_id, lineage_id=lineage_id,
            )
        if branch_id:
            await session.run(
                "MATCH (p:Person {person_id: $person_id}), (b:Branch {branch_id: $branch_id}) "
                "CREATE (p)-[:IN_BRANCH]->(b)",
                person_id=person_id, branch_id=branch_id,
            )


# ============ 人物 ============
async def create_person(data) -> dict:
    person_id = uuid.uuid4().hex[:24]
    props = person_params(data)
    props["person_id"] = person_id
    sets = ", ".join(f"p.{k} = ${k}" for k in props)
    cypher = f"CREATE (p:Person {{person_id: $person_id}}) SET {sets} RETURN p"
    async with driver.session() as session:
        rec = await session.run(cypher, **props)
        record = await rec.single()
    lineage_id = getattr(data, "lineage_id", None)
    branch_id = getattr(data, "branch_id", None)
    if lineage_id or branch_id:
        await _attach_lineage_rels(person_id, lineage_id, branch_id)
    person_index.invalidate_person_index()
    return await get_person(person_id) or node_to_dict(record["p"])


async def get_person(person_id: str) -> Optional[dict]:
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (p:Person {person_id: $person_id}) "
            "OPTIONAL MATCH (p)-[:BELONGS_TO]->(l:Lineage) "
            "OPTIONAL MATCH (p)-[:IN_BRANCH]->(b:Branch) "
            "RETURN p, l.lineage_id AS lid, l.name AS lname, "
            "b.branch_id AS bid, b.name AS bname",
            person_id=person_id,
        )
        record = await rec.single()
    if not record:
        return None
    nd = node_to_dict(record["p"])
    nd["lineage_id"] = record["lid"]
    nd["lineage_name"] = record["lname"]
    nd["branch_id"] = record["bid"]
    nd["branch_name"] = record["bname"]
    return nd


async def list_persons(
    search: Optional[str] = None,
    gender: Optional[str] = None,
    generation: Optional[int] = None,
    lineage_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    limit: int = 1000,
    offset: int = 0,
) -> Tuple[List[dict], int]:
    where = []
    params: dict = {}
    if search:
        where.append("toLower(p.name) CONTAINS toLower($search)")
        params["search"] = search
    if gender and gender != "unknown":
        where.append("p.gender = $gender")
        params["gender"] = gender
    if generation is not None:
        where.append("p.generation = $generation")
        params["generation"] = generation
    if branch_id:
        where.append("(p)-[:IN_BRANCH]->(:Branch {branch_id: $branch_id})")
        params["branch_id"] = branch_id
    elif lineage_id:
        # 直接归谱 OR 归该谱某房支
        where.append(
            "(p)-[:BELONGS_TO]->(:Lineage {lineage_id: $lineage_id}) OR "
            "(p)-[:IN_BRANCH]->(:Branch {lineage_id: $lineage_id})"
        )
        params["lineage_id"] = lineage_id
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    async with driver.session() as session:
        rec = await session.run(
            f"MATCH (p:Person) {where_clause} RETURN count(p) AS total",
            **params,
        )
        total = (await rec.single())["total"]

        rec = await session.run(
            f"MATCH (p:Person) {where_clause} "
            "OPTIONAL MATCH (p)-[:BELONGS_TO]->(l:Lineage) "
            "OPTIONAL MATCH (p)-[:IN_BRANCH]->(b:Branch) "
            "RETURN p, l.lineage_id AS lid, l.name AS lname, "
            "b.branch_id AS bid, b.name AS bname "
            "ORDER BY p.name SKIP $offset LIMIT $limit",
            **params, offset=offset, limit=limit,
        )
        records = [r async for r in rec]

    items = []
    for r in records:
        nd = node_to_dict(r["p"])
        nd["lineage_id"] = r["lid"]
        nd["lineage_name"] = r["lname"]
        nd["branch_id"] = r["bid"]
        nd["branch_name"] = r["bname"]
        items.append(nd)

    # 批量取父母/子女（列表页展示）
    if items:
        ids = [it["person_id"] for it in items]
        async with driver.session() as session:
            rec = await session.run(
                "MATCH (p:Person)-[:PARENT_OF]->(c:Person) WHERE p.person_id IN $ids "
                "RETURN p.person_id AS pid, collect(c.person_id) AS children",
                ids=ids,
            )
            children_map = {r["pid"]: r["children"] async for r in rec}
            rec = await session.run(
                "MATCH (f:Person)-[:PARENT_OF]->(p:Person) WHERE p.person_id IN $ids "
                "RETURN p.person_id AS pid, collect(f.person_id) AS parents",
                ids=ids,
            )
            parents_map = {r["pid"]: r["parents"] async for r in rec}
        for it in items:
            it["children"] = children_map.get(it["person_id"], [])
            it["parents"] = parents_map.get(it["person_id"], [])

    return items, total


async def update_person(person_id: str, data) -> Optional[dict]:
    props = person_params(data)
    sets = ", ".join(f"p.{k} = ${k}" for k in props) if props else "p.notes = p.notes"
    props["person_id"] = person_id
    async with driver.session() as session:
        rec = await session.run(
            f"MATCH (p:Person {{person_id: $person_id}}) SET {sets} RETURN p",
            **props,
        )
        record = await rec.single()
    if not record:
        return None
    # 谱系归属：data.lineage_id/branch_id 显式给出（含 "" 移除）才处理
    if hasattr(data, "lineage_id") and hasattr(data, "branch_id"):
        lineage_id = data.lineage_id if data.lineage_id is not None else None
        branch_id = data.branch_id if data.branch_id is not None else None
        # 只在前端显式传了归属字段时重建关系（否则保持不动）
        if "lineage_id" in data.model_fields_set or "branch_id" in data.model_fields_set:
            # 未传的一侧保持原值
            cur = await get_person(person_id)
            if "lineage_id" not in data.model_fields_set:
                lineage_id = cur.get("lineage_id")
            if "branch_id" not in data.model_fields_set:
                branch_id = cur.get("branch_id")
            await _attach_lineage_rels(person_id, lineage_id or None, branch_id or None)
    person_index.invalidate_person_index()
    return await get_person(person_id)


async def delete_person(person_id: str) -> bool:
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (p:Person {person_id: $person_id}) DETACH DELETE p "
            "RETURN count(p) AS n",
            person_id=person_id,
        )
        record = await rec.single()
    person_index.invalidate_person_index()
    return bool(record and record["n"])


async def batch_delete(person_ids: List[str]) -> int:
    """批量物理删除人物（含其全部关联关系）。"""
    ids = list(dict.fromkeys([i for i in person_ids if i]))
    if not ids:
        return 0
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (p:Person) WHERE p.person_id IN $ids "
            "DETACH DELETE p RETURN count(p) AS n",
            ids=ids,
        )
        record = await rec.single()
    person_index.invalidate_person_index()
    return record["n"] if record else 0


async def delete_persons_by_scope(
    lineage_id: str, branch_id: Optional[str] = None
) -> int:
    """按分类删除人物：删除某谱系（或其中某房支）下的全部人物（物理删除，含全部关联关系）。
    谱系 / 房支节点本身保留。"""
    if branch_id:
        cypher = (
            "MATCH (p:Person) WHERE (p)-[:IN_BRANCH]->(:Branch {branch_id: $branch_id}) "
            "DETACH DELETE p RETURN count(p) AS n"
        )
        params: dict = {"branch_id": branch_id}
    else:
        cypher = (
            "MATCH (p:Person) WHERE "
            "(p)-[:BELONGS_TO]->(:Lineage {lineage_id: $lineage_id}) OR "
            "(p)-[:IN_BRANCH]->(:Branch {lineage_id: $lineage_id}) "
            "DETACH DELETE p RETURN count(p) AS n"
        )
        params = {"lineage_id": lineage_id}
    async with driver.session() as session:
        rec = await session.run(cypher, **params)
        record = await rec.single()
    person_index.invalidate_person_index()
    return record["n"] if record else 0


# ============ 人物合并 ============
# ============ 疑似同名 AI 裁定（跨卷，仅建议不自动合并） ============
# 归一化姓名与前端 LineageDetail.normName 一致：去空格/全角空格/制表符 + 忽略大小写。
# 只对「归一化后完全相同」的候选组做 LLM 裁定——这类组近乎确定的重复，AI 主要帮忙区分
# 「真同名不同人（生卒/房支矛盾 → 建议不合并）」与「跨卷重复写入（写法略异 → 建议合并）」。
_AI_DUP_BATCH_GROUPS = 12  # 每批最多组数（控制单次 LLM 调用体量）
_AI_DUP_BATCH_CHARS = 6000  # 每批累计字符上限


def _dup_norm_name(name: str) -> str:
    return re.sub(r"[ \t\u3000]", "", str(name or "").strip().lower())


async def _llm_text_chat(prompt: str, timeout: float = 300.0) -> str:
    """纯文本对话调用 206（关思考）。谱系同名裁定数据量小，串行逐批调用即可。"""
    payload = {
        "model": settings.QWEN_MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": settings.VISION_MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {"Authorization": f"Bearer {settings.SGLANG_API_KEY}"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{settings.SGLANG_URL}/v1/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


async def ai_review_duplicate_groups(lineage_id: str) -> dict:
    """对该谱系下全部人物按归一化名聚簇，把候选组送 LLM 裁定是否同一人。

    返回 {"total_groups", "groups", "error"}：
    - groups: [{key, same, keep_person_id, keep_name, confidence, reason}]；
    - 不自动合并任何节点，仅提供建议供前端「疑似同名合并」弹窗使用。
    """
    items, _ = await list_persons(lineage_id=lineage_id, limit=5000)
    bucket: Dict[str, List[dict]] = {}
    for it in items:
        key = _dup_norm_name(it.get("name") or "")
        if not key:
            continue
        bucket.setdefault(key, []).append(it)
    clusters = [m for m in bucket.values() if len(m) >= 2]
    if not clusters:
        return {"total_groups": 0, "groups": [], "error": ""}

    # 分批：组数 + 累计字符双上限
    batches: List[List[List[dict]]] = []
    cur: List[List[dict]] = []
    cur_chars = 0
    for c in clusters:
        c_chars = sum(len(str(x.get("name") or "")) + len(str(x.get("biography") or "")) for x in c)
        if cur and (len(cur) >= _AI_DUP_BATCH_GROUPS or cur_chars + c_chars > _AI_DUP_BATCH_CHARS):
            batches.append(cur)
            cur, cur_chars = [], 0
        cur.append(c)
        cur_chars += c_chars + len(str(c[0].get("name") or ""))
    if cur:
        batches.append(cur)

    dup_prompt = """你是族谱人物查重专家。下面是同一谱系中「姓名归一化后相同或近似」的人物候选组：这类多半是跨卷重复写入（同一人），但也可能恰好是同名不同人。请判定每组内成员是否真的是同一人。

每组格式（下面候选组中，组名写在「组「...」」里，成员行以编号开头）：
组「某归一化姓名」：
 0) 姓名 | 性别 | 生卒 | 房支 | 事迹
 1) 姓名 | 性别 | 生卒 | 房支 | 事迹

判定要点：
- 生卒一致、事迹/谱系行文一致 → 同一人（same=true），并给出建议保留的成员编号 keep_index（通常选信息更全或名字更规范者）；
- 生卒互相矛盾、属于不同房支、或明确是不同支的两名同名人 → 不同人（same=false）；
- 名字写法略异（繁简/空格/称呼）本身不足以判为不同人，要结合生卒与房支。

宁缺毋滥：拿不准时 same=false。严格输出 JSON（不要任何其他文字）：
{{"verdicts":[{{"group":"组名原文","same":true,"keep_index":0,"confidence":0.95,"reason":"30字内理由"}}]}}
每组都必须输出一条，group 值必须与候选组里「组「...」」的名字完全一致。

候选组：
{groups}"""

    def _member_line(idx: int, it: dict) -> str:
        g = {"male": "男", "female": "女"}.get(it.get("gender") or "", "未知")
        bd = "~".join(
            x for x in (str(it.get("birth_year") or ""), str(it.get("death_year") or "")) if x
        ) or "不详"
        branch = it.get("branch_name") or "未归房"
        bio = str(it.get("biography") or "").replace("\n", "")[:60]
        return f"{idx}) {it.get('name') or ''} | {g} | {bd} | {branch} | {bio}"

    from app.services.vision_service import parse_json_from_llm

    # raw_verdicts: key -> {same, keep_index, confidence, reason}（key=组归一化名）
    raw_by_key: Dict[str, dict] = {}
    try:
        for batch in batches:
            blocks = []
            valid_keys = set()
            for c in batch:
                key = _dup_norm_name(c[0].get("name") or "")
                valid_keys.add(key)
                lines = "\n".join(_member_line(i, it) for i, it in enumerate(c))
                blocks.append(f"组「{key}」:\n{lines}")
            prompt = dup_prompt.format(groups="\n\n".join(blocks))
            # 批级轻量重试（LLM 瞬时抖动），仍失败则整体回滚给调用方报错
            raw = ""
            last_exc: Exception | None = None
            for attempt in range(3):
                try:
                    raw = await _llm_text_chat(
                        prompt, timeout=settings.CONSOLIDATE_TIMEOUT
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt < 2:
                        await asyncio.sleep(2 * (attempt + 1))
            if not raw:
                raise RuntimeError(f"LLM 调用失败: {last_exc}")
            data = parse_json_from_llm(raw) or {}
            for v in data.get("verdicts") or []:
                if not isinstance(v, dict):
                    continue
                key = str(v.get("group") or "").strip()
                if not key or key not in valid_keys:
                    continue  # 忽略本批不存在的组键（模型幻觉），宁缺毋滥
                try:
                    ki = int(v.get("keep_index") or 0)
                except (TypeError, ValueError):
                    ki = 0
                try:
                    conf = float(v.get("confidence") or 0)
                except (TypeError, ValueError):
                    conf = 0
                raw_by_key[key] = {
                    "same": bool(v.get("same", True)),
                    "keep_index": ki,
                    "confidence": round(min(max(conf, 0.0), 1.0), 2),
                    "reason": str(v.get("reason") or "")[:60],
                }
    except Exception as exc:  # noqa: BLE001
        logger.warning("谱系同名 AI 裁定失败（不影响本地分组/人工合并）: %s", exc)
        return {
            "total_groups": len(clusters),
            "groups": [],
            "error": f"AI 分析失败（模型服务暂不可用？）：{exc}",
        }

    # 组装输出：未覆盖的组用保守默认（不预判），覆盖的组映射建议保留成员
    out_groups: List[dict] = []
    for c in clusters:
        key = _dup_norm_name(c[0].get("name") or "")
        v = raw_by_key.get(key)
        if v is None:
            out_groups.append(
                {"key": key, "same": True, "keep_person_id": None, "keep_name": None,
                 "confidence": 0.0, "reason": ""}
            )
            continue
        same = v["same"]
        ki = v["keep_index"]
        if ki < 0 or ki >= len(c):
            ki = 0
        keep_person_id = c[ki].get("person_id")
        keep_name = c[ki].get("name")
        out_groups.append(
            {"key": key, "same": same, "keep_person_id": keep_person_id,
             "keep_name": keep_name, "confidence": v["confidence"], "reason": v["reason"]}
        )
    return {"total_groups": len(clusters), "groups": out_groups, "error": ""}


async def merge_persons(primary_id: str, secondary_ids: List[str]) -> dict:
    """把重复/疑似同名的若干人物节点合并进主节点（供同谱系跨卷人工归并）。

    规则：
    - primary 为保留节点；secondary 上 primary 为空（None/空串）的字段补入 primary
      （生卒/籍贯/简介/备注等，gender 在 primary 为 unknown 时亦可被 secondary 补全）。
    - secondary 的全部关系（亲属 PARENT_OF/SPOUSE_OF、来源 HAS_DOCUMENT、
      归属 BELONGS_TO/IN_BRANCH）重连到 primary；若 primary 已连到同一对象且同类型，
      则不重复建立（避免树视图出现重复父子/配偶）。
    - primary 与 secondary 之间原有的关系被移除（合并后即自环，无意义）。
    - 全部处理完后删除 secondary 节点。

    返回 {merged: 实际合并数, remaining: 处理前 secondary 数量}。
    """
    sids = list(dict.fromkeys(i for i in (secondary_ids or []) if i))
    sids = [i for i in sids if i and i != primary_id]
    if not sids:
        raise ValueError("未指定待合并人物，或待合并集合中已包含主节点")

    async with driver.session() as session:
        # 确认主节点存在
        rec = await session.run(
            "MATCH (p:Person {person_id: $pid}) RETURN p.name AS n", pid=primary_id
        )
        if not (await rec.single()):
            raise ValueError("主人物不存在")

        # 主从之间已有关系先移除（合并后即成自环）
        await session.run(
            "MATCH (a:Person {person_id: $pid}), (b:Person) "
            "WHERE b.person_id IN $sids MATCH (a)-[r]-(b) DELETE r",
            pid=primary_id, sids=sids,
        )

        # 逐 secondary：补属性 → 迁关系
        for sid in sids:
            # 1) 属性补全（primary 为空才补）
            await session.run(
                "MATCH (pri:Person {person_id: $pid}), (sec:Person {person_id: $sid}) "
                "SET pri.gender = CASE WHEN pri.gender IN ['', 'unknown'] AND sec.gender NOT IN ['', 'unknown'] "
                "     THEN sec.gender ELSE pri.gender END, "
                "pri.birth_year = coalesce(pri.birth_year, sec.birth_year), "
                "pri.death_year = coalesce(pri.death_year, sec.death_year), "
                "pri.birth_date = coalesce(pri.birth_date, sec.birth_date), "
                "pri.death_date = coalesce(pri.death_date, sec.death_date), "
                "pri.birth_place = coalesce(pri.birth_place, sec.birth_place), "
                "pri.death_place = coalesce(pri.death_place, sec.death_place), "
                "pri.generation = coalesce(pri.generation, sec.generation), "
                "pri.is_alive = coalesce(pri.is_alive, sec.is_alive), "
                "pri.photo_url = coalesce(pri.photo_url, sec.photo_url), "
                "pri.biography = coalesce(pri.biography, sec.biography), "
                "pri.notes = CASE WHEN (pri.notes IS NULL OR pri.notes = '') "
                "     THEN sec.notes ELSE pri.notes END, "
                "pri.name = CASE WHEN (pri.name IS NULL OR pri.name = '') "
                "     THEN sec.name ELSE pri.name END",
                pid=primary_id, sid=sid,
            )

            # 2a) PARENT_OF：sec 为父（出向）→ 迁为 primary 之父
            await session.run(
                "MATCH (pri:Person {person_id: $pid}), (sec:Person {person_id: $sid}) "
                "MATCH (sec)-[r:PARENT_OF]->(t:Person) WHERE t.person_id <> $pid "
                "OPTIONAL MATCH (pri)-[er:PARENT_OF]->(t) "
                "WITH pri, t, er WHERE er IS NULL "
                "CREATE (pri)-[:PARENT_OF]->(t)",
                pid=primary_id, sid=sid,
            )
            # 2b) PARENT_OF：sec 为子（被指向）→ primary 亦为其子
            await session.run(
                "MATCH (pri:Person {person_id: $pid}), (sec:Person {person_id: $sid}) "
                "MATCH (x:Person)-[r:PARENT_OF]->(sec) "
                "OPTIONAL MATCH (x)-[er:PARENT_OF]->(pri) "
                "WITH pri, x, er WHERE er IS NULL "
                "CREATE (x)-[:PARENT_OF]->(pri)",
                pid=primary_id, sid=sid,
            )
            # 2c) SPOUSE_OF：双向迁（方向无语义，按无向去重）
            await session.run(
                "MATCH (pri:Person {person_id: $pid}), (sec:Person {person_id: $sid}) "
                "MATCH (sec)-[r:SPOUSE_OF]-(t:Person) WHERE t.person_id <> $pid "
                "OPTIONAL MATCH (pri)-[er:SPOUSE_OF]-(t) "
                "WITH pri, t, er WHERE er IS NULL "
                "CREATE (pri)-[:SPOUSE_OF]->(t)",
                pid=primary_id, sid=sid,
            )
            # 2d) HAS_DOCUMENT：来源文档归属
            await session.run(
                "MATCH (pri:Person {person_id: $pid}), (sec:Person {person_id: $sid}) "
                "MATCH (sec)-[r:HAS_DOCUMENT]->(d:Document) "
                "OPTIONAL MATCH (pri)-[er:HAS_DOCUMENT]->(d) "
                "WITH pri, d, er WHERE er IS NULL "
                "CREATE (pri)-[:HAS_DOCUMENT]->(d)",
                pid=primary_id, sid=sid,
            )
            # 2e) BELONGS_TO / IN_BRANCH：谱系 / 房支归属（同谱系下与 primary 目标常相同 → 自动去重）
            await session.run(
                "MATCH (pri:Person {person_id: $pid}), (sec:Person {person_id: $sid}) "
                "MATCH (sec)-[r:BELONGS_TO]->(l:Lineage) "
                "OPTIONAL MATCH (pri)-[er:BELONGS_TO]->(l) "
                "WITH pri, l, er WHERE er IS NULL "
                "CREATE (pri)-[:BELONGS_TO]->(l)",
                pid=primary_id, sid=sid,
            )
            await session.run(
                "MATCH (pri:Person {person_id: $pid}), (sec:Person {person_id: $sid}) "
                "MATCH (sec)-[r:IN_BRANCH]->(b:Branch) "
                "OPTIONAL MATCH (pri)-[er:IN_BRANCH]->(b) "
                "WITH pri, b, er WHERE er IS NULL "
                "CREATE (pri)-[:IN_BRANCH]->(b)",
                pid=primary_id, sid=sid,
            )

        # 3) 物理删除所有 secondary 节点
        rec = await session.run(
            "MATCH (p:Person) WHERE p.person_id IN $sids DETACH DELETE p "
            "RETURN count(p) AS n",
            sids=sids,
        )
        merged = (await rec.single())["n"]

    person_index.invalidate_person_index()
    return {"merged": merged, "secondary_count": len(sids)}


# ============ 关系 ============
def _rel_type(semantic_type: str) -> str:
    return "PARENT_OF" if semantic_type == "parent_child" else "SPOUSE_OF"


async def create_relation(
    semantic_type: str,
    from_person_id: str,
    to_person_id: str,
    marriage_date: Optional[str] = None,
) -> Optional[dict]:
    rel_type = _rel_type(semantic_type)
    rel_id = uuid.uuid4().hex[:24]
    cypher = (
        f"MATCH (a:Person {{person_id: $from_person_id}}), "
        f"(b:Person {{person_id: $to_person_id}}) "
        f"MERGE (a)-[r:{rel_type}]->(b) "
        "ON CREATE SET r.rel_id = $rel_id "
        "SET r.rel_id = coalesce(r.rel_id, $rel_id), r.marriage_date = $marriage_date "
        "RETURN a, b, r"
    )
    async with driver.session() as session:
        rec = await session.run(
            cypher,
            from_person_id=from_person_id,
            to_person_id=to_person_id,
            rel_id=rel_id,
            marriage_date=marriage_date,
        )
        record = await rec.single()
    if not record:
        return None
    a, b, r = record["a"], record["b"], record["r"]
    return {
        "rel_id": r.get("rel_id") or rel_id,
        "type": rel_type,
        "from_person_id": a["person_id"],
        "from_name": a["name"],
        "to_person_id": b["person_id"],
        "to_name": b["name"],
        "marriage_date": r.get("marriage_date"),
    }


async def list_relations() -> List[dict]:
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (a:Person)-[r:PARENT_OF|SPOUSE_OF]->(b:Person) "
            "RETURN r.rel_id AS rel_id, type(r) AS type, "
            "a.person_id AS from_id, a.name AS from_name, "
            "b.person_id AS to_id, b.name AS to_name, "
            "r.marriage_date AS marriage_date "
            "ORDER BY type, from_name"
        )
        rows = [r async for r in rec]
    return [
        {
            "rel_id": row["rel_id"],
            "type": row["type"],
            "from_person_id": row["from_id"],
            "from_name": row["from_name"],
            "to_person_id": row["to_id"],
            "to_name": row["to_name"],
            "marriage_date": row["marriage_date"],
        }
        for row in rows
    ]


async def delete_relation(rel_id: str) -> bool:
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (:Person)-[r:PARENT_OF|SPOUSE_OF]->(:Person) "
            "WHERE r.rel_id = $rel_id DELETE r RETURN count(r) AS n",
            rel_id=rel_id,
        )
        record = await rec.single()
    return bool(record and record["n"])
