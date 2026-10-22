"""Submit and review membership applications through explicit ports."""

import logging
from datetime import datetime
from typing import Protocol

from app.membership.domain.application import Applicant, MemberCode, review, submit

logger = logging.getLogger(__name__)


class MemberAccount(Applicant, Protocol):
    id: int
    phone: str


class MembershipRepository(Protocol):
    def find(self, user_id: int) -> MemberAccount | None: ...

    def save(self, account: MemberAccount) -> None: ...

    def save_review(self, account: MemberAccount, admin_id: int, action: str) -> None: ...


class ApprovalNotifier(Protocol):
    def __call__(self, phone: str) -> bool: ...


class MembershipCommands:
    def __init__(self, repository: MembershipRepository, notify: ApprovalNotifier, now: datetime) -> None:
        self.repository = repository
        self.notify = notify
        self.now = now

    def apply(self, account: MemberAccount, note: str | None) -> MemberAccount:
        submit(account, note, self.now)
        self.repository.save(account)
        return account

    def reject(self, user_id: int, admin_id: int) -> MemberAccount:
        account = self.repository.find(user_id)
        review(account, admin_id, approve=False)
        self.repository.save_review(account, admin_id, "member_reject")
        return account

    def approve(self, user_id: int, admin_id: int) -> tuple[MemberAccount, MemberCode]:
        account = self.repository.find(user_id)
        review(account, admin_id, approve=True)
        self.repository.save_review(account, admin_id, "member_approve")
        try:
            notified = self.notify(account.phone)
        except Exception as exc:
            logger.error("Member approval notification failed for user %s: %s", account.id, type(exc).__name__)
            notified = False
        outcome = MemberCode.APPROVED_NOTIFIED if notified else MemberCode.APPROVED_UNNOTIFIED
        return account, outcome
