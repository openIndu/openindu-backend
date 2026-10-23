"""Admin operation audit log model."""
from sqlalchemy import BigInteger, Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from app.shared.models import Base, BigIntPK


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    admin_id = Column(BigInteger, nullable=False, index=True)
    target_user_id = Column(BigInteger, nullable=True, index=True)
    action = Column(String(50), nullable=False, index=True)
    detail = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True, default=dict)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
