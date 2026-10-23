"""Account administration use cases: role change, blacklist, force logout, delete."""
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from app.modules.membership.domain import rules

NowFn = Callable[[], datetime]
MaskFn = Callable[[object], object]


class ManagedUser(Protocol):
    id: int
    phone: str
    role: str
    is_active: bool
    is_blacklisted: bool
    blacklisted_at: datetime | None
    blacklisted_by: int | None
    tokens_invalidated_at: datetime | None
    deleted_at: datetime | None


class UserRepository(Protocol):
    def find_by_id(self, user_id: int) -> ManagedUser | None: ...

    def save(self, user: ManagedUser) -> ManagedUser: ...


class AuditLog(Protocol):
    def record(self, admin_id: int, target_user_id: int, action: str, detail: dict | None = None) -> None: ...


class SessionTerminator(Protocol):
    def deactivate_active(self, user_id: int) -> None: ...


class ChangeRole:
    def __init__(self, users: UserRepository, audits: AuditLog) -> None:
        self.users = users
        self.audits = audits

    def execute(self, user_id: int, admin_id: int, role: str) -> ManagedUser:
        rules.require_known_role(role)
        user = self.users.find_by_id(user_id)
        rules.require_existing(user)
        old_role = user.role
        user.role = role
        self.audits.record(admin_id, user.id, "role_change", {"old_role": old_role, "new_role": role})
        return self.users.save(user)


class BlacklistUser:
    def __init__(self, users: UserRepository, audits: AuditLog, sessions: SessionTerminator, now: NowFn) -> None:
        self.users = users
        self.audits = audits
        self.sessions = sessions
        self._now = now

    def execute(self, user_id: int, admin_id: int) -> ManagedUser:
        user = self.users.find_by_id(user_id)
        rules.require_existing(user)
        now = self._now()
        user.is_active = False
        user.is_blacklisted = True
        user.blacklisted_at = now
        user.blacklisted_by = admin_id
        user.tokens_invalidated_at = now
        self.sessions.deactivate_active(user_id)
        self.audits.record(admin_id, user.id, "blacklist")
        return self.users.save(user)


class UnblacklistUser:
    def __init__(self, users: UserRepository, audits: AuditLog) -> None:
        self.users = users
        self.audits = audits

    def execute(self, user_id: int, admin_id: int) -> ManagedUser:
        user = self.users.find_by_id(user_id)
        rules.require_existing(user)
        user.is_active = True
        user.is_blacklisted = False
        user.blacklisted_at = None
        user.blacklisted_by = None
        user.tokens_invalidated_at = None
        self.audits.record(admin_id, user.id, "unblacklist")
        return self.users.save(user)


class ForceLogout:
    def __init__(self, users: UserRepository, audits: AuditLog, sessions: SessionTerminator, now: NowFn) -> None:
        self.users = users
        self.audits = audits
        self.sessions = sessions
        self._now = now

    def execute(self, user_id: int, admin_id: int) -> ManagedUser:
        user = self.users.find_by_id(user_id)
        rules.require_existing(user)
        user.tokens_invalidated_at = self._now()
        self.sessions.deactivate_active(user_id)
        self.audits.record(admin_id, user.id, "force_logout")
        return self.users.save(user)


class DeleteUser:
    """Soft-delete: hide from the admin list and kill sessions/tokens.

    Associated rows (visit_events / download_records / login_sessions /
    admin_audit_log) are preserved for audit history.
    """

    def __init__(
        self,
        users: UserRepository,
        audits: AuditLog,
        sessions: SessionTerminator,
        now: NowFn,
        mask: MaskFn,
    ) -> None:
        self.users = users
        self.audits = audits
        self.sessions = sessions
        self._now = now
        self._mask = mask

    def execute(self, user_id: int, admin_id: int) -> ManagedUser:
        user = self.users.find_by_id(user_id)
        rules.require_deletable(user, admin_id)
        now = self._now()
        user.deleted_at = now
        user.is_active = False
        user.tokens_invalidated_at = now
        self.sessions.deactivate_active(user_id)
        self.audits.record(admin_id, user.id, "delete", {"phone": self._mask(user.phone)})
        return self.users.save(user)
