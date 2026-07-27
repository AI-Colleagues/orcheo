"""Scoped cleanup tests for abandoned Hosted Apps filesystem prefixes."""

import os
import sys
from datetime import datetime
from pathlib import Path

from orcheo_backend.app.hosted_apps import cleanup
from orcheo_backend.app.hosted_apps.cleanup import (
    reconcile_filesystem,
    reconcile_postgres,
)


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


def test_reconcile_postgres_delegates_to_store() -> None:
    calls: list[datetime] = []

    class _FakeStore:
        def delete_expired_staging(self, cutoff: datetime) -> int:
            calls.append(cutoff)
            return 3

    removed = reconcile_postgres(_FakeStore(), retention_seconds=60)

    assert removed == 3
    assert len(calls) == 1


def test_main_once_uses_postgres_backend_and_closes_store(monkeypatch) -> None:
    events: list[tuple[str, ...]] = []

    class _FakeStore:
        def delete_expired_staging(self, cutoff: datetime) -> int:
            events.append(("reconcile", cutoff))
            return 0

        def close(self) -> None:
            events.append(("close",))

    def fake_postgres_bundle_store(dsn: str) -> _FakeStore:
        events.append(("open", dsn))
        return _FakeStore()

    monkeypatch.setenv("ORCHEO_APP_BUNDLE_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://example")
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_PARTIAL_RETENTION_SECONDS", "60")
    monkeypatch.setattr(cleanup, "PostgresBundleStore", fake_postgres_bundle_store)
    monkeypatch.setattr(sys, "argv", ["cleanup", "--once"])

    cleanup.main()

    assert events[0] == ("open", "postgresql://example")
    assert events[1][0] == "reconcile"
    assert events[-1] == ("close",)
