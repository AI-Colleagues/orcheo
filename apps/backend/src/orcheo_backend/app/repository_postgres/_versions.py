"""Workflow version persistence helpers."""

from __future__ import annotations
import json
from difflib import unified_diff
from typing import Any
from uuid import UUID
from orcheo.listeners import compile_listener_subscriptions
from orcheo.models.workflow import WorkflowVersion
from orcheo_backend.app.repository import (
    VersionDiff,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.repository_postgres._listeners import ListenerRepositoryMixin
from orcheo_backend.app.repository_postgres._persistence import PostgresPersistenceMixin


class WorkflowVersionMixin(PostgresPersistenceMixin):
    """Manage workflow version records."""

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
        await self._ensure_initialized()
        async with self._lock:
            workflow = await self._get_workflow_locked(workflow_id)
            workspace_id = workflow.workspace_id
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) AS max_version
                      FROM workflow_versions
                     WHERE workflow_id = %s
                    """,
                    (str(workflow_id),),
                )
                row = await cursor.fetchone()
                max_version = 0
                if row and row["max_version"] is not None:
                    max_version = int(row["max_version"])
                next_version_number = max_version + 1

                version = WorkflowVersion(
                    workflow_id=workflow_id,
                    workspace_id=workspace_id,
                    version=next_version_number,
                    graph=json.loads(json.dumps(graph)),
                    metadata=dict(metadata),
                    runnable_config=dict(runnable_config) if runnable_config else None,
                    created_by=created_by,
                    notes=notes,
                )
                version.record_event(actor=created_by, action="version_created")

                await conn.execute(
                    """
                    INSERT INTO workflow_versions (
                        id,
                        workflow_id,
                        workspace_id,
                        version,
                        payload,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(version.id),
                        str(workflow_id),
                        version.workspace_id,
                        version.version,
                        self._dump_model(version),
                        version.created_at,
                        version.updated_at,
                    ),
                )
            if isinstance(self, ListenerRepositoryMixin):
                compiled = compile_listener_subscriptions(
                    workflow_id,
                    version.id,
                    version.graph,
                )
                await self._replace_listener_subscriptions_locked(
                    workflow_id,
                    compiled,
                    actor=created_by,
                )
            return version.model_copy(deep=True)

    async def list_versions(self, workflow_id: UUID) -> list[WorkflowVersion]:
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(workflow_id)
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT payload
                      FROM workflow_versions
                     WHERE workflow_id = %s
                  ORDER BY version ASC
                    """,
                    (str(workflow_id),),
                )
                rows = await cursor.fetchall()
            result = []
            for row in rows:
                payload = row["payload"]
                if isinstance(payload, str):
                    version = WorkflowVersion.model_validate_json(payload)
                else:
                    version = WorkflowVersion.model_validate(payload)
                result.append(version.model_copy(deep=True))
            return result

    async def get_version_by_number(
        self, workflow_id: UUID, version_number: int
    ) -> WorkflowVersion:
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(workflow_id)
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT payload
                      FROM workflow_versions
                     WHERE workflow_id = %s AND version = %s
                    """,
                    (str(workflow_id), version_number),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise WorkflowVersionNotFoundError(f"v{version_number}")
            payload = row["payload"]
            if isinstance(payload, str):
                return WorkflowVersion.model_validate_json(payload).model_copy(
                    deep=True
                )
            return WorkflowVersion.model_validate(payload).model_copy(deep=True)

    async def update_version_runnable_config(
        self,
        workflow_id: UUID,
        *,
        version_number: int,
        runnable_config: dict[str, Any] | None,
        actor: str,
    ) -> WorkflowVersion:
        """Update only runnable config for an existing workflow version."""
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(workflow_id)
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT id, payload
                      FROM workflow_versions
                     WHERE workflow_id = %s AND version = %s
                    """,
                    (str(workflow_id), version_number),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise WorkflowVersionNotFoundError(f"v{version_number}")

                payload = row["payload"]
                version = (
                    WorkflowVersion.model_validate_json(payload)
                    if isinstance(payload, str)
                    else WorkflowVersion.model_validate(payload)
                )
                version.runnable_config = (
                    dict(runnable_config) if runnable_config is not None else None
                )
                version.record_event(
                    actor=actor,
                    action="version_runnable_config_updated",
                )

                await conn.execute(
                    """
                    UPDATE workflow_versions
                       SET payload = %s,
                           updated_at = %s
                     WHERE id = %s
                    """,
                    (
                        self._dump_model(version),
                        version.updated_at,
                        row["id"],
                    ),
                )
            return version.model_copy(deep=True)

    async def get_version(self, version_id: UUID) -> WorkflowVersion:
        await self._ensure_initialized()
        async with self._lock:
            return await self._get_version_locked(version_id)

    async def get_latest_version(self, workflow_id: UUID) -> WorkflowVersion:
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(workflow_id)
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT payload
                      FROM workflow_versions
                     WHERE workflow_id = %s
                  ORDER BY version DESC
                     LIMIT 1
                    """,
                    (str(workflow_id),),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise WorkflowVersionNotFoundError("latest")
            payload = row["payload"]
            if isinstance(payload, str):
                return WorkflowVersion.model_validate_json(payload).model_copy(
                    deep=True
                )
            return WorkflowVersion.model_validate(payload).model_copy(deep=True)

    async def diff_versions(
        self,
        workflow_id: UUID,
        base_version: int,
        target_version: int,
    ) -> VersionDiff:
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
