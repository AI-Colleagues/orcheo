"""Worker ownership heartbeat and lease loss tests."""

from __future__ import annotations
from datetime import timedelta
from threading import Event
from unittest.mock import MagicMock, Mock
from uuid import uuid4
import psycopg
import pytest
from orcheo_backend.worker import run_heartbeat


@pytest.mark.parametrize("owns_run", [True, False])
def test_renew_worker_run_lease_uses_owned_running_row(
    monkeypatch: pytest.MonkeyPatch,
    owns_run: bool,
) -> None:
    """A heartbeat updates only its own running row."""
    run_id = uuid4()
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = (
        (str(run_id),) if owns_run else None
    )
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(run_heartbeat.psycopg, "connect", connect)

    assert (
        run_heartbeat.renew_worker_run_lease("postgresql://test", run_id, "owner")
        is owns_run
    )

    connect.assert_called_once_with(
        "postgresql://test", connect_timeout=5, options="-c statement_timeout=5000"
    )
    query, params = connection.execute.call_args.args
    assert "status = 'running' AND worker_owner_token = %s" in query
    assert "worker_heartbeat_at = clock_timestamp()" in query
    assert params == (run_heartbeat.WORKER_LEASE_DURATION, str(run_id), "owner")


def test_heartbeat_connection_reuses_and_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renewals reuse one connection; discarding it allows bounded reconnection."""
    first, second = MagicMock(), MagicMock()
    first.execute.return_value.fetchone.return_value = ("run",)
    second.execute.return_value.fetchone.return_value = ("run",)
    connect = Mock(side_effect=[first, second])
    monkeypatch.setattr(run_heartbeat.psycopg, "connect", connect)
    connection = run_heartbeat._HeartbeatConnection("postgresql://test")
    run_id = uuid4()
    for _ in range(2):
        assert run_heartbeat.renew_worker_run_lease(
            "postgresql://test", run_id, "owner", connection
        )
    assert connect.call_count == 1
    connection.close()
    first.close.assert_called_once()
    assert run_heartbeat.renew_worker_run_lease(
        "postgresql://test", run_id, "owner", connection
    )
    assert connect.call_count == 2
    connection.close()
    second.close.assert_called_once()


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


def test_worker_termination_exits_with_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forced shutdown uses a failing process exit status."""
    exit_process = Mock()
    monkeypatch.setattr(run_heartbeat.os, "_exit", exit_process)

    run_heartbeat._terminate_unresponsive_worker()

    exit_process.assert_called_once_with(1)


def test_lease_loss_after_shutdown_does_not_cancel_or_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A heartbeat finishing after context shutdown leaves the worker alone."""
    stop = Event()
    stop.set()
    lost = Event()
    cancel = Mock()
    terminate = Mock()
    monkeypatch.setattr(run_heartbeat, "_terminate_unresponsive_worker", terminate)

    run_heartbeat._signal_lease_loss(stop, lost, uuid4(), cancel)

    assert not lost.is_set()
    cancel.assert_not_called()
    terminate.assert_not_called()


def test_cancellation_callback_failure_still_enforces_shutdown(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A broken cancellation callback cannot let a worker outlive its lease."""
    stop = Event()
    lost = Event()
    cancel = Mock(side_effect=RuntimeError("cancel failed"))
    terminate = Mock()
    run_id = uuid4()
    monkeypatch.setattr(run_heartbeat, "WORKER_LEASE_LOSS_SHUTDOWN_SECONDS", 0)
    monkeypatch.setattr(run_heartbeat, "_terminate_unresponsive_worker", terminate)

    run_heartbeat._signal_lease_loss(stop, lost, run_id, cancel)

    assert lost.is_set()
    cancel.assert_called_once_with()
    terminate.assert_called_once_with()
    assert f"Could not cancel run {run_id} after lease loss" in caplog.text
    assert "cancel failed" in caplog.text


def test_heartbeat_recovers_from_transient_database_outage(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A brief renewal failure allows another heartbeat before lease expiry."""
    renewed = Event()
    attempts = 0

    def renew(*_args: object) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise psycopg.OperationalError("temporary outage")
        renewed.set()
        return True

    monkeypatch.setattr(run_heartbeat, "WORKER_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(run_heartbeat, "renew_worker_run_lease", renew)
    monkeypatch.setattr(run_heartbeat, "WORKER_LEASE_DURATION", timedelta(hours=1))
    with run_heartbeat.worker_run_heartbeat(
        "postgresql://test", uuid4(), "owner"
    ) as lost:
        assert renewed.wait(1)
        assert not lost.is_set()

    assert attempts >= 2
    assert "temporary outage" in caplog.text
