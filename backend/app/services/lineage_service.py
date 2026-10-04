"""谱系（宗谱/族谱）与房支管理：Neo4j 读写。

数据模型：
- (:Lineage {lineage_id, name, note, created_at})          谱系/宗谱，如「温岭林家」
- (:Branch {branch_id, lineage_id, name, note, created_at}) 房支，如「第三房」，属于某个谱系
- (:Person)-[:BELONGS_TO]->(:Lineage)   人物归谱（可选）
- (:Person)-[:IN_BRANCH]->(:Branch)     人物归房（可选）
- (:Branch)-[:OF_LINEAGE]->(:Lineage)   房支归属谱系
"""
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

from app.core.database import driver

logger = logging.getLogger("genealogy.lineage")


def _dt_str(value) -> Optional[str]:
    return str(value) if value is not None else None


# 档案编号首段：如 J148（字母+数字）；用于排除中文普通命名的误判
_ARCHIVE_PREFIX_RE = re.compile(r"^[A-Za-z]+\d+$")


def infer_lineage_key_from_filename(filename: Optional[str]) -> Optional[str]:
    """从扫描件文件名推断谱系档案编号（取前 3 段）。

    例：J148-001-001-001.pdf / J148-001-001-002.pdf → J148-001-001（同一谱系）
    非档案式命名（如「林氏宗谱-卷一」不足 3 段或首段非档案号）返回 None，不自动归属。
    """
    if not filename:
        return None
    base = os.path.splitext(os.path.basename(filename))[0].strip()
    parts = [p.strip() for p in base.split("-") if p.strip()]
    if len(parts) < 3 or not _ARCHIVE_PREFIX_RE.match(parts[0]):
        return None
    return "-".join(parts[:3]).upper()


def _normalize_code(code: Optional[str]) -> Optional[str]:
    return code.strip().upper() if code and code.strip() else None


def lineage_to_dict(node) -> dict:
    d = dict(node)
    return {
        "lineage_id": d.get("lineage_id"),
        "name": d.get("name"),
        "code": d.get("code"),
        "note": d.get("note"),
        "created_at": _dt_str(d.get("created_at")),
    }


def branch_to_dict(node) -> dict:
    d = dict(node)
    return {
        "branch_id": d.get("branch_id"),
        "lineage_id": d.get("lineage_id"),
        "name": d.get("name"),
        "note": d.get("note"),
        "created_at": _dt_str(d.get("created_at")),
    }


async def find_lineage_by_code(code: str) -> Optional[dict]:
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (l:Lineage {code: $code}) RETURN l", code=_normalize_code(code)
        )
        record = await rec.single()
    if not record:
        return None
    return lineage_to_dict(record["l"])


async def create_lineage(name: str, note: Optional[str] = None, code: Optional[str] = None) -> dict:
    code = _normalize_code(code)
    if code:
        existing = await find_lineage_by_code(code)
        if existing:
            raise ValueError(f"档案编号 {code} 已被谱系「{existing['name']}」使用")
    lineage_id = uuid.uuid4().hex[:24]
    async with driver.session() as session:
        rec = await session.run(
            "CREATE (l:Lineage {lineage_id: $lineage_id, name: $name, code: $code, note: $note, "
            "created_at: datetime()}) RETURN l",
            lineage_id=lineage_id, name=name, code=code, note=note,
        )
        record = await rec.single()
    return lineage_to_dict(record["l"])


async def ensure_lineage_by_code(code: str, name: str, note: Optional[str] = None) -> dict:
    """按档案编号查找谱系，不存在则创建；已存在（人工可能已改名）不覆盖其名称。

    依赖 Neo4j 对 l.code 的唯一约束保证并发上传同编号文件不会重复创建。
    """
    code = _normalize_code(code)
    if not code:
        raise ValueError("档案编号为空")
    async with driver.session() as session:
        rec = await session.run(
            "MERGE (l:Lineage {code: $code}) "
            "ON CREATE SET l.lineage_id = $lineage_id, l.name = $name, "
            "l.note = $note, l.created_at = datetime() "
            "RETURN l",
            code=code, lineage_id=uuid.uuid4().hex[:24], name=name, note=note,
        )
        record = await rec.single()
    return lineage_to_dict(record["l"])


async def get_lineage(lineage_id: str) -> Optional[dict]:
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (l:Lineage {lineage_id: $lineage_id}) RETURN l", lineage_id=lineage_id
        )
        record = await rec.single()
    if not record:
        return None
    return lineage_to_dict(record["l"])


async def update_lineage(
    lineage_id: str,
    name: Optional[str] = None,
    note: Optional[str] = None,
    code: Optional[str] = None,
) -> Optional[dict]:
    sets = []
    params: dict = {"lineage_id": lineage_id}
    if name is not None:
        sets.append("l.name = $name")
        params["name"] = name
    if note is not None:
        sets.append("l.note = $note")
        params["note"] = note
    if code is not None:
        new_code = _normalize_code(code)
        # 其他谱系占用同一编号则拒绝
        existing = await find_lineage_by_code(new_code) if new_code else None
        if existing and existing["lineage_id"] != lineage_id:
            raise ValueError(f"档案编号 {new_code} 已被谱系「{existing['name']}」使用")
        sets.append("l.code = $code")
        params["code"] = new_code
    if not sets:
        return await get_lineage(lineage_id)
    async with driver.session() as session:
        rec = await session.run(
            f"MATCH (l:Lineage {{lineage_id: $lineage_id}}) SET {', '.join(sets)} RETURN l",
            **params,
        )
        record = await rec.single()
    if not record:
        return None
    return lineage_to_dict(record["l"])


async def delete_lineage(lineage_id: str, force: bool = False) -> tuple[bool, int]:
    """删除谱系及全部房支。

    force=False（默认）：仅删除谱系/房支节点，归属人物保留（解除归属不删除），
    调用前应确保无归属人物。
    force=True：先级联删除归属该谱系/房支的全部人物（含其全部关联关系边），
    再删除谱系与房支，返回 (是否删除, 连带删除的人物数)。
    """
    if not force:
        async with driver.session() as session:
            rec = await session.run(
                "MATCH (l:Lineage {lineage_id: $lineage_id}) "
                "OPTIONAL MATCH (l)<-[:OF_LINEAGE]-(b:Branch) "
                "DETACH DELETE b, l RETURN count(l) AS n",
                lineage_id=lineage_id,
            )
            record = await rec.single()
        return bool(record and record["n"]), 0
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (l:Lineage {lineage_id: $lineage_id}) "
            "OPTIONAL MATCH (l)<-[:OF_LINEAGE]-(b:Branch) "
            "OPTIONAL MATCH (p:Person) "
            "WHERE (p)-[:BELONGS_TO]->(l) OR (p)-[:IN_BRANCH]->(:Branch)-[:OF_LINEAGE]->(l) "
            "WITH l, collect(DISTINCT b) AS bs, collect(DISTINCT p) AS ps "
            "FOREACH (x IN ps | DETACH DELETE x) "
            "FOREACH (x IN bs | DETACH DELETE x) "
            "DETACH DELETE l RETURN size(ps) AS n",
            lineage_id=lineage_id,
        )
        record = await rec.single()
    if not record:
        return False, 0
    return True, record["n"]


async def list_lineages() -> List[dict]:
    """谱系列表（含房支与人数）。"""
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (l:Lineage) "
            "OPTIONAL MATCH (p:Person) "
            "WHERE (p)-[:BELONGS_TO]->(l) OR (p)-[:IN_BRANCH]->(:Branch)-[:OF_LINEAGE]->(l) "
            "WITH l, count(DISTINCT p) AS person_count "
            "OPTIONAL MATCH (b:Branch)-[:OF_LINEAGE]->(l) "
            "WITH l, person_count, collect(DISTINCT b {.branch_id, .lineage_id, .name, .note, .created_at}) AS branches "
            "RETURN l, person_count, branches ORDER BY l.created_at"
        )
        rows = [r async for r in rec]
    items = []
    for row in rows:
        item = lineage_to_dict(row["l"])
        item["person_count"] = row["person_count"]
        item["branches"] = [branch_to_dict(b) for b in row["branches"]]
        items.append(item)
    return items


async def create_branch(lineage_id: str, name: str, note: Optional[str] = None) -> Optional[dict]:
    """在某谱系下新建房支；谱系不存在返回 None。"""
    branch_id = uuid.uuid4().hex[:24]
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (l:Lineage {lineage_id: $lineage_id}) "
            "CREATE (b:Branch {branch_id: $branch_id, lineage_id: $lineage_id, "
            "name: $name, note: $note, created_at: datetime()}) "
            "CREATE (b)-[:OF_LINEAGE]->(l) RETURN b",
            lineage_id=lineage_id, branch_id=branch_id, name=name, note=note,
        )
        record = await rec.single()
    if not record:
        return None
    return branch_to_dict(record["b"])


async def get_branch(branch_id: str) -> Optional[dict]:
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (b:Branch {branch_id: $branch_id}) RETURN b", branch_id=branch_id
        )
        record = await rec.single()
    if not record:
        return None
    return branch_to_dict(record["b"])


async def update_branch(branch_id: str, name: Optional[str] = None, note: Optional[str] = None) -> Optional[dict]:
    sets = []
    params: dict = {"branch_id": branch_id}
    if name is not None:
        sets.append("b.name = $name")
        params["name"] = name
    if note is not None:
        sets.append("b.note = $note")
        params["note"] = note
    if not sets:
        return await get_branch(branch_id)
    async with driver.session() as session:
        rec = await session.run(
            f"MATCH (b:Branch {{branch_id: $branch_id}}) SET {', '.join(sets)} RETURN b",
            **params,
        )
        record = await rec.single()
    if not record:
        return None
    return branch_to_dict(record["b"])


async def delete_branch(branch_id: str) -> bool:
    """删除房支（人物仅解除房支归属，不删除）。"""
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (b:Branch {branch_id: $branch_id}) DETACH DELETE b RETURN count(b) AS n",
            branch_id=branch_id,
        )
        record = await rec.single()
    return bool(record and record["n"])


async def lineage_branch_names(
    lineage_id: Optional[str] = None, branch_id: Optional[str] = None
) -> tuple:
    """校验 lineage/branch 存在且匹配，返回 (lineage_id, lineage_name, branch_id, branch_name)。"""
    lineage_name = None
    branch_name = None
    if lineage_id:
        lineage = await get_lineage(lineage_id)
        if not lineage:
            raise ValueError(f"谱系不存在: {lineage_id}")
        lineage_name = lineage["name"]
    if branch_id:
        branch = await get_branch(branch_id)
        if not branch:
            raise ValueError(f"房支不存在: {branch_id}")
        if lineage_id and branch["lineage_id"] != lineage_id:
            raise ValueError("房支与谱系不匹配")
        branch_name = branch["name"]
        if not lineage_id:
            lineage_id = branch["lineage_id"]
            lineage = await get_lineage(lineage_id)
            lineage_name = lineage["name"] if lineage else None
    return lineage_id, lineage_name, branch_id, branch_name
