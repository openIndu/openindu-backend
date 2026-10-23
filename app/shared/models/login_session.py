"""Login session model for online statistics."""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, String, UniqueConstraint, func

from app.shared.models import Base


class LoginSession(Base):
    __tablename__ = "login_sessions"
    __table_args__ = (UniqueConstraint("user_id", "ip_address", "user_agent", name="uq_login_session_device"),)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    # Browser/client scoped id supplied by Portal/Admin. It lets us retire the
    # previous account's session when the same browser switches users without
    # banning legitimate multi-account traffic from the same NAT IP.
    client_id = Column(String(64), nullable=True, index=True)
    ip_address = Column(String(64), nullable=False)
    user_agent = Column(String(512), nullable=False, default="")
    geo_location = Column(String(255), nullable=True)
    last_active_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
