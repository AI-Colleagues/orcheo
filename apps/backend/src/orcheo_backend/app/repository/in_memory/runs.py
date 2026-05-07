"""Workflow run lifecycle helpers."""

from __future__ import annotations
from typing import Any
from uuid import UUID
from orcheo.models.workflow import WorkflowRun
from orcheo_backend.app.repository.errors import (
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
)
from orcheo_backend.app.repository.in_memory.state import InMemoryRepositoryState


class WorkflowRunMixin(InMemoryRepositoryState):
    """Implements run creation and state transitions."""

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
        """Create a workflow run tied to a version."""
        async with self._lock:
            workflow = self._workflows.get(workflow_id)
            if workflow is None or workflow.is_archived:
                raise WorkflowNotFoundError(str(workflow_id))

            await self._ensure_workflow_health(
                workflow_id,
                actor=actor or triggered_by,
                workspace_id=workspace_id,
            )

            run = self._create_run_locked(
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
        """Return runs associated with the provided workflow (most recent first)."""
        async with self._lock:
            if workflow_id not in self._workflows:
                raise WorkflowNotFoundError(str(workflow_id))
            version_ids = self._workflow_versions.get(workflow_id, [])
            run_ids = [
                run_id
                for version_id in version_ids
                for run_id in self._version_runs.get(version_id, [])
            ]
            runs = sorted(
                (
                    self._runs[run_id].model_copy(deep=True)
                    for run_id in run_ids
                    if workspace_id is None
                    or self._run_workspaces.get(run_id) is None
                    or self._run_workspaces.get(run_id) == workspace_id
                ),
                key=lambda r: r.created_at,
                reverse=True,
            )
            if limit is not None:
                runs = runs[:limit]
            return runs

    async def get_run(
        self,
        run_id: UUID,
        *,
        workspace_id: str | None = None,
    ) -> WorkflowRun:
        """Fetch a run by its identifier."""
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise WorkflowRunNotFoundError(str(run_id))
            stored_workspace_id = self._run_workspaces.get(run_id)
            if (
                workspace_id is not None
                and stored_workspace_id is not None
                and stored_workspace_id != workspace_id
            ):
                raise WorkflowRunNotFoundError(str(run_id))
            return run.model_copy(deep=True)

    async def mark_run_started(self, run_id: UUID, *, actor: str) -> WorkflowRun:
        """Mark the specified run as started."""
        return await self._update_run(run_id, lambda run: run.mark_started(actor=actor))

    async def mark_run_succeeded(
        self,
        run_id: UUID,
        *,
        actor: str,
        output: dict[str, Any] | None,
    ) -> WorkflowRun:
        """Mark the specified run as succeeded with optional output."""
        run = await self._update_run(
            run_id,
            lambda run: run.mark_succeeded(actor=actor, output=output),
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
        """Transition the run to a failed state."""
        run = await self._update_run(
            run_id,
            lambda run: run.mark_failed(actor=actor, error=error),
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
        """Cancel a run, optionally including a reason."""
        run = await self._update_run(
            run_id,
            lambda run: run.mark_cancelled(actor=actor, reason=reason),
        )
        self._release_cron_run(run_id)
        self._trigger_layer.clear_retry_state(run_id)
        return run


__all__ = ["WorkflowRunMixin"]
