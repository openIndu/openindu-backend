"""Software download-link issuance: visibility, daily quota, counters, signing."""
from collections.abc import Callable
from typing import Protocol

from app.catalog.domain import rules
from app.domain.catalog import CatalogCode, CatalogError

NowFn = Callable[[], object]


class SoftwareEntry(Protocol):
    id: int
    original_name: str
    submitted_by: int | None
    versions: list


class SoftwareCatalog(Protocol):
    def find(self, software_id: int) -> SoftwareEntry | None: ...

    def find_version(self, version_id: int, software_id: int) -> object | None: ...

    def latest_active_version(self, software_id: int, published_only: bool) -> object | None: ...


class DownloadLedger(Protocol):
    def count_today(self, user_id: int, resource_type: str, since) -> int: ...

    def record_download(self, software: SoftwareEntry, version, user_id: int, ip: str | None) -> None: ...


class LinkSigner(Protocol):
    def download_url(self, oss_key: str) -> dict: ...


class IssueSoftwareDownloadLink:
    """Issue a short-lived download URL for a published version.

    Quota consumption, counter bumps and the log row commit together before
    the URL is signed — the same ordering as the original handler.
    """

    def __init__(self, catalog: SoftwareCatalog, ledger: DownloadLedger, signer: LinkSigner, now: NowFn, daily_limit: int) -> None:
        self.catalog = catalog
        self.ledger = ledger
        self.signer = signer
        self._now = now
        self._limit = daily_limit

    def execute_latest(self, software_id: int, user, ip: str | None) -> dict:
        software = self.catalog.find(software_id)
        if not software:
            raise CatalogError(CatalogCode.SW_NOT_FOUND)
        is_reviewer = user.role == "admin" or software.submitted_by == user.id
        version = self.catalog.latest_active_version(software_id, published_only=not is_reviewer)
        if not version:
            raise CatalogError(CatalogCode.SW_VERSION_NOT_FOUND)
        return self._issue(software, version, user, ip)

    def execute_version(self, software_id: int, version_id: int, user, ip: str | None) -> dict:
        software = self.catalog.find(software_id)
        version = self.catalog.find_version(version_id, software_id) if software else None
        # The original collapses a missing software, a missing version and a
        # hidden version into one 404 — preserved verbatim here.
        is_reviewer = user.role == "admin" or (software is not None and software.submitted_by == user.id)
        if not software or not version or (not version.is_published and not is_reviewer):
            raise CatalogError(CatalogCode.SW_VERSION_NOT_FOUND)
        return self._issue(software, version, user, ip)

    def _issue(self, software: SoftwareEntry, version, user, ip: str | None) -> dict:
        used = self.ledger.count_today(user.id, "software", rules.today_start(self._now()))
        rules.require_download_quota(user.role, used, self._limit)
        self.ledger.record_download(software, version, user.id, ip)
        signed = self.signer.download_url(version.oss_key)
        return {
            "download_url": signed["url"],
            "expires_in": signed["expires_in"],
            "filename": software.original_name,
            "version": version.version,
        }
