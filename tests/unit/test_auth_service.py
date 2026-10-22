"""Unit tests for AuthService — SMS code send/verify, token creation, login/register."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from jose import jwt

from app.core.config import settings
from app.domain.auth import AuthError, BizCode
from app.models.sms_code import SmsCode
from app.models.token_blacklist import TokenBlacklist
from app.models.user import User
from app.services.auth_service import AuthService, auth_service, utcnow


def make_user(id=1, phone="13800138000", role="user", active=True, blacklisted=False):
    """Helper to create a User instance for tests."""
    user = User(
        id=id,
        phone=phone,
        role=role,
        is_active=active,
        is_blacklisted=blacklisted,
        created_at=utcnow(),
        last_login=utcnow(),
    )
    return user


def make_sms_code(phone="13800138000", code="888888", expires_offset_minutes=5,
                  is_used=False, verify_attempts=0, created_at=None):
    """Helper to create an SmsCode instance for tests."""
    now = created_at or utcnow()
    return SmsCode(
        phone=phone,
        code=code,
        expires_at=now + timedelta(minutes=expires_offset_minutes),
        last_sent_at=now,
        verify_attempts=verify_attempts,
        is_used=is_used,
        created_at=now,
    )


class TestPhoneValidation:
    """Phone number format validation."""

    def test_valid_phone(self):
        """Valid Chinese mobile numbers should pass."""
        auth_service._validate_phone("13800138000")

    def test_invalid_phone_too_short(self):
        with pytest.raises(AuthError, match=BizCode.AUTH_INVALID_PHONE.value):
            auth_service._validate_phone("1380013800")

    def test_invalid_phone_letters(self):
        with pytest.raises(AuthError, match=BizCode.AUTH_INVALID_PHONE.value):
            auth_service._validate_phone("1380013800a")

    def test_invalid_phone_non_1_start(self):
        with pytest.raises(AuthError, match=BizCode.AUTH_INVALID_PHONE.value):
            auth_service._validate_phone("23800138000")


class TestSendCode:
    """SMS code sending logic."""

    @pytest.fixture(autouse=True)
    def disable_captcha(self, monkeypatch):
        """These tests exercise the SMS-side limits via a MagicMock db; the
        captcha gate runs against the same mock and rejects it, so turn the
        gate off here — TestCaptcha covers it against a real session."""
        monkeypatch.setattr(settings, "CAPTCHA_ENFORCED", False)

    def test_send_code_success(self):
        """First send_code should succeed."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        auth_service.send_code(mock_db, "13800138000")
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_send_code_rate_limit(self):
        """Sending within 60 seconds should be rate-limited."""
        mock_db = MagicMock()
        recent_code = make_sms_code(created_at=utcnow() - timedelta(seconds=30))
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = recent_code

        with pytest.raises(AuthError, match=BizCode.AUTH_SMS_RATE_LIMIT.value):
            auth_service.send_code(mock_db, "13800138000")

    def test_send_code_daily_limit(self):
        """Sending more than 10 times a day should be blocked."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.count.return_value = 10

        with pytest.raises(AuthError, match=BizCode.AUTH_SMS_DAILY_LIMIT.value):
            auth_service.send_code(mock_db, "13800138000")


class TestVerifyCode:
    """SMS code verification logic."""

    def test_verify_code_success(self):
        """Correct code should verify successfully (mock mode disabled)."""
        mock_db = MagicMock()
        code_record = make_sms_code(code="888888")
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = code_record

        with patch.object(settings, "SMS_MOCK_ENABLED", False):
            result = auth_service.verify_code(mock_db, "13800138000", "888888")
        assert result is True
        assert code_record.is_used is True

    def test_verify_code_wrong_code(self):
        """Wrong code should fail verification."""
        mock_db = MagicMock()
        code_record = make_sms_code(code="888888")
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = code_record

        with patch.object(settings, "SMS_MOCK_ENABLED", False):
            result = auth_service.verify_code(mock_db, "13800138000", "000000")
        assert result is False

    def test_verify_code_expired(self):
        """Expired code (no record found) should fail."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        with patch.object(settings, "SMS_MOCK_ENABLED", False):
            result = auth_service.verify_code(mock_db, "13800138000", "888888")
        assert result is False

    def test_verify_code_too_many_attempts(self):
        """After 3 failed attempts, the code should be marked used."""
        mock_db = MagicMock()
        code_record = make_sms_code(code="888888", verify_attempts=3)
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = code_record

        with patch.object(settings, "SMS_MOCK_ENABLED", False):
            result = auth_service.verify_code(mock_db, "13800138000", "888888")
        assert result is False
        assert code_record.is_used is True

    def test_verify_mock_code(self):
        """In mock mode, the mock code should always pass."""
        mock_db = MagicMock()
        with patch.object(settings, "SMS_MOCK_ENABLED", True):
            result = auth_service.verify_code(mock_db, "13800138000", settings.SMS_MOCK_CODE)
            assert result is True


class TestCreateToken:
    """JWT token creation."""

    def test_create_token_pair(self):
        """create_token_pair should return access + refresh tokens with correct structure."""
        user = make_user()
        tokens = auth_service.create_token_pair(user)

        assert "access_token" in tokens
        assert "refresh_token" in tokens
        assert tokens["token_type"] == "bearer"
        assert tokens["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

        # Decode the tokens
        access_payload = jwt.decode(
            tokens["access_token"], settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        assert access_payload["sub"] == "1"
        assert access_payload["phone"] == "13800138000"
        assert access_payload["role"] == "user"
        assert access_payload["type"] == "access"

        refresh_payload = jwt.decode(
            tokens["refresh_token"], settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        assert refresh_payload["type"] == "refresh"

    def test_token_includes_jti(self):
        """Each token should have a unique jti."""
        user = make_user()
        tokens = auth_service.create_token_pair(user)
        access_payload = jwt.decode(
            tokens["access_token"], settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        refresh_payload = jwt.decode(
            tokens["refresh_token"], settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        assert access_payload["jti"] != refresh_payload["jti"]


class TestLogin:
    """Login flow."""

    def test_login_success(self):
        """Login with valid code should return user + tokens."""
        mock_db = MagicMock()

        # Patch verify_code to return True
        with patch.object(auth_service, "verify_code", return_value=True):
            user = make_user()
            mock_db.query.return_value.filter.return_value.first.return_value = user

            result = auth_service.login(mock_db, "13800138000", "888888")
            assert result["user"]["phone"] == "13800138000"
            assert "access_token" in result["tokens"]

    def test_login_user_not_found(self):
        """Login with unknown phone should raise 404."""
        mock_db = MagicMock()

        with patch.object(auth_service, "verify_code", return_value=True):
            mock_db.query.return_value.filter.return_value.first.return_value = None

            with pytest.raises(AuthError, match=BizCode.AUTH_USER_NOT_FOUND.value):
                auth_service.login(mock_db, "13800138000", "888888")

    def test_login_blacklisted(self):
        """Blacklisted user should not be able to login."""
        mock_db = MagicMock()

        with patch.object(auth_service, "verify_code", return_value=True):
            user = make_user(blacklisted=True)
            mock_db.query.return_value.filter.return_value.first.return_value = user

            with pytest.raises(AuthError, match=BizCode.AUTH_ACCOUNT_DISABLED.value):
                auth_service.login(mock_db, "13800138000", "888888")


class TestRegister:
    """Registration flow."""

    def test_register_success(self):
        """First user registration should create admin role."""
        mock_db = MagicMock()

        with patch.object(auth_service, "verify_code", return_value=True):
            # No existing user
            mock_db.query.return_value.filter.return_value.first.return_value = None
            # Count returns 0 (first user)
            mock_db.query.return_value.scalar.return_value = 0

            result = auth_service.register(mock_db, "13800138000", "888888")
            # The first user should be admin
            args, _ = mock_db.add.call_args
            added_user = args[0]
            assert added_user.role == "admin"
            assert added_user.phone == "13800138000"
            assert result["user"]["phone"] == "13800138000"

    def test_register_existing_user(self):
        """Registering with existing phone should raise 409."""
        mock_db = MagicMock()

        with patch.object(auth_service, "verify_code", return_value=True):
            existing_user = make_user()
            mock_db.query.return_value.filter.return_value.first.return_value = existing_user

            with pytest.raises(AuthError, match=BizCode.AUTH_USER_EXISTS.value):
                auth_service.register(mock_db, "13800138000", "888888")


class TestSignInOutcome:
    def test_new_account_has_explicit_outcome(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        db.query.return_value.scalar.return_value = 1
        with patch.object(auth_service, "verify_code", return_value=True), patch.object(
            auth_service, "create_token_pair", return_value={"access_token": "token"}
        ):
            account, outcome = auth_service.sign_in(db, "13800138000", "888888")
        assert outcome == BizCode.AUTH_ACCOUNT_CREATED
        assert account["is_new_user"] is True

    def test_existing_account_has_login_outcome(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = make_user()
        with patch.object(auth_service, "verify_code", return_value=True), patch.object(
            auth_service, "create_token_pair", return_value={"access_token": "token"}
        ):
            account, outcome = auth_service.sign_in(db, "13800138000", "888888")
        assert outcome == BizCode.AUTH_LOGIN_SUCCESS
        assert account["is_new_user"] is False


class TestBlacklistToken:
    """Token blacklisting."""

    def test_blacklist_token(self):
        """Blacklisting a valid token should add it to the blacklist."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None  # not already blacklisted

        payload = {"jti": "abc123", "sub": "1", "type": "access", "exp": 9999999999}
        auth_service.blacklist_token(mock_db, payload)
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_blacklist_duplicate_token(self):
        """Blacklisting an already blacklisted token should be a no-op."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = TokenBlacklist(jti="abc123")

        payload = {"jti": "abc123", "sub": "1"}
        auth_service.blacklist_token(mock_db, payload)
        mock_db.add.assert_not_called()


class TestRefreshToken:
    """Token refresh logic."""

    def test_refresh_token_success(self):
        """Valid refresh token should return new token pair."""
        mock_db = MagicMock()
        user = make_user()

        # Create a valid refresh token
        tokens = auth_service.create_token_pair(user)
        refresh_token = tokens["refresh_token"]

        # Mock: token not blacklisted (1st call), user exists (2nd call),
        # token not blacklisted again (3rd call during blacklist_token)
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            None,  # not blacklisted (check in refresh_token)
            user,  # user found
            None,  # not blacklisted (check in blacklist_token)
        ]

        result = auth_service.refresh_token(mock_db, refresh_token)
        assert "access_token" in result["tokens"]
        assert result["tokens"]["token_type"] == "bearer"

    def test_refresh_with_access_token_fails(self):
        """Using an access token for refresh should fail."""
        mock_db = MagicMock()
        user = make_user()
        tokens = auth_service.create_token_pair(user)

        with pytest.raises(AuthError, match=BizCode.AUTH_REFRESH_INVALID.value):
            auth_service.refresh_token(mock_db, tokens["access_token"])


class TestLogout:
    """Logout flow."""

    def test_logout(self):
        """Logout should blacklist the token."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        user = make_user()
        tokens = auth_service.create_token_pair(user)

        auth_service.logout(mock_db, tokens["access_token"])
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


class TestSliderCaptcha:
    """Slider-puzzle challenge issuance, verification, ticket consumption
    and the send-code gate."""

    @pytest.fixture()
    def db_session(self):
        """Real SQLite session (tables created by conftest) with slider rows cleared."""
        from app.core.database import SessionLocal
        from app.models.slider_challenge import SliderChallenge

        session = SessionLocal()
        session.query(SliderChallenge).delete()
        session.commit()
        yield session
        session.query(SliderChallenge).delete()
        session.commit()
        session.close()

    def test_issue_slider_challenge_creates_challenge(self, db_session):
        """issue_slider_challenge returns images + geometry backed by a DB row."""
        from app.models.slider_challenge import SliderChallenge

        result = auth_service.issue_slider_challenge(db_session, ip="1.2.3.4")

        assert result["challenge_id"] > 0
        assert result["background"].startswith("data:image/png;base64,")
        assert result["piece"].startswith("data:image/png;base64,")
        assert result["expires_in"] == 120
        row = db_session.query(SliderChallenge).filter(SliderChallenge.id == result["challenge_id"]).one()
        assert row.ip_address == "1.2.3.4"
        assert row.is_used is False
        assert row.verified is False
        assert 0 < row.x < 320
        assert 0 <= row.y < 160

    def test_verify_slider_accepts_exact_and_within_tolerance(self, db_session):
        """Exact hit and ±tolerance both pass once, then the challenge is spent."""
        from app.models.slider_challenge import SliderChallenge

        result = auth_service.issue_slider_challenge(db_session)
        row = db_session.query(SliderChallenge).filter(
            SliderChallenge.id == result["challenge_id"]).one()

        ticket = auth_service.verify_slider_challenge(db_session, result["challenge_id"], row.x)
        assert ticket["ticket"]
        assert ticket["expires_in"] == 300
        with pytest.raises(AuthError, match=BizCode.AUTH_SLIDER_FAILED.value):
            auth_service.verify_slider_challenge(db_session, result["challenge_id"], row.x)

    def test_verify_slider_rejects_out_of_tolerance(self, db_session):
        """A drag distance beyond ±tolerance fails and consumes the challenge."""
        from app.models.slider_challenge import SliderChallenge
        from app.services.auth_service import SLIDER_TOLERANCE

        result = auth_service.issue_slider_challenge(db_session)
        row = db_session.query(SliderChallenge).filter(
            SliderChallenge.id == result["challenge_id"]).one()

        with pytest.raises(AuthError, match=BizCode.AUTH_SLIDER_FAILED.value):
            auth_service.verify_slider_challenge(
                db_session, result["challenge_id"], row.x + SLIDER_TOLERANCE + 1)
        db_session.refresh(row)
        assert row.is_used is True
        assert row.verified is False

    def test_verify_slider_rejects_missing_challenge(self, db_session):
        with pytest.raises(AuthError, match=BizCode.AUTH_SLIDER_FAILED.value):
            auth_service.verify_slider_challenge(db_session, 999999, 100)

    def test_verify_slider_rejects_expired(self, db_session):
        from app.models.slider_challenge import SliderChallenge

        challenge = SliderChallenge(x=200, y=50, expires_at=utcnow() - timedelta(minutes=1))
        db_session.add(challenge)
        db_session.commit()
        db_session.refresh(challenge)

        with pytest.raises(AuthError, match=BizCode.AUTH_SLIDER_FAILED.value):
            auth_service.verify_slider_challenge(db_session, challenge.id, 200)

    def test_ticket_single_use_for_send_code(self, db_session):
        """The ticket gates exactly one send_code; the second use fails."""
        from app.models.slider_challenge import SliderChallenge
        from app.models.sms_code import SmsCode

        result = auth_service.issue_slider_challenge(db_session)
        row = db_session.query(SliderChallenge).filter(
            SliderChallenge.id == result["challenge_id"]).one()
        ticket = auth_service.verify_slider_challenge(db_session, result["challenge_id"], row.x)["ticket"]

        auth_service.send_code(db_session, "13800138000", ticket)
        assert db_session.query(SmsCode).filter(SmsCode.phone == "13800138000").count() == 1
        with pytest.raises(AuthError, match=BizCode.AUTH_SLIDER_REQUIRED.value):
            auth_service.send_code(db_session, "13800138001", ticket)

    def test_send_code_requires_slider_ticket_when_enforced(self, db_session, monkeypatch):
        """With CAPTCHA_ENFORCED, send_code without a ticket fails before
        touching the SMS tables."""
        from app.models.sms_code import SmsCode

        monkeypatch.setattr(settings, "CAPTCHA_ENFORCED", True)
        db_session.query(SmsCode).delete()
        db_session.commit()

        with pytest.raises(AuthError, match=BizCode.AUTH_SLIDER_REQUIRED.value):
            auth_service.send_code(db_session, "13800138000")
        assert db_session.query(SmsCode).filter(SmsCode.phone == "13800138000").count() == 0

    def test_send_code_skips_captcha_when_disabled(self, db_session, monkeypatch):
        """CAPTCHA_ENFORCED=false bypasses the gate (prod escape hatch)."""
        from app.models.sms_code import SmsCode

        monkeypatch.setattr(settings, "CAPTCHA_ENFORCED", False)
        db_session.query(SmsCode).delete()
        db_session.commit()

        auth_service.send_code(db_session, "13800138000")
        assert db_session.query(SmsCode).filter(SmsCode.phone == "13800138000").count() == 1
