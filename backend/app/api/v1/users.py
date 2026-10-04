from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import (
    WEAK_PASSWORD_RULE,
    get_current_user,
    hash_password,
    is_weak_password,
    require_role,
    validate_module_codes,
    verify_password,
)
from app.models.orm import User
from app.models.schemas import UserCreate, UserOut, UserUpdate
from app.services.audit_service import log_action

router = APIRouter(prefix="/users", tags=["用户管理"])


@router.get("", response_model=List[UserOut])
def list_users(
    search: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    query = db.query(User).order_by(User.id)
    if search:
        query = query.filter(User.username.ilike(f"%{search}%"))
    return query.all()


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    data: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    role = data.role if data.role in ("admin", "editor", "viewer") else "viewer"
    try:
        perms = validate_module_codes(data.permissions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 模块权限仅对操作员生效；admin/viewer 一律忽略该字段
    perms = perms if role == "editor" else None
    user = User(
        username=data.username,
        password=hash_password(data.password),
        full_name=data.full_name,
        email=data.email,
        role=role,
        permissions=perms,
        is_active=True,
        # 新建操作员首次登录必须改密；其它角色若设了弱密码（≤6 位或字符类型单一）也须先改密
        must_change_password=(role == "editor") or is_weak_password(data.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_action(current_user.id, "create_user", "user", str(user.id), f"创建用户 {user.username}")
    return user


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if data.full_name is not None:
        user.full_name = data.full_name
    if data.email is not None:
        user.email = data.email
    if data.role is not None:
        if data.role not in ("admin", "editor", "viewer"):
            raise HTTPException(status_code=400, detail="非法角色")
        user.role = data.role
    if data.permissions is not None:
        try:
            perms = validate_module_codes(data.permissions)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # 模块权限仅对操作员生效；切到 admin/viewer 时清空
        user.permissions = perms if user.role == "editor" else None
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password:
        user.password = hash_password(data.password)
        # 管理员重置密码后：操作员一律下次登录须改密（旧惯例）；其它角色仅当新密码为弱密码时标记
        user.must_change_password = (user.role == "editor") or is_weak_password(
            data.password
        )
    db.commit()
    db.refresh(user)
    log_action(current_user.id, "update_user", "user", str(user_id), f"更新用户 {user.username}")
    return user


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.username == "admin":
        raise HTTPException(status_code=400, detail="admin 为系统内置账号，不可删除")
    # 用户删除采用外键 ON DELETE SET NULL：该用户的操作日志/任务/上传/分享/谱书条目保留，
    # 仅 user_id/created_by/uploaded_by 置空（历史数据不因删号丢失）。
    db.delete(user)
    db.commit()
    log_action(current_user.id, "delete_user", "user", str(user_id), f"删除用户 {user.username}")


@router.post("/me/password")
def change_my_password(
    old_password: str,
    new_password: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not verify_password(old_password, current_user.password):
        raise HTTPException(status_code=400, detail="原密码错误")
    if is_weak_password(new_password):
        raise HTTPException(
            status_code=400,
            detail=f"新密码强度不足：{WEAK_PASSWORD_RULE}",
        )
    current_user.password = hash_password(new_password)
    # 完成强制改密后清除标记
    current_user.must_change_password = False
    db.commit()
    log_action(current_user.id, "change_password", "user", str(current_user.id))
    return {"ok": True}
