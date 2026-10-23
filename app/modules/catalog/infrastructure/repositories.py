"""SQLAlchemy adapters for the software catalog."""
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.shared.domain.catalog import CatalogCode, CatalogError
from app.shared.models.document import Document
from app.shared.models.download_log import DownloadLog
from app.shared.models.resource_tag import ResourceTag
from app.shared.models.software import Software, SoftwareVersion


def _duplicate_oss_key(exc: IntegrityError) -> bool:
    """True when an IntegrityError is the software_versions.oss_key unique hit
    (Postgres names the constraint, SQLite names the column)."""
    return "oss_key" in str(exc).lower()


class SqlTagDirectory:
    def __init__(self, db: Session) -> None:
        self.db = db

    def active_values(self, tag_type: str) -> list[str]:
        return [
            t.value
            for t in self.db.query(ResourceTag).filter(ResourceTag.type == tag_type, ResourceTag.is_active == True).all()  # noqa: E712
        ]

    def series_exists(self, series: str, brand: str, category: str) -> bool:
        return self.db.query(ResourceTag).filter(
            ResourceTag.type == "doc_series",
            ResourceTag.value == series,
            ResourceTag.brand_value == brand,
            ResourceTag.parent_value == category,
            ResourceTag.is_active == True,  # noqa: E712
        ).first() is not None


class SqlSoftwareCatalog:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── queries ───────────────────────────────────────────────────────────────

    def find(self, software_id: int) -> Software | None:
        return self.db.query(Software).filter(Software.id == software_id).first()

    def find_owned(self, software_id: int, submitter_id: int) -> Software | None:
        return self.db.query(Software).filter(Software.id == software_id, Software.submitted_by == submitter_id).first()

    def list_by_submitter(self, submitter_id: int, limit: int, offset: int) -> tuple[list[Software], int]:
        """One member's submissions, newest first, with a total for truncation hints."""
        base = self.db.query(Software).filter(Software.submitted_by == submitter_id, Software.is_active.is_(True))
        total = base.count()
        rows = (
            base.options(selectinload(Software.versions))
            .order_by(Software.created_at.desc(), Software.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return rows, total

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
        try:
            self.db.flush()
            self.db.add(SoftwareVersion(
                software_id=sw.id, version=version, original_name=original_name,
                file_size=meta["file_size"], file_hash=meta["file_hash"], oss_key=meta["oss_key"],
            ))
            self.db.commit()
            self.db.refresh(sw)
        except IntegrityError as exc:
            self.db.rollback()
            if _duplicate_oss_key(exc):
                raise CatalogError(CatalogCode.SOFTWARE_FILE_EXISTS) from None
            raise
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
        except IntegrityError as exc:
            self.db.rollback()
            if _duplicate_oss_key(exc):
                raise CatalogError(CatalogCode.SOFTWARE_FILE_EXISTS) from None
            raise
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
        except IntegrityError as exc:
            self.db.rollback()
            if _duplicate_oss_key(exc):
                raise CatalogError(CatalogCode.VERSION_FILE_EXISTS) from None
            raise
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

    def record_document_download(self, doc: Document, user_id: int, ip: str | None) -> None:
        doc.download_count = (doc.download_count or 0) + 1
        self.db.add(DownloadLog(user_id=user_id, resource_type="document", resource_id=doc.id, ip_address=ip))
        self.db.commit()

    def record_document_preview(self, doc: Document, user_id: int, ip: str | None) -> None:
        # Previewing is not a download: no download_count bump, own resource_type.
        self.db.add(DownloadLog(user_id=user_id, resource_type="document_preview", resource_id=doc.id, ip_address=ip))
        self.db.commit()


class SqlDocumentCatalog:
    def __init__(self, db: Session) -> None:
        self.db = db

    def find(self, doc_id: int) -> Document | None:
        return self.db.query(Document).filter(Document.id == doc_id).first()

    def find_owned(self, doc_id: int, submitter_id: int) -> Document | None:
        return self.db.query(Document).filter(Document.id == doc_id, Document.submitted_by == submitter_id).first()

    def list_by_submitter(self, submitter_id: int, limit: int, offset: int) -> tuple[list[Document], int]:
        base = self.db.query(Document).filter(Document.submitted_by == submitter_id)
        total = base.count()
        rows = base.order_by(Document.upload_time.desc(), Document.id.desc()).offset(offset).limit(limit).all()
        return rows, total

    def matching(self, criteria, is_published: bool | None) -> list[Document]:
        q = self.db.query(Document)
        if criteria.ids is not None:
            q = q.filter(Document.id.in_(criteria.ids))
        else:
            if criteria.brand:
                q = q.filter(Document.brand == criteria.brand)
            if criteria.category:
                q = q.filter(Document.category == criteria.category)
            if criteria.series:
                q = q.filter(Document.series == criteria.series)
            if criteria.keyword:
                q = q.filter(Document.original_name.ilike(f"%{criteria.keyword}%"))
        if is_published is not None:
            q = q.filter(Document.is_published == is_published)
        return q.all()

    def add(self, *, meta: dict, original_name: str, brand: str, category: str, series: str | None, description: str | None, submitted_by: int | None, submission_status: str) -> Document:
        doc = Document(
            filename=meta["filename"], original_name=original_name, brand=brand, category=category,
            series=series, file_size=meta["file_size"], file_hash=meta["file_hash"],
            oss_key=meta["oss_key"], description=description,
            submitted_by=submitted_by, submission_status=submission_status,
        )
        try:
            self.db.add(doc)
            self.db.commit()
            self.db.refresh(doc)
        except Exception:
            self.db.rollback()
            raise
        return doc

    def save(self, entry: Document) -> Document:
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def save_many(self, entries: list[Document]) -> None:
        self.db.commit()

    def delete(self, entry: Document) -> None:
        self.db.delete(entry)
        self.db.commit()


class SqlTagBoard:
    """ResourceTag CRUD plus the rename/in-use cascades over documents and software."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def find(self, tag_id: int) -> ResourceTag | None:
        return self.db.query(ResourceTag).filter(ResourceTag.id == tag_id).first()

    def exists(self, tag_type: str, value: str, parent_value: str | None, scoped: bool) -> bool:
        q = self.db.query(ResourceTag).filter(ResourceTag.type == tag_type, ResourceTag.value == value)
        if scoped:
            q = q.filter(ResourceTag.parent_value == parent_value)
        return q.first() is not None

    def create(self, *, tag_type: str, value: str, label_zh: str, parent_value: str | None, brand_value: str | None, sort_order: int) -> ResourceTag:
        tag = ResourceTag(
            type=tag_type, value=value, label_zh=label_zh,
            parent_value=parent_value, brand_value=brand_value, sort_order=sort_order,
        )
        self.db.add(tag)
        self.db.commit()
        self.db.refresh(tag)
        return tag

    def rename_references(self, old_value: str, new_value: str, tag_type: str) -> None:
        # Cascade: update all software/docs referencing this tag, then the
        # parent_value / brand_value references inside the tag table itself.
        column = {
            "sw_brand": Software.brand,
            "sw_category": Software.category,
            "doc_brand": Document.brand,
            "doc_category": Document.category,
            "doc_series": Document.series,
        }.get(tag_type)
        if column is not None:
            self.db.query(column.class_).filter(column == old_value).update({column: new_value})
        self.db.query(ResourceTag).filter(ResourceTag.parent_value == old_value).update({ResourceTag.parent_value: new_value})
        self.db.query(ResourceTag).filter(ResourceTag.brand_value == old_value).update({ResourceTag.brand_value: new_value})

    def in_use(self, tag: ResourceTag) -> bool:
        checks = {
            "doc_brand": lambda: self.db.query(Document).filter(Document.brand == tag.value).first(),
            "sw_brand": lambda: self.db.query(Software).filter(Software.brand == tag.value).first(),
            "doc_category": lambda: self.db.query(Document).filter(Document.category == tag.value).first(),
            "sw_category": lambda: self.db.query(Software).filter(Software.category == tag.value).first(),
            "doc_series": lambda: self.db.query(Document).filter(Document.series == tag.value).first(),
        }
        lookup = checks.get(tag.type)
        return lookup() is not None if lookup else False

    def delete_child_series(self, parent_value: str) -> None:
        self.db.query(ResourceTag).filter(ResourceTag.type == "doc_series", ResourceTag.parent_value == parent_value).delete()

    def save(self, tag: ResourceTag) -> ResourceTag:
        self.db.commit()
        self.db.refresh(tag)
        return tag

    def delete(self, tag: ResourceTag) -> None:
        self.db.delete(tag)
        self.db.commit()
