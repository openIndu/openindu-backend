"""Unit tests for the membership context rules and use cases."""
from types import SimpleNamespace

import pytest

from app.modules.membership.application.administration import (
    BlacklistUser,
    ChangeRole,
    DeleteUser,
    ForceLogout,
    UnblacklistUser,
)
from app.modules.membership.application.review import ReviewApplication
from app.modules.membership.domain import rules
from app.shared.domain.membership import MemberCode, MemberError

NOW = SimpleNamespace(year=2026, month=10, day=22)


def make_user(uid=1, phone="13900000000", role="user", **kw):
    user = SimpleNamespace(
        id=uid,
        phone=phone,
        nickname=None,
        role=role,
        is_active=True,
        is_blacklisted=False,
        blacklisted_at=None,
        blacklisted_by=None,
        tokens_invalidated_at=None,
        deleted_at=None,
        member_apply_status=None,
        member_apply_note=None,
        member_apply_at=None,
        member_reviewed_by=None,
    )
    for key, value in kw.items():
        setattr(user, key, value)
    return user


class FakeUserStore:
    def __init__(self, *users):
        self.users = {u.id: u for u in users}
        self.saved = []

    def find_by_id(self, user_id):
        return self.users.get(user_id)

    def set_application(self, applicant, note, at):
        applicant.member_apply_status = "pending"
        applicant.member_apply_note = note
        applicant.member_apply_at = at
        applicant.member_reviewed_by = None
        return applicant

    def save(self, user):
        self.saved.append(user)
        return user


class FakeAudits:
    def __init__(self):
        self.records = []

    def record(self, admin_id, target_user_id, action, detail=None):
        self.records.append((admin_id, target_user_id, action, detail))


class FakeSessions:
    def __init__(self):
        self.deactivated = []

    def deactivate_active(self, user_id):
        self.deactivated.append(user_id)


class FakeNotifier:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def send(self, phone):
        self.calls.append(phone)
        return self.result


# ── rules ─────────────────────────────────────────────────────────────────────

def test_apply_blocked_for_member_and_admin():
    for role in ("member", "admin"):
        with pytest.raises(MemberError) as exc:
            rules.require_apply_allowed(role, None)
        assert exc.value.code is MemberCode.ALREADY_MEMBER


def test_apply_blocked_while_pending():
    with pytest.raises(MemberError) as exc:
        rules.require_apply_allowed("user", "pending")
    assert exc.value.code is MemberCode.APPLICATION_PENDING


def test_rejected_application_may_re_apply():
    rules.require_apply_allowed("user", "rejected")


def test_review_requires_pending_status():
    with pytest.raises(MemberError) as exc:
        rules.require_reviewable("approved")
    assert exc.value.code is MemberCode.APPLICATION_NOT_PENDING


def test_known_roles_only():
    with pytest.raises(MemberError) as exc:
        rules.require_known_role("superuser")
    assert exc.value.code is MemberCode.INVALID_ROLE
    for role in ("user", "member", "admin"):
        rules.require_known_role(role)


def test_deletable_checks_existing_deleted_and_self():
    with pytest.raises(MemberError) as missing:
        rules.require_deletable(None, admin_id=2)
    assert missing.value.code is MemberCode.USER_NOT_FOUND

    with pytest.raises(MemberError) as deleted:
        rules.require_deletable(make_user(deleted_at=NOW), admin_id=2)
    assert deleted.value.code is MemberCode.USER_ALREADY_DELETED

    with pytest.raises(MemberError) as myself:
        rules.require_deletable(make_user(uid=2), admin_id=2)
    assert myself.value.code is MemberCode.CANNOT_DELETE_SELF

    rules.require_deletable(make_user(uid=3), admin_id=2)


# ── review ────────────────────────────────────────────────────────────────────

def test_review_unknown_user():
    store = FakeUserStore()
    notifier = FakeNotifier()
    use_case = ReviewApplication(store, FakeAudits(), notifier, mask=lambda p: p)
    with pytest.raises(MemberError) as exc:
        use_case.execute(99, reviewer_id=1, approve=True)
    assert exc.value.code is MemberCode.USER_NOT_FOUND
    assert notifier.calls == []


def test_review_reject_records_audit_and_skips_sms():
    target = make_user(uid=5, member_apply_status="pending")
    store = FakeUserStore(target)
    audits = FakeAudits()
    notifier = FakeNotifier()

    view = ReviewApplication(store, audits, notifier, mask=lambda p: p).execute(5, reviewer_id=1, approve=False)

    assert view.status == "rejected"
    assert view.reviewed_by == 1
    assert target.role == "user"
    assert audits.records == [(1, 5, "member_reject", {"user_id": 5})]
    assert notifier.calls == []


def test_review_approve_upgrades_and_notifies():
    target = make_user(uid=6, member_apply_status="pending")
    store = FakeUserStore(target)
    audits = FakeAudits()
    notifier = FakeNotifier(result=True)

    view = ReviewApplication(store, audits, notifier, mask=lambda p: p).execute(6, reviewer_id=1, approve=True)

    assert view.status == "approved"
    assert view.current_role == "member"
    assert view.sms_notified is True
    assert audits.records == [(1, 6, "member_approve", {"user_id": 6})]
    assert notifier.calls == [target.phone]


# ── administration ────────────────────────────────────────────────────────────

def test_change_role_records_old_and_new():
    user = make_user(uid=7, role="user")
    store = FakeUserStore(user)
    audits = FakeAudits()

    ChangeRole(store, audits).execute(7, admin_id=1, role="member")

    assert user.role == "member"
    assert audits.records == [(1, 7, "role_change", {"old_role": "user", "new_role": "member"})]


def test_blacklist_disables_and_terminates_sessions():
    user = make_user(uid=8)
    store = FakeUserStore(user)
    sessions = FakeSessions()

    BlacklistUser(store, FakeAudits(), sessions, now=lambda: NOW).execute(8, admin_id=1)

    assert user.is_active is False
    assert user.is_blacklisted is True
    assert user.blacklisted_at is NOW
    assert user.blacklisted_by == 1
    assert user.tokens_invalidated_at is NOW
    assert sessions.deactivated == [8]


def test_unblacklist_clears_blacklist_state():
    user = make_user(uid=9, is_active=False, is_blacklisted=True, blacklisted_at=NOW, blacklisted_by=1,
                     tokens_invalidated_at=NOW)
    store = FakeUserStore(user)

    UnblacklistUser(store, FakeAudits()).execute(9, admin_id=1)

    assert user.is_active is True
    assert user.is_blacklisted is False
    assert user.blacklisted_at is None
    assert user.blacklisted_by is None
    assert user.tokens_invalidated_at is None


def test_force_logout_invalidates_tokens_only():
    user = make_user(uid=10)
    store = FakeUserStore(user)
    sessions = FakeSessions()

    ForceLogout(store, FakeAudits(), sessions, now=lambda: NOW).execute(10, admin_id=1)

    assert user.tokens_invalidated_at is NOW
    assert user.is_active is True
    assert user.is_blacklisted is False
    assert sessions.deactivated == [10]


def test_delete_soft_deletes_and_audits_masked_phone():
    user = make_user(uid=11, phone="13900001111")
    store = FakeUserStore(user)
    audits = FakeAudits()
    sessions = FakeSessions()

    DeleteUser(store, audits, sessions, now=lambda: NOW, mask=lambda p: f"<{p}>").execute(11, admin_id=2)

    assert user.deleted_at is NOW
    assert user.is_active is False
    assert user.tokens_invalidated_at is NOW
    assert sessions.deactivated == [11]
    assert audits.records == [(2, 11, "delete", {"phone": "<13900001111>"})]
