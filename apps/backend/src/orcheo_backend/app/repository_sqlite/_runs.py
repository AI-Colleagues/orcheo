"""Workflow run persistence and lifecycle helpers."""

from __future__ import annotations
from collections.abc import Callable
from typing import Any
from uuid import UUID
from orcheo.models.workflow import WorkflowRun
from orcheo.tenancy.scoping import ensure_tenant_id
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
)
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
        tenant_id: str | None = None,
    ) -> WorkflowRun:
        await self._ensure_initialized()
        async with self._lock:
            workflow = await self._get_workflow_locked(workflow_id)
            if workflow.is_archived:
                raise WorkflowNotFoundError(str(workflow_id))
            await self._ensure_workflow_health(
                workflow_id,
                actor=actor or triggered_by,
                tenant_id=tenant_id,
            )
            run = await self._create_run_locked(
                workflow_id=workflow_id,
                workflow_version_id=workflow_version_id,
                triggered_by=triggered_by,
                input_payload=input_payload,
                actor=actor,
                runnable_config=runnable_config,
                tenant_id=tenant_id,
            )
            return run.model_copy(deep=True)

    async def list_runs_for_workflow(
        self,
        workflow_id: UUID,
        *,
        limit: int | None = None,
        tenant_id: str | None = None,
    ) -> list[WorkflowRun]:
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(workflow_id)
            if tenant_id is not None:
                tid = ensure_tenant_id(tenant_id)
                query = """
                    SELECT payload
                      FROM workflow_runs
                     WHERE workflow_id = ?
                       AND (tenant_id = ? OR tenant_id IS NULL)
                  ORDER BY created_at DESC
                """
                params: list[Any] = [str(workflow_id), tid]
            else:
                query = """
                    SELECT payload
                      FROM workflow_runs
                     WHERE workflow_id = ?
                  ORDER BY created_at DESC
                """
                params = [str(workflow_id)]
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

    async def get_run(
        self,
        run_id: UUID,
        *,
        tenant_id: str | None = None,
    ) -> WorkflowRun:
        await self._ensure_initialized()
        async with self._lock:
            run = await self._get_run_locked(run_id)
            if tenant_id is not None:
                tid = ensure_tenant_id(tenant_id)
                row_tid = await self._get_run_tenant_id_locked(run_id)
                if row_tid is not None and row_tid != tid:
                    raise WorkflowRunNotFoundError(str(run_id))
            return run

    async def _get_run_tenant_id_locked(self, run_id: UUID) -> str | None:
        """Return the tenant_id column for a workflow_run row."""
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT tenant_id FROM workflow_runs WHERE id = ?",
                (str(run_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        try:
            return row["tenant_id"]
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
                await conn.executescript(
                    """
                    DELETE FROM workflow_runs;
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
