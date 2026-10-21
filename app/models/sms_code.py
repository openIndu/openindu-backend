"""SMS verification code model."""
from sqlalchemy import Boolean, Column, DateTime, Integer, String, func

from app.models import Base, BigIntPK


class SmsCode(Base):
    __tablename__ = "sms_codes"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    phone = Column(String(20), nullable=False, index=True)
    code = Column(String(6), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    last_sent_at = Column(DateTime, nullable=False)
    verify_attempts = Column(Integer, nullable=False, default=0)
    is_used = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
