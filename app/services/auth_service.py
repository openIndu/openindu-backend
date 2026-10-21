"""SMS authentication and JWT lifecycle service."""
import logging
import random
import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from jose import jwt
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.captcha_challenge import CaptchaChallenge
from app.models.login_session import LoginSession
from app.models.sms_code import SmsCode
from app.models.token_blacklist import TokenBlacklist
from app.models.user import User

logger = logging.getLogger(__name__)
PHONE_RE = re.compile(r"^1\d{10}$")
# No 0/O, 1/I/L look-alikes so a rendered captcha is unambiguous to humans.
CAPTCHA_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except Exception:  # Catch all JWT-related errors (invalid, expired, malformed)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的 token"
        )


class AuthService:
    """Phone + SMS auth with access/refresh JWT rotation."""

    @staticmethod
    def _validate_phone(phone: str) -> None:
        if not PHONE_RE.match(phone):
            raise HTTPException(status_code=400, detail="手机号格式不正确")

    @staticmethod
    def _create_token(user: User, token_type: str, expires_delta: timedelta) -> tuple[str, str, datetime]:
        now = utcnow()
        expires_at = now + expires_delta
        jti = uuid.uuid4().hex
        payload = {
            "sub": str(user.id),
            "phone": user.phone,
            "role": user.role,
            "type": token_type,
            "jti": jti,
            "iat": now,
            "exp": expires_at,
        }
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM), jti, expires_at

    @classmethod
    def create_token_pair(cls, user: User) -> dict:
        access_token, access_jti, access_expires_at = cls._create_token(
            user, "access", timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        refresh_token, refresh_jti, refresh_expires_at = cls._create_token(
            user, "refresh", timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        )
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "access_jti": access_jti,
            "refresh_jti": refresh_jti,
            "access_expires_at": access_expires_at,
            "refresh_expires_at": refresh_expires_at,
        }

    def get_captcha(self, db: Session, ip: str | None = None) -> dict:
        """Create an image captcha challenge; returns captcha_id + PNG data URI."""
        now = utcnow()
        # Opportunistic cleanup keeps the table small without a scheduler job.
        db.query(CaptchaChallenge).filter(CaptchaChallenge.expires_at < now).delete()
        answer = "".join(random.choices(CAPTCHA_ALPHABET, k=4))
        challenge = CaptchaChallenge(answer=answer, expires_at=now + timedelta(minutes=5), ip_address=ip)
        db.add(challenge)
        db.commit()
        db.refresh(challenge)
        return {"captcha_id": challenge.id, "image": self._render_captcha(answer), "expires_in": 300}

    @staticmethod
    def _render_captcha(answer: str) -> str:
        """Render the challenge text as a PNG data URI. Lazy import mirrors
        _send_sms so the unit-test import path stays dependency-light."""
        import base64
        import io

        from captcha.image import ImageCaptcha

        buffer = io.BytesIO()
        ImageCaptcha(width=160, height=60).write(answer, buffer)
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    def verify_captcha(self, db: Session, captcha_id: int | None, text: str | None) -> None:
        """Consume and check a captcha challenge; raises 400 on any mismatch.

        The challenge is single-use regardless of outcome — consumed even on a
        wrong guess — so a captured id cannot be replayed or brute-forced.
        """
        record = None
        if captcha_id is not None:
            record = db.query(CaptchaChallenge).filter(CaptchaChallenge.id == captcha_id).first()
        valid = (
            record is not None
            and not record.is_used
            and record.expires_at > utcnow()
            and bool(text)
            and record.answer == text.strip().upper()
        )
        if record is not None:
            record.is_used = True
            db.commit()
        if not valid:
            raise HTTPException(status_code=400, detail="图形验证码错误或已过期，请刷新后重试")

    def send_code(self, db: Session, phone: str, captcha_id: int | None = None, captcha_text: str | None = None) -> None:
        if settings.CAPTCHA_ENFORCED:
            self.verify_captcha(db, captcha_id, captcha_text)
        self._validate_phone(phone)
        now = utcnow()
        latest = db.query(SmsCode).filter(SmsCode.phone == phone).order_by(SmsCode.created_at.desc()).first()
        if latest and latest.created_at and (now - latest.created_at).total_seconds() < 60:
            raise HTTPException(status_code=429, detail="验证码发送过于频繁，请稍后再试")
        today_start = datetime(now.year, now.month, now.day)
        today_count = db.query(SmsCode).filter(SmsCode.phone == phone, SmsCode.created_at >= today_start).count()
        if today_count >= 10:
            raise HTTPException(status_code=429, detail="今日验证码发送次数已达上限")
        code = settings.SMS_MOCK_CODE if settings.SMS_MOCK_ENABLED else f"{random.randint(0, 999999):06d}"
        db.add(SmsCode(phone=phone, code=code, expires_at=now + timedelta(minutes=5), last_sent_at=now))
        db.commit()
        if not settings.SMS_MOCK_ENABLED:
            self._send_sms(phone, code)

    @staticmethod
    def _send_sms(phone: str, code: str) -> None:
        try:
            from alibabacloud_dysmsapi20170525.client import Client
            from alibabacloud_dysmsapi20170525.models import SendSmsRequest
            from alibabacloud_tea_openapi.models import Config
            cfg = Config(
                access_key_id=settings.SMS_ACCESS_KEY,
                access_key_secret=settings.SMS_ACCESS_KEY_SECRET,
                endpoint="dysmsapi.aliyuncs.com",
            )
            client = Client(cfg)
            req = SendSmsRequest(
                phone_numbers=phone,
                sign_name=settings.SMS_SIGN_NAME,
                template_code=settings.SMS_TEMPLATE_ID,
                template_param=f'{{"code":"{code}"}}',
            )
            resp = client.send_sms(req)
            if resp.body.code != "OK":
                raise RuntimeError(f"阿里云短信发送失败: {resp.body.message}")
        except Exception as exc:
            logger.error("SMS send failed to %s: %s", phone, exc)
            raise HTTPException(status_code=502, detail=f"短信发送失败: {exc}")

    def verify_code(self, db: Session, phone: str, code: str) -> bool:
        self._validate_phone(phone)
        if settings.SMS_MOCK_ENABLED and code == settings.SMS_MOCK_CODE:
            return True
        now = utcnow()
        record = db.query(SmsCode).filter(
            SmsCode.phone == phone,
            SmsCode.is_used.is_(False),
            SmsCode.expires_at > now,
        ).order_by(SmsCode.created_at.desc()).first()
        if not record:
            return False
        record.verify_attempts += 1
        if record.verify_attempts > 3:
            record.is_used = True
            db.commit()
            return False
        ok = record.code == code
        if ok:
            record.is_used = True
        db.commit()
        return ok

    def register(self, db: Session, phone: str, code: str) -> dict:
        if not self.verify_code(db, phone, code):
            raise HTTPException(status_code=400, detail="验证码错误或已过期")
        existing = db.query(User).filter(User.phone == phone).first()
        if existing:
            # A soft-deleted row still occupies the unique phone slot; surface
            # a distinct message so the admin can decide whether to restore or
            # purge before the phone can be reused.
            if existing.deleted_at is not None:
                raise HTTPException(status_code=409, detail="该手机号曾被删除，如需重新注册请联系管理员恢复")
            raise HTTPException(status_code=409, detail="用户已存在")
        role = "admin" if db.query(func.count(User.id)).scalar() == 0 else "user"
        user = User(phone=phone, role=role, is_active=True, is_blacklisted=False, last_login=utcnow())
        db.add(user)
        db.commit()
        db.refresh(user)
        return {"user": user.to_dict(), "tokens": self.create_token_pair(user)}

    def login(self, db: Session, phone: str, code: str) -> dict:
        if not self.verify_code(db, phone, code):
            raise HTTPException(status_code=400, detail="验证码错误或已过期")
        user = db.query(User).filter(User.phone == phone).first()
        if not user:
            raise HTTPException(status_code=400, detail="用户不存在，请先注册")
        if user.deleted_at is not None:
            raise HTTPException(status_code=403, detail="账号已被删除")
        if not user.is_active or user.is_blacklisted:
            raise HTTPException(status_code=403, detail="账号已被禁用")
        user.last_login = utcnow()
        db.commit()
        db.refresh(user)
        return {"user": user.to_dict(), "tokens": self.create_token_pair(user)}

    def sign_in(self, db: Session, phone: str, code: str) -> dict:
        """Single-step auth: verify code, then login if user exists or
        auto-register on the spot. Returns the same envelope as login/register
        with an extra ``is_new_user`` flag so the frontend can show a "首次登录
        已为你创建账号" hint when appropriate.
        """
        if not self.verify_code(db, phone, code):
            raise HTTPException(status_code=400, detail="验证码错误或已过期")
        user = db.query(User).filter(User.phone == phone).first()
        is_new_user = False
        if user is None:
            role = "admin" if db.query(func.count(User.id)).scalar() == 0 else "user"
            user = User(phone=phone, role=role, is_active=True, is_blacklisted=False, last_login=utcnow())
            db.add(user)
            db.commit()
            db.refresh(user)
            is_new_user = True
        else:
            if user.deleted_at is not None:
                raise HTTPException(status_code=403, detail="账号已被删除")
            if not user.is_active or user.is_blacklisted:
                raise HTTPException(status_code=403, detail="账号已被禁用")
            user.last_login = utcnow()
            db.commit()
            db.refresh(user)
        return {"user": user.to_dict(), "tokens": self.create_token_pair(user), "is_new_user": is_new_user}

    def blacklist_token(self, db: Session, payload: dict, reason: str = "logout") -> None:
        jti = payload.get("jti")
        user_id = payload.get("sub")
        token_type = payload.get("type", "access")
        exp = payload.get("exp")
        if not jti or not user_id or db.query(TokenBlacklist).filter(TokenBlacklist.jti == jti).first():
            return
        expires_at = datetime.fromtimestamp(exp, tz=UTC).replace(tzinfo=None) if exp else utcnow()
        db.add(TokenBlacklist(jti=jti, user_id=int(user_id), token_type=token_type, reason=reason, expires_at=expires_at))
        db.commit()

    def refresh_token(self, db: Session, refresh_token: str) -> dict:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效 refresh token")
        if db.query(TokenBlacklist).filter(TokenBlacklist.jti == payload.get("jti")).first():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh token 已失效")
        user = db.query(User).filter(User.id == int(payload["sub"])).first()
        if not user or not user.is_active or user.is_blacklisted:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")
        # Check if tokens were invalidated after this refresh token was issued
        if user.tokens_invalidated_at:
            iat = payload.get("iat")
            if iat:
                token_issued_at = datetime.fromtimestamp(iat, tz=UTC).replace(tzinfo=None)
                if user.tokens_invalidated_at > token_issued_at:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 已被撤销（强制登出）")
        self.blacklist_token(db, payload, reason="refresh_rotation")
        return {"user": user.to_dict(), "tokens": self.create_token_pair(user)}

    def logout(self, db: Session, token: str, user_agent: str | None = None, client_id: str | None = None) -> None:
        payload = decode_token(token)
        self.blacklist_token(db, payload, reason="logout")
        user_id = payload.get("sub")
        if user_id and client_id:
            db.query(LoginSession).filter(
                LoginSession.user_id == int(user_id),
                LoginSession.client_id == client_id,
            ).update({"is_active": False})
            db.commit()
        elif user_id and user_agent:
            db.query(LoginSession).filter(
                LoginSession.user_id == int(user_id),
                LoginSession.user_agent == user_agent,
            ).update({"is_active": False})
            db.commit()

    def delete_account(self, db: Session, user: User, token: str, user_agent: str | None = None) -> None:
        """Permanently delete user account and invalidate all tokens."""
        # Blacklist current token
        payload = decode_token(token)
        self.blacklist_token(db, payload, reason="account_deleted")

        # Invalidate all user tokens
        user.tokens_invalidated_at = utcnow()
        db.commit()

        # Delete user data (soft delete via blacklisting + marking inactive)
        user.is_active = False
        user.is_blacklisted = True
        user.blacklisted_at = utcnow()
        db.commit()

        # Clear login sessions
        db.query(LoginSession).filter(LoginSession.user_id == user.id).update({"is_active": False})
        db.commit()

    def change_phone(self, db: Session, user: User, new_phone: str, code: str) -> None:
        """Change user phone number with verification."""
        self._validate_phone(new_phone)

        # Verify code for the new phone
        if not self.verify_code(db, new_phone, code):
            raise HTTPException(status_code=400, detail="验证码错误或已过期")

        # Check if new phone is already registered
        existing = db.query(User).filter(User.phone == new_phone, User.id != user.id).first()
        if existing:
            raise HTTPException(status_code=409, detail="该手机号已被注册")

        user.phone = new_phone
        db.commit()
        db.refresh(user)


auth_service = AuthService()
