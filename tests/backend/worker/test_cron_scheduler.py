"""Independent lean scheduler lifecycle tests."""

from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, Mock
import pytest
from orcheo_backend.worker import cron_scheduler
from orcheo_backend.worker.tasks import dispatch_cron_triggers


@pytest.mark.asyncio
async def test_standalone_scheduler_starts_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown releases the scheduler without using a workflow execution slot."""
    stop = asyncio.Event()
    service = Mock(start=AsyncMock(side_effect=stop.set), stop=AsyncMock())
    factory = Mock(return_value=service)
    repository = Mock()
    monkeypatch.setattr(cron_scheduler, "get_repository", lambda: repository)
    monkeypatch.setattr(cron_scheduler, "CronSchedulerService", factory)
    await cron_scheduler.run_scheduler(stop)
    factory.assert_called_once_with(repository=repository)
    service.start.assert_awaited_once()
    service.stop.assert_awaited_once()


def test_old_queued_cron_tasks_do_not_compete_with_standalone_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upgrade leftovers cannot dispatch a schedule alongside its new owner."""
    monkeypatch.setenv("ORCHEO_CRON_DISPATCH_OWNER", "scheduler")
    assert dispatch_cron_triggers() == {"dispatched_runs": []}
