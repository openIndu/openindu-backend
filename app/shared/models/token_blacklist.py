"""JWT token blacklist model."""
from sqlalchemy import BigInteger, Column, DateTime, String, func

from app.shared.models import Base


class TokenBlacklist(Base):
    __tablename__ = "token_blacklist"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    jti = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    token_type = Column(String(20), nullable=False)
    reason = Column(String(50), nullable=False, default="logout")
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
