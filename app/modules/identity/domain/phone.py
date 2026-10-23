"""Phone number rules without HTTP, persistence, or SMS dependencies."""
import re

from app.shared.domain.auth import AuthError, BizCode

PHONE_RE = re.compile(r"^1\d{10}$")


def require_valid_phone(phone: str) -> None:
    if not PHONE_RE.match(phone):
        raise AuthError(BizCode.AUTH_INVALID_PHONE)
