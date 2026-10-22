"""Software administration use cases: versions, metadata, publishing, deletion."""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.catalog.domain import rules
from app.domain.catalog import CatalogCode, CatalogError

NowFn = Callable[[], object]


class TagDirectory(Protocol):
    def active_values(self, tag_type: str) -> list[str]: ...


class SoftwareEntry(Protocol):
    id: int
    brand: str
    category: str
    is_published: bool
    versions: list


class SoftwareCatalog(Protocol):
    def find(self, software_id: int) -> SoftwareEntry | None: ...

    def find_version(self, version_id: int, software_id: int) -> object | None: ...

    def versions_matching(self, criteria: "VersionCriteria", is_published: bool | None) -> list: ...

    def add_version(self, software: SoftwareEntry, *, meta: dict, original_name: str, version: str) -> object: ...

    def save_version(self, version) -> None: ...

    def save_versions(self, versions: list) -> None: ...

    def save(self, entry: SoftwareEntry) -> SoftwareEntry: ...

    def delete(self, entry: SoftwareEntry) -> None: ...

    def delete_version(self, version) -> None: ...

    def delete_versions(self, versions: list) -> None: ...

    def delete_childless(self, software_ids: set[int]) -> None: ...


class PackageStorage(Protocol):
    def upload(self, file_stream, filename: str, prefix: str, content_type: str | None) -> dict: ...

    def delete(self, oss_key: str) -> None: ...


@dataclass(frozen=True)
class VersionCriteria:
    """Bulk-selection shape; the repository turns it into the query.

    ``version_ids`` / ``software_ids`` are explicit selections; when both are
    None the brand/category/keyword filters select the whole matching set.
    """

    version_ids: list[int] | None = None
    software_ids: list[int] | None = None
    brand: str | None = None
    category: str | None = None
    keyword: str | None = None


class AddSoftwareVersion:
    def __init__(self, catalog: SoftwareCatalog, storage: PackageStorage, software_prefix: str) -> None:
        self.catalog = catalog
        self.storage = storage
        self._prefix = software_prefix

    def execute(self, software_id: int, file_stream, filename: str | None, content_type: str | None, version: str) -> object:
        software = self.catalog.find(software_id)
        if not software:
            raise CatalogError(CatalogCode.SW_NOT_FOUND)
        if rules.file_ext(filename) not in rules.ALLOWED_SOFTWARE_EXTS:
            raise CatalogError(CatalogCode.UNSUPPORTED_PACKAGE_FORMAT)
        meta = self.storage.upload(file_stream, filename, f"{self._prefix}/{software.brand}", content_type)
        return self.catalog.add_version(software, meta=meta, original_name=filename or "", version=version)


class DeleteSoftwareVersion:
    def __init__(self, catalog: SoftwareCatalog, storage: PackageStorage) -> None:
        self.catalog = catalog
        self.storage = storage

    def execute(self, software_id: int, version_id: int) -> None:
        version = self.catalog.find_version(version_id, software_id)
        if not version:
            raise CatalogError(CatalogCode.SW_VERSION_NOT_FOUND)
        self.storage.delete(version.oss_key)
        self.catalog.delete_version(version)


class UpdateSoftwareEntry:
    def __init__(self, catalog: SoftwareCatalog, tags: TagDirectory) -> None:
        self.catalog = catalog
        self.tags = tags

    def execute(self, software_id: int, *, original_name: str | None = None, brand: str | None = None, category: str | None = None, description: str | None = None) -> SoftwareEntry:
        software = self.catalog.find(software_id)
        if not software:
            raise CatalogError(CatalogCode.SW_NOT_FOUND)
        if brand is not None:
            if brand not in self.tags.active_values("sw_brand"):
                raise CatalogError(CatalogCode.INVALID_BRAND)
            software.brand = brand
        if category is not None:
            if category not in self.tags.active_values("sw_category"):
                raise CatalogError(CatalogCode.INVALID_CATEGORY)
            software.category = category
        if original_name is not None:
            software.original_name = rules.require_non_empty_name(original_name)
        if description is not None:
            software.description = description
        return self.catalog.save(software)


class BulkSetSoftwarePublishing:
    """Publish is per-version; only rows whose state differs get flipped."""

    def __init__(self, catalog: SoftwareCatalog) -> None:
        self.catalog = catalog

    def execute(self, criteria: VersionCriteria, publish: bool) -> int:
        rules.require_selection(criteria.version_ids, CatalogCode.NO_VERSIONS_SELECTED)
        rules.require_selection(criteria.software_ids, CatalogCode.NO_SOFTWARE_SELECTED)
        rows = self.catalog.versions_matching(criteria, is_published=not publish)
        for version in rows:
            version.is_published = publish
        self.catalog.save_versions(rows)
        return len(rows)


class BulkDeleteSoftwareVersions:
    """Delete versions (and stored files); parents without versions follow."""

    def __init__(self, catalog: SoftwareCatalog, storage: PackageStorage) -> None:
        self.catalog = catalog
        self.storage = storage

    def execute(self, criteria: VersionCriteria) -> int:
        rules.require_selection(criteria.version_ids, CatalogCode.NO_VERSIONS_SELECTED)
        rules.require_selection(criteria.software_ids, CatalogCode.NO_SOFTWARE_SELECTED)
        versions = self.catalog.versions_matching(criteria, is_published=None)
        if not versions:
            return 0

        software_ids: set[int] = set()
        for version in versions:
            self.storage.delete(version.oss_key)
            software_ids.add(version.software_id)
        self.catalog.delete_versions(versions)
        self.catalog.delete_childless(software_ids)
        return len(versions)


class ToggleSoftwarePublishing:
    """Legacy whole-entry toggle: flips every version, mirrors on the parent."""

    def __init__(self, catalog: SoftwareCatalog) -> None:
        self.catalog = catalog

    def execute(self, software_id: int) -> SoftwareEntry:
        software = self.catalog.find(software_id)
        if not software:
            raise CatalogError(CatalogCode.SW_NOT_FOUND)
        target = not software.is_published
        for version in software.versions:
            version.is_published = target
        software.is_published = target
        return self.catalog.save(software)


class ToggleVersionPublishing:
    def __init__(self, catalog: SoftwareCatalog) -> None:
        self.catalog = catalog

    def execute(self, software_id: int, version_id: int) -> object:
        version = self.catalog.find_version(version_id, software_id)
        if not version:
            raise CatalogError(CatalogCode.SW_VERSION_NOT_FOUND)
        version.is_published = not version.is_published
        # Reflect "any version published" onto the parent so the legacy
        # collapsed listing still shows the right status badge.
        software = version.software
        software.is_published = any(v.is_published for v in software.versions)
        self.catalog.save_version(version)
        return version


class DeleteSoftware:
    def __init__(self, catalog: SoftwareCatalog, storage: PackageStorage) -> None:
        self.catalog = catalog
        self.storage = storage

    def execute(self, software_id: int) -> None:
        software = self.catalog.find(software_id)
        if not software:
            raise CatalogError(CatalogCode.SW_NOT_FOUND)
        for version in software.versions:
            self.storage.delete(version.oss_key)
        self.catalog.delete(software)
