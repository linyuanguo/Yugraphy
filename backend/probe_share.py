"""查访客分享来源与状态(只读)。"""
from app.core.database import SessionLocal
from app.models.orm import AuditLog, User, VisitShare


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


with SessionLocal() as db:
    print("=== visit_shares ===")
    for s in db.query(VisitShare).order_by(VisitShare.id).all():
        print(
            f"id={s.id} name={s.name!r} note={s.note!r} revoked={s.revoked} "
            f"created={fmt(s.created_at)} expires={fmt(s.expires_at)} "
            f"search={s.allow_search} chat={s.allow_chat} token={s.token[:8]}..."
        )
    print("=== 审计: 访客分享相关动作 ===")
    for log in (
        db.query(AuditLog)
        .filter(AuditLog.action.like("%visit_share%"))
        .order_by(AuditLog.id)
        .all()
    ):
        user = db.query(User).filter(User.id == log.user_id).first()
        print(
            f"id={log.id} at={fmt(log.created_at)} by={user.username if user else log.user_id} "
            f"action={log.action} target={log.target_type}:{log.target_id} detail={log.detail}"
        )
print("done")
