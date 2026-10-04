"""探测：上传识别后数据去向（任务/谱系/人物/文档）。"""
import json
from datetime import datetime

import psycopg2
from app.core.config import settings
from app.core.database import driver

print("=== PG import_tasks ===")
conn = psycopg2.connect(
    dbname=settings.PG_DB, user=settings.PG_USER, password=settings.PG_PASSWORD,
    host=settings.PG_HOST, port=settings.PG_PORT,
)
cur = conn.cursor()
cur.execute(
    """SELECT id, task_id, file_path, file_size, status, stage, total_pages, done_pages,
              created_at, updated_at, file_sha256,
              (result IS NOT NULL) AS has_result
       FROM import_tasks ORDER BY created_at DESC LIMIT 20"""
)
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
for r in rows:
    print(dict(zip(cols, r)))

print("\n=== PG lineages / branches ===")
for tbl in ("lineages", "branches", "content_entries", "file_metadata"):
    cur.execute(f"SELECT count(*) FROM {tbl}")
    print(tbl, cur.fetchone()[0])
cur.execute("SELECT id, name, code, note, created_at FROM lineages ORDER BY created_at DESC LIMIT 10")
for r in cur.fetchall():
    print("lineage:", r)

print("\n=== Neo4j counts ===")
with driver.session() as s:
    for label in ("Person", "Document", "Lineage", "Branch", "ContentEntry"):
        v = s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
        print(label, v)
    print("BELONGS_TO rels:", s.run("MATCH ()-[r:BELONGS_TO]->() RETURN count(r) AS c").single()["c"])
    print("HAS_DOCUMENT rels:", s.run("MATCH ()-[r:HAS_DOCUMENT]->() RETURN count(r) AS c").single()["c"])
    print("--- Lineages:")
    for rec in s.run("MATCH (l:Lineage) RETURN l.name AS name, l.code AS code, id(l) AS nid"):
        print(" ", rec["name"], rec["code"], rec["nid"])
    print("--- sample Persons (recent-ish):")
    for rec in s.run(
        "MATCH (p:Person) OPTIONAL MATCH (p)-[b:BELONGS_TO]->(l:Lineage) "
        "RETURN p.name AS name, p.person_id AS pid, l.code AS lcode ORDER BY p.name LIMIT 20"
    ):
        print(" ", dict(rec))

print("\n=== tasks.py 应用状态提示 ===")
cur.execute(
    """SELECT id, status, done_pages, total_pages FROM import_tasks
       WHERE status IN ('done','failed') ORDER BY updated_at DESC LIMIT 10"""
)
for r in cur.fetchall():
    print(r)
conn.close()
driver.close()
print("probe done")
