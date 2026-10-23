"""Scheduled maintenance jobs for authentication and presence data."""

from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.shared.core.database import SessionLocal
from app.shared.models.login_session import LoginSession
from app.shared.models.token_blacklist import TokenBlacklist


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def cleanup_expired_tokens():
    db = SessionLocal()
    try:
        db.query(TokenBlacklist).filter(TokenBlacklist.expires_at < now()).delete()
        db.commit()
    finally:
        db.close()


def cleanup_sessions():
    db = SessionLocal()
    try:
        cutoff = now() - timedelta(minutes=5)
        db.query(LoginSession).filter(LoginSession.last_active_at < cutoff).update({"is_active": False})
        db.commit()
    finally:
        db.close()


class MaintenanceScheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone="UTC")

    def start(self):
        if self.scheduler.running:
            return
        self.scheduler.add_job(cleanup_expired_tokens, "interval", hours=1, id="token_cleanup", replace_existing=True)
        self.scheduler.add_job(cleanup_sessions, "interval", minutes=1, id="session_cleanup", replace_existing=True)
        self.scheduler.start()

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
