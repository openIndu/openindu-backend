"""Slider-puzzle challenge and its one-time verification ticket (anti-bot gate)."""
from sqlalchemy import Boolean, Column, DateTime, Integer, String, func

from app.models import Base, BigIntPK


class SliderChallenge(Base):
    __tablename__ = "slider_challenges"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    # True notch offset on the background image; the client submits its drag
    # distance and it must land within `tolerance` pixels.
    x = Column(Integer, nullable=False)
    y = Column(Integer, nullable=False)
    tolerance = Column(Integer, nullable=False, default=10)
    # Verified challenges carry a short-lived single-use ticket that
    # /auth/send-code consumes instead of the raw answer.
    verified = Column(Boolean, nullable=False, default=False)
    ticket = Column(String(64), nullable=True, index=True)
    ticket_expires_at = Column(DateTime, nullable=True)
    is_used = Column(Boolean, nullable=False, default=False)
    ip_address = Column(String(64), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
