"""ResourceTag model — stores dynamic brand / category / series metadata."""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, func

from app.shared.core.utils import iso_utc
from app.shared.models import Base


class ResourceTag(Base):
    __tablename__ = "resource_tags"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    type = Column(String(20), nullable=False, index=True)    # doc_brand | sw_brand | doc_category | sw_category | doc_series
    value = Column(String(100), nullable=False)               # slug used in DB (e.g. siemens, fx)
    label_zh = Column(String(200), nullable=False)            # Chinese display name
    parent_value = Column(String(100), nullable=True, index=True)  # parent category value for series
    brand_value = Column(String(100), nullable=True, index=True)   # brand slug for series (e.g. siemens)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "value": self.value,
            "label_zh": self.label_zh,
            "parent_value": self.parent_value,
            "brand_value": self.brand_value,
            "is_active": self.is_active,
            "sort_order": self.sort_order,
            "created_at": iso_utc(self.created_at),
        }
