"""Hosted Apps filesystem cleanup and reconciliation process."""

from __future__ import annotations
import argparse
import os
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path


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
    while True:  # pragma: no cover - daemon mode is exercised by deployment probes
        reconcile_filesystem(root, retention_seconds=retention)
        if args.once:
            return
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover - module entry point
    main()
