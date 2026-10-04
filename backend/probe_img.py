"""诊断任务 06b125bfc36b 页面图:DB image_url + MinIO 对象 + /files 访问。"""
import os

from app.core.database import SessionLocal
from app.models.orm import ImportTask

from app.services import file_service

TASK = "06b125bfc36b"
with SessionLocal() as db:
    t = db.query(ImportTask).filter(ImportTask.task_id == TASK).first()
    if not t:
        print("task not found")
        raise SystemExit
    res = t.result or {}
    pages = res.get("pages", [])
    print(f"pages={len(pages)} status={t.status}")
    urls = [p.get("image_url") for p in pages[:3]]
    for u in urls:
        print("url:", u)
    if pages:
        obj = pages[0]["image_url"].removeprefix("/files/")
        print("obj0:", obj)
        try:
            st = file_service._client().stat_object(file_service.settings.MINIO_BUCKET, obj)
            print("minio exists:", st.object_name, st.size)
        except Exception as e:  # noqa
            print("minio stat err:", repr(e)[:200])
    # 该任务前缀对象数
    try:
        objs = list(
            file_service._client().list_objects(
                file_service.settings.MINIO_BUCKET,
                prefix=f"scans/{TASK}/",
                recursive=True,
            )
        )
        print("minio object count:", len(objs))
    except Exception as e:  # noqa
        print("list err:", repr(e)[:200])
print("done")
