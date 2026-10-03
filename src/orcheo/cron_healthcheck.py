"""Check standalone scheduler progress without importing the backend."""

from __future__ import annotations
import math
import os
import sys
import time
from pathlib import Path
from orcheo.broker_healthcheck import main as broker_healthcheck


def heartbeat_path() -> Path:
    """Return a container-local path shared by the scheduler and its probe."""
    return Path(os.getenv("ORCHEO_CRON_HEALTH_FILE", "/tmp/orcheo-cron-heartbeat"))


def record_heartbeat(path: Path, interval_seconds: float) -> None:
    """Atomically record a successful poll and its allowed freshness window."""
    deadline = time.monotonic() + max(30.0, interval_seconds * 3)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(f"{os.getpid()} {deadline}\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def scheduler_is_healthy(path: Path) -> bool:
    """Require a live scheduler process and a recent successful dispatch poll."""
    try:
        pid_text, deadline_text = path.read_text().split()
        pid, deadline = int(pid_text), float(deadline_text)
        if pid <= 0 or not math.isfinite(deadline) or time.monotonic() > deadline:
            return False
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def main() -> int:
    """Fail when either scheduler progress or broker connectivity is unhealthy."""
    if not scheduler_is_healthy(heartbeat_path()):
        return 1
    return broker_healthcheck()


if __name__ == "__main__":
    sys.exit(main())
