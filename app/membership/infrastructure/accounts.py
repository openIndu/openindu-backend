"""SQLAlchemy account and audit adapter for membership commands."""

from sqlalchemy.orm import Session

from app.core.utils import _is_private, mask_phone
from app.membership.application.queries import ApplicationListCriteria, UserListCriteria
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

    def list_users(self, criteria: UserListCriteria) -> dict:
        query = self.db.query(User).filter(User.deleted_at.is_(None))
        if criteria.keyword:
            query = query.filter(User.phone.ilike(f"%{criteria.keyword}%"))
        if criteria.role:
            query = query.filter(User.role == criteria.role)
        if criteria.apply_status == "none":
            query = query.filter(User.member_apply_status.is_(None))
        elif criteria.apply_status:
            query = query.filter(User.member_apply_status == criteria.apply_status)

        sort_column = {"created_at": User.created_at, "last_login": User.last_login}[criteria.sort_by]
        query = query.order_by(sort_column.asc() if criteria.sort_order == "asc" else sort_column.desc())
        total = query.count()
        accounts = query.offset((criteria.page - 1) * criteria.size).limit(criteria.size).all()
        items = [self._enrich_user(account.to_dict(), account.id) for account in accounts]
        return {"items": items, "total": total, "page": criteria.page, "size": criteria.size}

    def list_applications(self, criteria: ApplicationListCriteria) -> dict:
        query = self.db.query(User).filter(
            User.member_apply_status.isnot(None), User.deleted_at.is_(None)
        )
        if criteria.status:
            query = query.filter(User.member_apply_status == criteria.status)

        sort_column = User.member_apply_at
        query = query.order_by(
            sort_column.asc() if criteria.sort_order == "asc" else sort_column.desc()
        )
        total = query.count()
        accounts = query.offset((criteria.page - 1) * criteria.size).limit(criteria.size).all()
        return {
            "items": accounts,
            "total": total,
            "page": criteria.page,
            "size": criteria.size,
        }

    def _enrich_user(self, user_dict: dict, user_id: int) -> dict:
        """Add active status and the most recent public IP/location to a user view."""
        active_session = self.db.query(LoginSession).filter(
            LoginSession.user_id == user_id, LoginSession.is_active.is_(True)
        ).first()
        user_dict["online"] = active_session is not None

        last_session = self.db.query(LoginSession).filter(
            LoginSession.user_id == user_id
        ).order_by(LoginSession.last_active_at.desc()).first()
        fallback_ip = last_session.ip_address if last_session else None
        fallback_location = getattr(last_session, "geo_location", None) if last_session else None

        sessions = self.db.query(LoginSession).filter(
            LoginSession.user_id == user_id
        ).order_by(LoginSession.last_active_at.desc()).limit(20).all()
        if not isinstance(sessions, list):
            sessions = []
        public_session = next(
            (session for session in sessions if session.ip_address and not _is_private(session.ip_address)), None
        )
        user_dict["login_ip"] = public_session.ip_address if public_session else fallback_ip
        user_dict["login_location"] = (
            getattr(public_session, "geo_location", None) if public_session else fallback_location
        )
        return user_dict
