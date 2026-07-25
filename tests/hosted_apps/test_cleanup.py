"""Scoped cleanup tests for abandoned Hosted Apps filesystem prefixes."""

import os
from pathlib import Path

from orcheo_backend.app.hosted_apps.cleanup import reconcile_filesystem


def test_cleanup_removes_only_expired_uncommitted_prefixes(tmp_path: Path) -> None:
    root = tmp_path / "app-bundles"
    stale = root / "staging" / "stale"
    current = root / "staging" / "current"
    ready = root / "deployments" / "ready"
    for path in (stale, current, ready):
        path.mkdir(parents=True)
    os.utime(stale, (1, 1))
    removed = reconcile_filesystem(root, retention_seconds=60)
    assert removed == 1
    assert not stale.exists()
    assert current.exists()
    assert ready.exists()
