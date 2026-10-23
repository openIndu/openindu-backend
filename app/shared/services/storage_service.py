"""Unified storage facade — delegates to local filesystem or S3 based on STORAGE_BACKEND.

Usage:
    from app.shared.services.storage_service import storage_service
    meta = storage_service.upload_file(file_stream, name, prefix)
    url  = storage_service.get_download_url(oss_key)
"""

import hashlib
import hmac
import time
from urllib.parse import quote

from app.shared.core.config import settings
from app.shared.services.file_storage import file_storage
from app.shared.services.oss_service import oss_service


class StorageService:
    """Facade that routes to the active backend."""

    LOCAL_DOWNLOAD_TOKEN_TTL_SECONDS = settings.PRESIGNED_URL_EXPIRE_MINUTES * 60

    def __init__(self) -> None:
        self._backend = settings.STORAGE_BACKEND

    @property
    def _impl(self):
        return file_storage if self._backend == "local" else oss_service

    @property
    def is_local(self) -> bool:
        return self._backend == "local"

    # -- delegated methods -------------------------------------------------

    def upload_file(self, file_stream, original_name: str, prefix: str,
                    content_type: str | None = None) -> dict:
        return self._impl.upload_file(file_stream, original_name, prefix, content_type)

    def delete_file(self, oss_key: str) -> None:
        self._impl.delete_file(oss_key)

    def list_objects(self, prefix: str) -> list[dict]:
        return self._impl.list_objects(prefix)

    # -- download URL ------------------------------------------------------

    def _sign_local_download(self, oss_key: str, expires_at: int) -> str:
        payload = f"{oss_key}:{expires_at}".encode()
        return hmac.new(settings.JWT_SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()

    def validate_local_download_token(self, oss_key: str, expires_at: int, token: str) -> bool:
        if expires_at < int(time.time()):
            return False
        expected = self._sign_local_download(oss_key, expires_at)
        return hmac.compare_digest(expected, token)

    def get_download_url(self, oss_key: str) -> dict:
        """Return a dict with 'url' and 'expires_in' suitable for the API response."""
        if self._backend == "local":
            normalized_key = oss_key.lstrip("/")
            expires_at = int(time.time()) + self.LOCAL_DOWNLOAD_TOKEN_TTL_SECONDS
            token = self._sign_local_download(normalized_key, expires_at)
            return {
                "url": f"/api/v1/files/{quote(normalized_key)}?expires={expires_at}&token={token}",
                "expires_in": self.LOCAL_DOWNLOAD_TOKEN_TTL_SECONDS,
            }
        # S3 / OSS mode: generate presigned URL
        url = self._impl.generate_presigned_url(
            oss_key, settings.PRESIGNED_URL_EXPIRE_MINUTES
        )
        return {
            "url": url,
            "expires_in": settings.PRESIGNED_URL_EXPIRE_MINUTES * 60,
        }

    def get_preview_url(self, oss_key: str) -> dict:
        """Return a presigned inline URL suitable for the browser PDF viewer."""
        if self._backend == "local":
            normalized_key = oss_key.lstrip("/")
            expires_at = int(time.time()) + self.LOCAL_DOWNLOAD_TOKEN_TTL_SECONDS
            token = self._sign_local_download(normalized_key, expires_at)
            return {
                "url": f"/api/v1/files/{quote(normalized_key)}?expires={expires_at}&token={token}",
                "expires_in": self.LOCAL_DOWNLOAD_TOKEN_TTL_SECONDS,
            }
        url = self._impl.generate_presigned_url(
            oss_key, settings.PRESIGNED_URL_EXPIRE_MINUTES, inline=True,
        )
        return {"url": url, "expires_in": settings.PRESIGNED_URL_EXPIRE_MINUTES * 60}

    def get_file_path(self, oss_key: str):
        """Only valid in local mode — returns the filesystem path."""
        if not self.is_local:
            raise RuntimeError("get_file_path is only available in local storage mode")
        return self._impl.get_file_path(oss_key)


storage_service = StorageService()
