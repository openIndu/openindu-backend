"""S3-compatible OSS service used for documents and software packages."""
import hashlib
from typing import BinaryIO

import boto3
from botocore.config import Config

from app.shared.core.config import settings
from app.shared.core.utils import oss_key_for_upload

# Streaming chunk for hash + multipart upload. Keeps memory flat regardless of
# file size (software packages may reach the GB range).
_CHUNK = 8 * 1024 * 1024  # 8 MiB


class OSSService:
    """Thin wrapper around boto3 S3 client."""

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=settings.OSS_ENDPOINT,
                aws_access_key_id=settings.OSS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.OSS_ACCESS_KEY_SECRET,
                region_name=settings.OSS_REGION,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "virtual"},
                    # Fail fast instead of hanging on a stalled OSS connection.
                    # The default botocore read_timeout (60s) + 5 legacy retries
                    # made large uploads appear frozen for many minutes.
                    connect_timeout=10,
                    read_timeout=120,
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
        return self._client

    @property
    def bucket(self) -> str:
        return settings.OSS_BUCKET

    def upload_file(self, file_stream: BinaryIO, original_name: str, prefix: str, content_type: str | None = None) -> dict:
        oss_key = oss_key_for_upload(original_name, prefix)
        filename = oss_key.rsplit("/", 1)[-1]

        # Single streaming pass for hash + size — no full-file memory copy.
        file_stream.seek(0)
        h = hashlib.sha256()
        size = 0
        while True:
            chunk = file_stream.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
        file_hash = h.hexdigest()

        # upload_fileobj does automatic multipart for large objects, keeping
        # memory bounded and enabling resumable transfers. Rewind first.
        file_stream.seek(0)
        extra_args = {"ContentType": content_type} if content_type else None
        self.client.upload_fileobj(
            Fileobj=file_stream,
            Bucket=self.bucket,
            Key=oss_key,
            ExtraArgs=extra_args,
        )
        return {"oss_key": oss_key, "filename": filename, "file_hash": file_hash, "file_size": size}

    def delete_file(self, oss_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=oss_key)

    def generate_presigned_url(self, oss_key: str, expiration_minutes: int = 5,
                                inline: bool = False) -> str:
        """Generate a presigned GET URL.

        When inline=True the URL forces inline disposition so browsers render
        the file in the built-in viewer (the object's stored Content-Type —
        application/pdf for documents uploaded via /documents/upload — is what
        the browser actually sees; Aliyun OSS rejects ResponseContentType as a
        query override with "Can not override response header on content-type").
        Default (False) keeps the attachment behavior for the download button.
        """
        params = {"Bucket": self.bucket, "Key": oss_key}
        if inline:
            params["ResponseContentDisposition"] = "inline"
        else:
            params["ResponseContentDisposition"] = "attachment"
        return self.client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expiration_minutes * 60,
        )

    # -- direct-to-OSS multipart upload (browser uploads large files) --------

    def presign_single_put(self, oss_key: str, content_type: str | None,
                           expiration_minutes: int) -> str:
        params = {"Bucket": self.bucket, "Key": oss_key}
        if content_type:
            params["ContentType"] = content_type
        return self.client.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=expiration_minutes * 60,
            HttpMethod="PUT",
        )

    def create_multipart_upload(self, oss_key: str, content_type: str | None) -> str:
        params = {"Bucket": self.bucket, "Key": oss_key}
        if content_type:
            params["ContentType"] = content_type
        resp = self.client.create_multipart_upload(**params)
        return resp["UploadId"]

    def presign_part_upload(self, oss_key: str, upload_id: str, part_number: int,
                            expiration_minutes: int) -> str:
        return self.client.generate_presigned_url(
            "upload_part",
            Params={"Bucket": self.bucket, "Key": oss_key,
                    "UploadId": upload_id, "PartNumber": part_number},
            ExpiresIn=expiration_minutes * 60,
            HttpMethod="PUT",
        )

    def complete_multipart_upload(self, oss_key: str, upload_id: str,
                                  parts: list[dict]) -> None:
        """parts: [{"PartNumber": int, "ETag": str}, ...] sorted by part number."""
        self.client.complete_multipart_upload(
            Bucket=self.bucket, Key=oss_key, UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )

    def abort_multipart_upload(self, oss_key: str, upload_id: str) -> None:
        self.client.abort_multipart_upload(
            Bucket=self.bucket, Key=oss_key, UploadId=upload_id,
        )

    def head_object(self, oss_key: str) -> bool:
        """Return True if the object exists."""
        try:
            self.client.head_object(Bucket=self.bucket, Key=oss_key)
            return True
        except Exception:
            return False

    def list_objects(self, prefix: str) -> list[dict]:
        objects: list[dict] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                objects.append({
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"],
                    "etag": obj["ETag"].strip('"'),
                })
        return objects


oss_service = OSSService()
