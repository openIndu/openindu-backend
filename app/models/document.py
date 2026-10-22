"""Document metadata model."""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.utils import iso_utc
from app.models import Base, BigIntPK


class Document(Base):
    __tablename__ = "documents"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    filename = Column(String(500), nullable=False)
    original_name = Column(String(500), nullable=False)
    brand = Column(String(50), nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)
    series = Column(String(100), nullable=True, index=True)
    file_size = Column(BigInteger, nullable=True)
    file_hash = Column(String(64), nullable=True, index=True)
    oss_key = Column(String(500), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    download_count = Column(Integer, nullable=False, default=0)
    sync_status = Column(String(20), nullable=False, default="pending")
    is_published = Column(Boolean, nullable=False, default=False)
    # Member submission tracking. Admin uploads keep the "published" default;
    # member uploads start as "pending" until an admin reviews them.
    submitted_by = Column(BigIntPK, ForeignKey("users.id"), nullable=True, index=True)
    submission_status = Column(String(20), nullable=False, default="published", server_default="published")
    rejected_reason = Column(Text, nullable=True)
    upload_time = Column(DateTime, server_default=func.now(), nullable=False)
    sync_time = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "original_name": self.original_name,
            "brand": self.brand,
            "category": self.category,
            "series": self.series,
            "file_size": self.file_size,
            "file_hash": self.file_hash,
            "oss_key": self.oss_key,
            "description": self.description,
            "download_count": self.download_count,
            "is_published": self.is_published,
            "submitted_by": self.submitted_by,
            "submission_status": self.submission_status,
            "rejected_reason": self.rejected_reason,
            "upload_time": iso_utc(self.upload_time),
        }
