"""SQLAlchemy account and audit adapter for membership commands."""

from sqlalchemy.orm import Session

from app.models.admin_audit_log import AdminAuditLog
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
