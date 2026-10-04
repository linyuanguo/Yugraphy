"""查任务状态 + 最近操作日志（诊断卡 100%）。"""
from app.core.database import SessionLocal
from app.models.orm import AuditLog, ImportTask, User

with SessionLocal() as db:
    print("== import_tasks ==")
    for r in db.query(ImportTask).order_by(ImportTask.created_at.desc()).limit(6).all():
        print(
            f"{r.task_id[:13]} {r.status:<8} stage={r.stage or '-':<12} "
            f"pages={r.total_pages}/{r.done_pages} "
            f"err={(r.error_msg or '')[:100]} file={(r.file_path or '')[:45]} up={r.updated_at}"
        )
    print("== audit recent ==")
    uids = {u.id: u.username for u in db.query(User).all()}
    for r in db.query(AuditLog).order_by(AuditLog.id.desc()).limit(15).all():
        print(f"{r.created_at}  {uids.get(r.user_id, r.user_id)}  {r.action:<20} {(r.detail or '')[:70]}")
print("done")
