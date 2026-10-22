"""Composition root for the catalog context (software side; documents follow)."""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.catalog.application.admin import (
    AddSoftwareVersion,
    BulkDeleteSoftwareVersions,
    BulkSetSoftwarePublishing,
    DeleteSoftware,
    DeleteSoftwareVersion,
    ToggleSoftwarePublishing,
    ToggleVersionPublishing,
    UpdateSoftwareEntry,
)
from app.catalog.application.downloads import IssueSoftwareDownloadLink
from app.catalog.application.submissions import (
    AbortDirectUpload,
    CompleteDirectUpload,
    PlanDirectUpload,
    ReviewSoftwareSubmission,
    SubmitSoftwarePackage,
    WithdrawSoftwareSubmission,
)
from app.catalog.infrastructure.repositories import (
    SqlDownloadLedger,
    SqlSoftwareCatalog,
    SqlTagDirectory,
)
from app.catalog.infrastructure.storage import (
    OssDirectUploadGateway,
    StorageLinkSigner,
    StoragePackageUploader,
)
from app.catalog.infrastructure.tokens import JwtUploadTokenCodec
from app.core.config import settings
from app.core.utils import utcnow

MB = 1024 * 1024


@dataclass(frozen=True)
class CatalogContext:
    submit_package: SubmitSoftwarePackage
    plan_direct_upload: PlanDirectUpload
    complete_direct_upload: CompleteDirectUpload
    abort_direct_upload: AbortDirectUpload
    review_submission: ReviewSoftwareSubmission
    withdraw_submission: WithdrawSoftwareSubmission
    issue_download_link: IssueSoftwareDownloadLink
    add_version: AddSoftwareVersion
    delete_version: DeleteSoftwareVersion
    update_entry: UpdateSoftwareEntry
    bulk_publish: BulkSetSoftwarePublishing
    bulk_delete_versions: BulkDeleteSoftwareVersions
    toggle_publish: ToggleSoftwarePublishing
    toggle_version_publish: ToggleVersionPublishing
    delete_software: DeleteSoftware


def catalog(db: Session) -> CatalogContext:
    tags = SqlTagDirectory(db)
    sw = SqlSoftwareCatalog(db)
    storage = StoragePackageUploader()
    signer = OssDirectUploadGateway()
    codec = JwtUploadTokenCodec()
    links = StorageLinkSigner()
    ledger = SqlDownloadLedger(db)
    now = utcnow
    software_prefix = settings.OSS_SOFTWARE_PREFIX
    return CatalogContext(
        submit_package=SubmitSoftwarePackage(tags, storage, sw, software_prefix, settings.SOFTWARE_MAX_SIZE_GB),
        plan_direct_upload=PlanDirectUpload(
            tags, sw, signer, codec, software_prefix,
            settings.SOFTWARE_MAX_SIZE_GB,
            settings.UPLOAD_PART_SIZE_MB * MB,
            settings.UPLOAD_PRESIGN_EXPIRE_MINUTES,
        ),
        complete_direct_upload=CompleteDirectUpload(sw, storage, signer, codec),
        abort_direct_upload=AbortDirectUpload(storage, signer, codec),
        review_submission=ReviewSoftwareSubmission(sw),
        withdraw_submission=WithdrawSoftwareSubmission(sw, storage),
        issue_download_link=IssueSoftwareDownloadLink(sw, ledger, links, now, settings.DOWNLOAD_DAILY_LIMIT),
        add_version=AddSoftwareVersion(sw, storage, software_prefix),
        delete_version=DeleteSoftwareVersion(sw, storage),
        update_entry=UpdateSoftwareEntry(sw, tags),
        bulk_publish=BulkSetSoftwarePublishing(sw),
        bulk_delete_versions=BulkDeleteSoftwareVersions(sw, storage),
        toggle_publish=ToggleSoftwarePublishing(sw),
        toggle_version_publish=ToggleVersionPublishing(sw),
        delete_software=DeleteSoftware(sw, storage),
    )
