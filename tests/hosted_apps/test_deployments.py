"""End-to-end tests for one-time staged bundle deployment materialization."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import uuid4
import zipfile
import pytest
from orcheo.hosted_apps import (
    BundleValidationError,
    DeploymentService,
    DeploymentStatus,
    FilesystemBundleStore,
)
from orcheo.hosted_apps.zip_validation import BundleValidationLimits


def _archive() -> bytes:
    """Build a small safe prebuilt bundle."""
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr("index.html", "<h1>Portal</h1>")
        bundle.writestr("assets/main.js", "console.log('ok')")
    return output.getvalue()


def test_completion_writes_manifest_last_and_marks_ready(tmp_path: Path) -> None:
    """Only a fully validated immutable prefix becomes a ready deployment."""
    data = _archive()
    service = DeploymentService(
        FilesystemBundleStore(tmp_path), limits=BundleValidationLimits()
    )
    upload, deployment = service.initiate(
        workspace_id=uuid4(),
        app_id=uuid4(),
        created_by="author",
        expected_size_bytes=len(data),
    )
    service.stage(upload.id, BytesIO(data))
    completed = service.complete(upload.id)
    assert completed.id == deployment.id
    assert completed.status is DeploymentStatus.READY
    assert completed.manifest_sha256 is not None
    assert (
        tmp_path / "deployments" / str(deployment.id) / "__manifest__.json"
    ).is_file()


def test_completion_rejects_replay_and_mismatched_bytes(tmp_path: Path) -> None:
    """An upload completion cannot validate two different archives."""
    service = DeploymentService(
        FilesystemBundleStore(tmp_path), limits=BundleValidationLimits()
    )
    upload, _ = service.initiate(
        workspace_id=uuid4(),
        app_id=uuid4(),
        created_by="author",
        expected_size_bytes=1,
    )
    service.stage(upload.id, BytesIO(_archive()))
    with pytest.raises(BundleValidationError) as exc_info:
        service.complete(upload.id)
    assert exc_info.value.code == "hosted_apps.upload.size_mismatch"
    with pytest.raises(BundleValidationError) as replay:
        service.complete(upload.id)
    assert replay.value.code == "hosted_apps.upload.already_completed"


def test_completion_removes_partially_written_deployment(tmp_path: Path) -> None:
    """A storage failure cannot leave an unreachable deployment prefix behind."""

    class FailingFilesystemStore(FilesystemBundleStore):
        def write_deployment_file(self, deployment_id, path, source):
            key = super().write_deployment_file(deployment_id, path, source)
            if path == "assets/main.js":
                raise RuntimeError("storage interrupted")
            return key

    data = _archive()
    service = DeploymentService(
        FailingFilesystemStore(tmp_path), limits=BundleValidationLimits()
    )
    upload, deployment = service.initiate(
        workspace_id=uuid4(),
        app_id=uuid4(),
        created_by="author",
        expected_size_bytes=len(data),
    )
    service.stage(upload.id, BytesIO(data))

    with pytest.raises(RuntimeError, match="storage interrupted"):
        service.complete(upload.id)

    assert not (tmp_path / "deployments" / str(deployment.id)).exists()
