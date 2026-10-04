import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.orm import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(
    sub: str,
    role: str,
    expires_delta: Optional[timedelta] = None,
    token_version: int = 0,
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    # tv=登录版本号：与 users.token_version 比对，不一致即已被新一次登录挤下线
    payload = {
        "sub": sub,
        "role": role,
        "exp": expire,
        "tv": int(token_version or 0),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        username: Optional[str] = payload.get("sub")
        if username is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    user = db.query(User).filter(User.username == username).first()
    if user is None or not user.is_active:
        raise credentials_exception
    # 单点登录：token 中的登录版本号落后于库中版本 → 该会话已被后一次登录挤下线。
    # 旧 token（无 tv 字段，本次改造前签发）放行到自然过期，避免升级后全体在线用户被踢。
    tv = payload.get("tv")
    if tv is not None and int(tv) != int(user.token_version or 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号已在其他位置登录，本机已退出登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(*roles: str):
    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="权限不足"
            )
        return current_user

    return checker


# 操作员可独立授权的功能模块（模块名与前端一致）
# visit = 分享访问（勾上即等价于其下三项全开，旧账号沿用此码）；
# visit_3d / visit_share / visit_dash = 分享访问下的三个子项，可单独授权。
MODULE_CODES = (
    "dashboard",
    "tree",
    "lineages",
    "documents",
    "tasks",
    "visit",
    "visit_3d",
    "visit_share",
    "visit_dash",
)


def validate_module_codes(perms):
    """校验并归一化模块权限清单；None 表示全部，返回去重列表或 None。"""
    if perms is None:
        return None
    if not isinstance(perms, list):
        raise ValueError("模块权限格式错误")
    # 丢弃空值/非字符串项：前端多选控件偶发传 null 时不该整个请求 422
    perms = [p for p in perms if isinstance(p, str) and p.strip()]
    bad = [p for p in perms if p not in MODULE_CODES]
    if bad:
        raise ValueError(f"未知的模块权限: {bad}")
    return list(dict.fromkeys(perms))


def editor_has_module(user: User, codes) -> bool:
    """操作员是否被授予 codes 中的任一模块。permissions=None 表示全部授权。"""
    if user.permissions is None:
        return True
    return bool(set(codes).intersection(user.permissions or []))


def require_module_write(*codes: str):
    """模块「可操作」权限：admin 全有；操作员须被授予其一；其它角色拒绝。"""

    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role == "admin":
            return current_user
        if current_user.role == "editor" and editor_has_module(current_user, codes):
            return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="未授予该模块的操作权限",
        )

    return checker


def require_module_read(*codes: str):
    """模块「浏览」权限：admin/只读角色均可浏览；操作员须被授予其一。"""

    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role in ("admin", "viewer"):
            return current_user
        if current_user.role == "editor" and editor_has_module(current_user, codes):
            return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="未授予该模块的访问权限",
        )

    return checker


# ============ 密码强度（用户拍板：弱密码=长度≤6 或字符类型少于 2 种） ============
WEAK_PASSWORD_RULE = "需至少 7 位，且包含至少两种字符（大写/小写字母、数字、符号）"
WEAK_PASSWORD_MAX_LEN = 6  # 长度 ≤6 即弱


def password_char_kinds(pwd: str) -> int:
    """统计密码字符类型数（小写/大写/数字/符号四类）。"""
    kinds = 0
    if re.search(r"[a-z]", pwd):
        kinds += 1
    if re.search(r"[A-Z]", pwd):
        kinds += 1
    if re.search(r"\d", pwd):
        kinds += 1
    if re.search(r"[^A-Za-z0-9]", pwd):
        kinds += 1
    return kinds


def is_weak_password(pwd: str) -> bool:
    """弱密码判定：长度 ≤6，或字符类型少于 2 种。"""
    if len(pwd) <= WEAK_PASSWORD_MAX_LEN:
        return True
    return password_char_kinds(pwd) < 2
