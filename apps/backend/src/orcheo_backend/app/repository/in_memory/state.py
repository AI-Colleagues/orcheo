"""Shared state and helpers for the in-memory repository."""

from __future__ import annotations
import asyncio
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID
from orcheo.listeners import ListenerCursor, ListenerDedupeRecord, ListenerSubscription
from orcheo.models import (
    Team,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)
from orcheo.models.workflow_refs import workflow_ref_is_uuid
from orcheo.runtime.runnable_config import merge_runnable_configs
from orcheo.triggers.layer import TriggerLayer
from orcheo.vault.oauth import CredentialHealthError, OAuthCredentialService
from orcheo_backend.app.repository.errors import (
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
    WorkflowVersionNotFoundError,
)


class InMemoryRepositoryState:
    """Holds shared state and primitives for the in-memory repository."""

    def __init__(
        self, credential_service: OAuthCredentialService | None = None
    ) -> None:
        """Initialize the repository state containers and dependencies."""
        self._lock = asyncio.Lock()
        self._workflows: dict[UUID, Workflow] = {}
        self._workflow_workspaces: dict[UUID, str] = {}
        # A handle can be active in multiple teams within one workspace, so the
        # index maps handle -> ordered list of workflow ids (default team first).
        self._active_workflow_handles: dict[str | None, dict[str, list[UUID]]] = {}
        self._archived_workflow_handles: dict[str | None, dict[str, list[UUID]]] = {}
        self._teams: dict[UUID, Team] = {}
        self._default_team_by_workspace: dict[str, str] = {}
        self._workflow_versions: dict[UUID, list[UUID]] = {}
        self._versions: dict[UUID, WorkflowVersion] = {}
        self._runs: dict[UUID, WorkflowRun] = {}
        self._run_workspaces: dict[UUID, str] = {}
        self._version_runs: dict[UUID, list[UUID]] = {}
        self._listener_subscriptions: dict[UUID, ListenerSubscription] = {}
        self._workflow_listener_subscriptions: dict[UUID, list[UUID]] = {}
        self._listener_cursors: dict[UUID, ListenerCursor] = {}
        self._listener_dedupe: dict[UUID, dict[str, ListenerDedupeRecord]] = {}
        self._credential_service = credential_service
        self._trigger_layer = TriggerLayer(health_guard=credential_service)

    async def reset(self) -> None:
        """Clear all stored workflows, versions, and runs."""
        async with self._lock:
            self._workflows.clear()
            self._workflow_workspaces.clear()
            self._active_workflow_handles.clear()
            self._archived_workflow_handles.clear()
            self._teams.clear()
            self._default_team_by_workspace.clear()
            self._workflow_versions.clear()
            self._versions.clear()
            self._runs.clear()
            self._run_workspaces.clear()
            self._version_runs.clear()
            self._listener_subscriptions.clear()
            self._workflow_listener_subscriptions.clear()
            self._listener_cursors.clear()
            self._listener_dedupe.clear()
            self._trigger_layer.reset()

    def _sync_listener_subscriptions_locked(
        self,
        workflow_id: UUID,
        workflow_version_id: UUID,
        graph: dict[str, Any],
        *,
        actor: str,
    ) -> None:
        """Synchronize listener subscriptions for the workflow version."""
        del workflow_id, workflow_version_id, graph, actor

    def _create_run_locked(  # noqa: C901
        self,
        *,
        workflow_id: UUID,
        workflow_version_id: UUID,
        triggered_by: str,
        input_payload: Mapping[str, Any],
        actor: str | None = None,
        runnable_config: Mapping[str, Any] | None = None,
        workspace_id: str | None = None,
    ) -> WorkflowRun:
        """Create and store a workflow run. Caller must hold the lock."""
        if workflow_id not in self._workflows:  # pragma: no cover, defensive
            raise WorkflowNotFoundError(str(workflow_id))

        version = self._versions.get(workflow_version_id)
        if version is None or version.workflow_id != workflow_id:
            raise WorkflowVersionNotFoundError(str(workflow_version_id))

        from orcheo_backend.app.workspace import get_workspace_repository
        from orcheo_backend.app.workspace_governance import get_workspace_governance

        workspace_record = None
        if workspace_id is not None:
            workspace_record = get_workspace_repository().get_workspace(
                UUID(workspace_id)
            )
            get_workspace_governance().reserve_run_slot(
                workspace_id,
                limit=workspace_record.quotas.max_concurrent_runs,
            )

        try:
            config_payload: dict[str, Any] | None = None
            if runnable_config:
                if hasattr(runnable_config, "model_dump"):
                    config_payload = runnable_config.model_dump(mode="json")  # type: ignore[arg-type]
                elif isinstance(runnable_config, Mapping):  # pragma: no branch
                    config_payload = dict(runnable_config)
            merged_config = merge_runnable_configs(
                version.runnable_config, config_payload
            )
            config_payload = merged_config.model_dump(
                mode="json",
                exclude_defaults=True,
                exclude_none=True,
            )
            tags = (
                list(config_payload.get("tags", []))
                if isinstance(config_payload, dict)
                else []
            )
            callbacks = (
                list(config_payload.get("callbacks", []))
                if isinstance(config_payload, dict)
                else []
            )
            metadata = (
                dict(config_payload.get("metadata", {}))
                if isinstance(config_payload, Mapping)
                else {}
            )
            run_name = (
                config_payload.get("run_name")
                if isinstance(config_payload, Mapping)
                else None
            )
            run = WorkflowRun(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                workflow_version_id=workflow_version_id,
                triggered_by=triggered_by,
                input_payload=dict(input_payload),
                runnable_config=config_payload
                if isinstance(config_payload, dict)
                else {},
                tags=tags,
                callbacks=callbacks,
                metadata=metadata,
                run_name=run_name,
            )
            run.record_event(actor=actor or triggered_by, action="run_created")
            self._runs[run.id] = run
            if workspace_id is not None:
                self._run_workspaces[run.id] = workspace_id
            self._version_runs.setdefault(workflow_version_id, []).append(run.id)
            self._trigger_layer.track_run(workflow_id, run.id)
            if triggered_by == "cron":
                self._trigger_layer.register_cron_run(run.id)
            return run
        except Exception:
            if workspace_id is not None:
                from orcheo_backend.app.workspace_governance import (
                    get_workspace_governance,
                )

                get_workspace_governance().release_run_slot(workspace_id)
            raise

    async def _update_run(
        self, run_id: UUID, updater: Callable[[WorkflowRun], None]
    ) -> WorkflowRun:
        """Apply a mutation to a run under lock and return a copy."""
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise WorkflowRunNotFoundError(str(run_id))
            updater(run)
            return run.model_copy(deep=True)

    def _release_cron_run(self, run_id: UUID) -> None:
        """Release overlap tracking for the provided cron run."""
        self._trigger_layer.release_cron_run(run_id)

    async def _ensure_workflow_health(
        self,
        workflow_id: UUID,
        *,
        actor: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        service = self._credential_service
        if service is None:
            return
        report = await service.ensure_workflow_health(
            workflow_id,
            actor=actor,
            workspace_id=workspace_id,
        )
        if not report.is_healthy:
            raise CredentialHealthError(report)

    def _is_default_team_workflow(self, workflow: Workflow) -> bool:
        """Return True when the workflow belongs to its workspace's default team."""
        if workflow.workspace_id is None or workflow.team_id is None:
            return False
        default_team = self._default_team_by_workspace.get(workflow.workspace_id)
        return default_team is not None and workflow.team_id == default_team

    def _active_handle_sort_key(self, workflow_id: UUID) -> tuple[int, float]:
        """Order active handle matches default-team-first, then most recent."""
        workflow = self._workflows.get(workflow_id)
        if workflow is None:  # pragma: no cover - defensive
            return (1, 0.0)
        is_default = self._is_default_team_workflow(workflow)
        return (0 if is_default else 1, -workflow.updated_at.timestamp())

    def _rebuild_handle_indexes_locked(self) -> None:
        """Rebuild handle indexes from workflow state. Caller must hold the lock."""
        self._active_workflow_handles.clear()
        self._archived_workflow_handles.clear()

        for workflow in self._workflows.values():
            if workflow.handle is None:
                continue
            workspace_id = workflow.workspace_id
            if workflow.is_archived:
                self._archived_workflow_handles.setdefault(workspace_id, {}).setdefault(
                    workflow.handle,
                    [],
                ).append(workflow.id)
                continue
            self._active_workflow_handles.setdefault(workspace_id, {}).setdefault(
                workflow.handle,
                [],
            ).append(workflow.id)

        # Within a (workspace, handle) bucket, surface the default team first so
        # bare-handle resolution honours the default-team-wins rule.
        for handles in self._active_workflow_handles.values():
            for matches in handles.values():
                matches.sort(key=self._active_handle_sort_key)

    def _ensure_handle_available_locked(
        self,
        handle: str | None,
        *,
        workflow_id: UUID | None,
        is_archived: bool,
        workspace_id: str | None = None,
        team_id: str | None = None,
    ) -> None:
        """Ensure the provided handle is valid for assignment within the team."""
        if handle is None:
            return

        del is_archived
        for existing in self._workflows.values():
            if existing.id == workflow_id or existing.handle != handle:
                continue
            if existing.workspace_id != workspace_id:
                continue
            # Handle uniqueness is scoped per team: the same handle may live in
            # different teams of one workspace.
            if existing.team_id != team_id:
                continue
            if not existing.is_archived:
                msg = f"Workflow handle '{handle}' is already in use."
                raise WorkflowHandleConflictError(msg)

    def _matches_team(self, workflow_id: UUID, team_id: str | None) -> bool:
        """Return True when *team_id* is unset or the workflow is in that team."""
        if team_id is None:
            return True
        workflow = self._workflows.get(workflow_id)
        return workflow is not None and workflow.team_id == team_id

    def _find_in_handle_index(
        self,
        index: dict[str | None, dict[str, list[UUID]]],
        scope_keys: list[str | None],
        normalized_ref: str,
        team_id: str | None,
    ) -> UUID | None:
        for scope_key in scope_keys:
            for match in index.get(scope_key, {}).get(normalized_ref, []):
                if self._matches_team(match, team_id):
                    return match
        return None

    def _resolve_by_uuid(
        self,
        normalized_ref: str,
        workspace_id: str | None,
        team_id: str | None,
    ) -> UUID | None:
        if team_id is not None or not workflow_ref_is_uuid(normalized_ref):
            return None
        workflow_uuid = UUID(normalized_ref)
        if workflow_uuid not in self._workflows:
            return None
        if workspace_id is None:
            return workflow_uuid
        stored = self._workflow_workspaces.get(workflow_uuid)
        if stored is None or stored == workspace_id:
            return workflow_uuid
        return None

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
        team_id: str | None = None,
    ) -> UUID:
        """Resolve a user-facing workflow ref to a canonical UUID."""
        normalized_ref = workflow_ref.strip().lower()
        if not normalized_ref:
            raise WorkflowNotFoundError("workflow ref is empty")

        async with self._lock:
            scope_keys: list[str | None]
            if workspace_id is not None:
                scope_keys = [workspace_id, None]
            else:
                scope_keys = [None, *self._active_workflow_handles.keys()]

            found = self._find_in_handle_index(
                self._active_workflow_handles, scope_keys, normalized_ref, team_id
            )
            if found is not None:
                return found

            if include_archived:
                found = self._find_in_handle_index(
                    self._archived_workflow_handles,
                    scope_keys,
                    normalized_ref,
                    team_id,
                )
                if found is not None:
                    return found

            found = self._resolve_by_uuid(normalized_ref, workspace_id, team_id)
            if found is not None:
                return found

        raise WorkflowNotFoundError(normalized_ref)


__all__ = ["InMemoryRepositoryState"]
