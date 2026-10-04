"""深挖：任务失败原因 / 谱系 / 人物 / 文档 / 文件现状。"""
import os

import psycopg2
from app.core.config import settings
from app.core.database import driver
from app.services import file_service

print("=== PG import_tasks error ===")
conn = psycopg2.connect(
    dbname=settings.PG_DB, user=settings.PG_USER, password=settings.PG_PASSWORD,
    host=settings.PG_HOST, port=settings.PG_PORT,
)
cur = conn.cursor()
cur.execute("SELECT task_id, status, error_msg FROM import_tasks ORDER BY created_at DESC LIMIT 5")
for r in cur.fetchall():
    print(r[0], r[1], "|", (r[2] or "")[:800])

print("\n=== audit_logs 最近 15 ===")
cur.execute(
    "SELECT id, action, resource, resource_id, detail, created_at "
    "FROM audit_logs ORDER BY id DESC LIMIT 15"
)
for r in cur.fetchall():
    print(r)
conn.close()

print("\n=== Neo4j counts ===")
with driver.session() as s:
    for label in ("Person", "Document", "Lineage", "Branch", "ContentEntry"):
        print(label, s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"])
    print("--- Lineages:")
    for rec in s.run("MATCH (l:Lineage) RETURN l.name AS n, l.code AS c, l.note AS note"):
        print(" ", rec["n"], "|", rec["c"], "|", (rec["note"] or "")[:60])
    print("--- Persons (all):")
    for rec in s.run(
        "MATCH (p:Person) OPTIONAL MATCH (p)-[:BELONGS_TO]->(l:Lineage) "
        "RETURN p.name AS n, l.code AS c ORDER BY p.name"
    ):
        print(" ", rec["n"], "|", rec["c"])
    print("--- Documents:")
    for rec in s.run("MATCH (d:Document) RETURN d.doc_id AS i, d.file_path AS f LIMIT 10"):
        print(" ", dict(rec))

print("\n=== uploads dir ===")
base = "/app/uploads/tasks"
if os.path.isdir(base):
    for tid in sorted(os.listdir(base)):
        p = os.path.join(base, tid)
        n = sum(len(fs) for _, _, fs in os.walk(p))
        print(tid, n, "files")
else:
    print("no uploads dir")

print("\n=== MinIO scans objects ===")
try:
    names = file_service.list_objects("scans", "", limit=10) if hasattr(file_service, "list_objects") else None
    print("scan prefixes:", names)
except Exception as e:  # noqa
    print("err", e)
driver.close()
print("probe2 done")
