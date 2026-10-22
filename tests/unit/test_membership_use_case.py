"""Membership transitions and commit-before-notification contract."""

from dataclasses import dataclass
from datetime import datetime

import pytest

from app.membership.application.commands import MembershipCommands
from app.membership.domain.application import MemberCode, MemberError


@dataclass
class Account:
    id: int = 7
    phone: str = "13800138000"
    role: str = "user"
    member_apply_status: str | None = None
    member_apply_note: str | None = None
    member_apply_at: datetime | None = None
    member_reviewed_by: int | None = None


class Repository:
    def __init__(self, account=None):
        self.account = account
        self.saved = False
        self.audit_action = None

    def find(self, user_id):
        return self.account if self.account and self.account.id == user_id else None

    def save(self, account):
        self.saved = True

    def save_review(self, account, admin_id, action):
        self.audit_action = action
        self.save(account)


NOW = datetime(2026, 10, 22)


def test_rejected_applicant_can_resubmit_and_reset_reviewer():
    account = Account(member_apply_status="rejected", member_reviewed_by=2)
    repo = Repository(account)
    result = MembershipCommands(repo, lambda _: True, NOW).apply(account, "new details")
    assert (result.member_apply_status, result.member_apply_note, result.member_reviewed_by) == (
        "pending", "new details", None
    )
    assert repo.saved


@pytest.mark.parametrize(
    ("account", "code"),
    [
        (Account(role="member"), MemberCode.ALREADY_MEMBER),
        (Account(role="admin"), MemberCode.ALREADY_MEMBER),
        (Account(member_apply_status="pending"), MemberCode.APPLICATION_PENDING),
    ],
)
def test_invalid_applications_do_not_persist(account, code):
    repo = Repository(account)
    with pytest.raises(MemberError) as error:
        MembershipCommands(repo, lambda _: True, NOW).apply(account, None)
    assert error.value.code == code
    assert not repo.saved


def test_approval_commits_before_notification():
    repo = Repository(Account(member_apply_status="pending"))

    def notify(phone):
        assert repo.saved
        assert repo.audit_action == "member_approve"
        return True

    account, outcome = MembershipCommands(repo, notify, NOW).approve(7, 3)
    assert (account.role, account.member_apply_status, account.member_reviewed_by) == ("member", "approved", 3)
    assert outcome == MemberCode.APPROVED_NOTIFIED


def test_notification_failure_does_not_undo_approval():
    repo = Repository(Account(member_apply_status="pending"))

    def fail(_phone):
        raise RuntimeError("gateway unavailable")

    account, outcome = MembershipCommands(repo, fail, NOW).approve(7, 3)
    assert repo.saved
    assert account.role == "member"
    assert outcome == MemberCode.APPROVED_UNNOTIFIED


def test_reject_does_not_notify():
    repo = Repository(Account(member_apply_status="pending"))
    account = MembershipCommands(repo, lambda _: pytest.fail("unexpected SMS"), NOW).reject(7, 3)
    assert (account.role, account.member_apply_status, repo.audit_action) == (
        "user", "rejected", "member_reject"
    )


@pytest.mark.parametrize("account", [None, Account(member_apply_status="approved")])
def test_review_requires_pending_application(account):
    repo = Repository(account)
    with pytest.raises(MemberError) as error:
        MembershipCommands(repo, lambda _: True, NOW).approve(7, 3)
    assert error.value.code == (MemberCode.USER_NOT_FOUND if account is None else MemberCode.APPLICATION_NOT_PENDING)
    assert not repo.saved
