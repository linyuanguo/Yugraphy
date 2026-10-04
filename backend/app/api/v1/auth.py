import time
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    get_current_user,
    is_weak_password,
    verify_password,
)
from app.models.orm import User
from app.models.schemas import CodeRequest, TokenResponse, UserOut
from app.services.audit_service import log_action
from app.services.hotp_service import issue, verify

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/hotp-code")
def issue_login_code(data: CodeRequest):
    """登录前取动态码（简化 HOTP）：服务端签发一个与用户名无关的 6 位码，短时有效、一次性。

    无需先输入用户名：前端**打开登录页即调用并展示**，人工抄入后随登录表单提交；
    替代原拼图滑块，防止无码直连登录接口做脚本探测/爆破（每次登录尝试都会消费一个码）。
    """
    code, ttl = issue(data.username or "")
    # server_time：服务端签发时刻（毫秒）。前端据此折算剩余有效期，
    # 避免「倒计时从收到响应才起算」导致的偏差——后端繁忙/网络慢时，
    # 前端还显示剩几秒，服务端其实已过期，用户填对码也登录失败。
    return {
        "ok": True,
        "code": code,
        "expires_in": ttl,
        "server_time": int(time.time() * 1000),
    }


@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    remember: bool = Form(default=False),
    hotp_code: str = Form(...),  # 登录动态码：须先由 /auth/hotp-code 签发（60 秒内一次性使用，与用户名无关）
    db: Session = Depends(get_db),
):
    # 先校验动态码（替代原拼图滑块）；校验通过即作废，前端重试会重新取码
    if not verify(hotp_code):
        raise HTTPException(status_code=400, detail="动态码校验失败，请重试")
    user = (
        db.query(User).filter(User.username == form_data.username).first()
    )
    if not user or not verify_password(form_data.password, user.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已禁用")
    # 弱密码存量也拦：登录时才有明文，现判并落库标记（含历史旧账号/管理员）。
    # 改密接口已要求强密码，成功后清标记；下次登录即恢复正常。
    if is_weak_password(form_data.password) and not user.must_change_password:
        user.must_change_password = True
        db.commit()
    log_action(user.id, "login", detail=f"用户登录（记住我={remember}）")
    expires_delta = (
        timedelta(days=settings.REMEMBER_TOKEN_EXPIRE_DAYS)
        if remember
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    # 单点登录：本次登录自增版本号，此前签发的所有 token 随即失效（别处在线会话被踢下线）
    user.token_version = int(user.token_version or 0) + 1
    db.commit()
    token = create_access_token(
        sub=user.username,
        role=user.role,
        expires_delta=expires_delta,
        token_version=user.token_version,
    )
    return TokenResponse(
        access_token=token,
        expires_in=int(expires_delta.total_seconds()),
        must_change_password=user.must_change_password,
    )


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user
