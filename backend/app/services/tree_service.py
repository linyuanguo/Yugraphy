"""家谱树数据服务：导出 G6 可直接渲染的图数据。"""
import uuid
from typing import List, Optional

from app.core.database import driver
from app.services.person_service import node_to_dict


def _attach_lineage(items: List[dict], rows) -> None:
    """把 rows 里的谱系/房支信息合并进 items（同序）。"""
    for it, r in zip(items, rows):
        it["lineage_id"] = r.get("lid")
        it["lineage_name"] = r.get("lname")
        it["branch_id"] = r.get("bid")
        it["branch_name"] = r.get("bname")


async def get_tree(lineage_id: Optional[str] = None) -> dict:
    async with driver.session() as session:
        if lineage_id:
            rec = await session.run(
                "MATCH (p:Person) "
                "WHERE (p)-[:BELONGS_TO]->(:Lineage {lineage_id: $lineage_id}) "
                "OR (p)-[:IN_BRANCH]->(:Branch {lineage_id: $lineage_id}) "
                "OPTIONAL MATCH (p)-[:BELONGS_TO]->(l:Lineage) "
                "OPTIONAL MATCH (p)-[:IN_BRANCH]->(b:Branch) "
                "RETURN p, l.lineage_id AS lid, l.name AS lname, "
                "b.branch_id AS bid, b.name AS bname",
                lineage_id=lineage_id,
            )
            rows = [r async for r in rec]
            person_ids = {r["p"]["person_id"] for r in rows}
            nodes = [node_to_dict(r["p"]) for r in rows]
            _attach_lineage(nodes, rows)

            rec = await session.run(
                "MATCH (a:Person)-[r:PARENT_OF|SPOUSE_OF]->(b:Person) "
                "WHERE a.person_id IN $ids AND b.person_id IN $ids "
                "RETURN r.rel_id AS rel_id, type(r) AS type, "
                "a.person_id AS source, b.person_id AS target, "
                "r.marriage_date AS marriage_date",
                ids=list(person_ids),
            )
            edge_rows = [r async for r in rec]
        else:
            rec = await session.run("MATCH (p:Person) RETURN p")
            nodes = [node_to_dict(r["p"]) async for r in rec]

            rec = await session.run(
                "MATCH (a:Person)-[r:PARENT_OF|SPOUSE_OF]->(b:Person) "
                "RETURN r.rel_id AS rel_id, type(r) AS type, "
                "a.person_id AS source, b.person_id AS target, "
                "r.marriage_date AS marriage_date"
            )
            edge_rows = [r async for r in rec]

    edges = [
        {
            "id": (row["rel_id"] or uuid.uuid4().hex[:24]),
            "source": row["source"],
            "target": row["target"],
            "type": row["type"],
            "marriage_date": row["marriage_date"],
        }
        for row in edge_rows
    ]

    return {"nodes": nodes, "edges": edges}


async def get_person_subtree(person_id: str, depth: int = 6) -> dict:
    """以某人为中心，向上/向下各取 depth 层。"""
    # 注意：Neo4j 不允许把变长关系深度作为参数（*0..$depth），必须内插字面量
    depth_lit = max(1, min(int(depth), 12))
    cypher = (
        "MATCH (root:Person {person_id: $person_id}) "
        f"OPTIONAL MATCH path = (root)-[:PARENT_OF|SPOUSE_OF*0..{depth_lit}]-(p:Person) "
        "WITH collect(distinct p) AS persons "
        "UNWIND persons AS pp "
        "OPTIONAL MATCH (pp)-[:BELONGS_TO]->(l:Lineage) "
        "OPTIONAL MATCH (pp)-[:IN_BRANCH]->(b:Branch) "
        "OPTIONAL MATCH (a:Person)-[r:PARENT_OF|SPOUSE_OF]->(bb:Person) "
        "WHERE a IN persons AND bb IN persons "
        "RETURN pp, l.name AS lname, b.name AS bname, b.branch_id AS bid, "
        "collect(distinct {rel_id: r.rel_id, type: type(r), "
        "source: a.person_id, target: bb.person_id, marriage_date: r.marriage_date}) AS edges"
    )
    async with driver.session() as session:
        rec = await session.run(cypher, person_id=person_id)
        rows = [r async for r in rec]

    seen_ids = set()
    nodes: List[dict] = []
    for row in rows:
        nd = node_to_dict(row["pp"])
        nd["lineage_name"] = row["lname"]
        nd["branch_name"] = row["bname"]
        nd["branch_id"] = row["bid"]
        if nd["person_id"] not in seen_ids:
            seen_ids.add(nd["person_id"])
            nodes.append(nd)
        for e in row["edges"]:
            if not e.get("rel_id"):
                continue
            e["id"] = e["rel_id"]
            e.pop("rel_id", None)

    edges = [
        e
        for row in rows
        for e in row["edges"]
        if e.get("id")
    ]
    # 去重
    edge_ids = set()
    uniq_edges = []
    for e in edges:
        if e["id"] not in edge_ids:
            edge_ids.add(e["id"])
            uniq_edges.append(e)

    return {"nodes": nodes, "edges": uniq_edges}
