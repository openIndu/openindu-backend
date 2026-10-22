"""SQLAlchemy adapters for the software catalog."""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.download_log import DownloadLog
from app.models.resource_tag import ResourceTag
from app.models.software import Software, SoftwareVersion


class SqlTagDirectory:
    def __init__(self, db: Session) -> None:
        self.db = db

    def active_values(self, tag_type: str) -> list[str]:
        return [
            t.value
            for t in self.db.query(ResourceTag).filter(ResourceTag.type == tag_type, ResourceTag.is_active == True).all()  # noqa: E712
        ]


class SqlSoftwareCatalog:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── queries ───────────────────────────────────────────────────────────────

    def find(self, software_id: int) -> Software | None:
        return self.db.query(Software).filter(Software.id == software_id).first()

    def find_owned(self, software_id: int, submitter_id: int) -> Software | None:
        return self.db.query(Software).filter(Software.id == software_id, Software.submitted_by == submitter_id).first()

    def find_version(self, version_id: int, software_id: int) -> SoftwareVersion | None:
        return self.db.query(SoftwareVersion).filter(SoftwareVersion.id == version_id, SoftwareVersion.software_id == software_id).first()

    def find_version_by_oss_key(self, oss_key: str) -> bool:
        return self.db.query(SoftwareVersion).filter(SoftwareVersion.oss_key == oss_key).first() is not None

    def latest_active_version(self, software_id: int, published_only: bool) -> SoftwareVersion | None:
        q = self.db.query(SoftwareVersion).filter(SoftwareVersion.software_id == software_id, SoftwareVersion.is_active.is_(True))
        if published_only:
            q = q.filter(SoftwareVersion.is_published.is_(True))
        return q.order_by(SoftwareVersion.upload_time.desc()).first()

    def versions_matching(self, criteria, is_published: bool | None) -> list[SoftwareVersion]:
        q = self.db.query(SoftwareVersion).join(Software, SoftwareVersion.software_id == Software.id)
        if criteria.version_ids is not None:
            q = q.filter(SoftwareVersion.id.in_(criteria.version_ids))
        elif criteria.software_ids is not None:
            q = q.filter(SoftwareVersion.software_id.in_(criteria.software_ids))
        else:
            if criteria.brand:
                q = q.filter(Software.brand == criteria.brand)
            if criteria.category:
                q = q.filter(Software.category == criteria.category)
            if criteria.keyword:
                q = q.filter(
                    (SoftwareVersion.original_name.ilike(f"%{criteria.keyword}%"))
                    | (Software.original_name.ilike(f"%{criteria.keyword}%"))
                )
        if is_published is not None:
            q = q.filter(SoftwareVersion.is_published == is_published)
        return q.all()

    # ── writes ────────────────────────────────────────────────────────────────

    def add_submission(self, *, meta: dict, original_name: str, brand: str, category: str, version: str, description: str | None, submitted_by: int | None, submission_status: str) -> Software:
        sw = Software(
            filename=meta["filename"], original_name=original_name, brand=brand, category=category,
            latest_version=version, description=description, submitted_by=submitted_by,
            submission_status=submission_status,
        )
        self.db.add(sw)
        self.db.flush()
        self.db.add(SoftwareVersion(
            software_id=sw.id, version=version, original_name=original_name,
            file_size=meta["file_size"], file_hash=meta["file_hash"], oss_key=meta["oss_key"],
        ))
        self.db.commit()
        self.db.refresh(sw)
        return sw

    def add_submission_from_oss(self, *, oss_key: str, original_name: str, brand: str, category: str, version: str, description: str | None, file_size: int, file_hash: str | None, submitted_by: int | None, submission_status: str) -> Software:
        sw = Software(
            filename=oss_key.rsplit("/", 1)[-1], original_name=original_name, brand=brand, category=category,
            latest_version=version, description=description, submitted_by=submitted_by,
            submission_status=submission_status,
        )
        try:
            self.db.add(sw)
            self.db.flush()
            self.db.add(SoftwareVersion(
                software_id=sw.id, version=version, original_name=original_name,
                file_size=file_size, file_hash=file_hash, oss_key=oss_key,
            ))
            self.db.commit()
            self.db.refresh(sw)
        except Exception:
            self.db.rollback()
            raise
        return sw

    def add_version_to_existing(self, software_id: int, *, oss_key: str, original_name: str, version: str, file_size: int, file_hash: str | None) -> Software:
        sw = self.find(software_id)
        try:
            self.db.add(SoftwareVersion(
                software_id=sw.id, version=version, original_name=original_name,
                file_size=file_size, file_hash=file_hash, oss_key=oss_key,
            ))
            sw.latest_version = version
            self.db.commit()
            self.db.refresh(sw)
        except Exception:
            self.db.rollback()
            raise
        return sw

    def add_version(self, software: Software, *, meta: dict, original_name: str, version: str) -> SoftwareVersion:
        ver = SoftwareVersion(
            software_id=software.id, version=version, original_name=original_name,
            file_size=meta["file_size"], file_hash=meta["file_hash"], oss_key=meta["oss_key"],
        )
        software.latest_version = version
        self.db.add(ver)
        self.db.commit()
        self.db.refresh(ver)
        return ver

    def save(self, entry: Software) -> Software:
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def save_version(self, version: SoftwareVersion) -> None:
        self.db.commit()
        self.db.refresh(version)

    def save_versions(self, versions: list[SoftwareVersion]) -> None:
        self.db.commit()

    def delete(self, entry: Software) -> None:
        self.db.delete(entry)
        self.db.commit()

    def delete_version(self, version: SoftwareVersion) -> None:
        self.db.delete(version)
        self.db.commit()

    def delete_versions(self, versions: list[SoftwareVersion]) -> None:
        """Row deletion without commit — the caller decides the transaction span."""
        for version in versions:
            self.db.delete(version)

    def delete_childless(self, software_ids: set[int]) -> None:
        for sid in software_ids:
            remaining = self.db.query(SoftwareVersion).filter(SoftwareVersion.software_id == sid).count()
            if remaining == 0:
                sw = self.db.get(Software, sid)
                if sw:
                    self.db.delete(sw)
        self.db.commit()


class SqlDownloadLedger:
    def __init__(self, db: Session) -> None:
        self.db = db

    def count_today(self, user_id: int, resource_type: str, since: datetime) -> int:
        return self.db.query(DownloadLog).filter(
            DownloadLog.user_id == user_id,
            DownloadLog.resource_type == resource_type,
            DownloadLog.created_at >= since,
        ).count()

    def record_download(self, software: Software, version: SoftwareVersion, user_id: int, ip: str | None) -> None:
        software.download_count = (software.download_count or 0) + 1
        version.download_count = (version.download_count or 0) + 1
        self.db.add(DownloadLog(user_id=user_id, resource_type="software", resource_id=software.id, ip_address=ip))
        self.db.commit()
