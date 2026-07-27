"""Hosted Apps bundle cleanup and reconciliation process."""

from __future__ import annotations
import argparse
import os
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from orcheo.hosted_apps import PostgresBundleStore


def reconcile_filesystem(root: Path, *, retention_seconds: int) -> int:
    """Delete only expired uncommitted staging and partial deployment prefixes."""
    resolved = root.expanduser().resolve()
    if resolved == Path("/") or len(resolved.parts) < 3:
        raise ValueError("Hosted Apps cleanup root is too broad.")
    cutoff = datetime.now(UTC) - timedelta(seconds=retention_seconds)
    removed = 0
    for relative in ("staging", "partial"):
        parent = resolved / relative
        if not parent.is_dir():
            continue
        for candidate in parent.iterdir():
            try:
                modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
            except FileNotFoundError:
                continue
            if modified > cutoff:
                continue
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()
            removed += 1
    return removed


def reconcile_postgres(store: PostgresBundleStore, *, retention_seconds: int) -> int:
    """Delete expired staged PostgreSQL bundle objects."""
    cutoff = datetime.now(UTC) - timedelta(seconds=retention_seconds)
    return store.delete_expired_staging(cutoff)


def main() -> None:
    """Run reconciliation periodically, or once for probes and maintenance."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    root = Path(
        os.environ.get("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", "/data/app-bundles")
    )
    retention = int(
        os.environ.get("ORCHEO_HOSTED_APPS_PARTIAL_RETENTION_SECONDS", "86400")
    )
    interval = int(os.environ.get("ORCHEO_HOSTED_APPS_CLEANUP_INTERVAL_SECONDS", "900"))
    backend = os.environ.get("ORCHEO_APP_BUNDLE_BACKEND", "filesystem").strip().lower()
    postgres_store = None
    if backend == "postgres":
        dsn = os.environ.get("ORCHEO_POSTGRES_DSN", "").strip()
        postgres_store = PostgresBundleStore(dsn)
    try:
        while True:  # pragma: no cover - daemon mode is exercised by deployment probes
            if postgres_store is not None:
                reconcile_postgres(postgres_store, retention_seconds=retention)
            else:
                reconcile_filesystem(root, retention_seconds=retention)
            if args.once:
                return
            time.sleep(interval)
    finally:
        if postgres_store is not None:
            postgres_store.close()


if __name__ == "__main__":  # pragma: no cover - module entry point
    main()
