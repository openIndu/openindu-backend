"""Storage adapters for the catalog context."""
from app.core.utils import oss_key_for_upload
from app.services.storage_service import storage_service


class StoragePackageUploader:
    """Sync package upload/delete via the shared storage facade."""

    def upload(self, file_stream, filename: str, prefix: str, content_type: str | None) -> dict:
        return storage_service.upload_file(file_stream, filename, prefix, content_type or "application/octet-stream")

    def delete(self, oss_key: str) -> None:
        storage_service.delete_file(oss_key)


class OssDirectUploadGateway:
    """Direct browser→OSS signing. Exposes the multipart/single operations of
    the concrete backend; the local backend reports ``is_local`` so callers
    fall back to the sync pipeline."""

    @property
    def is_local(self) -> bool:
        return storage_service.is_local

    def new_oss_key(self, filename: str, prefix: str) -> str:
        return oss_key_for_upload(filename, prefix)

    def create_multipart(self, oss_key: str, content_type: str | None) -> str:
        return storage_service._impl.create_multipart_upload(oss_key, content_type)

    def presign_part(self, oss_key: str, upload_id: str, part_number: int, expire_minutes: int) -> str:
        return storage_service._impl.presign_part_upload(oss_key, upload_id, part_number, expire_minutes)

    def presign_single(self, oss_key: str, content_type: str | None, expire_minutes: int) -> str:
        return storage_service._impl.presign_single_put(oss_key, content_type, expire_minutes)

    def complete_multipart(self, oss_key: str, upload_id: str, parts: list[dict]) -> None:
        storage_service._impl.complete_multipart_upload(oss_key, upload_id, parts)

    def abort_multipart(self, oss_key: str, upload_id: str) -> None:
        storage_service._impl.abort_multipart_upload(oss_key, upload_id)

    def head_exists(self, oss_key: str) -> bool:
        return bool(storage_service._impl.head_object(oss_key))


class StorageLinkSigner:
    def download_url(self, oss_key: str) -> dict:
        return storage_service.get_download_url(oss_key)
