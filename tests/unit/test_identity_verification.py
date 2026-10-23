"""Identity verification behavior: phone rules, SMS codes, slider CAPTCHA."""
import random
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from app.core.utils import utcnow
from app.domain.auth import AuthError, BizCode
from app.modules.identity.application.captcha import ConsumeSliderTicket, IssueSliderChallenge, VerifySliderChallenge
from app.modules.identity.application.verification_codes import SendVerificationCode, SmsDeliveryError, VerifySmsCode
from app.modules.identity.domain.phone import require_valid_phone
from app.modules.identity.domain.verification_policy import DAILY_LIMIT, RESEND_INTERVAL_SECONDS
from app.modules.identity.infrastructure.slider_challenges import PilChallengeRenderer, SqlSliderChallengeRepository


class FakeCodeStore:
    def __init__(self, latest=None, today_count=0, active=None):
        self._latest = latest
        self._today_count = today_count
        self._active = active
        self.added = []
        self.saved = []

    def latest(self, phone):
        return self._latest

    def count_since(self, phone, since):
        return self._today_count

    def latest_active(self, phone, now):
        return self._active

    def add(self, phone, code, expires_at, now):
        self.added.append((phone, code))

    def save(self, record):
        self.saved.append(record)


class NoGateway:
    def send(self, phone, code):
        raise AssertionError("gateway must not be called in mock mode")


class TestPhoneValidation:
    def test_valid_phone(self):
        require_valid_phone("13800138000")

    def test_invalid_phone_too_short(self):
        with pytest.raises(AuthError, match=BizCode.AUTH_INVALID_PHONE.value):
            require_valid_phone("1380013800")

    def test_invalid_phone_letters(self):
        with pytest.raises(AuthError, match=BizCode.AUTH_INVALID_PHONE.value):
            require_valid_phone("1380013800a")

    def test_invalid_phone_non_1_start(self):
        with pytest.raises(AuthError, match=BizCode.AUTH_INVALID_PHONE.value):
            require_valid_phone("23800138000")


class TestSendCode:
    def test_first_send_persists_a_code(self):
        store = FakeCodeStore()
        SendVerificationCode(store, NoGateway(), None, random.Random(1), utcnow, mock_code="888888").execute(
            "13800138000"
        )
        assert store.added and store.added[0][0] == "13800138000"

    def test_send_within_resend_interval_is_rate_limited(self):
        recent = MagicMock(created_at=utcnow() - timedelta(seconds=RESEND_INTERVAL_SECONDS - 30))
        store = FakeCodeStore(latest=recent)
        with pytest.raises(AuthError, match=BizCode.AUTH_SMS_RATE_LIMIT.value):
            SendVerificationCode(store, NoGateway(), None, random.Random(1), utcnow, mock_code="888888").execute(
                "13800138000"
            )
        assert not store.added

    def test_daily_quota_blocks_the_eleventh_send(self):
        store = FakeCodeStore(today_count=DAILY_LIMIT)
        with pytest.raises(AuthError, match=BizCode.AUTH_SMS_DAILY_LIMIT.value):
            SendVerificationCode(store, NoGateway(), None, random.Random(1), utcnow, mock_code="888888").execute(
                "13800138000"
            )

    def test_gateway_failure_maps_to_sms_failed(self):
        class BrokenGateway:
            def send(self, phone, code):
                raise SmsDeliveryError("provider down")

        store = FakeCodeStore()
        with pytest.raises(AuthError, match=BizCode.AUTH_SMS_FAILED.value):
            SendVerificationCode(store, BrokenGateway(), None, random.Random(1), utcnow).execute("13800138000")

    def test_ticket_gate_runs_before_persisting(self):
        class SpentGate:
            def execute(self, ticket):
                raise AuthError(BizCode.AUTH_SLIDER_REQUIRED)

        store = FakeCodeStore()
        with pytest.raises(AuthError, match=BizCode.AUTH_SLIDER_REQUIRED.value):
            SendVerificationCode(store, NoGateway(), SpentGate(), random.Random(1), utcnow, mock_code="888888").execute(
                "13800138000"
            )
        assert not store.added


class TestVerifyCode:
    def make_active(self, code="888888", verify_attempts=0):
        return MagicMock(code=code, is_used=False, verify_attempts=verify_attempts)

    def test_correct_code_verifies_and_burns(self):
        record = self.make_active()
        store = FakeCodeStore(active=record)
        assert VerifySmsCode(store, utcnow).execute("13800138000", "888888") is True
        assert record.is_used is True

    def test_wrong_code_fails_without_burning(self):
        record = self.make_active()
        store = FakeCodeStore(active=record)
        assert VerifySmsCode(store, utcnow).execute("13800138000", "000000") is False
        assert record.is_used is False

    def test_missing_code_fails(self):
        assert VerifySmsCode(FakeCodeStore(), utcnow).execute("13800138000", "888888") is False

    def test_too_many_attempts_burns_the_code(self):
        record = self.make_active(verify_attempts=3)
        store = FakeCodeStore(active=record)
        assert VerifySmsCode(store, utcnow).execute("13800138000", "888888") is False
        assert record.is_used is True

    def test_mock_code_always_passes_without_the_store(self):
        assert VerifySmsCode(FakeCodeStore(), utcnow, mock_code="888888").execute("13800138000", "888888") is True


class TestSliderCaptcha:
    """Challenge issuance, verification, ticket consumption — against real rows."""

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

    @pytest.fixture()
    def challenges(self, db_session):
        return SqlSliderChallengeRepository(db_session)

    def test_issue_creates_a_challenge_row(self, challenges, db_session):
        from app.models.slider_challenge import SliderChallenge

        result = IssueSliderChallenge(challenges, PilChallengeRenderer(), random.Random(7), utcnow).execute(ip="1.2.3.4")

        assert result.challenge_id > 0
        assert result.background.startswith("data:image/png;base64,")
        assert result.piece.startswith("data:image/png;base64,")
        assert result.expires_in == 120
        row = db_session.query(SliderChallenge).filter(SliderChallenge.id == result.challenge_id).one()
        assert row.ip_address == "1.2.3.4"
        assert row.is_used is False
        assert row.verified is False
        assert 0 < row.x < 320
        assert 0 <= row.y < 160

    def test_exact_and_within_tolerance_pass_exactly_once(self, challenges, db_session):
        from app.models.slider_challenge import SliderChallenge

        issue = IssueSliderChallenge(challenges, PilChallengeRenderer(), random.Random(7), utcnow)
        verify = VerifySliderChallenge(challenges, None, utcnow)
        result = issue.execute()
        row = db_session.query(SliderChallenge).filter(SliderChallenge.id == result.challenge_id).one()

        ticket = verify.execute(result.challenge_id, row.x)
        assert ticket.ticket
        assert ticket.expires_in == 300
        with pytest.raises(AuthError, match=BizCode.AUTH_SLIDER_FAILED.value):
            verify.execute(result.challenge_id, row.x)

    def test_out_of_tolerance_fails_and_consumes(self, challenges, db_session):
        from app.models.slider_challenge import SliderChallenge
        from app.modules.identity.domain.captcha import SLIDER_TOLERANCE

        issue = IssueSliderChallenge(challenges, PilChallengeRenderer(), random.Random(7), utcnow)
        verify = VerifySliderChallenge(challenges, None, utcnow)
        result = issue.execute()
        row = db_session.query(SliderChallenge).filter(SliderChallenge.id == result.challenge_id).one()

        with pytest.raises(AuthError, match=BizCode.AUTH_SLIDER_FAILED.value):
            verify.execute(result.challenge_id, row.x + SLIDER_TOLERANCE + 1)
        db_session.refresh(row)
        assert row.is_used is True
        assert row.verified is False

    def test_unknown_challenge_fails(self, challenges):
        with pytest.raises(AuthError, match=BizCode.AUTH_SLIDER_FAILED.value):
            VerifySliderChallenge(challenges, None, utcnow).execute(999999, 100)

    def test_expired_challenge_fails(self, challenges, db_session):
        from app.models.slider_challenge import SliderChallenge

        challenge = SliderChallenge(x=200, y=50, expires_at=utcnow() - timedelta(minutes=1))
        db_session.add(challenge)
        db_session.commit()
        db_session.refresh(challenge)

        with pytest.raises(AuthError, match=BizCode.AUTH_SLIDER_FAILED.value):
            VerifySliderChallenge(challenges, None, utcnow).execute(challenge.id, 200)

    def test_ticket_gates_exactly_one_send(self, challenges, db_session):
        from app.models.slider_challenge import SliderChallenge
        from app.models.sms_code import SmsCode
        from app.modules.identity.infrastructure.verification_codes import SqlCodeStore

        issue = IssueSliderChallenge(challenges, PilChallengeRenderer(), random.Random(7), utcnow)
        verify = VerifySliderChallenge(challenges, None, utcnow)
        consume = ConsumeSliderTicket(challenges, utcnow)
        send = SendVerificationCode(
            SqlCodeStore(db_session), NoGateway(), consume, random.Random(1), utcnow, mock_code="888888"
        )

        result = issue.execute()
        row = db_session.query(SliderChallenge).filter(SliderChallenge.id == result.challenge_id).one()
        ticket = verify.execute(result.challenge_id, row.x).ticket

        send.execute("13800138000", ticket)
        assert db_session.query(SmsCode).filter(SmsCode.phone == "13800138000").count() == 1
        with pytest.raises(AuthError, match=BizCode.AUTH_SLIDER_REQUIRED.value):
            send.execute("13800138001", ticket)

    def test_enforced_gate_requires_a_ticket(self, challenges, db_session):
        from app.models.sms_code import SmsCode

        db_session.query(SmsCode).delete()
        db_session.commit()
        store = FakeCodeStore()
        send = SendVerificationCode(store, NoGateway(), ConsumeSliderTicket(challenges, utcnow), random.Random(1), utcnow, mock_code="888888")

        with pytest.raises(AuthError, match=BizCode.AUTH_SLIDER_REQUIRED.value):
            send.execute("13800138000")
        assert db_session.query(SmsCode).filter(SmsCode.phone == "13800138000").count() == 0

    def test_disabled_gate_skips_the_ticket(self, challenges, db_session):
        from app.models.sms_code import SmsCode
        from app.modules.identity.infrastructure.verification_codes import SqlCodeStore

        db_session.query(SmsCode).delete()
        db_session.commit()
        SendVerificationCode(
            SqlCodeStore(db_session), NoGateway(), None, random.Random(1), utcnow, mock_code="888888"
        ).execute("13800138000")
        assert db_session.query(SmsCode).filter(SmsCode.phone == "13800138000").count() == 1
