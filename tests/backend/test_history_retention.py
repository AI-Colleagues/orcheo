"""Tests for the workflow trace expiry background task."""

from __future__ import annotations
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
import pytest
from orcheo_backend.app.history import retention


class FakeHistoryStore:
    """Stand-in for PostgresRunHistoryStore recording prune cutoffs."""

    def __init__(self, pruned: int = 0) -> None:
        self.pruned = pruned
        self.cutoffs: list[datetime] = []

    async def prune_histories_older_than(self, cutoff: datetime) -> int:
        self.cutoffs.append(cutoff)
        return self.pruned


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> FakeHistoryStore:
    store = FakeHistoryStore(pruned=2)
    monkeypatch.setattr(retention, "PostgresRunHistoryStore", FakeHistoryStore)
    monkeypatch.setitem(retention._history_store_ref, "store", store)
    monkeypatch.setitem(retention._trace_retention_task, "task", None)
    monkeypatch.setattr(retention, "TRACE_RETENTION_INITIAL_DELAY_SECONDS", 0)
    return store


def _settings(values: dict[str, Any]) -> Any:
    return lambda: SimpleNamespace(
        get=lambda key, default=None: values.get(key, default)
    )


@pytest.mark.parametrize(
    ("values", "expected"),
    [({}, 7), ({"TRACE_RETENTION_DAYS": 14}, 14), ({"TRACE_RETENTION_DAYS": "0"}, 0)],
)
def test_trace_retention_days_reads_settings(
    monkeypatch: pytest.MonkeyPatch, values: dict[str, Any], expected: int
) -> None:
    monkeypatch.setattr(retention, "get_settings", _settings(values))
    assert retention.trace_retention_days() == expected


@pytest.mark.parametrize("raw", ["bad", -3])
def test_trace_retention_days_falls_back_on_invalid(
    monkeypatch: pytest.MonkeyPatch, raw: object
) -> None:
    monkeypatch.setattr(
        retention, "get_settings", _settings({"TRACE_RETENTION_DAYS": raw})
    )
    assert retention.trace_retention_days() == (7 if raw == "bad" else 0)


@pytest.mark.asyncio
async def test_prune_expired_traces_uses_retention_cutoff(
    fake_store: FakeHistoryStore,
) -> None:
    before = datetime.now(tz=UTC)
    pruned = await retention.prune_expired_traces(fake_store, 5)  # type: ignore[arg-type]

    assert pruned == 2
    (cutoff,) = fake_store.cutoffs
    assert (
        before - timedelta(days=5) <= cutoff <= datetime.now(tz=UTC) - timedelta(days=5)
    )


@pytest.mark.asyncio
async def test_prune_expired_traces_skips_log_when_nothing_pruned(
    fake_store: FakeHistoryStore, caplog: pytest.LogCaptureFixture
) -> None:
    fake_store.pruned = 0
    with caplog.at_level("INFO", logger=retention.logger.name):
        assert await retention.prune_expired_traces(fake_store, 1) == 0  # type: ignore[arg-type]
    assert not caplog.records


@pytest.mark.asyncio
async def test_retention_task_prunes_then_cancels(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeHistoryStore
) -> None:
    monkeypatch.setattr(retention, "trace_retention_days", lambda: 3)
    monkeypatch.setattr(retention, "TRACE_RETENTION_INTERVAL_SECONDS", 3600)

    await retention.ensure_trace_retention_task()
    task = retention._trace_retention_task["task"]
    assert task is not None

    # A second call must not spawn a duplicate task.
    await retention.ensure_trace_retention_task()
    assert retention._trace_retention_task["task"] is task

    for _ in range(3):
        await asyncio.sleep(0)
    assert len(fake_store.cutoffs) == 1

    await retention.cancel_trace_retention_task()
    assert task.cancelled()
    assert retention._trace_retention_task["task"] is None


@pytest.mark.asyncio
async def test_retention_task_survives_prune_errors(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeHistoryStore
) -> None:
    async def _boom(_cutoff: datetime) -> int:
        raise RuntimeError("db down")

    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if seconds:
            raise asyncio.CancelledError

    monkeypatch.setattr(fake_store, "prune_histories_older_than", _boom)
    monkeypatch.setattr(retention, "trace_retention_days", lambda: 3)
    monkeypatch.setattr(retention.asyncio, "sleep", _sleep)

    await retention.ensure_trace_retention_task()
    task = retention._trace_retention_task["task"]
    assert task is not None
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sleeps == [0, retention.TRACE_RETENTION_INTERVAL_SECONDS]
    assert retention._trace_retention_task["task"] is None


@pytest.mark.asyncio
async def test_retention_task_propagates_cancellation_during_prune(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeHistoryStore
) -> None:
    async def _cancel(_cutoff: datetime) -> int:
        raise asyncio.CancelledError

    monkeypatch.setattr(fake_store, "prune_histories_older_than", _cancel)
    monkeypatch.setattr(retention, "trace_retention_days", lambda: 3)

    await retention.ensure_trace_retention_task()
    task = retention._trace_retention_task["task"]
    assert task is not None
    with pytest.raises(asyncio.CancelledError):
        await task
    assert retention._trace_retention_task["task"] is None


@pytest.mark.asyncio
async def test_retention_task_disabled_when_zero_days(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeHistoryStore
) -> None:
    monkeypatch.setattr(retention, "trace_retention_days", lambda: 0)

    await retention.ensure_trace_retention_task()

    assert retention._trace_retention_task["task"] is None
    assert fake_store.cutoffs == []


@pytest.mark.asyncio
async def test_retention_task_skips_non_postgres_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(retention._history_store_ref, "store", object())
    monkeypatch.setitem(retention._trace_retention_task, "task", None)

    await retention.ensure_trace_retention_task()

    assert retention._trace_retention_task["task"] is None


@pytest.mark.asyncio
async def test_cancel_retention_task_is_noop_when_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(retention._trace_retention_task, "task", None)
    await retention.cancel_trace_retention_task()
    assert retention._trace_retention_task["task"] is None


@pytest.mark.asyncio
async def test_retention_task_defers_first_sweep(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeHistoryStore
) -> None:
    monkeypatch.setattr(retention, "trace_retention_days", lambda: 3)
    monkeypatch.setattr(retention, "TRACE_RETENTION_INITIAL_DELAY_SECONDS", 3600)

    await retention.ensure_trace_retention_task()
    for _ in range(3):
        await asyncio.sleep(0)
    await retention.cancel_trace_retention_task()

    assert fake_store.cutoffs == []
