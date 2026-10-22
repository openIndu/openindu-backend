"""Software submission use cases: sync upload, direct-to-OSS pipeline, review."""
import math
from collections.abc import Callable
from typing import Protocol

from app.catalog.domain import rules
from app.domain.catalog import CatalogCode, CatalogError

NowFn = Callable[[], object]


class TagDirectory(Protocol):
    def active_values(self, tag_type: str) -> list[str]: ...


class PackageStorage(Protocol):
    def upload(self, file_stream, filename: str, prefix: str, content_type: str | None) -> dict: ...

    def delete(self, oss_key: str) -> None: ...


class UploadSigner(Protocol):
    """Direct-to-OSS signing; the local backend has no direct-upload ability."""

    is_local: bool

    def new_oss_key(self, filename: str, prefix: str) -> str: ...

    def create_multipart(self, oss_key: str, content_type: str | None) -> str: ...

    def presign_part(self, oss_key: str, upload_id: str, part_number: int, expire_minutes: int) -> str: ...

    def presign_single(self, oss_key: str, content_type: str | None, expire_minutes: int) -> str: ...

    def complete_multipart(self, oss_key: str, upload_id: str, parts: list[dict]) -> None: ...

    def abort_multipart(self, oss_key: str, upload_id: str) -> None: ...

    def head_exists(self, oss_key: str) -> bool: ...


class UploadTokenCodec(Protocol):
    """Signed carrier for submission metadata between /upload/init and /complete."""

    def encode(self, claims: dict) -> str: ...

    def decode(self, token: str) -> dict: ...


class SoftwareEntry(Protocol):
    id: int
    brand: str
    category: str
    submission_status: str | None
    is_published: bool
    rejected_reason: str | None
    versions: list


class SoftwareCatalog(Protocol):
    def find(self, software_id: int) -> SoftwareEntry | None: ...

    def find_owned(self, software_id: int, submitter_id: int) -> SoftwareEntry | None: ...

    def find_version_by_oss_key(self, oss_key: str) -> bool: ...

    def add_submission(
        self,
        *,
        meta: dict,
        original_name: str,
        brand: str,
        category: str,
        version: str,
        description: str | None,
        submitted_by: int | None,
        submission_status: str,
    ) -> SoftwareEntry: ...

    def add_submission_from_oss(
        self,
        *,
        oss_key: str,
        original_name: str,
        brand: str,
        category: str,
        version: str,
        description: str | None,
        file_size: int,
        file_hash: str | None,
        submitted_by: int | None,
        submission_status: str,
    ) -> SoftwareEntry: ...

    def add_version_to_existing(
        self, software_id: int, *, oss_key: str, original_name: str, version: str, file_size: int, file_hash: str | None
    ) -> SoftwareEntry: ...

    def save(self, entry: SoftwareEntry) -> SoftwareEntry: ...

    def delete(self, entry: SoftwareEntry) -> None: ...


class SubmitSoftwarePackage:
    """Sync multipart upload: validate → storage → DB, deleting the stored file
    if the DB write fails so no orphan blob is left behind."""

    def __init__(self, tags: TagDirectory, storage: PackageStorage, catalog: SoftwareCatalog, software_prefix: str, max_size_gb: int) -> None:
        self.tags = tags
        self.storage = storage
        self.catalog = catalog
        self._prefix = software_prefix
        self._max_gb = max_size_gb

    def execute(self, file_stream, filename: str | None, content_type: str | None, brand: str, category: str, version: str, description: str, submitter, size_bytes: int | None = None) -> SoftwareEntry:
        rules.require_valid_brand_category(brand, category, self.tags.active_values("sw_brand"), self.tags.active_values("sw_category"))
        rules.require_valid_package(filename, size_bytes if size_bytes is not None else self._size_of(file_stream), self._max_gb)

        meta = self.storage.upload(file_stream, filename, f"{self._prefix}/{brand}", content_type or "application/octet-stream")
        try:
            return self.catalog.add_submission(
                meta=meta,
                original_name=filename or "",
                brand=brand,
                category=category,
                version=version,
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


class PlanDirectUpload:
    """Sign a direct browser→OSS upload for large packages.

    Submission metadata rides in the signed token so /upload/complete can stamp
    the entry without trusting any client-sent field there.
    """

    def __init__(self, tags: TagDirectory, catalog: SoftwareCatalog, signer: UploadSigner, codec: UploadTokenCodec, software_prefix: str, max_size_gb: int, part_size_bytes: int, expire_minutes: int) -> None:
        self.tags = tags
        self.catalog = catalog
        self.signer = signer
        self.codec = codec
        self._prefix = software_prefix
        self._max_gb = max_size_gb
        self._part_size = part_size_bytes
        self._expire = expire_minutes

    def execute(self, submitter, *, filename: str, brand: str | None, category: str | None, version: str, description: str, content_type: str | None, size: int, software_id: int | None) -> dict:
        rules.require_member_new_entry(submitter.role, software_id)
        if software_id:
            existing = self.catalog.find(software_id)
            if not existing:
                raise CatalogError(CatalogCode.UPLOAD_TARGET_NOT_FOUND)
            brand = existing.brand
            category = existing.category
        else:
            rules.require_valid_brand_category(brand or "", category or "", self.tags.active_values("sw_brand"), self.tags.active_values("sw_category"))
        rules.require_valid_package(filename, size, self._max_gb)
        rules.require_positive_size(size)

        # Reject a duplicate package at init, before any byte is transferred:
        # the storage key is deterministic (prefix/brand/filename), so an
        # existing version row with the same key can never land. In local/sync
        # mode the caller re-uploads via POST /upload, whose storage backend
        # derives the identical key.
        oss_key = self.signer.new_oss_key(filename, f"{self._prefix}/{brand}")
        if self.catalog.find_version_by_oss_key(oss_key):
            raise CatalogError(CatalogCode.VERSION_FILE_EXISTS if software_id else CatalogCode.SOFTWARE_FILE_EXISTS)

        if self.signer.is_local:
            return {"mode": "sync"}
        claims = {
            "oss_key": oss_key,
            "filename": filename,
            "brand": brand,
            "category": category,
            "version": version,
            "description": description,
            "content_type": content_type,
            "size": size,
            "software_id": software_id,
            "is_admin": submitter.role == "admin",
            "submitted_by": submitter.id,
        }

        if size > self._part_size:
            upload_id = self.signer.create_multipart(oss_key, content_type)
            part_urls = [self.signer.presign_part(oss_key, upload_id, i + 1, self._expire) for i in range(math.ceil(size / self._part_size))]
            claims["mode"] = "multipart"
            claims["upload_id"] = upload_id
            token = self.codec.encode(claims)
            return {
                "mode": "multipart", "token": token, "oss_key": oss_key,
                "part_size": self._part_size, "part_urls": part_urls,
                "expires_in": self._expire * 60,
            }

        upload_url = self.signer.presign_single(oss_key, content_type, self._expire)
        claims["mode"] = "single"
        token = self.codec.encode(claims)
        return {
            "mode": "single", "token": token, "oss_key": oss_key,
            "upload_url": upload_url, "expires_in": self._expire * 60,
        }


class CompleteDirectUpload:
    """Finalize a direct upload: merge/verify on OSS, then stamp the DB entry.

    DB failure deletes the uploaded object so no ghost file survives; a member
    whose token tries to target an existing entry loses the file too.
    """

    def __init__(self, catalog: SoftwareCatalog, storage: PackageStorage, signer: UploadSigner, codec: UploadTokenCodec) -> None:
        self.catalog = catalog
        self.storage = storage
        self.signer = signer
        self.codec = codec

    def execute(self, token: str, parts: list[tuple[int, str]] | None, file_hash: str | None) -> tuple[SoftwareEntry, str]:
        claims = self.codec.decode(token)
        mode = claims.get("mode")
        oss_key = claims["oss_key"]
        is_admin = bool(claims.get("is_admin"))
        submitted_by = claims.get("submitted_by")

        if mode == "multipart":
            if not parts:
                raise CatalogError(CatalogCode.PARTS_MISSING)
            ordered = [{"PartNumber": n, "ETag": etag} for n, etag in sorted(parts, key=lambda p: p[0])]
            try:
                self.signer.complete_multipart(oss_key, claims["upload_id"], ordered)
            except Exception:
                self.signer.abort_multipart(oss_key, claims["upload_id"])
                raise CatalogError(CatalogCode.MULTIPART_MERGE_FAILED)
        elif mode == "single":
            if not self.signer.head_exists(oss_key):
                raise CatalogError(CatalogCode.FILE_NOT_ON_OSS)
        else:
            raise CatalogError(CatalogCode.UPLOAD_TOKEN_UNUSABLE)

        existing_id = claims.get("software_id")
        if existing_id:
            return self._complete_version(existing_id, claims, oss_key, is_admin, file_hash)
        return self._complete_new_entry(claims, oss_key, submitted_by, file_hash)

    def _complete_version(self, existing_id, claims, oss_key, is_admin, file_hash) -> tuple[SoftwareEntry, str]:
        if not is_admin:
            self.storage.delete(oss_key)
            raise CatalogError(CatalogCode.MEMBER_CANNOT_ADD_VERSION)
        if not self.catalog.find(existing_id):
            self.storage.delete(oss_key)
            raise CatalogError(CatalogCode.UPLOAD_TARGET_NOT_FOUND)
        if self.catalog.find_version_by_oss_key(oss_key):
            self.storage.delete(oss_key)
            raise CatalogError(CatalogCode.VERSION_FILE_EXISTS)
        try:
            entry = self.catalog.add_version_to_existing(
                existing_id,
                oss_key=oss_key,
                original_name=claims["filename"],
                version=claims["version"],
                file_size=claims["size"],
                file_hash=file_hash,
            )
        except Exception:
            self.storage.delete(oss_key)
            raise
        return entry, "版本已添加"

    def _complete_new_entry(self, claims, oss_key, submitted_by, file_hash) -> tuple[SoftwareEntry, str]:
        if self.catalog.find_version_by_oss_key(oss_key):
            self.storage.delete(oss_key)
            raise CatalogError(CatalogCode.SOFTWARE_FILE_EXISTS)
        try:
            entry = self.catalog.add_submission_from_oss(
                oss_key=oss_key,
                original_name=claims["filename"],
                brand=claims["brand"],
                category=claims["category"],
                version=claims["version"],
                description=claims.get("description"),
                file_size=claims["size"],
                file_hash=file_hash,
                submitted_by=submitted_by,
                submission_status=rules.PENDING,
            )
        except Exception:
            self.storage.delete(oss_key)
            raise
        message = "投稿成功，等待管理员审核"
        return entry, message


class AbortDirectUpload:
    def __init__(self, storage: PackageStorage, signer: UploadSigner, codec: UploadTokenCodec) -> None:
        self.storage = storage
        self.signer = signer
        self.codec = codec

    def execute(self, token: str) -> None:
        claims = self.codec.decode(token)
        mode = claims.get("mode")
        oss_key = claims["oss_key"]
        if mode == "multipart":
            self.signer.abort_multipart(oss_key, claims["upload_id"])
        elif mode == "single":
            self.storage.delete(oss_key)


class ReviewSoftwareSubmission:
    """Admin review: publish (entry + every version), or reject with a reason."""

    def __init__(self, catalog: SoftwareCatalog) -> None:
        self.catalog = catalog

    def execute(self, software_id: int, action: str, reason: str | None) -> tuple[SoftwareEntry, str]:
        entry = self.catalog.find(software_id)
        if not entry:
            raise CatalogError(CatalogCode.SW_NOT_FOUND)
        rules.require_known_review_action(action)

        if action == "publish":
            for ver in entry.versions:
                ver.is_published = True
            entry.is_published = True
            entry.submission_status = rules.PUBLISHED
            entry.rejected_reason = None
            self.catalog.save(entry)
            return entry, "已发布"

        entry.rejected_reason = rules.require_reject_reason(reason)
        rules.require_rejectable(entry.submission_status, entry.is_published)
        entry.submission_status = rules.REJECTED
        self.catalog.save(entry)
        return entry, "已驳回"


class WithdrawSoftwareSubmission:
    """Member deletes own submission — ownership is enforced by submitted_by."""

    def __init__(self, catalog: SoftwareCatalog, storage: PackageStorage) -> None:
        self.catalog = catalog
        self.storage = storage

    def execute(self, software_id: int, submitter_id: int) -> None:
        entry = self.catalog.find_owned(software_id, submitter_id)
        if not entry:
            raise CatalogError(CatalogCode.SUBMISSION_NOT_FOUND)
        for ver in entry.versions:
            self.storage.delete(ver.oss_key)
        self.catalog.delete(entry)
