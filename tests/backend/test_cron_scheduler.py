"""Unit tests for the in-process cron scheduler."""

from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from orcheo_backend.app.cron_scheduler import (
    DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS,
    CronSchedulerService,
    cron_dispatch_interval_seconds,
    inprocess_cron_enabled,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("", True),
        ("nonsense", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("OFF", False),
        ("true", True),
        ("TRUE", True),
        (" 1 ", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_inprocess_cron_enabled(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool
) -> None:
    """Cron dispatch is on unless the environment explicitly disables it."""
    if value is None:
        monkeypatch.delenv("ORCHEO_INPROCESS_CRON", raising=False)
    else:
        monkeypatch.setenv("ORCHEO_INPROCESS_CRON", value)
    assert inprocess_cron_enabled() is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS),
        ("5", 5.0),
        ("0.5", 0.5),
        ("not-a-number", DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS),
        ("0", DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS),
        ("-3", DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS),
        # float() accepts these and both compare False against <= 0, so
        # without an explicit finiteness check they reach asyncio.wait_for,
        # whose selector raises TypeError inside the event loop itself.
        ("nan", DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS),
        ("NaN", DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS),
        ("inf", DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS),
        ("-inf", DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS),
    ],
)
def test_cron_dispatch_interval_seconds(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: float
) -> None:
    """Invalid, non-positive, or non-finite intervals fall back to the default."""
    if value is None:
        monkeypatch.delenv("ORCHEO_CRON_DISPATCH_INTERVAL", raising=False)
    else:
        monkeypatch.setenv("ORCHEO_CRON_DISPATCH_INTERVAL", value)
    assert cron_dispatch_interval_seconds() == expected


def test_scheduler_reads_interval_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unspecified interval is resolved from the environment."""
    monkeypatch.setenv("ORCHEO_CRON_DISPATCH_INTERVAL", "12")
    scheduler = CronSchedulerService(repository=MagicMock())
    assert scheduler._interval_seconds == 12.0


@pytest.mark.asyncio
async def test_dispatch_once_returns_run_count() -> None:
    """dispatch_once reports how many runs the repository created."""
    repository = MagicMock()
    repository.dispatch_due_cron_runs = AsyncMock(
        return_value=[MagicMock(), MagicMock()]
    )
    scheduler = CronSchedulerService(repository=repository, interval_seconds=0.01)

    assert await scheduler.dispatch_once() == 2
    assert await scheduler.dispatch_once() == 2


@pytest.mark.asyncio
async def test_dispatch_once_without_due_runs() -> None:
    """No due runs is not an error and dispatches nothing."""
    repository = MagicMock()
    repository.dispatch_due_cron_runs = AsyncMock(return_value=[])
    scheduler = CronSchedulerService(repository=repository, interval_seconds=0.01)

    assert await scheduler.dispatch_once() == 0


@pytest.mark.asyncio
async def test_start_dispatches_repeatedly_then_stops() -> None:
    """The loop polls the repository until stopped."""
    dispatched = asyncio.Event()
    repository = MagicMock()

    async def _dispatch() -> list[object]:
        dispatched.set()
        return []

    repository.dispatch_due_cron_runs = AsyncMock(side_effect=_dispatch)
    scheduler = CronSchedulerService(repository=repository, interval_seconds=0.01)

    await scheduler.start()
    # A second start is a no-op while the loop is already running.
    await scheduler.start()
    await asyncio.wait_for(dispatched.wait(), timeout=1)
    await scheduler.stop()

    assert scheduler._task is None
    assert repository.dispatch_due_cron_runs.await_count >= 1


@pytest.mark.asyncio
async def test_stop_without_start_is_noop() -> None:
    """Stopping a scheduler that never started does nothing."""
    scheduler = CronSchedulerService(repository=MagicMock(), interval_seconds=0.01)
    await scheduler.stop()
    assert scheduler._task is None


@pytest.mark.asyncio
async def test_dispatch_failures_do_not_stop_the_loop() -> None:
    """A failing dispatch is logged and the loop keeps polling."""
    calls: list[int] = []
    succeeded = asyncio.Event()

    async def _dispatch() -> list[object]:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is down")
        succeeded.set()
        return []

    repository = MagicMock()
    repository.dispatch_due_cron_runs = AsyncMock(side_effect=_dispatch)
    scheduler = CronSchedulerService(repository=repository, interval_seconds=0.01)

    await scheduler.start()
    await asyncio.wait_for(succeeded.wait(), timeout=1)
    await scheduler.stop()

    assert len(calls) >= 2


@pytest.mark.asyncio
async def test_stop_cancels_a_blocked_dispatch() -> None:
    """A dispatch that never returns is cancelled on stop."""
    started = asyncio.Event()

    async def _dispatch() -> list[object]:
        started.set()
        await asyncio.sleep(3600)
        return []  # pragma: no cover - cancelled before returning

    repository = MagicMock()
    repository.dispatch_due_cron_runs = AsyncMock(side_effect=_dispatch)
    scheduler = CronSchedulerService(repository=repository, interval_seconds=0.01)

    await scheduler.start()
    await asyncio.wait_for(started.wait(), timeout=1)
    await scheduler.stop()

    assert scheduler._task is None


@pytest.mark.asyncio
async def test_stop_after_loop_exits_on_its_own() -> None:
    """A loop that already noticed the stop event is not cancelled again."""
    repository = MagicMock()
    repository.dispatch_due_cron_runs = AsyncMock(return_value=[])
    scheduler = CronSchedulerService(repository=repository, interval_seconds=0.01)

    await scheduler.start()
    task = scheduler._task
    assert task is not None
    scheduler._stop_event.set()
    await asyncio.wait_for(task, timeout=1)

    await scheduler.stop()

    assert task.done()
    assert not task.cancelled()
    assert scheduler._task is None
