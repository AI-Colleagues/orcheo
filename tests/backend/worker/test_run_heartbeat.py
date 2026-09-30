"""Worker ownership heartbeat and lease loss tests."""

from __future__ import annotations
from datetime import timedelta
from threading import Event
from unittest.mock import MagicMock
from uuid import uuid4
import psycopg
import pytest
from orcheo_backend.worker import run_heartbeat


def test_renew_worker_run_lease_uses_owned_running_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A heartbeat updates only its own running row."""
    run_id = uuid4()
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = (str(run_id),)
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(run_heartbeat.psycopg, "connect", connect)

    assert run_heartbeat.renew_worker_run_lease("postgresql://test", run_id, "owner")

    connect.assert_called_once_with(
        "postgresql://test", connect_timeout=5, options="-c statement_timeout=5000"
    )
    query, params = connection.execute.call_args.args
    assert "status = 'running' AND worker_owner_token = %s" in query
    assert "worker_heartbeat_at = clock_timestamp()" in query
    assert params == (run_heartbeat.WORKER_LEASE_DURATION, str(run_id), "owner")


def test_heartbeat_stops_execution_when_ownership_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed owner token signals the worker to stop."""
    cancelled = Event()
    monkeypatch.setattr(run_heartbeat, "WORKER_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(run_heartbeat, "renew_worker_run_lease", lambda *_: False)

    with run_heartbeat.worker_run_heartbeat(
        "postgresql://test", uuid4(), "owner", on_lease_lost=cancelled.set
    ) as lost:
        assert cancelled.wait(1)
        assert lost.is_set()


def test_heartbeat_continues_while_workflow_thread_is_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long synchronous node does not starve the independent heartbeat."""
    renewed = Event()
    monkeypatch.setattr(run_heartbeat, "WORKER_HEARTBEAT_INTERVAL_SECONDS", 0.01)

    def renew(*_args: object) -> bool:
        renewed.set()
        return True

    monkeypatch.setattr(run_heartbeat, "renew_worker_run_lease", renew)

    with run_heartbeat.worker_run_heartbeat("postgresql://test", uuid4(), "owner"):
        assert renewed.wait(1)


def test_heartbeat_stops_after_prolonged_database_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker stops when it cannot prove liveness within the lease."""
    cancelled = Event()
    monkeypatch.setattr(run_heartbeat, "WORKER_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(run_heartbeat, "WORKER_LEASE_DURATION", timedelta())

    def fail_renewal(*_args: object) -> bool:
        raise psycopg.OperationalError("database unavailable")

    monkeypatch.setattr(run_heartbeat, "renew_worker_run_lease", fail_renewal)

    with run_heartbeat.worker_run_heartbeat(
        "postgresql://test", uuid4(), "owner", on_lease_lost=cancelled.set
    ) as lost:
        assert cancelled.wait(1)
        assert lost.is_set()


def test_unresponsive_workflow_process_is_terminated_after_lease_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocking node cannot outlive the recovery grace period."""
    terminated = Event()
    monkeypatch.setattr(run_heartbeat, "WORKER_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(run_heartbeat, "WORKER_LEASE_LOSS_SHUTDOWN_SECONDS", 0.01)
    monkeypatch.setattr(run_heartbeat, "renew_worker_run_lease", lambda *_: False)
    monkeypatch.setattr(run_heartbeat, "_terminate_unresponsive_worker", terminated.set)

    with run_heartbeat.worker_run_heartbeat("postgresql://test", uuid4(), "owner"):
        assert terminated.wait(1)
