"""Membership application use cases: apply, review, approval notification."""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.membership import MemberCode, MemberError
from app.modules.membership.domain import rules

NowFn = Callable[[], datetime]
MaskFn = Callable[[object], object]


class Applicant(Protocol):
    id: int
    phone: str
    nickname: str | None
    role: str
    member_apply_status: str | None
    member_apply_note: str | None
    member_apply_at: datetime | None
    member_reviewed_by: int | None


class ApplicantRepository(Protocol):
    def find_by_id(self, user_id: int) -> Applicant | None: ...

    def set_application(self, applicant: Applicant, note: str | None, at: datetime) -> Applicant: ...

    def save(self, applicant: Applicant) -> Applicant: ...


class AuditLog(Protocol):
    def record(self, admin_id: int, target_user_id: int, action: str, detail: dict | None = None) -> None: ...


class ApprovalNotifier(Protocol):
    def send(self, phone: str) -> bool: ...


@dataclass(frozen=True)
class ApplicationView:
    id: int
    status: str | None
    created_at: datetime | None
    note: str | None = None
    reviewed_by: int | None = None
    phone: str | None = None
    nickname: str | None = None
    current_role: str | None = None
    sms_notified: bool | None = None


class ApplyForMembership:
    """Re-applying after a rejection is allowed and flips the state back."""

    def __init__(self, applicants: ApplicantRepository, now: NowFn) -> None:
        self.applicants = applicants
        self._now = now

    def execute(self, applicant: Applicant, note: str | None) -> ApplicationView:
        rules.require_apply_allowed(applicant.role, applicant.member_apply_status)
        updated = self.applicants.set_application(applicant, note, self._now())
        return ApplicationView(id=updated.id, status=updated.member_apply_status, created_at=updated.member_apply_at)


class ReviewApplication:
    """Approve or reject a pending application.

    The decision is committed before the SMS goes out — a failed notice must
    never roll the approval back, only surface in the response.
    """

    def __init__(
        self,
        applicants: ApplicantRepository,
        audits: AuditLog,
        notifier: ApprovalNotifier,
        mask: MaskFn,
    ) -> None:
        self.applicants = applicants
        self.audits = audits
        self.notifier = notifier
        self._mask = mask

    def execute(self, user_id: int, reviewer_id: int, approve: bool) -> ApplicationView:
        target = self.applicants.find_by_id(user_id)
        if target is None:
            raise MemberError(MemberCode.USER_NOT_FOUND)
        rules.require_reviewable(target.member_apply_status)

        notified: bool | None = None
        target.member_reviewed_by = reviewer_id
        if approve:
            target.role = "member"
            target.member_apply_status = "approved"
        else:
            target.member_apply_status = "rejected"
        self.audits.record(reviewer_id, target.id, "member_approve" if approve else "member_reject", {"user_id": user_id})
        saved = self.applicants.save(target)

        if approve:
            # The notifier owns failure handling — it logs and returns False
            # instead of raising, so approval can never roll back here.
            notified = self.notifier.send(saved.phone)

        return ApplicationView(
            id=saved.id,
            status=saved.member_apply_status,
            created_at=saved.member_apply_at,
            note=saved.member_apply_note,
            reviewed_by=saved.member_reviewed_by,
            phone=self._mask(saved.phone),
            nickname=saved.nickname,
            current_role=saved.role,
            sms_notified=notified,
        )
