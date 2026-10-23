"""Session rules: which token may refresh, which account stays refreshable."""
from datetime import datetime
from typing import Any, Protocol

from app.shared.domain.auth import AuthError, BizCode


class RefreshableAccount(Protocol):
    is_active: bool
    is_blacklisted: bool


def require_refresh_token_type(payload: dict[str, Any]) -> None:
    if payload.get("type") != "refresh":
        raise AuthError(BizCode.AUTH_REFRESH_INVALID)


def require_not_revoked(is_revoked: bool) -> None:
    if is_revoked:
        raise AuthError(BizCode.AUTH_REFRESH_REVOKED)


def require_refreshable(account: RefreshableAccount | None) -> None:
    """The refresh path has its own coarser outcome — a missing, deactivated
    or blacklisted account all read as "unavailable" (unlike login, which
    distinguishes removed/disabled)."""
    if account is None or not account.is_active or account.is_blacklisted:
        raise AuthError(BizCode.AUTH_ACCOUNT_UNAVAILABLE)


def require_issued_after_invalidation(issued_at: datetime | None, invalidated_at: datetime | None) -> None:
    """Reject tokens issued before the account's tokens were invalidated."""
    if invalidated_at is not None and issued_at is not None and invalidated_at > issued_at:
        raise AuthError(BizCode.AUTH_FORCED_LOGOUT)


def require_new_phone_free(existing_other_account: object | None) -> None:
    """Change-phone rule: any other account holding the phone blocks it,
    regardless of that account's deleted/disabled state."""
    if existing_other_account is not None:
        raise AuthError(BizCode.AUTH_PHONE_EXISTS)
