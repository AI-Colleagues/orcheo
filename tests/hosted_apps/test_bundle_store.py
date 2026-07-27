"""Tests for the local-only Hosted Apps bundle store."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import uuid4
import pytest
from orcheo.hosted_apps import FilesystemBundleStore, migrate_filesystem_bundles


def test_filesystem_store_hides_and_reads_server_selected_paths(tmp_path: Path) -> None:
    """Staged and immutable assets stay rooted under the configured private directory."""
    store = FilesystemBundleStore(tmp_path / "bundles")
    upload_id = uuid4()
    deployment_id = uuid4()
    key = store.write_staged(upload_id, BytesIO(b"archive"))
    assert store.open_staged(key).read() == b"archive"
    store.write_deployment_file(deployment_id, "assets/main.js", BytesIO(b"app"))
    assert store.open_deployment_file(deployment_id, "assets/main.js").read() == b"app"
    store.healthcheck()


def test_filesystem_store_rejects_path_traversal(tmp_path: Path) -> None:
    """Object-store logical keys can never escape local development storage."""
    store = FilesystemBundleStore(tmp_path / "bundles")
    with pytest.raises(ValueError, match="relative POSIX"):
        store.open_staged("../outside")


def test_migrate_filesystem_bundles_missing_root_returns_zero(tmp_path: Path) -> None:
    """A legacy root that was never created has nothing to migrate."""
    store = FilesystemBundleStore(tmp_path / "bundles")
    assert migrate_filesystem_bundles(tmp_path / "missing", store) == 0


def test_migrate_filesystem_bundles_skips_missing_staging_and_deployments(
    tmp_path: Path,
) -> None:
    """A legacy root without staging/ or deployments/ migrates nothing."""
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    store = FilesystemBundleStore(tmp_path / "bundles")
    assert migrate_filesystem_bundles(legacy_root, store) == 0


def test_migrate_filesystem_bundles_skips_malformed_entries(tmp_path: Path) -> None:
    """Malformed legacy entries are left in place instead of migrated."""
    legacy_root = tmp_path / "legacy"
    staging = legacy_root / "staging"
    deployments = legacy_root / "deployments"
    staging.mkdir(parents=True)
    deployments.mkdir(parents=True)

    valid_upload = uuid4()
    (staging / str(valid_upload)).mkdir()
    (staging / str(valid_upload) / "archive.zip").write_bytes(b"archive")

    malformed_staging = staging / "not-a-uuid"
    malformed_staging.mkdir()
    (malformed_staging / "archive.zip").write_bytes(b"ignored")

    (deployments / "stray.txt").write_text("not a deployment directory")

    malformed_deployment = deployments / "not-a-uuid-either"
    malformed_deployment.mkdir()
    (malformed_deployment / "index.html").write_bytes(b"<h1>ignored</h1>")

    valid_deployment = uuid4()
    valid_deployment_dir = deployments / str(valid_deployment)
    (valid_deployment_dir / "assets").mkdir(parents=True)
    (valid_deployment_dir / "index.html").write_bytes(b"<h1>App</h1>")
    (valid_deployment_dir / "__manifest__.json").write_bytes(b'{"files":{}}')

    target = FilesystemBundleStore(tmp_path / "bundles")
    migrated = migrate_filesystem_bundles(legacy_root, target)

    assert migrated == 3
    assert (
        target.open_staged(f"staging/{valid_upload}/archive.zip").read() == b"archive"
    )
    assert target.open_deployment_file(valid_deployment, "index.html").read() == (
        b"<h1>App</h1>"
    )
    # Malformed and stray entries are never touched, only skipped.
    assert (malformed_staging / "archive.zip").exists()
    assert (malformed_deployment / "index.html").exists()
    assert (deployments / "stray.txt").exists()
