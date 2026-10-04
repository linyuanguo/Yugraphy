"""只读验证:force 删除 CQL 的归属人物计数与列表 person_count 一致(不删数据)。"""
import asyncio

from app.core.database import driver


async def main():
    async with driver.session() as session:
        # 找一个人物最多的谱系
        rec = await session.run(
            "MATCH (l:Lineage) OPTIONAL MATCH (p:Person) "
            "WHERE (p)-[:BELONGS_TO]->(l) OR (p)-[:IN_BRANCH]->(:Branch)-[:OF_LINEAGE]->(l) "
            "WITH l, count(DISTINCT p) AS c ORDER BY c DESC LIMIT 3 "
            "RETURN l.lineage_id AS id, l.name AS name, c"
        )
        rows = [r async for r in rec]
        for r in rows:
            # 复刻 force 删除的收集查询(不加 FOREACH),只统计
            rec2 = await session.run(
                "MATCH (l:Lineage {lineage_id: $lid}) "
                "OPTIONAL MATCH (l)<-[:OF_LINEAGE]-(b:Branch) "
                "OPTIONAL MATCH (p:Person) "
                "WHERE (p)-[:BELONGS_TO]->(l) OR (p)-[:IN_BRANCH]->(:Branch)-[:OF_LINEAGE]->(l) "
                "WITH collect(DISTINCT b) AS bs, collect(DISTINCT p) AS ps "
                "RETURN size(bs) AS nb, size(ps) AS np",
                lid=r["id"],
            )
            rec3 = await rec2.single()
            print(f"{r['name']} list_count={r['c']} force_collect_persons={rec3['np']} branches={rec3['nb']}")
    await driver.close()


asyncio.run(main())
print("done")
