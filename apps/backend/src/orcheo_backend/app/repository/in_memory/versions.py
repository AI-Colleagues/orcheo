"""Workflow version helpers for the in-memory repository."""

from __future__ import annotations
import json
from difflib import unified_diff
from typing import Any
from uuid import UUID
from orcheo.models.workflow import WorkflowVersion
from orcheo_backend.app.repository.errors import (
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.repository.in_memory.state import InMemoryRepositoryState
from orcheo_backend.app.repository.protocol import VersionDiff


class WorkflowVersionMixin(InMemoryRepositoryState):
    """Persist and query workflow versions."""

    async def create_version(
        self,
        workflow_id: UUID,
        *,
        graph: dict[str, Any],
        metadata: dict[str, Any],
        runnable_config: dict[str, Any] | None = None,
        notes: str | None,
        created_by: str,
    ) -> WorkflowVersion:
        """Create and store a new workflow version."""
        async with self._lock:
            workflow = self._workflows.get(workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError(str(workflow_id))
            tenant_id = self._workflow_tenants.get(workflow_id)

            version_ids = self._workflow_versions.setdefault(workflow_id, [])
            next_version_number = len(version_ids) + 1
            version = WorkflowVersion(
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                version=next_version_number,
                graph=json.loads(json.dumps(graph)),
                metadata=dict(metadata),
                runnable_config=dict(runnable_config) if runnable_config else None,
                created_by=created_by,
                notes=notes,
            )
            version.record_event(actor=created_by, action="version_created")
            self._versions[version.id] = version
            version_ids.append(version.id)
            self._version_runs.setdefault(version.id, [])
            self._sync_listener_subscriptions_locked(
                workflow_id,
                version.id,
                version.graph,
                actor=created_by,
            )
            return version.model_copy(deep=True)

    async def list_versions(self, workflow_id: UUID) -> list[WorkflowVersion]:
        """Return the versions belonging to the given workflow."""
        async with self._lock:
            version_ids = self._workflow_versions.get(workflow_id)
            if version_ids is None:
                raise WorkflowNotFoundError(str(workflow_id))
            return [
                self._versions[version_id].model_copy(deep=True)
                for version_id in version_ids
            ]

    async def get_version_by_number(
        self, workflow_id: UUID, version_number: int
    ) -> WorkflowVersion:
        """Fetch a workflow version by its human readable number."""
        async with self._lock:
            version_ids = self._workflow_versions.get(workflow_id)
            if version_ids is None:
                raise WorkflowNotFoundError(str(workflow_id))
            for version_id in version_ids:
                version = self._versions[version_id]
                if version.version == version_number:
                    return version.model_copy(deep=True)
            raise WorkflowVersionNotFoundError(f"v{version_number}")

    async def update_version_runnable_config(
        self,
        workflow_id: UUID,
        *,
        version_number: int,
        runnable_config: dict[str, Any] | None,
        actor: str,
    ) -> WorkflowVersion:
        """Update only runnable config for a workflow version."""
        async with self._lock:
            version_ids = self._workflow_versions.get(workflow_id)
            if version_ids is None:
                raise WorkflowNotFoundError(str(workflow_id))
            for version_id in version_ids:
                version = self._versions[version_id]
                if version.version == version_number:
                    version.runnable_config = (
                        dict(runnable_config) if runnable_config is not None else None
                    )
                    version.record_event(
                        actor=actor,
                        action="version_runnable_config_updated",
                    )
                    return version.model_copy(deep=True)
            raise WorkflowVersionNotFoundError(f"v{version_number}")

    async def get_version(self, version_id: UUID) -> WorkflowVersion:
        """Retrieve a workflow version by its identifier."""
        async with self._lock:
            version = self._versions.get(version_id)
            if version is None:
                raise WorkflowVersionNotFoundError(str(version_id))
            return version.model_copy(deep=True)

    async def get_latest_version(self, workflow_id: UUID) -> WorkflowVersion:
        """Return the most recent workflow version for the workflow."""
        async with self._lock:
            version_ids = self._workflow_versions.get(workflow_id)
            if version_ids is None:
                raise WorkflowNotFoundError(str(workflow_id))
            if not version_ids:
                raise WorkflowVersionNotFoundError("latest")
            latest_version_id = version_ids[-1]
            version = self._versions.get(latest_version_id)
            if version is None:
                raise WorkflowVersionNotFoundError(str(latest_version_id))
            return version.model_copy(deep=True)

    async def diff_versions(
        self, workflow_id: UUID, base_version: int, target_version: int
    ) -> VersionDiff:
        """Compute a unified diff between two workflow versions."""
        base = await self.get_version_by_number(workflow_id, base_version)
        target = await self.get_version_by_number(workflow_id, target_version)

        base_serialized = json.dumps(base.graph, indent=2, sort_keys=True).splitlines()
        target_serialized = json.dumps(
            target.graph,
            indent=2,
            sort_keys=True,
        ).splitlines()

        diff = list(
            unified_diff(
                base_serialized,
                target_serialized,
                fromfile=f"v{base_version}",
                tofile=f"v{target_version}",
                lineterm="",
            )
        )
        return VersionDiff(
            base_version=base_version,
            target_version=target_version,
            diff=diff,
        )


__all__ = ["WorkflowVersionMixin"]
