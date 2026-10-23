"""Software catalog rules without framework dependencies."""
from datetime import date, datetime
from typing import Protocol

from app.shared.domain.catalog import CatalogCode, CatalogError

ALLOWED_SOFTWARE_EXTS = {".zip", ".exe", ".msi", ".rar", ".7z", ".pkg", ".package"}
PENDING = "pending"
PUBLISHED = "published"
REJECTED = "rejected"


def file_ext(filename: str | None) -> str:
    return "." + filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""


class Submitter(Protocol):
    id: int
    role: str


def require_valid_brand_category(brand: str, category: str, brands: list[str], categories: list[str]) -> None:
    if brand not in brands:
        raise CatalogError(CatalogCode.INVALID_BRAND)
    if category not in categories:
        raise CatalogError(CatalogCode.INVALID_CATEGORY)


def require_valid_package(filename: str | None, size_bytes: int, max_size_gb: int) -> None:
    if file_ext(filename) not in ALLOWED_SOFTWARE_EXTS:
        raise CatalogError(CatalogCode.UNSUPPORTED_PACKAGE_FORMAT)
    if size_bytes > max_size_gb * 1024 * 1024 * 1024:
        raise CatalogError(CatalogCode.FILE_TOO_LARGE)


def require_positive_size(size: int) -> None:
    if size <= 0:
        raise CatalogError(CatalogCode.FILE_SIZE_INVALID)


def require_reject_reason(reason: str | None) -> str:
    stripped = (reason or "").strip()
    if not stripped:
        raise CatalogError(CatalogCode.REJECT_REASON_REQUIRED)
    return stripped


def require_rejectable(submission_status: str | None, is_published: bool) -> None:
    if submission_status == PUBLISHED and is_published:
        raise CatalogError(CatalogCode.REJECT_PUBLISHED_BLOCKED)


def require_known_review_action(action: str) -> None:
    if action not in ("publish", "reject"):
        raise CatalogError(CatalogCode.INVALID_REVIEW_ACTION)


def require_visible(is_published: bool, is_reviewer: bool) -> None:
    """Unpublished entries 404 for everyone except owner/admin — no info leak."""
    if not is_published and not is_reviewer:
        raise CatalogError(CatalogCode.SW_NOT_FOUND)


def require_member_new_entry(role: str, software_id: int | None) -> None:
    """Members may only create new entries, never append versions to existing ones."""
    if software_id and role != "admin":
        raise CatalogError(CatalogCode.MEMBER_CANNOT_ADD_VERSION)


def require_download_quota(role: str, used_today: int, daily_limit: int, code: CatalogCode = CatalogCode.DOWNLOAD_LIMIT_REACHED, **params: object) -> None:
    # Admins bypass the 5/day cap — they curate the catalog and shouldn't be
    # locked out while validating uploads or fielding member tickets.
    if role == "admin":
        return
    if used_today >= daily_limit:
        raise CatalogError(code, **params)


def require_selection(ids: list[int] | None, code: CatalogCode) -> None:
    if ids is not None and not ids:
        raise CatalogError(code)


def require_non_empty_name(name: str | None, code: CatalogCode = CatalogCode.NAME_REQUIRED) -> str:
    stripped = (name or "").strip()
    if not stripped:
        raise CatalogError(code)
    return stripped


def require_valid_pdf(filename: str | None) -> None:
    if not filename or not filename.lower().endswith(".pdf"):
        raise CatalogError(CatalogCode.DOC_UNSUPPORTED_FORMAT)


def require_document_size(size_bytes: int, max_size_mb: int) -> None:
    if size_bytes > max_size_mb * 1024 * 1024:
        raise CatalogError(CatalogCode.FILE_TOO_LARGE)


def require_valid_series(series: str | None, exists: bool) -> None:
    """A series must be an active tag under (brand, category); empty is fine."""
    if not series:
        return
    if not exists:
        raise CatalogError(CatalogCode.INVALID_SERIES)


def today_start(now: datetime) -> datetime:
    return datetime.combine(date(now.year, now.month, now.day), datetime.min.time())
