"""Pure membership application state transitions."""

from datetime import datetime
from enum import StrEnum
from typing import Protocol


class MemberCode(StrEnum):
    ALREADY_MEMBER = "MEMBER_ALREADY_MEMBER"
    APPLICATION_PENDING = "MEMBER_APPLICATION_PENDING"
    USER_NOT_FOUND = "MEMBER_USER_NOT_FOUND"
    APPLICATION_NOT_PENDING = "MEMBER_APPLICATION_NOT_PENDING"
    APPLICATION_SUBMITTED = "MEMBER_APPLICATION_SUBMITTED"
    APPLICATION_REJECTED = "MEMBER_APPLICATION_REJECTED"
    APPROVED_NOTIFIED = "MEMBER_APPROVED_NOTIFIED"
    APPROVED_UNNOTIFIED = "MEMBER_APPROVED_UNNOTIFIED"
    INVALID_ROLE = "MEMBER_INVALID_ROLE"
    USER_ALREADY_DELETED = "MEMBER_USER_ALREADY_DELETED"
    CANNOT_DELETE_SELF = "MEMBER_CANNOT_DELETE_SELF"
    ROLE_UPDATED = "MEMBER_ROLE_UPDATED"
    USER_BLACKLISTED = "MEMBER_USER_BLACKLISTED"
    USER_UNBLACKLISTED = "MEMBER_USER_UNBLACKLISTED"
    USER_LOGGED_OUT = "MEMBER_USER_LOGGED_OUT"
    USER_DELETED = "MEMBER_USER_DELETED"


class MemberError(Exception):
    def __init__(self, code: MemberCode) -> None:
        self.code = code
        super().__init__(code.value)


class Applicant(Protocol):
    role: str
    member_apply_status: str | None
    member_apply_note: str | None
    member_apply_at: datetime | None
    member_reviewed_by: int | None


class AdministeredAccount(Applicant, Protocol):
    id: int
    phone: str
    role: str
    is_active: bool
    is_blacklisted: bool
    blacklisted_at: datetime | None
    blacklisted_by: int | None
    tokens_invalidated_at: datetime | None
    deleted_at: datetime | None


def submit(account: Applicant, note: str | None, now: datetime) -> None:
    if account.role in ("member", "admin"):
        raise MemberError(MemberCode.ALREADY_MEMBER)
    if account.member_apply_status == "pending":
        raise MemberError(MemberCode.APPLICATION_PENDING)
    account.member_apply_status = "pending"
    account.member_apply_note = note
    account.member_apply_at = now
    account.member_reviewed_by = None


def review(account: Applicant | None, admin_id: int, *, approve: bool) -> None:
    if account is None:
        raise MemberError(MemberCode.USER_NOT_FOUND)
    if account.member_apply_status != "pending":
        raise MemberError(MemberCode.APPLICATION_NOT_PENDING)
    account.member_apply_status = "approved" if approve else "rejected"
    account.member_reviewed_by = admin_id
    if approve:
        account.role = "member"


def change_role(account: AdministeredAccount | None, role: str) -> None:
    if role not in ("user", "member", "admin"):
        raise MemberError(MemberCode.INVALID_ROLE)
    require_user(account)
    account.role = role


def blacklist(account: AdministeredAccount | None, admin_id: int, now: datetime) -> None:
    require_user(account)
    account.is_active = False
    account.is_blacklisted = True
    account.blacklisted_at = now
    account.blacklisted_by = admin_id
    account.tokens_invalidated_at = now


def unblacklist(account: AdministeredAccount | None) -> None:
    require_user(account)
    account.is_active = True
    account.is_blacklisted = False
    account.blacklisted_at = None
    account.blacklisted_by = None
    account.tokens_invalidated_at = None


def force_logout(account: AdministeredAccount | None, now: datetime) -> None:
    require_user(account)
    account.tokens_invalidated_at = now


def soft_delete(account: AdministeredAccount | None, admin_id: int, now: datetime) -> None:
    require_user(account)
    if account.deleted_at is not None:
        raise MemberError(MemberCode.USER_ALREADY_DELETED)
    if account.id == admin_id:
        raise MemberError(MemberCode.CANNOT_DELETE_SELF)
    account.deleted_at = now
    account.is_active = False
    account.tokens_invalidated_at = now


def require_user(account: AdministeredAccount | None) -> None:
    if account is None:
        raise MemberError(MemberCode.USER_NOT_FOUND)
