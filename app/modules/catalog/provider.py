"""Composition root for the catalog context (software, documents, tags)."""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.utils import utcnow
from app.modules.catalog.application.admin import (
    AddSoftwareVersion,
    BulkDeleteSoftwareVersions,
    BulkSetSoftwarePublishing,
    DeleteSoftware,
    DeleteSoftwareVersion,
    ToggleSoftwarePublishing,
    ToggleVersionPublishing,
    UpdateSoftwareEntry,
)
from app.modules.catalog.application.documents import (
    BulkSetDocumentPublishing,
    DeleteDocument,
    IssueDocumentLink,
    ListDocumentSubmissions,
    ReviewDocumentSubmission,
    SubmitDocument,
    ToggleDocumentPublishing,
    UpdateDocumentEntry,
    WithdrawDocumentSubmission,
)
from app.modules.catalog.application.downloads import IssueSoftwareDownloadLink
from app.modules.catalog.application.submissions import (
    AbortDirectUpload,
    CompleteDirectUpload,
    ListSoftwareSubmissions,
    PlanDirectUpload,
    ReviewSoftwareSubmission,
    SubmitSoftwarePackage,
    WithdrawSoftwareSubmission,
)
from app.modules.catalog.application.tags import CreateTag, DeleteTag, UpdateTag
from app.modules.catalog.infrastructure.repositories import (
    SqlDocumentCatalog,
    SqlDownloadLedger,
    SqlSoftwareCatalog,
    SqlTagBoard,
    SqlTagDirectory,
)
from app.modules.catalog.infrastructure.storage import (
    OssDirectUploadGateway,
    StorageLinkSigner,
    StoragePackageUploader,
)
from app.modules.catalog.infrastructure.tokens import JwtUploadTokenCodec

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
    submit_document: SubmitDocument
    review_document: ReviewDocumentSubmission
    withdraw_document: WithdrawDocumentSubmission
    issue_document_link: IssueDocumentLink
    update_document: UpdateDocumentEntry
    bulk_publish_documents: BulkSetDocumentPublishing
    toggle_document_publish: ToggleDocumentPublishing
    delete_document: DeleteDocument
    create_tag: CreateTag
    update_tag: UpdateTag
    delete_tag: DeleteTag
    list_software_submissions: ListSoftwareSubmissions
    list_document_submissions: ListDocumentSubmissions


def catalog(db: Session) -> CatalogContext:
    tags = SqlTagDirectory(db)
    sw = SqlSoftwareCatalog(db)
    docs = SqlDocumentCatalog(db)
    board = SqlTagBoard(db)
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
        submit_document=SubmitDocument(tags, storage, docs, settings.OSS_DOC_PREFIX, settings.DOCUMENT_MAX_SIZE_MB),
        review_document=ReviewDocumentSubmission(docs),
        withdraw_document=WithdrawDocumentSubmission(docs, storage),
        issue_document_link=IssueDocumentLink(docs, ledger, links, now, settings.DOWNLOAD_DAILY_LIMIT, settings.PREVIEW_DAILY_LIMIT),
        update_document=UpdateDocumentEntry(docs, tags),
        bulk_publish_documents=BulkSetDocumentPublishing(docs),
        toggle_document_publish=ToggleDocumentPublishing(docs),
        delete_document=DeleteDocument(docs, storage),
        create_tag=CreateTag(board),
        update_tag=UpdateTag(board),
        delete_tag=DeleteTag(board),
        list_software_submissions=ListSoftwareSubmissions(sw),
        list_document_submissions=ListDocumentSubmissions(docs),
    )
