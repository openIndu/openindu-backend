"""Identity session behavior: token issuing, refresh rotation, logout, deletion."""
from dataclasses import asdict
from datetime import timedelta

import jwt
import pytest

from app.modules.identity.application.sessions import (
    ChangePhone,
    DeleteAccount,
    EndSession,
    IssueTokenPair,
    RefreshSession,
)
from app.modules.identity.domain.session_rules import require_new_phone_free
from app.modules.identity.infrastructure.tokens import JwtTokenCodec, decode_token
from app.shared.core.config import settings
from app.shared.core.utils import utcnow
from app.shared.domain.auth import AuthError, BizCode


def make_account(**overrides):
    fields = dict(
        id=1,
        phone="13800138000",
        role="user",
        is_active=True,
        is_blacklisted=False,
        blacklisted_at=None,
        tokens_invalidated_at=None,
    )
    fields.update(overrides)
    return type("Account", (), fields)()


class FakeBlacklist:
    def __init__(self, revoked=()):
        self._revoked = set(revoked)
        self.added = []

    def exists(self, jti):
        return jti in self._revoked

    def add(self, jti, user_id, token_type, reason, expires_at):
        self.added.append((jti, user_id, token_type, reason))


class FakeAccounts:
    def __init__(self, by_id=None, by_phone=None):
        self._by_id = by_id or {}
        self._by_phone = by_phone or {}

    def find_by_id(self, account_id):
        return self._by_id.get(account_id)

    def find_by_phone(self, phone):
        return self._by_phone.get(phone)

    def update_phone(self, account, phone):
        account.phone = phone
        return account

    def save(self, account):
        return account


class FakeSessions:
    def __init__(self):
        self.deactivated = []
        self.deactivated_all = []

    def deactivate(self, user_id, client_id=None, user_agent=None):
        self.deactivated.append((user_id, client_id, user_agent))

    def deactivate_all(self, user_id):
        self.deactivated_all.append(user_id)


def make_issuer():
    return IssueTokenPair(JwtTokenCodec(), None, utcnow, settings.ACCESS_TOKEN_EXPIRE_MINUTES,
                          settings.REFRESH_TOKEN_EXPIRE_DAYS)


class TestIssueTokenPair:
    def test_pair_has_access_and_refresh_with_expected_claims(self):
        pair = make_issuer().execute(make_account())
        tokens = asdict(pair)
        assert tokens["token_type"] == "bearer"
        assert tokens["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

        access = jwt.decode(pair.access_token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        assert access["sub"] == "1"
        assert access["phone"] == "13800138000"
        assert access["role"] == "user"
        assert access["type"] == "access"
        refresh = jwt.decode(pair.refresh_token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        assert refresh["type"] == "refresh"

    def test_each_token_gets_a_unique_jti(self):
        pair = make_issuer().execute(make_account())
        assert pair.access_jti != pair.refresh_jti

    def test_decode_token_roundtrip(self):
        pair = make_issuer().execute(make_account())
        assert decode_token(pair.access_token)["type"] == "access"


class TestRefreshSession:
    def make_refresh(self, account, blacklist):
        return RefreshSession(JwtTokenCodec(), blacklist, FakeAccounts(by_id={account.id: account}),
                              make_issuer(), utcnow)

    def test_valid_refresh_rotates_and_revokes(self):
        account = make_account()
        old_pair = make_issuer().execute(account)
        blacklist = FakeBlacklist()
        session = self.make_refresh(account, blacklist)

        _, new_pair = session.execute(old_pair.refresh_token)
        assert new_pair.access_token
        assert blacklist.added and blacklist.added[0][3] == "refresh_rotation"

    def test_access_token_cannot_refresh(self):
        account = make_account()
        pair = make_issuer().execute(account)
        with pytest.raises(AuthError, match=BizCode.AUTH_REFRESH_INVALID.value):
            self.make_refresh(account, FakeBlacklist()).execute(pair.access_token)

    def test_revoked_refresh_is_rejected(self):
        account = make_account()
        pair = make_issuer().execute(account)
        payload = jwt.decode(pair.refresh_token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        with pytest.raises(AuthError, match=BizCode.AUTH_REFRESH_REVOKED.value):
            self.make_refresh(account, FakeBlacklist(revoked={payload["jti"]})).execute(pair.refresh_token)

    def test_unavailable_account_is_rejected(self):
        account = make_account(is_active=False)
        pair = make_issuer().execute(account)
        with pytest.raises(AuthError, match=BizCode.AUTH_ACCOUNT_UNAVAILABLE.value):
            self.make_refresh(account, FakeBlacklist()).execute(pair.refresh_token)

    def test_tokens_invalidated_after_issuance_force_logout(self):
        account = make_account(tokens_invalidated_at=utcnow() + timedelta(seconds=5))
        pair = make_issuer().execute(account)
        with pytest.raises(AuthError, match=BizCode.AUTH_FORCED_LOGOUT.value):
            self.make_refresh(account, FakeBlacklist()).execute(pair.refresh_token)


class TestEndSession:
    def test_logout_blacklists_and_deactivates_by_client_id(self):
        account = make_account()
        pair = make_issuer().execute(account)
        blacklist = FakeBlacklist()
        sessions = FakeSessions()

        EndSession(JwtTokenCodec(), blacklist, sessions, utcnow).execute(
            pair.access_token, user_agent="UA", client_id="client-1"
        )
        assert blacklist.added and blacklist.added[0][3] == "logout"
        assert sessions.deactivated == [(1, "client-1", None)]

    def test_logout_falls_back_to_user_agent(self):
        account = make_account()
        pair = make_issuer().execute(account)
        sessions = FakeSessions()

        EndSession(JwtTokenCodec(), FakeBlacklist(), sessions, utcnow).execute(pair.access_token, user_agent="UA")
        assert sessions.deactivated == [(1, None, "UA")]


class TestDeleteAccount:
    def test_account_is_disabled_and_sessions_cleared(self):
        account = make_account()
        sessions = FakeSessions()
        result = DeleteAccount(JwtTokenCodec(), FakeBlacklist(), sessions, FakeAccounts(), utcnow).execute(
            account, make_issuer().execute(account).access_token
        )
        assert result.is_active is False
        assert result.is_blacklisted is True
        assert result.blacklisted_at is not None
        assert result.tokens_invalidated_at is not None
        assert sessions.deactivated_all == [1]


class TestChangePhone:
    def test_requires_valid_format(self):
        use_case = ChangePhone(lambda phone, code: True, FakeAccounts())
        with pytest.raises(AuthError, match=BizCode.AUTH_INVALID_PHONE.value):
            use_case.execute(make_account(), "1234", "888888")

    def test_requires_a_valid_code(self):
        use_case = ChangePhone(lambda phone, code: False, FakeAccounts())
        with pytest.raises(AuthError, match=BizCode.AUTH_INVALID_CODE.value):
            use_case.execute(make_account(), "13900139000", "888888")

    def test_rejects_a_phone_held_by_another_account(self):
        other = make_account(id=2, phone="13900139000")
        use_case = ChangePhone(lambda phone, code: True, FakeAccounts(by_phone={"13900139000": other}))
        with pytest.raises(AuthError, match=BizCode.AUTH_PHONE_EXISTS.value):
            use_case.execute(make_account(), "13900139000", "888888")

    def test_updates_the_phone(self):
        account = make_account()
        use_case = ChangePhone(lambda phone, code: True, FakeAccounts())
        assert use_case.execute(account, "13900139000", "888888").phone == "13900139000"

    def test_rule_rejects_any_existing_account(self):
        with pytest.raises(AuthError, match=BizCode.AUTH_PHONE_EXISTS.value):
            require_new_phone_free(object())
