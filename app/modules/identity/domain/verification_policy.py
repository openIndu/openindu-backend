"""SMS verification-code policy: rate limits and attempt rules.

The 429 messages quote these values, so they must stay the single source of
truth (see ``app.core.err_code``).
"""
import math
from datetime import datetime

from app.shared.domain.auth import AuthError, BizCode

# Per-phone send-code limits: minimum seconds between two SMS and the
# per-natural-day cap.
RESEND_INTERVAL_SECONDS = 60
DAILY_LIMIT = 10
# A code stops verifying after this many failed attempts and is burned.
MAX_VERIFY_ATTEMPTS = 3


def require_resend_allowed(elapsed_seconds: float | None) -> None:
    if elapsed_seconds is None:
        return
    if elapsed_seconds < RESEND_INTERVAL_SECONDS:
        remaining = max(1, math.ceil(RESEND_INTERVAL_SECONDS - elapsed_seconds))
        raise AuthError(BizCode.AUTH_SMS_RATE_LIMIT, remaining=remaining)


def require_daily_quota(sent_today: int) -> None:
    if sent_today >= DAILY_LIMIT:
        raise AuthError(BizCode.AUTH_SMS_DAILY_LIMIT, limit=DAILY_LIMIT)


def day_start(now: datetime) -> datetime:
    """Natural-day boundary used for the daily quota (naive, like ``now``)."""
    return datetime(now.year, now.month, now.day)


def attempts_exhausted(failed_attempts: int) -> bool:
    return failed_attempts > MAX_VERIFY_ATTEMPTS
