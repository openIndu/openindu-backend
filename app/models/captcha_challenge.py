"""Image captcha challenge model (anti-bot gate before SMS sending)."""
from sqlalchemy import Boolean, Column, DateTime, String, func

from app.models import Base, BigIntPK


class CaptchaChallenge(Base):
    __tablename__ = "captcha_challenges"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    answer = Column(String(8), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    is_used = Column(Boolean, nullable=False, default=False)
    ip_address = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
