"""Submit and review membership applications through explicit ports."""

import logging
from datetime import datetime
from typing import Protocol

from app.membership.domain.application import (
    AdministeredAccount,
    Applicant,
    MemberCode,
    blacklist,
    change_role,
    force_logout,
    review,
    soft_delete,
    submit,
    unblacklist,
)

logger = logging.getLogger(__name__)


class MemberAccount(Applicant, Protocol):
    id: int
    phone: str


class MembershipRepository(Protocol):
    def find(self, user_id: int) -> AdministeredAccount | None: ...

    def save(self, account: MemberAccount) -> None: ...

    def save_review(self, account: AdministeredAccount, admin_id: int, action: str) -> None: ...

    def save_admin_action(self, account: AdministeredAccount, admin_id: int, action: str, detail: dict) -> None: ...

    def deactivate_sessions(self, user_id: int) -> None: ...


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

    def change_role(self, user_id: int, role: str, admin_id: int) -> AdministeredAccount:
        account = self.repository.find(user_id)
        old_role = getattr(account, "role", None)
        change_role(account, role)
        self.repository.save_admin_action(
            account, admin_id, "role_change", {"old_role": old_role, "new_role": role}
        )
        return account

    def get_admin_outcome(self, action: str) -> MemberCode:
        return {
            "role_change": MemberCode.ROLE_UPDATED,
            "blacklist": MemberCode.USER_BLACKLISTED,
            "unblacklist": MemberCode.USER_UNBLACKLISTED,
            "force_logout": MemberCode.USER_LOGGED_OUT,
            "delete": MemberCode.USER_DELETED,
        }[action]

    def blacklist(self, user_id: int, admin_id: int) -> AdministeredAccount:
        account = self.repository.find(user_id)
        blacklist(account, admin_id, self.now)
        self.repository.deactivate_sessions(user_id)
        self.repository.save_admin_action(account, admin_id, "blacklist", {})
        return account

    def unblacklist(self, user_id: int, admin_id: int) -> AdministeredAccount:
        account = self.repository.find(user_id)
        unblacklist(account)
        self.repository.save_admin_action(account, admin_id, "unblacklist", {})
        return account

    def force_logout(self, user_id: int, admin_id: int) -> AdministeredAccount:
        account = self.repository.find(user_id)
        force_logout(account, self.now)
        self.repository.deactivate_sessions(user_id)
        self.repository.save_admin_action(account, admin_id, "force_logout", {})
        return account

    def delete_user(self, user_id: int, admin_id: int) -> AdministeredAccount:
        account = self.repository.find(user_id)
        soft_delete(account, admin_id, self.now)
        self.repository.deactivate_sessions(user_id)
        self.repository.save_admin_action(account, admin_id, "delete", {"phone": account.phone})
        return account
