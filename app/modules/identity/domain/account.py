"""Account rules without HTTP, persistence, or SMS dependencies."""

from typing import Protocol

from app.domain.auth import AuthError, BizCode


class AccountState(Protocol):
    deleted_at: object | None
    is_active: bool
    is_blacklisted: bool


def require_available(account: AccountState) -> None:
    if account.deleted_at is not None:
        raise AuthError(BizCode.AUTH_ACCOUNT_REMOVED)
    if not account.is_active or account.is_blacklisted:
        raise AuthError(BizCode.AUTH_ACCOUNT_DISABLED)


def require_unregistered(account: AccountState | None) -> None:
    if account is None:
        return
    if account.deleted_at is not None:
        raise AuthError(BizCode.AUTH_PHONE_PREVIOUSLY_DELETED)
    raise AuthError(BizCode.AUTH_USER_EXISTS)


def initial_role(existing_accounts: int) -> str:
    return "admin" if existing_accounts == 0 else "user"
