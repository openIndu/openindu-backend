"""Anonymous/authenticated visit event model for dashboard analytics."""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, String, func

from app.shared.models import Base


class VisitEvent(Base):
    __tablename__ = "visit_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ip_address = Column(String(64), nullable=False, index=True)
    # Browser/client scoped id. Unified across PV/UV analytics and login
    # sessions; historical rows without it fall back to ip_address for UV.
    client_id = Column(String(64), nullable=True, index=True)
    event_type = Column(String(32), nullable=False, default="page_view", index=True)
    user_agent = Column(String(512), nullable=False, default="")
    path = Column(String(512), nullable=False, default="/")
    geo_location = Column(String(255), nullable=True)
    country_code = Column(String(32), nullable=True)
    is_authenticated = Column(Boolean, nullable=False, default=False, index=True)
    user_id = Column(BigInteger, nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
