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
