"""Document catalog use cases: submission, review, links, admin actions."""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.domain.catalog import CatalogCode, CatalogError
from app.modules.catalog.domain import rules

NowFn = Callable[[], object]


class TagDirectory(Protocol):
    def active_values(self, tag_type: str) -> list[str]: ...

    def series_exists(self, series: str, brand: str, category: str) -> bool: ...


class PackageStorage(Protocol):
    def upload(self, file_stream, filename: str, prefix: str, content_type: str | None) -> dict: ...

    def delete(self, oss_key: str) -> None: ...


class DocumentEntry(Protocol):
    id: int
    brand: str
    category: str
    series: str | None
    original_name: str
    submitted_by: int | None
    submission_status: str | None
    is_published: bool
    rejected_reason: str | None
    download_count: int | None
    oss_key: str


class DocumentCatalog(Protocol):
    def find(self, doc_id: int) -> DocumentEntry | None: ...

    def find_owned(self, doc_id: int, submitter_id: int) -> DocumentEntry | None: ...

    def list_by_submitter(self, submitter_id: int, limit: int, offset: int) -> tuple[list[DocumentEntry], int]: ...

    def matching(self, criteria: "DocumentCriteria", is_published: bool | None) -> list: ...

    def add(self, *, meta: dict, original_name: str, brand: str, category: str, series: str | None, description: str | None, submitted_by: int | None, submission_status: str) -> DocumentEntry: ...

    def save(self, entry: DocumentEntry) -> DocumentEntry: ...

    def save_many(self, entries: list) -> None: ...

    def delete(self, entry: DocumentEntry) -> None: ...


class DownloadLedger(Protocol):
    def count_today(self, user_id: int, resource_type: str, since) -> int: ...

    def record_document_download(self, doc: DocumentEntry, user_id: int, ip: str | None) -> None: ...

    def record_document_preview(self, doc: DocumentEntry, user_id: int, ip: str | None) -> None: ...


class LinkSigner(Protocol):
    def download_url(self, oss_key: str) -> dict: ...

    def preview_url(self, oss_key: str) -> dict: ...


@dataclass(frozen=True)
class DocumentCriteria:
    """Bulk-selection shape; the repository turns it into the query."""

    ids: list[int] | None = None
    brand: str | None = None
    category: str | None = None
    series: str | None = None
    keyword: str | None = None


class SubmitDocument:
    """Sync upload: validate → storage → DB, deleting the stored file if the
    DB write fails so no orphan blob is left behind."""

    def __init__(self, tags: TagDirectory, storage: PackageStorage, docs: DocumentCatalog, doc_prefix: str, max_size_mb: int) -> None:
        self.tags = tags
        self.storage = storage
        self.docs = docs
        self._prefix = doc_prefix
        self._max_mb = max_size_mb

    def execute(self, file_stream, filename: str | None, content_type: str | None, brand: str, category: str, series: str, description: str, submitter, size_bytes: int | None = None) -> DocumentEntry:
        rules.require_valid_brand_category(brand, category, self.tags.active_values("doc_brand"), self.tags.active_values("doc_category"))
        rules.require_valid_series(series or None, self.tags.series_exists(series, brand, category) if series else False)
        rules.require_valid_pdf(filename)
        rules.require_document_size(size_bytes if size_bytes is not None else self._size_of(file_stream), self._max_mb)

        meta = self.storage.upload(file_stream, filename, f"{self._prefix}/{brand}", content_type or "application/pdf")
        try:
            return self.docs.add(
                meta=meta,
                original_name=filename or "",
                brand=brand,
                category=category,
                series=series or None,
                description=description,
                submitted_by=submitter.id,
                submission_status=rules.PENDING,
            )
        except Exception:
            self.storage.delete(meta["oss_key"])
            raise

    def _size_of(self, file_stream) -> int:
        pos = file_stream.tell()
        file_stream.seek(0, 2)
        size = file_stream.tell()
        file_stream.seek(pos)
        return size


class ReviewDocumentSubmission:
    def __init__(self, docs: DocumentCatalog) -> None:
        self.docs = docs

    def execute(self, doc_id: int, action: str, reason: str | None) -> tuple[DocumentEntry, str]:
        doc = self.docs.find(doc_id)
        if not doc:
            raise CatalogError(CatalogCode.DOC_NOT_FOUND)
        rules.require_known_review_action(action)

        if action == "publish":
            doc.is_published = True
            doc.submission_status = rules.PUBLISHED
            doc.rejected_reason = None
            self.docs.save(doc)
            return doc, "已发布"

        doc.rejected_reason = rules.require_reject_reason(reason)
        rules.require_rejectable(doc.submission_status, doc.is_published)
        doc.submission_status = rules.REJECTED
        self.docs.save(doc)
        return doc, "已驳回"


class WithdrawDocumentSubmission:
    def __init__(self, docs: DocumentCatalog, storage: PackageStorage) -> None:
        self.docs = docs
        self.storage = storage

    def execute(self, doc_id: int, submitter_id: int) -> None:
        doc = self.docs.find_owned(doc_id, submitter_id)
        if not doc:
            raise CatalogError(CatalogCode.SUBMISSION_NOT_FOUND)
        self.storage.delete(doc.oss_key)
        self.docs.delete(doc)


class IssueDocumentLink:
    """Download and preview links with separate daily quotas.

    Preview never bumps ``download_count`` and logs under its own resource
    type so in-page reading can't bypass the download cap.
    """

    def __init__(self, docs: DocumentCatalog, ledger: DownloadLedger, signer: LinkSigner, now: NowFn, download_limit: int, preview_limit: int) -> None:
        self.docs = docs
        self.ledger = ledger
        self.signer = signer
        self._now = now
        self._download_limit = download_limit
        self._preview_limit = preview_limit

    def _visible(self, doc_id: int, user) -> DocumentEntry:
        doc = self.docs.find(doc_id)
        is_reviewer = user.role == "admin" or (doc is not None and doc.submitted_by == user.id)
        if not doc or (not doc.is_published and not is_reviewer):
            raise CatalogError(CatalogCode.DOC_NOT_FOUND)
        return doc

    def execute_download(self, doc_id: int, user, ip: str | None) -> dict:
        doc = self._visible(doc_id, user)
        used = self.ledger.count_today(user.id, "document", rules.today_start(self._now()))
        rules.require_download_quota(user.role, used, self._download_limit, CatalogCode.DOC_DOWNLOAD_LIMIT_REACHED)
        self.ledger.record_document_download(doc, user.id, ip)
        signed = self.signer.download_url(doc.oss_key)
        return {"download_url": signed["url"], "expires_in": signed["expires_in"], "filename": doc.original_name}

    def execute_preview(self, doc_id: int, user, ip: str | None) -> dict:
        doc = self._visible(doc_id, user)
        used = self.ledger.count_today(user.id, "document_preview", rules.today_start(self._now()))
        rules.require_download_quota(user.role, used, self._preview_limit, CatalogCode.DOC_PREVIEW_LIMIT_REACHED, limit=self._preview_limit)
        self.ledger.record_document_preview(doc, user.id, ip)
        signed = self.signer.preview_url(doc.oss_key)
        return {"preview_url": signed["url"], "expires_in": signed["expires_in"], "filename": doc.original_name}


class UpdateDocumentEntry:
    def __init__(self, docs: DocumentCatalog, tags: TagDirectory) -> None:
        self.docs = docs
        self.tags = tags

    def execute(self, doc_id: int, *, original_name: str | None = None, brand: str | None = None, category: str | None = None, series: str | None = None, description: str | None = None) -> DocumentEntry:
        doc = self.docs.find(doc_id)
        if not doc:
            raise CatalogError(CatalogCode.DOC_NOT_FOUND)
        if brand is not None:
            if brand not in self.tags.active_values("doc_brand"):
                raise CatalogError(CatalogCode.INVALID_BRAND)
            doc.brand = brand
            if series is None:
                doc.series = None
        if category is not None:
            if category not in self.tags.active_values("doc_category"):
                raise CatalogError(CatalogCode.INVALID_CATEGORY)
            doc.category = category
            if series is None:
                doc.series = None
        if series is not None:
            rules.require_valid_series(series or None, self.tags.series_exists(series, doc.brand, doc.category) if series else False)
            doc.series = series or None
        if original_name is not None:
            doc.original_name = rules.require_non_empty_name(original_name, CatalogCode.DOC_NAME_REQUIRED)
        if description is not None:
            doc.description = description
        return self.docs.save(doc)


class BulkSetDocumentPublishing:
    def __init__(self, docs: DocumentCatalog) -> None:
        self.docs = docs

    def execute(self, criteria: DocumentCriteria, publish: bool) -> int:
        rules.require_selection(criteria.ids, CatalogCode.NO_DOCUMENTS_SELECTED)
        rows = self.docs.matching(criteria, is_published=not publish)
        for doc in rows:
            doc.is_published = publish
        self.docs.save_many(rows)
        return len(rows)


class ToggleDocumentPublishing:
    def __init__(self, docs: DocumentCatalog) -> None:
        self.docs = docs

    def execute(self, doc_id: int) -> DocumentEntry:
        doc = self.docs.find(doc_id)
        if not doc:
            raise CatalogError(CatalogCode.DOC_NOT_FOUND)
        doc.is_published = not doc.is_published
        return self.docs.save(doc)


class DeleteDocument:
    def __init__(self, docs: DocumentCatalog, storage: PackageStorage) -> None:
        self.docs = docs
        self.storage = storage

    def execute(self, doc_id: int) -> None:
        doc = self.docs.find(doc_id)
        if not doc:
            raise CatalogError(CatalogCode.DOC_NOT_FOUND)
        self.storage.delete(doc.oss_key)
        self.docs.delete(doc)


class ListDocumentSubmissions:
    """Read-only listing of a member's own document submissions, page-shaped."""

    def __init__(self, docs: DocumentCatalog) -> None:
        self.docs = docs

    def execute(self, submitter_id: int, limit: int, offset: int) -> tuple[list[DocumentEntry], int]:
        return self.docs.list_by_submitter(submitter_id, limit, offset)
