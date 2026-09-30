"""Timing contract for PostgreSQL-backed worker run ownership."""

from __future__ import annotations
from datetime import timedelta


WORKER_HEARTBEAT_INTERVAL_SECONDS = 20
WORKER_LEASE_DURATION = timedelta(minutes=2)
WORKER_RECOVERY_GRACE = timedelta(minutes=3)
WORKER_LEASE_LOSS_SHUTDOWN_SECONDS = 30
ORPHANED_WORKER_RUN_ERROR = "Worker execution stopped after its heartbeat expired."
