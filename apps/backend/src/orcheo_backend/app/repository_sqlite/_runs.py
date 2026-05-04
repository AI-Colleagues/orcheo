"""Workflow run persistence and lifecycle helpers."""

from __future__ import annotations
from collections.abc import Callable
from typing import Any
from uuid import UUID
from orcheo.models.workflow import WorkflowRun
from orcheo_backend.app.repository import WorkflowNotFoundError
from orcheo_backend.app.repository_sqlite._persistence import SqlitePersistenceMixin


class WorkflowRunMixin(SqlitePersistenceMixin):
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
    ) -> WorkflowRun:
        await self._ensure_initialized()
        async with self._lock:
            workflow = await self._get_workflow_locked(workflow_id)
            if workflow.is_archived:
                raise WorkflowNotFoundError(str(workflow_id))
            await self._ensure_workflow_health(workflow_id, actor=actor or triggered_by)
            run = await self._create_run_locked(
                workflow_id=workflow_id,
                workflow_version_id=workflow_version_id,
                triggered_by=triggered_by,
                input_payload=input_payload,
                actor=actor,
                runnable_config=runnable_config,
            )
            return run.model_copy(deep=True)

    async def list_runs_for_workflow(
        self, workflow_id: UUID, *, limit: int | None = None
    ) -> list[WorkflowRun]:
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(workflow_id)
            query = """
                SELECT payload
                  FROM workflow_runs
                 WHERE workflow_id = ?
              ORDER BY created_at DESC
            """
            params: list[Any] = [str(workflow_id)]
            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)
            async with self._connection() as conn:
                cursor = await conn.execute(query, tuple(params))
                rows = await cursor.fetchall()
            return [
                WorkflowRun.model_validate_json(row["payload"]).model_copy(deep=True)
                for row in rows
            ]

    async def get_run(self, run_id: UUID) -> WorkflowRun:
        await self._ensure_initialized()
        async with self._lock:
            return await self._get_run_locked(run_id)

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
                await conn.executescript(
                    """
                    DELETE FROM workflow_runs;
                    DELETE FROM workflow_run_remediations;
                    DELETE FROM workflow_versions;
                    DELETE FROM workflows;
                    DELETE FROM webhook_triggers;
                    DELETE FROM cron_triggers;
                    DELETE FROM retry_policies;
                    DELETE FROM listener_dedupe;
                    DELETE FROM listener_cursors;
                    DELETE FROM listener_subscriptions;
                    """
                )
            self._trigger_layer.reset()

    async def _update_run(
        self,
        run_id: UUID,
        updater: Callable[[WorkflowRun], None],
    ) -> WorkflowRun:
        await self._ensure_initialized()
        async with self._lock:
            run = await self._get_run_locked(run_id)
            updater(run)
            async with self._connection() as conn:
                await conn.execute(
                    """
                    UPDATE workflow_runs
                       SET status = ?, payload = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (
                        run.status.value,
                        self._dump_model(run),
                        run.updated_at.isoformat(),
                        str(run.id),
                    ),
                )
            return run.model_copy(deep=True)


__all__ = ["WorkflowRunMixin"]
