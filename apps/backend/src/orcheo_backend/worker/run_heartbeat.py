"""Independent database heartbeat for a Celery worker's active run."""

from __future__ import annotations
import logging
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event, Thread
from uuid import UUID
import psycopg
from orcheo_backend.app.run_ownership import (
    WORKER_HEARTBEAT_INTERVAL_SECONDS,
    WORKER_LEASE_DURATION,
    WORKER_LEASE_LOSS_SHUTDOWN_SECONDS,
)


logger = logging.getLogger(__name__)


def _terminate_unresponsive_worker() -> None:
    """Stop a worker process that kept executing after losing its lease."""
    os._exit(1)


def _signal_lease_loss(
    stop: Event,
    ownership_lost: Event,
    run_id: UUID,
    on_lease_lost: Callable[[], None] | None,
) -> None:
    """Cancel execution, then enforce a bounded shutdown for blocking nodes."""
    if stop.is_set():
        return
    ownership_lost.set()
    logger.error("Worker lost ownership of run %s", run_id)
    if on_lease_lost is not None:
        try:
            on_lease_lost()
        except Exception:
            logger.exception("Could not cancel run %s after lease loss", run_id)
    if not stop.wait(WORKER_LEASE_LOSS_SHUTDOWN_SECONDS):
        logger.critical(
            "Run %s did not stop after lease loss; terminating worker process",
            run_id,
        )
        _terminate_unresponsive_worker()


def renew_worker_run_lease(dsn: str, run_id: UUID, owner_token: str) -> bool:
    """Renew ownership in a separate connection; return false if it was lost."""
    with psycopg.connect(
        dsn, connect_timeout=5, options="-c statement_timeout=5000"
    ) as conn:
        cursor = conn.execute(
            """
            UPDATE workflow_runs
               SET worker_heartbeat_at = clock_timestamp(),
                   worker_lease_expires_at = clock_timestamp() + %s
             WHERE id = %s AND status = 'running' AND worker_owner_token = %s
         RETURNING id
            """,
            (WORKER_LEASE_DURATION, str(run_id), owner_token),
        )
        return cursor.fetchone() is not None


@contextmanager
def worker_run_heartbeat(
    dsn: str,
    run_id: UUID,
    owner_token: str,
    *,
    on_lease_lost: Callable[[], None] | None = None,
) -> Iterator[Event]:
    """Keep a long-running workflow leased even if its async loop is busy."""
    stop = Event()
    ownership_lost = Event()

    def run() -> None:
        last_renewed = time.monotonic()
        while not stop.wait(WORKER_HEARTBEAT_INTERVAL_SECONDS):
            try:
                if not renew_worker_run_lease(dsn, run_id, owner_token):
                    _signal_lease_loss(stop, ownership_lost, run_id, on_lease_lost)
                    return
                last_renewed = time.monotonic()
            except Exception:
                logger.exception("Could not renew worker lease for run %s", run_id)
                if (
                    time.monotonic() - last_renewed
                    >= WORKER_LEASE_DURATION.total_seconds()
                ):
                    _signal_lease_loss(stop, ownership_lost, run_id, on_lease_lost)
                    return

    thread = Thread(target=run, name=f"run-heartbeat-{run_id}", daemon=True)
    thread.start()
    try:
        yield ownership_lost
    finally:
        stop.set()
        thread.join(timeout=10)
