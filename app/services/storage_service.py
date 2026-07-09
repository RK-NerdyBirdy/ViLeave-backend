"""
app/services/storage_service.py
────────────────────────────────
Cloudflare R2 integration via boto3 (S3-compatible API).

R2 specifics vs AWS S3:
  - Endpoint URL must be set explicitly: https://<account_id>.r2.cloudflarestorage.com
  - Region is always "auto" for R2
  - Presigned URLs use the same boto3 generate_presigned_url API
  - No data transfer fees between R2 and workers/internet

Object key convention:
    leaves/{year}/{student_uuid}/{request_uuid}.pdf

This ensures easy lifecycle management (e.g., "delete all docs for student X")
and avoids key collisions across cohort years.
"""
import io
import uuid
from datetime import datetime
from functools import lru_cache
from typing import BinaryIO

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import get_settings

settings = get_settings()


def _make_r2_key(student_id: uuid.UUID, request_id: uuid.UUID) -> str:
    """Build a deterministic, collision-safe R2 object key."""
    year = datetime.utcnow().year
    return f"leaves/{year}/{student_id}/{request_id}.pdf"


@lru_cache(maxsize=1)
def _get_s3_client():
    return boto3.client(
        "s3",
        # Fix: Use the R2 S3 API Endpoint, NOT the public URL
        endpoint_url=settings.r2_endpoint, 
        
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",  
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "adaptive"},
        ),
    )


class StorageService:
    """
    Thin wrapper around boto3 providing the three operations our app needs:
      1. upload   — store a PDF when a leave request is submitted
      2. presign  — generate a short-lived URL for viewing the PDF
      3. delete   — remove the PDF on HOD rejection
    """

    def __init__(self):
        self._client = _get_s3_client()
        self._bucket = settings.r2_bucket_name

    def upload_pdf(
        self,
        file_content: bytes | BinaryIO,
        student_id: uuid.UUID,
        request_id: uuid.UUID,
    ) -> str:
        """
        Upload a PDF to R2.

        Returns the R2 object key (NOT a URL — we always generate presigned
        URLs at access time so we keep full access control).
        Raises StorageError on upload failure.
        """
        key = _make_r2_key(student_id, request_id)

        try:
            if isinstance(file_content, bytes):
                file_content = io.BytesIO(file_content)

            self._client.upload_fileobj(
                file_content,
                self._bucket,
                key,
                ExtraArgs={
                    "ContentType": "application/pdf",
                    # Prevent browsers from executing the file
                    "ContentDisposition": "inline",
                },
            )
            return key
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            raise StorageError(f"R2 upload failed [{error_code}]: {exc}") from exc

    def generate_presigned_url(self, r2_key: str, expires_in: int = 900) -> str:
        """
        Generate a presigned GET URL for a PDF (default: 15 minutes).
        The URL grants read-only access to a single object — no credentials
        are shared with the client.

        Args:
            r2_key:     The R2 object key returned by upload_pdf()
            expires_in: TTL in seconds (default 900 = 15 min)
        """
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": r2_key},
                ExpiresIn=expires_in,
            )
        except ClientError as exc:
            raise StorageError(f"Failed to generate presigned URL: {exc}") from exc

    def delete_object(self, r2_key: str) -> None:
        """
        Permanently delete a PDF from R2.
        Called on HOD rejection. R2 delete is idempotent — deleting a
        non-existent key does not raise an error.
        """
        try:
            self._client.delete_object(Bucket=self._bucket, Key=r2_key)
        except ClientError as exc:
            raise StorageError(f"R2 deletion failed: {exc}") from exc


class StorageError(Exception):
    """Raised when any R2 operation fails. Caught at the service/router layer."""
    pass


# ── Module-level singleton ────────────────────────────────────────────────────
# Import this in services: from app.services.storage_service import storage
storage = StorageService()
