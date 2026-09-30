"""Republish persisted runs stranded by a temporary Celery broker outage."""

from __future__ import annotations
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from orcheo_backend.app.local_execution import inprocess_execution_enabled
from orcheo_backend.app.repository import WorkflowRepository
from orcheo_backend.app.repository_postgres import PostgresWorkflowRepository
from orcheo_backend.app.repository_postgres._triggers import _enqueue_run_for_execution


logger = logging.getLogger(__name__)
STALE_ACTIVE_RUN_AGE = timedelta(hours=1)
STALE_ACTIVE_RUN_CHECK_INTERVAL = 5


async def reconcile_pending_runs(repository: PostgresWorkflowRepository) -> int:
    """Republish a bounded batch of stale pending runs; return the number sent."""
    published = 0
    for _ in range(20):
        # Claim one at a time so a broker failure leaves the rest eligible for
        # the next pass instead of stamping an entire unpublished batch.
        runs = await repository.claim_stale_pending_runs(limit=1)
        if not runs:
            break
        run = runs[0]
        if not await asyncio.to_thread(_enqueue_run_for_execution, run):
            break
        await repository.mark_run_enqueued(run.id)
        published += 1
    if published:
        logger.warning("Republished %d stale pending runs", published)
    return published


async def run_pending_reconciler(repository: PostgresWorkflowRepository) -> None:
    """Keep checking for stranded runs while the backend is alive."""
    passes = 0
    while True:
        try:
            await reconcile_pending_runs(repository)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Pending run reconciliation failed")
        try:
            if passes % STALE_ACTIVE_RUN_CHECK_INTERVAL == 0:
                await log_stale_active_runs(repository)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Stale active run monitoring failed")
        passes += 1
        await asyncio.sleep(60)


async def log_stale_active_runs(repository: PostgresWorkflowRepository) -> None:
    """Warn operators about old quota slots without changing run state."""
    cutoff = datetime.now(UTC) - STALE_ACTIVE_RUN_AGE
    for group in await repository.list_stale_active_runs(older_than=cutoff):
        logger.warning(
            "Stale active workflow runs: workspace_id=%s status=%s count=%d "
            "oldest_updated_at=%s; review worker and run history",
            group.workspace_id,
            group.status,
            group.count,
            group.oldest_updated_at.isoformat(),
        )


@asynccontextmanager
async def pending_run_reconciliation(
    repository: WorkflowRepository,
) -> AsyncIterator[None]:
    """Run reconciliation only for PostgreSQL deployments using Celery."""
    if (
        not isinstance(repository, PostgresWorkflowRepository)
        or inprocess_execution_enabled()
    ):
        yield
        return
    task = asyncio.create_task(
        run_pending_reconciler(repository), name="pending_run_reconciler"
    )
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
