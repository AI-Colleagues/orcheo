"""Safe staged-upload completion and immutable deployment materialization."""

from __future__ import annotations
import hashlib
import zipfile
from collections.abc import Mapping
from datetime import timedelta
from io import BytesIO
from typing import BinaryIO, cast
from uuid import UUID
from orcheo.hosted_apps.bundle_store import AppBundleStore
from orcheo.hosted_apps.errors import BundleValidationError
from orcheo.hosted_apps.models import AppDeployment, AppUpload, DeploymentStatus
from orcheo.hosted_apps.zip_validation import BundleValidationLimits, validate_bundle
from orcheo.models.base import _utcnow


__all__ = ["DeploymentService", "UploadNotFoundError"]


class UploadNotFoundError(KeyError):
    """Raised when an upload id is not owned by this deployment service."""


class DeploymentService:
    """Coordinates staged archives without treating storage prefixes as publication.

    A deployment becomes ready only after all extracted objects are written under its
    immutable prefix and the validator-generated manifest is successfully written last.
    Callers persist the returned domain records in the same transaction as their audit
    and outbox entries in a durable repository.
    """

    def __init__(
        self, store: AppBundleStore, *, limits: BundleValidationLimits
    ) -> None:
        """Initialize the service with explicit ZIP-resource limits."""
        self._store = store
        self._limits = limits
        self._uploads: dict[UUID, AppUpload] = {}
        self._deployments: dict[UUID, AppDeployment] = {}

    def initiate(
        self,
        *,
        workspace_id: UUID,
        app_id: UUID,
        created_by: str,
        expected_size_bytes: int,
        expected_sha256: str | None = None,
    ) -> tuple[AppUpload, AppDeployment]:
        """Create a short-lived upload and unpublishable deployment candidate."""
        if expected_size_bytes > self._limits.max_archive_bytes:
            raise BundleValidationError(
                "hosted_apps.bundle.archive_too_large",
                "Bundle archive exceeds the allowed size.",
            )
        deployment = AppDeployment(
            workspace_id=workspace_id,
            app_id=app_id,
            created_by=created_by,
        )
        upload = AppUpload(
            deployment_id=deployment.id,
            workspace_id=workspace_id,
            app_id=app_id,
            staging_key=f"pending/{deployment.id}",
            expected_size_bytes=expected_size_bytes,
            expected_sha256=expected_sha256,
            expires_at=_utcnow() + timedelta(hours=1),
            created_by=created_by,
        )
        self._uploads[upload.id] = upload
        self._deployments[deployment.id] = deployment
        return upload, deployment

    def stage(self, upload_id: UUID, source: BinaryIO) -> AppUpload:
        """Store upload bytes privately using a server-generated staging key."""
        upload = self._get_upload(upload_id)
        self._ensure_pending(upload)
        upload.staging_key = self._store.write_staged(upload.id, source)
        return upload

    def complete(self, upload_id: UUID) -> AppDeployment:
        """Verify a staged archive and materialize an immutable ready deployment."""
        upload = self._get_upload(upload_id)
        self._ensure_pending(upload)
        deployment = self._deployments[upload.deployment_id]
        try:
            with self._store.open_staged(upload.staging_key) as archive:
                archive_digest, archive_size = self._digest(archive)
            if archive_size != upload.expected_size_bytes:
                raise BundleValidationError(
                    "hosted_apps.upload.size_mismatch",
                    "Uploaded archive size does not match the declared size.",
                )
            if upload.expected_sha256 and upload.expected_sha256 != archive_digest:
                raise BundleValidationError(
                    "hosted_apps.upload.checksum_mismatch",
                    "Uploaded archive checksum does not match the declared checksum.",
                )
            with self._store.open_staged(upload.staging_key) as archive:
                manifest = validate_bundle(archive, limits=self._limits)
            with self._store.open_staged(upload.staging_key) as archive:
                self._write_assets(deployment.id, archive, manifest.files)
            manifest_bytes = manifest.model_dump_json(exclude_none=True).encode()
            self._store.write_manifest(deployment.id, BytesIO(manifest_bytes))
            deployment.status = DeploymentStatus.READY
            deployment.archive_sha256 = archive_digest
            deployment.manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
            deployment.validated_at = _utcnow()
            upload.actual_size_bytes = archive_size
            upload.actual_sha256 = archive_digest
            upload.completed_at = _utcnow()
            upload.status = "completed"
            return deployment
        except BundleValidationError as exc:
            deployment.status = DeploymentStatus.FAILED
            deployment.validation_error_code = exc.code
            deployment.validation_error_message = str(exc)
            deployment.validated_at = _utcnow()
            upload.status = "failed"
            raise

    def get_deployment(self, deployment_id: UUID) -> AppDeployment:
        """Return a deployment candidate or raise a stable not-found error."""
        try:
            return self._deployments[deployment_id]
        except KeyError as exc:
            raise UploadNotFoundError(str(deployment_id)) from exc

    def _write_assets(
        self, deployment_id: UUID, archive: BinaryIO, files: Mapping[str, object]
    ) -> None:
        """Extract only validator-approved members into the immutable prefix."""
        with zipfile.ZipFile(archive) as bundle:
            for path in files:
                with bundle.open(path) as source:
                    self._store.write_deployment_file(
                        deployment_id, path, cast(BinaryIO, source)
                    )

    @staticmethod
    def _digest(source: BinaryIO) -> tuple[str, int]:
        """Digest an archive incrementally without retaining archive bytes."""
        digest = hashlib.sha256()
        size = 0
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        return digest.hexdigest(), size

    def _get_upload(self, upload_id: UUID) -> AppUpload:
        """Resolve an upload without revealing any cross-workspace metadata."""
        try:
            return self._uploads[upload_id]
        except KeyError as exc:
            raise UploadNotFoundError(str(upload_id)) from exc

    @staticmethod
    def _ensure_pending(upload: AppUpload) -> None:
        """Reject expiry/replay before any provider object is examined."""
        if upload.status != "pending" or upload.completed_at is not None:
            raise BundleValidationError(
                "hosted_apps.upload.already_completed",
                "Upload has already been completed.",
            )
        if upload.expires_at <= _utcnow():
            upload.status = "expired"
            raise BundleValidationError(
                "hosted_apps.upload.expired", "Upload has expired."
            )
