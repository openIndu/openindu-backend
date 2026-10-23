"""SQLAlchemy and SMS adapters for the membership context."""
import logging

from sqlalchemy.orm import Session

from app.core.utils import mask_phone
from app.models.admin_audit_log import AdminAuditLog
from app.models.login_session import LoginSession
from app.models.user import User
from app.services.sms_service import sms_service

logger = logging.getLogger(__name__)


class SqlUserStore:
    """User persistence shared by the application and administration use cases."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def find_by_id(self, user_id: int) -> User | None:
        return self.db.query(User).filter(User.id == user_id).first()

    def set_application(self, applicant: User, note: str | None, at) -> User:
        """Re-applying after a rejection overwrites the state back to pending."""
        applicant.member_apply_status = "pending"
        applicant.member_apply_note = note
        applicant.member_apply_at = at
        applicant.member_reviewed_by = None
        return self.save(applicant)

    def save(self, user: User) -> User:
        self.db.commit()
        self.db.refresh(user)
        return user


class SqlAuditLog:
    """Appends audit rows to the current transaction; the caller commits."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def record(self, admin_id: int, target_user_id: int, action: str, detail: dict | None = None) -> None:
        self.db.add(AdminAuditLog(admin_id=admin_id, target_user_id=target_user_id, action=action, detail=detail or {}))


class SqlLoginSessionDeactivator:
    """Bulk-deactivates a user's active login sessions."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def deactivate_active(self, user_id: int) -> None:
        self.db.query(LoginSession).filter(
            LoginSession.user_id == user_id,
            LoginSession.is_active.is_(True),
        ).update({"is_active": False}, synchronize_session=False)


class SmsApprovalNotifier:
    """Member-approved SMS via the shared client.

    A failed notice never raises: it logs (with the masked phone) and reports
    False, so a committed approval can never roll back because of delivery.
    """

    def send(self, phone: str) -> bool:
        try:
            return sms_service.send_member_approved(phone)
        except Exception as exc:
            logger.error("Member-approved SMS failed for %s: %s", mask_phone(phone), exc)
            return False
