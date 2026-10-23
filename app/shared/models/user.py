"""User model."""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, String, func

from app.shared.core.utils import iso_utc
from app.shared.models import Base, BigIntPK


class User(Base):
    __tablename__ = "users"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    phone = Column(String(20), unique=True, nullable=False, index=True)
    nickname = Column(String(50), nullable=True)
    role = Column(String(20), nullable=False, default="user")
    is_active = Column(Boolean, nullable=False, default=True)
    is_blacklisted = Column(Boolean, nullable=False, default=False)
    blacklisted_at = Column(DateTime, nullable=True)
    blacklisted_by = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    tokens_invalidated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    last_login = Column(DateTime, nullable=True)
    # Soft delete marker. NULL = live user; set = removed from admin UI and
    # login. Existing visit_events / download_records / login_sessions are
    # preserved for audit history.
    deleted_at = Column(DateTime, nullable=True)
    # Member application state — NULL means never applied
    member_apply_status = Column(String(20), nullable=True)
    member_apply_note = Column(String(500), nullable=True)
    member_apply_at = Column(DateTime, nullable=True)
    member_reviewed_by = Column(BigInteger, ForeignKey("users.id"), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "phone": self.phone,
            "nickname": self.nickname,
            "role": self.role,
            "is_active": self.is_active,
            "is_blacklisted": self.is_blacklisted,
            "blacklisted_at": iso_utc(self.blacklisted_at),
            "blacklisted_by": self.blacklisted_by,
            "tokens_invalidated_at": iso_utc(self.tokens_invalidated_at),
            "created_at": iso_utc(self.created_at),
            "last_login": iso_utc(self.last_login),
            "deleted_at": iso_utc(self.deleted_at),
            "member_apply_status": self.member_apply_status,
            "member_apply_at": iso_utc(self.member_apply_at),
        }
