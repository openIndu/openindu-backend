"""Brand cross-mapping model."""
from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Index, String, Text

from app.shared.models import Base


class BrandMapping(Base):
    __tablename__ = "brand_mappings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source_brand = Column(String(50), nullable=False)
    target_brand = Column(String(50), nullable=False)
    item_type = Column(String(50), nullable=False)   # io / memory / timer / counter / communication / instruction
    source_value = Column(String(200), nullable=False)
    target_value = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_brand_mapping_src_tgt", "source_brand", "target_brand"),
    )
