"""访客只读数据服务：人物详情（含亲缘）+ 同音字/模糊搜索。"""
from typing import List, Optional

from app.core.database import driver
from app.services import person_index, person_service
from app.utils.pinyin_utils import fuzzy_match


async def get_person_detail(person_id: str) -> Optional[dict]:
    """人物详情 + 父母/子女/配偶（只读视图）。"""
    person = await person_service.get_person(person_id)
    if not person:
        return None
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (p:Person {person_id:$id})<-[:PARENT_OF]-(par:Person) "
            "RETURN DISTINCT par ORDER BY par.birth_year",
            id=person_id,
        )
        parents = [person_service.node_to_dict(r["par"]) async for r in rec]

        rec = await session.run(
            "MATCH (p:Person {person_id:$id})-[:PARENT_OF]->(c:Person) "
            "RETURN DISTINCT c ORDER BY c.birth_year",
            id=person_id,
        )
        children = [person_service.node_to_dict(r["c"]) async for r in rec]

        rec = await session.run(
            "MATCH (p:Person {person_id:$id})-[:SPOUSE_OF]-(s:Person) RETURN DISTINCT s",
            id=person_id,
        )
        spouses = [person_service.node_to_dict(r["s"]) async for r in rec]

    return {**person, "parents": parents, "children": children, "spouses": spouses}


async def search_persons(query: str, limit: int = 20) -> List[dict]:
    """姓名搜索：直接子串 → 同音/首字 → 拼音缩写 → 错别字容错，按匹配度排序。"""
    persons = await person_index.get_persons_index() or []
    q = query.strip()
    if not q:
        return persons[:limit]
    hits: List[Tuple[int, dict]] = []
    for p in persons:
        ok, score = fuzzy_match(q, p["name"] or "")
        if ok:
            hits.append((score, p))
    hits.sort(key=lambda x: x[0])
    return [p for _, p in hits[:limit]]
