"""SMS verification-code use cases: send (rate-limited) and verify."""
import random
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from app.domain.auth import AuthError, BizCode
from app.modules.identity.domain import verification_policy as policy
from app.modules.identity.domain.phone import require_valid_phone

CODE_LIFETIME_SECONDS = 300

NowFn = Callable[[], datetime]


class SmsDeliveryError(Exception):
    """The provider rejected or failed to deliver a verification code."""


class CodeRecord(Protocol):
    code: str
    is_used: bool
    verify_attempts: int


class CodeStore(Protocol):
    def latest(self, phone: str) -> CodeRecord | None: ...

    def count_since(self, phone: str, since: datetime) -> int: ...

    def latest_active(self, phone: str, now: datetime) -> CodeRecord | None: ...

    def add(self, phone: str, code: str, expires_at: datetime, now: datetime) -> None: ...

    def save(self, record: CodeRecord) -> None: ...


class SmsGateway(Protocol):
    def send(self, phone: str, code: str) -> None: ...


class TicketGate(Protocol):
    def execute(self, ticket: str | None) -> None: ...


class SendVerificationCode:
    """Validate, rate-limit, gate on CAPTCHA, then persist and deliver a code.

    The ticket gate runs after the rate limits so a 429 does not burn the
    user's already-verified ticket.
    """

    def __init__(
        self,
        codes: CodeStore,
        gateway: SmsGateway,
        ticket_gate: TicketGate | None,
        rng: random.Random,
        now: NowFn,
        mock_code: str | None = None,
    ) -> None:
        self.codes = codes
        self.gateway = gateway
        self.ticket_gate = ticket_gate
        self.rng = rng
        self._now = now
        # When set, the code is fixed and nothing goes out over the wire.
        self.mock_code = mock_code

    def execute(self, phone: str, slider_ticket: str | None = None) -> None:
        require_valid_phone(phone)
        now = self._now()
        latest = self.codes.latest(phone)
        elapsed = (now - latest.created_at).total_seconds() if latest and latest.created_at else None
        policy.require_resend_allowed(elapsed)
        policy.require_daily_quota(self.codes.count_since(phone, policy.day_start(now)))
        if self.ticket_gate is not None:
            self.ticket_gate.execute(slider_ticket)
        code = self.mock_code if self.mock_code is not None else f"{self.rng.randint(0, 999999):06d}"
        self.codes.add(phone, code, now + timedelta(seconds=CODE_LIFETIME_SECONDS), now)
        if self.mock_code is None:
            try:
                self.gateway.send(phone, code)
            except SmsDeliveryError as exc:
                raise AuthError(BizCode.AUTH_SMS_FAILED) from exc


class VerifySmsCode:
    """Check a candidate code; returns bool so authentication can stay decoupled."""

    def __init__(self, codes: CodeStore, now: NowFn, mock_code: str | None = None) -> None:
        self.codes = codes
        self._now = now
        self.mock_code = mock_code

    def __call__(self, phone: str, code: str) -> bool:
        return self.execute(phone, code)

    def execute(self, phone: str, code: str) -> bool:
        require_valid_phone(phone)
        if self.mock_code is not None and code == self.mock_code:
            return True
        record = self.codes.latest_active(phone, self._now())
        if not record:
            return False
        record.verify_attempts += 1
        if policy.attempts_exhausted(record.verify_attempts):
            record.is_used = True
            self.codes.save(record)
            return False
        ok = record.code == code
        if ok:
            record.is_used = True
        self.codes.save(record)
        return ok
