"""Authentication API."""
from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, require_auth
from app.core.utils import ok, real_client_ip
from app.models.user import User
from app.services.auth_service import auth_service

router = APIRouter(prefix="/auth")
limiter = Limiter(key_func=get_remote_address)
security = HTTPBearer()


class PhoneRequest(BaseModel):
    phone: str
    captcha_id: int | None = None
    captcha_text: str | None = None


class AuthRequest(BaseModel):
    phone: str
    code: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ProfileUpdateRequest(BaseModel):
    nickname: str | None = None


class PhoneChangeRequest(BaseModel):
    new_phone: str
    code: str


@router.get("/captcha")
@limiter.limit("30/minute")
async def captcha(request: Request, db: Session = Depends(get_db)):
    """Fresh image captcha challenge for gating /auth/send-code."""
    return ok(auth_service.get_captcha(db, real_client_ip(request)))


@router.post("/send-code")
@limiter.limit("5/minute")
async def send_code(request: Request, body: PhoneRequest, db: Session = Depends(get_db)):
    auth_service.send_code(db, body.phone, body.captcha_id, body.captcha_text)
    return ok(message="验证码已发送")


@router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, body: AuthRequest, db: Session = Depends(get_db)):
    return ok(auth_service.login(db, body.phone, body.code), "登录成功")


@router.post("/register")
async def register(body: AuthRequest, db: Session = Depends(get_db)):
    return ok(auth_service.register(db, body.phone, body.code), "注册成功")


@router.post("/sign-in")
@limiter.limit("10/minute")
async def sign_in(request: Request, body: AuthRequest, db: Session = Depends(get_db)):
    """Unified login/register entry point.

    Verifies the SMS code, then logs the user in if the phone already has an
    account, or creates one on the spot and logs in. The response envelope
    matches /login and /register, with an extra ``is_new_user`` boolean for
    the frontend to surface an onboarding hint on first-time login.

    /login and /register remain for backwards compatibility with any external
    caller; new portal flows should use /sign-in instead.
    """
    result = auth_service.sign_in(db, body.phone, body.code)
    message = "首次登录，已为你创建账号" if result.get("is_new_user") else "登录成功"
    return ok(result, message)


@router.post("/refresh")
async def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    return ok(auth_service.refresh_token(db, body.refresh_token), "刷新成功")


@router.get("/me")
async def me(current_user: User = Depends(require_auth)):
    return ok(current_user.to_dict())


@router.patch("/me")
async def update_me(body: ProfileUpdateRequest, db: Session = Depends(get_db), current_user: User = Depends(require_auth)):
    nickname = (body.nickname or "").strip()
    current_user.nickname = nickname or None
    db.commit()
    db.refresh(current_user)
    return ok(current_user.to_dict(), "资料已更新")


@router.post("/logout")
async def logout(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db), current_user: User = Depends(require_auth)):
    ua = request.headers.get("user-agent", "")[:512]
    client_id = (request.headers.get("x-openindu-client-id") or "").strip()[:64] or None
    auth_service.logout(db, credentials.credentials, user_agent=ua, client_id=client_id)
    return ok(message="已登出")


@router.delete("/me")
async def delete_account(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """Permanently delete user account and all associated data."""
    ua = request.headers.get("user-agent", "")[:512]
    auth_service.delete_account(db, current_user, credentials.credentials, user_agent=ua)
    return ok(message="账号已注销")


@router.post("/change-phone")
async def change_phone(
    body: PhoneChangeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """Change user phone number with verification code."""
    auth_service.change_phone(db, current_user, body.new_phone, body.code)
    return ok({"user": current_user.to_dict()}, "手机号已更新")
