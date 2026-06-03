"""Delegated blob storage backends for ChatKit attachments.

Provides a provider-neutral ``BlobBackend`` Protocol and an ``S3BlobBackend``
implementation compatible with AWS S3, Cloudflare R2, and MinIO.  The default
backend remains Postgres (handled directly in ``AttachmentService``); this
module is only loaded when delegated storage is configured.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Protocol, runtime_checkable


logger = logging.getLogger(__name__)


@runtime_checkable
class BlobBackend(Protocol):
    """Provider-neutral interface for delegated blob storage."""

    def make_key(self, workspace_id: str, attachment_id: str) -> str:
        """Return the object key for a given workspace/attachment pair."""
        ...

    async def put(self, key: str, data: bytes, *, sha256: str, size_bytes: int) -> None:
        """Upload *data* to *key*."""
        ...

    async def load(self, key: str) -> bytes:
        """Download and return the bytes stored at *key*."""
        ...

    async def delete(self, key: str) -> None:
        """Remove the object at *key* (best-effort; no error if missing)."""
        ...


class S3BlobBackend:
    """S3-compatible blob storage backend (AWS S3, Cloudflare R2, MinIO).

    Boto3 is used synchronously and wrapped with ``asyncio.run_in_executor``
    so the event loop is not blocked.  The boto3 client is created lazily on
    first use and reused across calls.
    """

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        """Initialize the S3-compatible backend client settings."""
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import boto3  # type: ignore[import-untyped]
            except ImportError as exc:
                msg = (
                    "boto3 must be installed to use the S3 blob backend. "
                    "Install it with: pip install boto3"
                )
                raise RuntimeError(msg) from exc

            kwargs: dict[str, Any] = {}
            if self._endpoint_url:
                kwargs["endpoint_url"] = self._endpoint_url
            if self._region:
                kwargs["region_name"] = self._region
            if self._access_key_id and self._secret_access_key:
                kwargs["aws_access_key_id"] = self._access_key_id
                kwargs["aws_secret_access_key"] = self._secret_access_key

            self._client = boto3.client("s3", **kwargs)
        return self._client

    def make_key(self, workspace_id: str, attachment_id: str) -> str:
        """Build the object key for a workspace-scoped attachment."""
        return f"attachments/{workspace_id}/{attachment_id}"

    async def put(self, key: str, data: bytes, *, sha256: str, size_bytes: int) -> None:
        """Upload attachment bytes to the configured bucket."""
        client = self._get_client()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentLength=size_bytes,
                Metadata={"sha256": sha256},
            ),
        )
        logger.debug("S3 put: bucket=%s key=%s bytes=%d", self._bucket, key, size_bytes)

    async def load(self, key: str) -> bytes:
        """Load attachment bytes from the configured bucket."""
        client = self._get_client()
        loop = asyncio.get_event_loop()
        response: dict[str, Any] = await loop.run_in_executor(
            None,
            lambda: client.get_object(Bucket=self._bucket, Key=key),
        )
        body = response["Body"]
        data: bytes = await loop.run_in_executor(None, body.read)
        logger.debug("S3 load: bucket=%s key=%s bytes=%d", self._bucket, key, len(data))
        return data

    async def delete(self, key: str) -> None:
        """Delete attachment bytes from the configured bucket."""
        client = self._get_client()
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: client.delete_object(Bucket=self._bucket, Key=key),
            )
            logger.debug("S3 delete: bucket=%s key=%s", self._bucket, key)
        except Exception:
            logger.warning(
                "S3 delete failed for bucket=%s key=%s",
                self._bucket,
                key,
                exc_info=True,
            )


def build_blob_backend(
    backend_name: str,
    *,
    bucket: str | None = None,
    endpoint_url: str | None = None,
    region: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> BlobBackend | None:
    """Return a configured blob backend, or ``None`` for the Postgres default.

    Returns ``None`` when *backend_name* is ``"postgres"`` so callers can skip
    the delegation path entirely.  Raises ``ValueError`` for unknown names.
    """
    if backend_name == "postgres":
        return None

    if backend_name == "s3":
        if not bucket:
            msg = "ORCHEO_CHATKIT_S3_BUCKET must be set when using the s3 blob backend."
            raise ValueError(msg)
        return S3BlobBackend(
            bucket,
            endpoint_url=endpoint_url,
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )

    msg = f"Unknown blob backend: {backend_name!r}"
    raise ValueError(msg)


__all__ = [
    "BlobBackend",
    "S3BlobBackend",
    "build_blob_backend",
]
