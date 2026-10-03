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
from orcheo.postgres_pools import connection_kwargs
from orcheo_backend.app.run_ownership import (
    WORKER_HEARTBEAT_INTERVAL_SECONDS,
    WORKER_LEASE_DURATION,
    WORKER_LEASE_LOSS_SHUTDOWN_SECONDS,
)


logger = logging.getLogger(__name__)


class _HeartbeatConnection:
    """Keep a dedicated connection outside the workflow's event loop and pool."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._connection: psycopg.Connection | None = None

    def get(self) -> psycopg.Connection:
        """Open lazily and commit each renewal independently."""
        if self._connection is None:
            kwargs = connection_kwargs(autocommit=True, row_factory=None)
            kwargs["connect_timeout"] = 5
            self._connection = psycopg.connect(
                self._dsn,
                options="-c statement_timeout=5000",
                **kwargs,
            )
        return self._connection

    def close(self) -> None:
        """Discard the connection after a failure or heartbeat shutdown."""
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()


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


def renew_worker_run_lease(
    dsn: str,
    run_id: UUID,
    owner_token: str,
    connection: _HeartbeatConnection | None = None,
) -> bool:
    """Renew ownership in a separate connection; return false if it was lost."""
    if connection is not None:
        return _renew_on_connection(connection.get(), run_id, owner_token)
    with psycopg.connect(
        dsn, connect_timeout=5, options="-c statement_timeout=5000"
    ) as conn:
        return _renew_on_connection(conn, run_id, owner_token)


def _renew_on_connection(
    conn: psycopg.Connection, run_id: UUID, owner_token: str
) -> bool:
    """Update only the running row still held by this owner."""
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
        connection = _HeartbeatConnection(dsn)
        try:
            while not stop.wait(WORKER_HEARTBEAT_INTERVAL_SECONDS):
                try:
                    if not renew_worker_run_lease(dsn, run_id, owner_token, connection):
                        _signal_lease_loss(stop, ownership_lost, run_id, on_lease_lost)
                        return
                    last_renewed = time.monotonic()
                except Exception:
                    connection.close()
                    logger.exception("Could not renew worker lease for run %s", run_id)
                    if (
                        time.monotonic() - last_renewed
                        >= WORKER_LEASE_DURATION.total_seconds()
                    ):
                        _signal_lease_loss(stop, ownership_lost, run_id, on_lease_lost)
                        return
        finally:
            connection.close()

    thread = Thread(target=run, name=f"run-heartbeat-{run_id}", daemon=True)
    thread.start()
    try:
        yield ownership_lost
    finally:
        stop.set()
        thread.join(timeout=10)
