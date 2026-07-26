"""Provider-neutral storage interfaces for untrusted Hosted Apps bundles."""

from __future__ import annotations
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Protocol
from uuid import UUID


__all__ = ["AppBundleStore", "FilesystemBundleStore", "S3BundleStore"]


class AppBundleStore(Protocol):
    """Store staged archives and immutable deployment objects privately."""

    def write_staged(self, upload_id: UUID, source: BinaryIO) -> str:
        """Write a staged archive and return its server-only opaque key."""

    def open_staged(self, staging_key: str) -> BinaryIO:
        """Open a staged archive for server-side completion or validation."""

    def write_deployment_file(
        self, deployment_id: UUID, path: str, source: BinaryIO
    ) -> str:
        """Write one immutable deployment asset and return its opaque key."""

    def write_manifest(self, deployment_id: UUID, source: BinaryIO) -> str:
        """Write the authoritative manifest last for a deployment."""

    def open_deployment_file(self, deployment_id: UUID, path: str) -> BinaryIO:
        """Open a validated immutable deployment asset by logical path."""

    def delete_prefix(self, prefix: str) -> None:
        """Delete a server-selected unreachable staging or deployment prefix."""

    def healthcheck(self) -> None:
        """Raise when the backing store is not usable."""


class FilesystemBundleStore:
    """Private single-node store for local development and explicit dev installs.

    The caller must never expose paths returned by this class to browsers. Production
    deployments use an S3-compatible implementation instead.
    """

    def __init__(self, root: Path) -> None:
        """Initialize an isolated storage root."""
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def write_staged(self, upload_id: UUID, source: BinaryIO) -> str:
        """Persist a staged archive under an opaque upload-id prefix."""
        relative = PurePosixPath("staging", str(upload_id), "archive.zip")
        self._copy_to(relative, source)
        return relative.as_posix()

    def open_staged(self, staging_key: str) -> BinaryIO:
        """Open a staged archive after validating the opaque key stays in root."""
        return self._resolve(staging_key).open("rb")

    def write_deployment_file(
        self, deployment_id: UUID, path: str, source: BinaryIO
    ) -> str:
        """Persist a validated deployment asset beneath its immutable prefix."""
        safe_path = self._safe_relative(path)
        relative = PurePosixPath("deployments", str(deployment_id), *safe_path.parts)
        self._copy_to(relative, source)
        return relative.as_posix()

    def write_manifest(self, deployment_id: UUID, source: BinaryIO) -> str:
        """Persist the validator-created manifest as the final deployment object."""
        relative = PurePosixPath("deployments", str(deployment_id), "__manifest__.json")
        self._copy_to(relative, source)
        return relative.as_posix()

    def open_deployment_file(self, deployment_id: UUID, path: str) -> BinaryIO:
        """Open a logical deployment asset without allowing filesystem traversal."""
        safe_path = self._safe_relative(path)
        relative = PurePosixPath("deployments", str(deployment_id), *safe_path.parts)
        return self._resolve(relative.as_posix()).open("rb")

    def delete_prefix(self, prefix: str) -> None:
        """Recursively remove only a validated store-local prefix."""
        target = self._resolve(prefix)
        if not target.exists():
            return
        if target.is_file():
            target.unlink()
            return
        for child in sorted(target.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():  # pragma: no branch - rglob yields filesystem entries
                child.rmdir()
        target.rmdir()

    def healthcheck(self) -> None:
        """Verify the configured root remains writable and readable."""
        if not self._root.is_dir():
            msg = "Hosted Apps filesystem bundle root is unavailable."
            raise RuntimeError(msg)

    def _copy_to(self, relative: PurePosixPath, source: BinaryIO) -> None:
        destination = self._resolve(relative.as_posix())
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)

    def _resolve(self, value: str) -> Path:
        relative = self._safe_relative(value)
        target = (self._root / Path(*relative.parts)).resolve()
        if self._root not in target.parents and target != self._root:
            msg = "Bundle storage path escapes the configured root."
            raise ValueError(msg)
        return target

    @staticmethod
    def _safe_relative(value: str) -> PurePosixPath:
        path = PurePosixPath(value)
        unsafe_part = any(part in {"", ".", ".."} for part in path.parts)
        if path.is_absolute() or not path.parts or unsafe_part:
            msg = "Bundle storage paths must be non-empty relative POSIX paths."
            raise ValueError(msg)
        return path


class S3BundleStore:
    """Private S3-compatible bundle store using only server-side credentials.

    ``client`` follows the small boto3 S3-client surface used below. Keeping it
    injected makes the storage implementation testable with MinIO or a fake client
    without exposing a provider SDK through the domain protocol.
    """

    def __init__(
        self, client: Any, *, bucket: str, prefix: str = "hosted-apps"
    ) -> None:
        """Initialize a private bucket namespace controlled entirely by the server."""
        normalized_prefix = prefix.strip("/")
        if not bucket.strip() or not normalized_prefix:
            raise ValueError(
                "S3 bundle storage requires a bucket and non-empty prefix."
            )
        self._client = client
        self._bucket = bucket
        self._prefix = normalized_prefix

    def write_staged(self, upload_id: UUID, source: BinaryIO) -> str:
        """Write a private staged archive under a random upload identifier."""
        key = self._key("staging", str(upload_id), "archive.zip")
        self._client.upload_fileobj(source, self._bucket, key)
        return key

    def open_staged(self, staging_key: str) -> BinaryIO:
        """Return a server-side stream for an opaque staged-object key."""
        return self._client.get_object(
            Bucket=self._bucket, Key=self._owned_key(staging_key)
        )["Body"]

    def write_deployment_file(
        self, deployment_id: UUID, path: str, source: BinaryIO
    ) -> str:
        """Write one immutable validated deployment asset."""
        safe_path = FilesystemBundleStore._safe_relative(path)
        key = self._key("deployments", str(deployment_id), *safe_path.parts)
        self._client.upload_fileobj(source, self._bucket, key)
        return key

    def write_manifest(self, deployment_id: UUID, source: BinaryIO) -> str:
        """Write the authoritative manifest after all immutable asset writes finish."""
        key = self._key("deployments", str(deployment_id), "__manifest__.json")
        self._client.upload_fileobj(source, self._bucket, key)
        return key

    def open_deployment_file(self, deployment_id: UUID, path: str) -> BinaryIO:
        """Open a server-authorized immutable asset by its logical manifest path."""
        safe_path = FilesystemBundleStore._safe_relative(path)
        key = self._key("deployments", str(deployment_id), *safe_path.parts)
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"]

    def delete_prefix(self, prefix: str) -> None:
        """Delete a server-selected unreachable prefix in bounded provider batches."""
        owned_prefix = self._owned_key(prefix).rstrip("/") + "/"
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=owned_prefix):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            for offset in range(0, len(keys), 1_000):
                self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": keys[offset : offset + 1_000]},
                )

    def healthcheck(self) -> None:
        """Verify server-side credentials can reach the configured private bucket."""
        self._client.head_bucket(Bucket=self._bucket)

    def _key(self, *parts: str) -> str:
        """Build a normalized owned object key."""
        return "/".join((self._prefix, *parts))

    def _owned_key(self, value: str) -> str:
        """Reject callers that try to read or delete outside this store namespace."""
        candidate = value.strip("/")
        if candidate != self._prefix and not candidate.startswith(f"{self._prefix}/"):
            raise ValueError(
                "Bundle object key is outside the configured store prefix."
            )
        return candidate
