"""Workflow run persistence and lifecycle helpers."""

from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from orcheo.models import WorkflowRun
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
)
from orcheo_backend.app.repository_postgres._persistence import PostgresPersistenceMixin


def _load_run(payload: dict[str, Any] | str) -> WorkflowRun:
    """Decode a run from either JSON text or a PostgreSQL JSONB mapping."""
    if isinstance(payload, str):
        return WorkflowRun.model_validate_json(payload)
    return WorkflowRun.model_validate(payload)


@dataclass(frozen=True)
class StaleActiveRuns:
    """Oldest active run and count for one workspace and status."""

    workspace_id: str
    status: str
    count: int
    oldest_updated_at: datetime


class WorkflowRunMixin(PostgresPersistenceMixin):
    """Create and update workflow runs."""

    async def create_run(
        self,
        workflow_id: UUID,
        *,
        workflow_version_id: UUID,
        triggered_by: str,
        input_payload: dict[str, Any],
        actor: str | None = None,
        runnable_config: dict[str, Any] | None = None,
        workspace_id: str | None = None,
    ) -> WorkflowRun:
        await self._ensure_initialized()
        async with self._lock:
            workflow = await self._get_workflow_locked(workflow_id)
            if workflow.is_archived:
                raise WorkflowNotFoundError(str(workflow_id))
            await self._ensure_workflow_health(
                workflow_id,
                actor=actor or triggered_by,
                workspace_id=workspace_id,
            )
            run = await self._create_run_locked(
                workflow_id=workflow_id,
                workflow_version_id=workflow_version_id,
                triggered_by=triggered_by,
                input_payload=input_payload,
                actor=actor,
                runnable_config=runnable_config,
                workspace_id=workspace_id,
            )
            return run.model_copy(deep=True)

    async def list_runs_for_workflow(
        self,
        workflow_id: UUID,
        *,
        limit: int | None = None,
        workspace_id: str | None = None,
    ) -> list[WorkflowRun]:
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(workflow_id)
            if workspace_id is not None:
                query = """
                    SELECT payload
                      FROM workflow_runs
                     WHERE workflow_id = %s
                       AND workspace_id = %s
                  ORDER BY created_at DESC
                """
                params: list[Any] = [str(workflow_id), workspace_id]
            else:
                query = """
                    SELECT payload
                      FROM workflow_runs
                     WHERE workflow_id = %s
                  ORDER BY created_at DESC
                """
                params = [str(workflow_id)]
            if limit is not None:
                query += " LIMIT %s"
                params.append(limit)
            async with self._connection() as conn:
                cursor = await conn.execute(query, tuple(params))
                rows = await cursor.fetchall()
            result = []
            for row in rows:
                run = _load_run(row["payload"])
                result.append(run.model_copy(deep=True))
            return result

    async def get_run(
        self,
        run_id: UUID,
        *,
        workspace_id: str | None = None,
    ) -> WorkflowRun:
        await self._ensure_initialized()
        async with self._lock:
            run = await self._get_run_locked(run_id)
            if workspace_id is not None:
                row_tid = await self._get_run_workspace_id_locked(run_id)
                if row_tid is not None and row_tid != workspace_id:
                    raise WorkflowRunNotFoundError(str(run_id))
            return run

    async def _get_run_workspace_id_locked(self, run_id: UUID) -> str | None:
        """Return the workspace_id column for a workflow_run row (Postgres)."""
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT workspace_id FROM workflow_runs WHERE id = %s",
                (str(run_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        try:
            return row["workspace_id"]
        except (KeyError, IndexError, TypeError):
            return None

    async def mark_run_started(self, run_id: UUID, *, actor: str) -> WorkflowRun:
        return await self._update_run(run_id, lambda run: run.mark_started(actor=actor))

    async def mark_run_succeeded(
        self,
        run_id: UUID,
        *,
        actor: str,
        output: dict[str, Any] | None,
    ) -> WorkflowRun:
        run = await self._update_run(
            run_id,
            lambda candidate: candidate.mark_succeeded(actor=actor, output=output),
        )
        self._release_cron_run(run_id)
        self._trigger_layer.clear_retry_state(run_id)
        return run

    async def mark_run_failed(
        self,
        run_id: UUID,
        *,
        actor: str,
        error: str,
    ) -> WorkflowRun:
        run = await self._update_run(
            run_id,
            lambda candidate: candidate.mark_failed(actor=actor, error=error),
        )
        self._release_cron_run(run_id)
        return run

    async def mark_run_cancelled(
        self,
        run_id: UUID,
        *,
        actor: str,
        reason: str | None,
    ) -> WorkflowRun:
        run = await self._update_run(
            run_id,
            lambda candidate: candidate.mark_cancelled(actor=actor, reason=reason),
        )
        self._release_cron_run(run_id)
        self._trigger_layer.clear_retry_state(run_id)
        return run

    async def reset(self) -> None:
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                await conn.execute("DELETE FROM workflow_runs")
                await conn.execute("DELETE FROM workflow_versions")
                await conn.execute("DELETE FROM workflows")
                await conn.execute("DELETE FROM webhook_triggers")
                await conn.execute("DELETE FROM cron_triggers")
                await conn.execute("DELETE FROM retry_policies")
                await conn.execute("DELETE FROM listener_dedupe")
                await conn.execute("DELETE FROM listener_cursors")
                await conn.execute("DELETE FROM listener_subscriptions")
            self._trigger_layer.reset()

    async def _update_run(
        self,
        run_id: UUID,
        updater: Callable[[WorkflowRun], None],
    ) -> WorkflowRun:
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    "SELECT payload FROM workflow_runs WHERE id = %s FOR UPDATE",
                    (str(run_id),),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise WorkflowRunNotFoundError(str(run_id))
                payload = row["payload"]
                run = _load_run(payload)
                updater(run)
                await conn.execute(
                    """
                    UPDATE workflow_runs
                       SET status = %s, payload = %s, updated_at = %s
                     WHERE id = %s
                    """,
                    (
                        run.status.value,
                        self._dump_model(run),
                        run.updated_at,
                        str(run.id),
                    ),
                )
            return run.model_copy(deep=True)

    async def claim_stale_pending_runs(
        self,
        *,
        min_age: timedelta = timedelta(minutes=2),
        retry_interval: timedelta = timedelta(minutes=5),
        limit: int = 20,
    ) -> list[WorkflowRun]:
        """Claim old pending runs for bounded, cross-process safe republishing."""
        await self._ensure_initialized()
        now = datetime.now(UTC)
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                WITH candidates AS (
                    SELECT id FROM workflow_runs
                    WHERE status = 'pending'
                      AND dispatch_requested = TRUE
                      AND enqueue_confirmed = FALSE
                      AND created_at <= %s
                      AND (last_enqueue_attempt_at IS NULL
                           OR last_enqueue_attempt_at <= %s)
                    ORDER BY created_at
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE workflow_runs AS runs
                   SET last_enqueue_attempt_at = %s
                  FROM candidates
                 WHERE runs.id = candidates.id
                RETURNING runs.payload
                """,
                (now - min_age, now - retry_interval, limit, now),
            )
            rows = await cursor.fetchall()
        return [_load_run(row["payload"]) for row in rows]

    async def list_stale_active_runs(
        self, *, older_than: datetime
    ) -> list[StaleActiveRuns]:
        """Find active quota slots that have not changed since the cutoff."""
        await self._ensure_initialized()
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT workspace_id, status, COUNT(*) AS count,
                       MIN(updated_at) AS oldest_updated_at
                  FROM workflow_runs
                 WHERE workspace_id IS NOT NULL
                   AND status IN ('pending', 'running')
                   AND updated_at <= %s
              GROUP BY workspace_id, status
                """,
                (older_than,),
            )
            rows = await cursor.fetchall()
        return [
            StaleActiveRuns(
                workspace_id=row["workspace_id"],
                status=row["status"],
                count=row["count"],
                oldest_updated_at=row["oldest_updated_at"],
            )
            for row in rows
        ]


__all__ = ["WorkflowRunMixin"]
