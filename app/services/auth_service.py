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
from app.models.login_session import LoginSession
from app.models.slider_challenge import SliderChallenge
from app.models.sms_code import SmsCode
from app.models.token_blacklist import TokenBlacklist
from app.models.user import User
from app.services.sms_service import sms_service

logger = logging.getLogger(__name__)
PHONE_RE = re.compile(r"^1\d{10}$")
# Slider-puzzle geometry. The piece body is PIECE px wide; the knob sticks out
# KNOB px above/beside it, so the sprite canvas is (PIECE+KNOB)².
SLIDER_WIDTH = 320
SLIDER_HEIGHT = 160
SLIDER_PIECE = 44
SLIDER_KNOB = 10
SLIDER_TOLERANCE = 5
# The notch never sits in the leftmost strip (where the piece starts) and the
# sprite must stay fully inside the background.
SLIDER_X_RANGE = (SLIDER_WIDTH // 2, SLIDER_WIDTH - SLIDER_PIECE - 6)
SLIDER_Y_RANGE = (2, SLIDER_HEIGHT - SLIDER_PIECE - 2 * SLIDER_KNOB - 2)


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

    def issue_slider_challenge(self, db: Session, ip: str | None = None) -> dict:
        """Create a slider-puzzle challenge; returns background + piece as PNG data URIs."""
        now = utcnow()
        # Opportunistic cleanup keeps the table small without a scheduler job.
        db.query(SliderChallenge).filter(SliderChallenge.expires_at < now).delete()
        x = random.randint(*SLIDER_X_RANGE)
        y = random.randint(*SLIDER_Y_RANGE)
        challenge = SliderChallenge(
            x=x,
            y=y,
            tolerance=SLIDER_TOLERANCE,
            expires_at=now + timedelta(minutes=2),
            ip_address=ip,
        )
        db.add(challenge)
        db.commit()
        db.refresh(challenge)
        background, piece = self._render_slider_challenge(x, y)
        return {
            "challenge_id": challenge.id,
            "background": background,
            "piece": piece,
            "piece_y": y,
            "expires_in": 120,
        }

    @staticmethod
    def _render_slider_challenge(x: int, y: int) -> tuple[str, str]:
        """Render the puzzle background (with the notch cut out) and the piece
        sprite as PNG data URIs. Lazy import mirrors _send_sms so the unit-test
        import path stays dependency-light."""
        import base64
        import io

        from PIL import Image, ImageDraw, ImageFilter

        piece_w = SLIDER_PIECE + SLIDER_KNOB
        # Puzzle-piece silhouette: square body with an outward knob on top and
        # an inward notch on the right, carved on an L-mode mask (white = opaque).
        mask = Image.new("L", (piece_w, piece_w), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, SLIDER_KNOB, SLIDER_PIECE, SLIDER_KNOB + SLIDER_PIECE), radius=6, fill=255)
        draw.pieslice(
            (SLIDER_PIECE // 2 - SLIDER_KNOB, 0, SLIDER_PIECE // 2 + SLIDER_KNOB, 2 * SLIDER_KNOB),
            180,
            360,
            fill=255,
        )
        draw.pieslice(
            (SLIDER_PIECE - SLIDER_KNOB, SLIDER_KNOB + SLIDER_PIECE // 2 - SLIDER_KNOB,
             SLIDER_PIECE + SLIDER_KNOB, SLIDER_KNOB + SLIDER_PIECE // 2 + SLIDER_KNOB),
            0,
            360,
            fill=0,
        )

        # Procedural background: vertical gradient plus translucent shapes, so
        # no image assets ship with the app and every challenge looks different.
        background = Image.new("RGB", (SLIDER_WIDTH, SLIDER_HEIGHT))
        top = (random.randint(40, 120), random.randint(80, 160), random.randint(140, 220))
        bottom = (random.randint(120, 220), random.randint(40, 110), random.randint(60, 160))
        bg_draw = ImageDraw.Draw(background)
        for row in range(SLIDER_HEIGHT):
            ratio = row / SLIDER_HEIGHT
            color = tuple(int(t + (b - t) * ratio) for t, b in zip(top, bottom))
            bg_draw.line((0, row, SLIDER_WIDTH, row), fill=color)
        shape_draw = ImageDraw.Draw(background, "RGBA")
        for _ in range(7):
            cx, cy = random.randint(0, SLIDER_WIDTH), random.randint(0, SLIDER_HEIGHT)
            radius = random.randint(8, 30)
            shade = (255, 255, 255, 26) if random.random() < 0.5 else (0, 0, 0, 22)
            shape_draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=shade)
        background = background.filter(ImageFilter.GaussianBlur(1))

        # Cut the notch out of the background: darken it and mark the edge in
        # white so the drop target is clearly visible.
        edge = mask.filter(ImageFilter.MaxFilter(5))
        overlay = Image.new("RGBA", background.size, (0, 0, 0, 0))
        hole = Image.new("RGBA", (piece_w, piece_w), (15, 18, 24, 150))
        overlay.paste(hole, (x, y), mask)
        ring = Image.new("RGBA", (piece_w, piece_w), (255, 255, 255, 170))
        edge_only = edge.point(lambda v: 255 if v > 0 else 0)
        ring_mask = Image.new("L", (piece_w, piece_w), 0)
        ring_mask.paste(edge_only, (0, 0))
        ring_mask.paste(mask, (0, 0))
        overlay.paste(ring, (x, y), ring_mask)
        background = Image.alpha_composite(background.convert("RGBA"), overlay)

        # The piece sprite is the exact pixels the notch removed.
        sprite = background.crop((x, y, x + piece_w, y + piece_w))
        sprite.putalpha(mask)

        def to_uri(image: Image.Image) -> str:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

        return to_uri(background.convert("RGB")), to_uri(sprite)

    def verify_slider_challenge(self, db: Session, challenge_id: int, drag_x: int) -> dict:
        """Check the submitted drag distance and hand out a one-time ticket.

        The challenge is single-use regardless of outcome — consumed even on a
        miss — so a captured id cannot be retried or brute-forced beyond its
        two-minute lifetime and the rate limit.
        """
        record = db.query(SliderChallenge).filter(SliderChallenge.id == challenge_id).first()
        valid = (
            record is not None
            and not record.is_used
            and record.expires_at > utcnow()
            and abs(drag_x - record.x) <= record.tolerance
        )
        now = utcnow()
        if record is not None:
            if valid:
                record.verified = True
                record.ticket = uuid.uuid4().hex
                record.ticket_expires_at = now + timedelta(minutes=5)
            # Either way the challenge is spent; a verified row keeps its ticket
            # for /auth/send-code to consume.
            record.is_used = True
            db.commit()
        if not valid:
            raise HTTPException(status_code=400, detail="滑块验证未通过，请重试")
        return {"ticket": record.ticket, "expires_in": 300}

    def consume_slider_ticket(self, db: Session, ticket: str | None) -> None:
        """Consume a verified slider ticket; raises 400 when it is missing,
        stale or already spent."""
        record = None
        if ticket:
            record = (
                db.query(SliderChallenge)
                .filter(
                    SliderChallenge.ticket == ticket,
                    SliderChallenge.verified.is_(True),
                    SliderChallenge.ticket_expires_at > utcnow(),
                )
                .first()
            )
        if record is not None:
            record.ticket = None
            db.commit()
        if record is None:
            raise HTTPException(status_code=400, detail="请先完成滑块验证")

    def send_code(self, db: Session, phone: str, slider_ticket: str | None = None) -> None:
        self._validate_phone(phone)
        now = utcnow()
        latest = db.query(SmsCode).filter(SmsCode.phone == phone).order_by(SmsCode.created_at.desc()).first()
        if latest and latest.created_at and (now - latest.created_at).total_seconds() < 60:
            raise HTTPException(status_code=429, detail="验证码发送过于频繁，请稍后再试")
        today_start = datetime(now.year, now.month, now.day)
        today_count = db.query(SmsCode).filter(SmsCode.phone == phone, SmsCode.created_at >= today_start).count()
        if today_count >= 10:
            raise HTTPException(status_code=429, detail="今日验证码发送次数已达上限")
        # The slider gate runs after the rate limits so a 429 does not burn the
        # user's already-verified ticket.
        if settings.CAPTCHA_ENFORCED:
            self.consume_slider_ticket(db, slider_ticket)
        code = settings.SMS_MOCK_CODE if settings.SMS_MOCK_ENABLED else f"{random.randint(0, 999999):06d}"
        db.add(SmsCode(phone=phone, code=code, expires_at=now + timedelta(minutes=5), last_sent_at=now))
        db.commit()
        if not settings.SMS_MOCK_ENABLED:
            self._send_sms(phone, code)

    @staticmethod
    def _send_sms(phone: str, code: str) -> None:
        try:
            sms_service.send_verification_code(phone, code)
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
