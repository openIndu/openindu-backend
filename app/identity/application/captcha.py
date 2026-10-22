"""Slider CAPTCHA use cases: issue, verify, and gate send-code on a ticket."""
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.domain.auth import AuthError, BizCode
from app.identity.domain import captcha as captcha_rules

CHALLENGE_LIFETIME_SECONDS = 120
TICKET_LIFETIME_SECONDS = 300

NowFn = Callable[[], datetime]


class Challenge(Protocol):
    id: int
    x: int
    y: int
    tolerance: int
    is_used: bool
    verified: bool
    ticket: str | None
    expires_at: datetime
    ticket_expires_at: datetime | None


class ChallengeRepository(Protocol):
    def purge_expired(self, now: datetime) -> None: ...

    def create(self, x: int, y: int, tolerance: int, expires_at: datetime, ip: str | None) -> Challenge: ...

    def find(self, challenge_id: int) -> Challenge | None: ...

    def save(self, challenge: Challenge) -> None: ...

    def find_ticket(self, ticket: str, now: datetime) -> Challenge | None: ...


class ChallengeRenderer(Protocol):
    def render(self, x: int, y: int) -> tuple[str, str]: ...


@dataclass(frozen=True)
class IssuedChallenge:
    challenge_id: int
    background: str
    piece: str
    piece_y: int
    expires_in: int


@dataclass(frozen=True)
class SliderTicket:
    ticket: str
    expires_in: int


class IssueSliderChallenge:
    """Create a fresh puzzle; opportunistic cleanup keeps the table small."""

    def __init__(
        self,
        challenges: ChallengeRepository,
        renderer: ChallengeRenderer,
        rng: random.Random,
        now: NowFn | None = None,
    ) -> None:
        self.challenges = challenges
        self.renderer = renderer
        self.rng = rng
        self._now: NowFn = now or (lambda: datetime.now().replace(tzinfo=None))

    def execute(self, ip: str | None = None) -> IssuedChallenge:
        now = self._now()
        self.challenges.purge_expired(now)
        x = self.rng.randint(*captcha_rules.SLIDER_X_RANGE)
        y = self.rng.randint(*captcha_rules.SLIDER_Y_RANGE)
        challenge = self.challenges.create(
            x, y, captcha_rules.SLIDER_TOLERANCE, now + timedelta(seconds=CHALLENGE_LIFETIME_SECONDS), ip
        )
        background, piece = self.renderer.render(x, y)
        return IssuedChallenge(challenge.id, background, piece, y, CHALLENGE_LIFETIME_SECONDS)


class VerifySliderChallenge:
    """Check the drag distance and hand out a one-time ticket.

    The challenge is single-use regardless of outcome — consumed even on a
    miss — so a captured id cannot be retried or brute-forced beyond its
    lifetime and the rate limit.
    """

    def __init__(
        self,
        challenges: ChallengeRepository,
        new_ticket: Callable[[], str] | None = None,
        now: NowFn | None = None,
    ) -> None:
        self.challenges = challenges
        self._new_ticket = new_ticket or (lambda: uuid.uuid4().hex)
        self._now: NowFn = now or (lambda: datetime.now().replace(tzinfo=None))

    def execute(self, challenge_id: int, drag_x: int) -> SliderTicket:
        now = self._now()
        record = self.challenges.find(challenge_id)
        verdict = captcha_rules.evaluate(record, drag_x, now)
        if record is not None:
            if verdict.passed:
                record.verified = True
                record.ticket = self._new_ticket()
                record.ticket_expires_at = now + timedelta(seconds=TICKET_LIFETIME_SECONDS)
            # Either way the challenge is spent; a verified row keeps its
            # ticket for /auth/send-code to consume.
            record.is_used = True
            self.challenges.save(record)
        captcha_rules.require_passed(verdict)
        return SliderTicket(record.ticket, TICKET_LIFETIME_SECONDS)  # type: ignore[index]


class ConsumeSliderTicket:
    """Consume a verified ticket; raises when missing, stale or already spent."""

    def __init__(self, challenges: ChallengeRepository, now: NowFn | None = None) -> None:
        self.challenges = challenges
        self._now: NowFn = now or (lambda: datetime.now().replace(tzinfo=None))

    def execute(self, ticket: str | None) -> None:
        record = self.challenges.find_ticket(ticket, self._now()) if ticket else None
        if record is not None:
            record.ticket = None
            self.challenges.save(record)
        if record is None:
            raise AuthError(BizCode.AUTH_SLIDER_REQUIRED)
