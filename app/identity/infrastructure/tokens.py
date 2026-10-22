"""JWT codec and persistence for token blacklist and login sessions."""
from datetime import datetime
from typing import Any

from jose import jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.utils import utcnow
from app.domain.auth import AuthError, BizCode
from app.models.login_session import LoginSession
from app.models.token_blacklist import TokenBlacklist


class JoseTokenCodec:
    """Encoding/decoding against the configured secret; invalid tokens raise
    the transport-independent AUTH_TOKEN_INVALID outcome."""

    def encode(self, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    def decode(self, token: str) -> dict[str, Any]:
        try:
            return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        except Exception:  # Catch all JWT-related errors (invalid, expired, malformed)
            raise AuthError(BizCode.AUTH_TOKEN_INVALID) from None


def decode_token(token: str) -> dict:
    """Module-level convenience for middleware/dependencies that only decode."""
    return JoseTokenCodec().decode(token)


class SqlTokenBlacklist:
    def __init__(self, db: Session) -> None:
        self.db = db

    def exists(self, jti: str | None) -> bool:
        if not jti:
            return False
        return self.db.query(TokenBlacklist).filter(TokenBlacklist.jti == jti).first() is not None

    def add(self, jti: str, user_id: int, token_type: str, reason: str, expires_at: datetime | None) -> None:
        self.db.add(
            TokenBlacklist(
                jti=jti,
                user_id=user_id,
                token_type=token_type,
                reason=reason,
                expires_at=expires_at or utcnow(),
            )
        )
        self.db.commit()


class SqlLoginSessionStore:
    def __init__(self, db: Session) -> None:
        self.db = db

    def deactivate(self, user_id: int, client_id: str | None = None, user_agent: str | None = None) -> None:
        query = self.db.query(LoginSession).filter(LoginSession.user_id == user_id)
        if client_id:
            query = query.filter(LoginSession.client_id == client_id)
        elif user_agent:
            query = query.filter(LoginSession.user_agent == user_agent)
        query.update({"is_active": False})
        self.db.commit()

    def deactivate_all(self, user_id: int) -> None:
        self.db.query(LoginSession).filter(LoginSession.user_id == user_id).update({"is_active": False})
        self.db.commit()
