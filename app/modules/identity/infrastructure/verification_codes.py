"""SQLAlchemy code store and the provider-backed SMS gateway."""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.utils import mask_phone
from app.models.sms_code import SmsCode
from app.modules.identity.application.verification_codes import SmsDeliveryError
from app.services.sms_service import sms_service

logger = logging.getLogger(__name__)


class SqlCodeStore:
    def __init__(self, db: Session) -> None:
        self.db = db

    def latest(self, phone: str) -> SmsCode | None:
        return self.db.query(SmsCode).filter(SmsCode.phone == phone).order_by(SmsCode.created_at.desc()).first()

    def count_since(self, phone: str, since: datetime) -> int:
        return self.db.query(SmsCode).filter(SmsCode.phone == phone, SmsCode.created_at >= since).count()

    def latest_active(self, phone: str, now: datetime) -> SmsCode | None:
        return (
            self.db.query(SmsCode)
            .filter(
                SmsCode.phone == phone,
                SmsCode.is_used.is_(False),
                SmsCode.expires_at > now,
            )
            .order_by(SmsCode.created_at.desc())
            .first()
        )

    def add(self, phone: str, code: str, expires_at: datetime, now: datetime) -> None:
        self.db.add(SmsCode(phone=phone, code=code, expires_at=expires_at, last_sent_at=now))
        self.db.commit()

    def save(self, record: SmsCode) -> None:
        self.db.commit()


class ProviderSmsGateway:
    """Delivers through the shared SMS provider client; provider failures are
    translated so the use case can map them to a transport-independent code."""

    def send(self, phone: str, code: str) -> None:
        try:
            sms_service.send_verification_code(phone, code)
        except Exception as exc:
            logger.error("SMS send failed to %s: %s", mask_phone(phone), type(exc).__name__)
            raise SmsDeliveryError(str(exc)) from exc
