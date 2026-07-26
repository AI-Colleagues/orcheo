"""Tests for the local-only Hosted Apps bundle store."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import uuid4
import pytest
from orcheo.hosted_apps import FilesystemBundleStore


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
