"""Slider-puzzle CAPTCHA geometry and verdict rules.

The piece body is PIECE px wide; the knob sticks out KNOB px above/beside it,
so the sprite canvas is (PIECE+KNOB)². Rendering is presentation and lives in
infrastructure; only the challenge's geometry and verdict belong here.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.shared.domain.auth import AuthError, BizCode

SLIDER_WIDTH = 320
SLIDER_HEIGHT = 160
SLIDER_PIECE = 44
SLIDER_KNOB = 10
SLIDER_TOLERANCE = 10
# The notch never sits in the leftmost strip (where the piece starts) and the
# sprite must stay fully inside the background.
SLIDER_X_RANGE = (SLIDER_WIDTH // 2, SLIDER_WIDTH - SLIDER_PIECE - 6)
SLIDER_Y_RANGE = (2, SLIDER_HEIGHT - SLIDER_PIECE - 2 * SLIDER_KNOB - 2)


class ChallengeState(Protocol):
    x: int
    tolerance: int
    is_used: bool
    verified: bool
    expires_at: datetime


@dataclass(frozen=True)
class Verdict:
    passed: bool


def evaluate(challenge: ChallengeState | None, drag_x: int, now: datetime) -> Verdict:
    """Single-use verdict: an unknown, spent or expired challenge always fails."""
    valid = (
        challenge is not None
        and not challenge.is_used
        and challenge.expires_at > now
        and abs(drag_x - challenge.x) <= challenge.tolerance
    )
    return Verdict(bool(valid))


def require_passed(verdict: Verdict) -> None:
    if not verdict.passed:
        raise AuthError(BizCode.AUTH_SLIDER_FAILED)


class TicketState(Protocol):
    verified: bool
    ticket: str | None
    ticket_expires_at: datetime | None


def ticket_consumable(record: TicketState | None, now: datetime) -> bool:
    return (
        record is not None
        and record.verified
        and record.ticket is not None
        and record.ticket_expires_at is not None
        and record.ticket_expires_at > now
    )
