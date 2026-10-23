"""Session use cases: token issuing, refresh rotation, logout, account deletion."""
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.modules.identity.domain import session_rules
from app.modules.identity.domain.phone import require_valid_phone
from app.shared.domain.auth import AuthError, BizCode

NowFn = Callable[[], datetime]
NewId = Callable[[], str]


class Account(Protocol):
    id: int
    phone: str
    role: str
    is_active: bool
    is_blacklisted: bool
    blacklisted_at: datetime | None
    tokens_invalidated_at: datetime | None


class AccountDirectory(Protocol):
    def find_by_id(self, account_id: int) -> Account | None: ...

    def find_by_phone(self, phone: str) -> Account | None: ...

    def update_phone(self, account: Account, phone: str) -> Account: ...

    def save(self, account: Account) -> Account: ...


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    access_jti: str
    refresh_jti: str
    access_expires_at: datetime
    refresh_expires_at: datetime


class TokenCodec(Protocol):
    def encode(self, claims: dict[str, Any]) -> str: ...

    def decode(self, token: str) -> dict[str, Any]: ...


class TokenBlacklist(Protocol):
    def exists(self, jti: str | None) -> bool: ...

    def add(self, jti: str, user_id: int, token_type: str, reason: str, expires_at: datetime | None) -> None: ...


class LoginSessionStore(Protocol):
    def deactivate(self, user_id: int, client_id: str | None = None, user_agent: str | None = None) -> None: ...

    def deactivate_all(self, user_id: int) -> None: ...


class IssueTokenPair:
    """Access + refresh JWT rotation policy; encoding is the codec's job."""

    def __init__(
        self,
        codec: TokenCodec,
        new_jti: NewId,
        now: NowFn,
        access_minutes: int,
        refresh_days: int,
    ) -> None:
        self.codec = codec
        self._new_jti = new_jti or (lambda: uuid.uuid4().hex)
        self._now = now
        self.access_minutes = access_minutes
        self.refresh_days = refresh_days

    def _claims(self, account: Account, token_type: str, jti: str, issued_at: datetime, expires_at: datetime) -> dict:
        return {
            "sub": str(account.id),
            "phone": account.phone,
            "role": account.role,
            "type": token_type,
            "jti": jti,
            "iat": issued_at,
            "exp": expires_at,
        }

    def execute(self, account: Account) -> TokenPair:
        now = self._now()
        access_jti, refresh_jti = self._new_jti(), self._new_jti()
        access_expires_at = now + timedelta(minutes=self.access_minutes)
        refresh_expires_at = now + timedelta(days=self.refresh_days)
        access_token = self.codec.encode(self._claims(account, "access", access_jti, now, access_expires_at))
        refresh_token = self.codec.encode(self._claims(account, "refresh", refresh_jti, now, refresh_expires_at))
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=self.access_minutes * 60,
            access_jti=access_jti,
            refresh_jti=refresh_jti,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
        )


def _naive(timestamp: Any) -> datetime | None:
    """Epoch seconds (or datetime) to naive UTC, the app's storage convention."""
    if timestamp is None:
        return None
    if isinstance(timestamp, datetime):
        return timestamp.replace(tzinfo=None)
    return datetime.fromtimestamp(timestamp, tz=UTC).replace(tzinfo=None)


def revoke_payload(blacklist: TokenBlacklist, payload: dict[str, Any], reason: str, now: datetime) -> None:
    """Blacklist one decoded token; duplicate or malformed payloads are no-ops."""
    jti = payload.get("jti")
    user_id = payload.get("sub")
    if not jti or not user_id or blacklist.exists(jti):
        return
    blacklist.add(jti, int(user_id), payload.get("type", "access"), reason, _naive(payload.get("exp")) or now)


class RefreshSession:
    """Rotate a refresh token into a fresh pair, revoking the old one."""

    def __init__(
        self,
        codec: TokenCodec,
        blacklist: TokenBlacklist,
        accounts: AccountDirectory,
        issue_tokens: IssueTokenPair,
        now: NowFn,
    ) -> None:
        self.codec = codec
        self.blacklist = blacklist
        self.accounts = accounts
        self.issue_tokens = issue_tokens
        self._now = now

    def execute(self, refresh_token: str) -> tuple[Account, TokenPair]:
        payload = self.codec.decode(refresh_token)
        session_rules.require_refresh_token_type(payload)
        session_rules.require_not_revoked(self.blacklist.exists(payload.get("jti")))
        account = self.accounts.find_by_id(int(payload["sub"]))
        session_rules.require_refreshable(account)
        session_rules.require_issued_after_invalidation(
            _naive(payload.get("iat")), account.tokens_invalidated_at
        )
        revoke_payload(self.blacklist, payload, reason="refresh_rotation", now=self._now())
        return account, self.issue_tokens.execute(account)


class EndSession:
    """Logout: blacklist the presented token and close matching sessions."""

    def __init__(
        self,
        codec: TokenCodec,
        blacklist: TokenBlacklist,
        sessions: LoginSessionStore,
        now: NowFn,
    ) -> None:
        self.codec = codec
        self.blacklist = blacklist
        self.sessions = sessions
        self._now = now

    def execute(self, token: str, user_agent: str | None = None, client_id: str | None = None) -> None:
        payload = self.codec.decode(token)
        revoke_payload(self.blacklist, payload, reason="logout", now=self._now())
        user_id = payload.get("sub")
        if not user_id:
            return
        if client_id:
            self.sessions.deactivate(int(user_id), client_id=client_id)
        elif user_agent:
            self.sessions.deactivate(int(user_id), user_agent=user_agent)


class DeleteAccount:
    """Permanently delete the account and invalidate every issued token."""

    def __init__(
        self,
        codec: TokenCodec,
        blacklist: TokenBlacklist,
        sessions: LoginSessionStore,
        accounts: AccountDirectory,
        now: NowFn,
    ) -> None:
        self.codec = codec
        self.blacklist = blacklist
        self.sessions = sessions
        self.accounts = accounts
        self._now = now

    def execute(self, account: Account, token: str) -> Account:
        payload = self.codec.decode(token)
        revoke_payload(self.blacklist, payload, reason="account_deleted", now=self._now())
        now = self._now()
        account.tokens_invalidated_at = now
        account.is_active = False
        account.is_blacklisted = True
        # Deleted accounts keep a blacklisted marker so the phone can be
        # recognised as previously used at registration time.
        account.blacklisted_at = now
        self.sessions.deactivate_all(account.id)
        return self.accounts.save(account)


class ChangePhone:
    """Change the account phone after verifying a code sent to the new number."""

    def __init__(self, verify_code: Callable[[str, str], bool], accounts: AccountDirectory) -> None:
        self.verify_code = verify_code
        self.accounts = accounts

    def execute(self, account: Account, new_phone: str, code: str) -> Account:
        require_valid_phone(new_phone)
        if not self.verify_code(new_phone, code):
            raise AuthError(BizCode.AUTH_INVALID_CODE)
        session_rules.require_new_phone_free(self.accounts.find_by_phone(new_phone))
        return self.accounts.update_phone(account, new_phone)
