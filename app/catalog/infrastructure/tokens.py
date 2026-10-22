"""Signed upload-token carrier between /upload/init and /upload/complete."""
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt

from app.core.config import settings
from app.domain.catalog import CatalogCode, CatalogError

TOKEN_LIFETIME_HOURS = 2


class JwtUploadTokenCodec:
    """Submission metadata rides in a JWT so /complete never trusts the client."""

    def encode(self, claims: dict) -> str:
        payload = {**claims, "exp": datetime.now(UTC) + timedelta(hours=TOKEN_LIFETIME_HOURS)}
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    def decode(self, token: str) -> dict:
        try:
            return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        except JWTError as exc:
            raise CatalogError(CatalogCode.UPLOAD_TOKEN_INVALID, reason=str(exc))
