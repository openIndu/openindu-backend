"""Portal content key/section model."""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from app.shared.core.utils import iso_utc
from app.shared.models import Base


class PortalContent(Base):
    __tablename__ = "portal_contents"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    section = Column(String(50), nullable=False, index=True)
    content = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "section": self.section,
            "content": self.content,
            "sort_order": self.sort_order,
            "is_active": self.is_active,
            "updated_at": iso_utc(self.updated_at),
        }
