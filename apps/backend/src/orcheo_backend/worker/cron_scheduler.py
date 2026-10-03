"""Dispatch lean-stack cron schedules without waiting in the workflow queue."""

from __future__ import annotations
import asyncio
import os
import signal
from orcheo.cron_healthcheck import heartbeat_path, record_heartbeat
from orcheo_backend.app.cron_scheduler import (
    CronSchedulerService,
    cron_dispatch_interval_seconds,
)
from orcheo_backend.app.dependencies import get_repository


async def run_scheduler(stop_event: asyncio.Event | None = None) -> None:
    """Run one scheduler until shutdown, publishing executions to Celery."""
    # Dispatch reads this flag at call time. Enforce it for direct callers too,
    # even if backend modules were imported with in-process execution enabled.
    os.environ["ORCHEO_INPROCESS_EXECUTION"] = "false"
    stop = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM) if stop_event is None else ()
    for sig in signals:
        loop.add_signal_handler(sig, stop.set)
    path = heartbeat_path()
    path.unlink(missing_ok=True)
    interval = cron_dispatch_interval_seconds()
    scheduler = CronSchedulerService(
        repository=get_repository(),
        interval_seconds=interval,
        on_dispatch=lambda: record_heartbeat(path, interval),
    )
    waiters: list[asyncio.Task[None | bool]] = []
    try:
        await scheduler.start()
        waiters.append(asyncio.create_task(stop.wait()))
        waiters.append(asyncio.create_task(scheduler.wait()))
        done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if not stop.is_set():
            for task in done:
                task.result()
            raise RuntimeError("Cron scheduler stopped unexpectedly")
    finally:
        for task in waiters:
            task.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        try:
            await scheduler.stop()
        finally:
            path.unlink(missing_ok=True)
            for sig in signals:
                loop.remove_signal_handler(sig)


if __name__ == "__main__":
    asyncio.run(run_scheduler())
