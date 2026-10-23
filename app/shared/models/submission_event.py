"""Append-only history of resource submissions and withdrawals."""
from sqlalchemy import BigInteger, Column, DateTime, Index, String, func

from app.shared.models import Base, BigIntPK


class SubmissionEvent(Base):
    __tablename__ = "submission_events"
    __table_args__ = (
        Index("ix_submission_events_actor_created", "actor_id", "created_at"),
        Index("ix_submission_events_resource", "resource_type", "resource_id"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    actor_id = Column(BigInteger, nullable=False)
    resource_type = Column(String(20), nullable=False)
    resource_id = Column(BigInteger, nullable=False)
    resource_name = Column(String(500), nullable=False)
    action = Column(String(20), nullable=False)
    version = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
