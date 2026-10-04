"""操作日志写入与保留策略清理。"""
from datetime import datetime, timedelta
from typing import Optional

from app.core.client_ip import current_client_ip
from app.core.database import SessionLocal
from app.models.orm import AppSetting, AuditLog, ImportTask

# AppSetting key：操作日志保留天数。空/0/非数字 = 永久保留；正数 = 保留 N 天
RETENTION_KEY = "audit_retention_days"


def log_action(
    user_id: Optional[int],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    detail: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> None:
    try:
        if not ip_address:
            ip_address = current_client_ip.get() or None
        with SessionLocal() as db:
            db.add(
                AuditLog(
                    user_id=user_id,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    detail=detail,
                    ip_address=ip_address,
                )
            )
            db.commit()
    except Exception:  # noqa: BLE001
        pass


def log_task_action(task_id: str, action: str, detail: Optional[str] = None) -> None:
    """按 task_id 查创建者并记录操作日志（IP 自动从当前请求上下文取）。

    用于转图/识别/整理等后台异步阶段：这些阶段没有 HTTP 请求对象，
    但会继承发起请求的 contextvar（操作者 IP），user_id 取任务创建者。
    """
    try:
        with SessionLocal() as db:
            t = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
            uid = t.created_by if t else None
        log_action(uid, action, "task", task_id, detail)
    except Exception:  # noqa: BLE001
        pass


def get_retention_days() -> int:
    """读取保留策略：返回保留天数；0 = 永久保留。"""
    try:
        with SessionLocal() as db:
            row = db.query(AppSetting).filter(AppSetting.key == RETENTION_KEY).first()
            if row and row.value:
                try:
                    return max(0, int(row.value))
                except (TypeError, ValueError):
                    return 0
    except Exception:  # noqa: BLE001
        pass
    return 0


def prune_audit_logs() -> int:
    """按保留策略删除过期操作日志（保留设为永久时不做任何事）。返回删除条数。"""
    days = get_retention_days()
    if days <= 0:
        return 0
    cutoff = datetime.now() - timedelta(days=days)
    try:
        with SessionLocal() as db:
            n = db.query(AuditLog).filter(AuditLog.created_at < cutoff).delete()
            if n:
                db.commit()
            return n
    except Exception:  # noqa: BLE001
        return 0
