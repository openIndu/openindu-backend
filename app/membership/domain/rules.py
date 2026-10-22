"""Membership and account-administration rules without framework dependencies."""
from typing import Protocol

from app.domain.membership import MemberCode, MemberError

APPLYABLE_ROLES = ("user",)
REVIEWABLE_STATUS = "pending"
KNOWN_ROLES = ("user", "member", "admin")


class Applicant(Protocol):
    role: str
    member_apply_status: str | None
    deleted_at: object | None


def require_apply_allowed(role: str, current_status: str | None) -> None:
    if role not in APPLYABLE_ROLES:
        raise MemberError(MemberCode.ALREADY_MEMBER)
    if current_status == REVIEWABLE_STATUS:
        raise MemberError(MemberCode.APPLICATION_PENDING)
    # A rejected application may be re-submitted; it flips back to pending.


def require_reviewable(current_status: str | None) -> None:
    if current_status != REVIEWABLE_STATUS:
        raise MemberError(MemberCode.APPLICATION_NOT_PENDING)


def require_existing(target) -> None:
    if target is None:
        raise MemberError(MemberCode.USER_NOT_FOUND)


def require_known_role(role: str) -> None:
    if role not in KNOWN_ROLES:
        raise MemberError(MemberCode.INVALID_ROLE)


def require_deletable(target, admin_id: int) -> None:
    require_existing(target)
    if target.deleted_at is not None:
        raise MemberError(MemberCode.USER_ALREADY_DELETED)
    if target.id == admin_id:
        raise MemberError(MemberCode.CANNOT_DELETE_SELF)
