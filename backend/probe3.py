"""确认：audit 列 / Neo4j 谱系 / MinIO scans / uploads 残留。"""
import os

import psycopg2
from app.core.config import settings
from app.core.database import driver

print("=== audit_logs 最近 8 ===")
conn = psycopg2.connect(
    dbname=settings.PG_DB, user=settings.PG_USER, password=settings.PG_PASSWORD,
    host=settings.PG_HOST, port=settings.PG_PORT,
)
cur = conn.cursor()
cur.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 8")
cols = [d[0] for d in cur.description]
for r in cur.fetchall():
    print(dict(zip(cols, r)))
conn.close()

print("\n=== Neo4j Lineage 明细 ===")
with driver.session() as s:
    for rec in s.run(
        "MATCH (l:Lineage) OPTIONAL MATCH (p:Person)-[:BELONGS_TO]->(l) "
        "RETURN l.name AS name, l.code AS code, l.note AS note, count(p) AS members, id(l) AS nid"
    ):
        print(dict(rec))

print("\n=== MinIO scans 前缀统计 ===")
from minio import Minio  # noqa: E402

client = Minio(
    settings.MINIO_ENDPOINT,
    access_key=settings.MINIO_USER,
    secret_key=settings.MINIO_PASSWORD,
    secure=False,
)
if client.bucket_exists(settings.MINIO_BUCKET):
    from collections import Counter

    c = Counter()
    for o in client.list_objects(settings.MINIO_BUCKET, prefix="scans/", recursive=True):
        parts = o.object_name.split("/")
        if len(parts) >= 2:
            c[parts[1]] += 1
    for k, v in sorted(c.items()):
        print(k, v, "objs")
else:
    print("no bucket")

print("\n=== uploads/tasks 残留 ===")
base = "/app/uploads/tasks"
if os.path.isdir(base):
    for tid in sorted(os.listdir(base)):
        p = os.path.join(base, tid)
        total = 0
        for root, dirs, files in os.walk(p):
            total += len(files)
        size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(p) for f in fs)
        print(tid, total, "files", size, "bytes")
else:
    print("no uploads dir")
driver.close()
print("probe3 done")
