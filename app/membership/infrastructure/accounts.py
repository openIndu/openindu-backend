"""SQLAlchemy account and audit adapter for membership commands."""

from sqlalchemy.orm import Session

from app.core.utils import mask_phone
from app.models.admin_audit_log import AdminAuditLog
from app.models.login_session import LoginSession
from app.models.user import User


class SqlMembershipRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def find(self, user_id: int) -> User | None:
        return self.db.query(User).filter(User.id == user_id).first()

    def save(self, account: User) -> None:
        self.db.commit()
        self.db.refresh(account)

    def save_review(self, account: User, admin_id: int, action: str) -> None:
        self.db.add(AdminAuditLog(
            admin_id=admin_id, target_user_id=account.id, action=action, detail={"user_id": account.id}
        ))
        self.save(account)

    def save_admin_action(self, account: User, admin_id: int, action: str, detail: dict) -> None:
        if "phone" in detail:
            detail = {**detail, "phone": mask_phone(detail["phone"])}
        self.db.add(AdminAuditLog(admin_id=admin_id, target_user_id=account.id, action=action, detail=detail))
        self.save(account)

    def deactivate_sessions(self, user_id: int) -> None:
        self.db.query(LoginSession).filter(
            LoginSession.user_id == user_id, LoginSession.is_active.is_(True)
        ).update({"is_active": False}, synchronize_session=False)
