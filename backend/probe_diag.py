"""综合诊断：谱系/用户/任务识别统计/页图方向(只读)。"""
import os
import tempfile

from PIL import Image

from app.core.config import settings
from app.core.database import SessionLocal, driver
from app.models.orm import AuditLog, ImportTask, User
from app.services import file_service

print("=== users ===")
with SessionLocal() as db:
    for u in db.query(User).order_by(User.id).all():
        print(f"  id={u.id} username={u.username} role={u.role}")

print("=== import_tasks (recent 5) ===")
with SessionLocal() as db:
    for t in db.query(ImportTask).order_by(ImportTask.created_at.desc()).limit(5).all():
        print(
            f"  {t.task_id} {os.path.basename(t.file_path or '')} status={t.status} "
            f"lineage={t.lineage_id} pages={t.total_pages}/{t.done_pages} sha={str(t.file_sha256 or '')[:8]}"
        )

print("=== result 统计 (06b125bfc36b) ===")
with SessionLocal() as db:
    t = db.query(ImportTask).filter(ImportTask.task_id == "06b125bfc36b").first()
if t and t.result:
    pages = t.result.get("pages", [])
    n_p = n_r = n_e = 0
    birth = death = place = bio = gender = 0
    person_pages = 0
    for p in pages:
        ps = p.get("persons", [])
        n_p += len(ps)
        n_r += len(p.get("relations", []))
        n_e += len(p.get("entries", []))
        if ps:
            person_pages += 1
        for x in ps:
            if x.get("birth_year"):
                birth += 1
            if x.get("death_year"):
                death += 1
            if x.get("birth_place"):
                place += 1
            if x.get("biography"):
                bio += 1
            if x.get("gender") not in (None, "unknown"):
                gender += 1
    print(
        f"  pages={len(pages)} person_pages={person_pages} persons={n_p} relations={n_r} entries={n_e}\n"
        f"  persons 有生年={birth} 有卒年={death} 有出生地={place} 有简介={bio} 性别已知={gender}"
    )

print("=== audit_logs recent 15 ===")
with SessionLocal() as db:
    for a in (
        db.query(AuditLog)
        .order_by(AuditLog.id.desc())
        .limit(15)
        .all()
    ):
        u = db.query(User).filter(User.id == a.user_id).first()
        print(
            f"  id={a.id} at={a.created_at} by={u.username if u else a.user_id}(role={u.role if u else '?'}) "
            f"action={a.action} detail={a.detail}"
        )

print("=== neo4j lineage J144-003-001 ===")


async def _lq():
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (l:Lineage) WHERE l.code = 'J144-003-001' OR l.name CONTAINS 'J144-003-001' "
            "OPTIONAL MATCH (p:Person) WHERE (p)-[:BELONGS_TO]->(l) OR (p)-[:IN_BRANCH]->(:Branch)-[:OF_LINEAGE]->(l) "
            "WITH l, count(DISTINCT p) AS pc OPTIONAL MATCH (b:Branch)-[:OF_LINEAGE]->(l) "
            "RETURN l.lineage_id AS lid, l.name AS name, l.code AS code, pc, count(b) AS bc"
        )
        rows = [dict(r) async for r in rec]
        for r in rows:
            print(f"  lineage: id={r['lid']} name={r['name']} code={r['code']} persons={r['pc']} branches={r['bc']}")
            # 人物属性样本
            s2 = await session.run(
                "MATCH (l:Lineage {lineage_id: $lid})<-[:BELONGS_TO]-(p:Person) "
                "WHERE p.birth_year IS NOT NULL OR p.biography IS NOT NULL OR p.birth_place IS NOT NULL "
                "RETURN p.name AS n, p.birth_year AS b, p.death_year AS d, p.birth_place AS bp, p.biography AS bio LIMIT 5",
                lid=r["lid"],
            )
            for x in [dict(r2) async for r2 in s2]:
                print(f"    sample attr: {x}")


import asyncio

asyncio.run(_lq())

print("=== 页图方向抽查 (scans/06b125bfc36b) ===")
client = file_service._client()
objs = list(
    client.list_objects(settings.MINIO_BUCKET, prefix="scans/06b125bfc36b/", recursive=True)
)
print(f"  对象数: {len(objs)}")
for obj in objs[:6]:
    name = obj.object_name
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        continue
    tmp = os.path.join(tempfile.gettempdir(), os.path.basename(name))
    client.fget_object(settings.MINIO_BUCKET, name, tmp)
    try:
        with Image.open(tmp) as im:
            w, h = im.size
            print(f"  {name}: {w}x{h} {'横向' if w > h else '纵向'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {name}: 读取失败 {exc}")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
print("done")
