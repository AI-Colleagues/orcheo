"""Dispatch lean-stack cron schedules without waiting in the workflow queue."""

from __future__ import annotations
import asyncio
import os
import signal
from orcheo_backend.app.cron_scheduler import CronSchedulerService
from orcheo_backend.app.dependencies import get_repository


async def run_scheduler(stop_event: asyncio.Event | None = None) -> None:
    """Run one scheduler until shutdown, publishing executions to Celery."""
    stop = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM) if stop_event is None else ()
    for sig in signals:
        loop.add_signal_handler(sig, stop.set)
    scheduler = CronSchedulerService(repository=get_repository())
    try:
        await scheduler.start()
        await stop.wait()
    finally:
        await scheduler.stop()
        for sig in signals:
            loop.remove_signal_handler(sig)


if __name__ == "__main__":
    os.environ["ORCHEO_INPROCESS_EXECUTION"] = "false"
    asyncio.run(run_scheduler())
