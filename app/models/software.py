"""Software package and version models."""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.core.utils import iso_utc
from app.models import Base, BigIntPK


class Software(Base):
    __tablename__ = "software"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    filename = Column(String(500), nullable=False)
    original_name = Column(String(500), nullable=False)
    brand = Column(String(50), nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)
    latest_version = Column(String(100), nullable=True)
    download_count = Column(Integer, nullable=False, default=0)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    is_published = Column(Boolean, nullable=False, default=False)
    # Member submission tracking. Admin uploads keep the "published" default;
    # member uploads start as "pending" until an admin reviews them.
    submitted_by = Column(BigIntPK, ForeignKey("users.id"), nullable=True, index=True)
    submission_status = Column(String(20), nullable=False, default="published", server_default="published")
    rejected_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    versions = relationship("SoftwareVersion", back_populates="software", cascade="all, delete-orphan")
    submitter = relationship("User")

    def to_dict(self, include_versions: bool = False) -> dict:
        # latest_version_size = size of the version row whose tag matches
        # `latest_version`. Surfaced on the list so the admin UI can show a
        # size column without an extra round-trip per row.
        latest_size = None
        if self.latest_version:
            for v in self.versions:
                if v.version == self.latest_version:
                    latest_size = v.file_size
                    break
        data = {
            "id": self.id,
            "filename": self.filename,
            "original_name": self.original_name,
            "brand": self.brand,
            "category": self.category,
            "latest_version": self.latest_version,
            "latest_version_size": latest_size,
            "versions_count": len(self.versions),
            "download_count": self.download_count,
            "description": self.description,
            "is_active": self.is_active,
            "is_published": self.is_published,
            "submitted_by": self.submitted_by,
            "submission_status": self.submission_status,
            "rejected_reason": self.rejected_reason,
            "created_at": iso_utc(self.created_at),
        }
        if include_versions:
            data["versions"] = [v.to_dict() for v in self.versions]
        return data


class SoftwareVersion(Base):
    __tablename__ = "software_versions"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    software_id = Column(BigInteger, ForeignKey("software.id"), nullable=False, index=True)
    version = Column(String(100), nullable=False)
    # Per-version display name — each upload preserves its real file name so
    # the portal can label two versions of the same software differently.
    original_name = Column(String(500), nullable=True)
    file_size = Column(BigInteger, nullable=True)
    file_hash = Column(String(64), nullable=True, index=True)
    oss_key = Column(String(500), nullable=False, unique=True)
    download_count = Column(Integer, nullable=False, default=0)
    upload_time = Column(DateTime, server_default=func.now(), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    # Publish per version, not per software — admins curate which versions
    # are visible to portal users without affecting other versions.
    is_published = Column(Boolean, nullable=False, default=False)

    software = relationship("Software", back_populates="versions")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "software_id": self.software_id,
            "version": self.version,
            "original_name": self.original_name,
            "file_size": self.file_size,
            "file_hash": self.file_hash,
            "oss_key": self.oss_key,
            "download_count": self.download_count,
            "upload_time": iso_utc(self.upload_time),
            "is_active": self.is_active,
            "is_published": self.is_published,
        }
