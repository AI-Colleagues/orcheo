"""Low-level PostgreSQL persistence helpers."""

from __future__ import annotations
import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID
from orcheo.models.workflow import Workflow, WorkflowRun, WorkflowVersion
from orcheo.models.workflow_refs import workflow_ref_is_uuid
from orcheo.runtime.runnable_config import merge_runnable_configs
from orcheo_backend.app.repository import (
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.repository_postgres._base import PostgresRepositoryBase


class PostgresPersistenceMixin(PostgresRepositoryBase):
    """Expose fetch helpers shared across mixins."""

    @staticmethod
    def _deserialize_workflow(payload: dict[str, Any] | str) -> Workflow:
        """Return a Workflow instance while stripping deprecated fields."""
        data: dict[str, Any]
        if isinstance(payload, str):
            data = json.loads(payload)
        else:
            data = payload
        data.pop("publish_token_hash", None)
        data.pop("publish_token_rotated_at", None)
        return Workflow.model_validate(data)

    async def _get_workflow_locked(self, workflow_id: UUID) -> Workflow:
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT payload FROM workflows WHERE id = %s", (str(workflow_id),)
            )
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowNotFoundError(str(workflow_id))
        return self._deserialize_workflow(row["payload"])

    async def _ensure_handle_available_locked(
        self,
        handle: str | None,
        *,
        workflow_id: UUID | None,
        is_archived: bool,
    ) -> None:
        """Ensure the provided handle can be assigned."""
        if handle is None:
            return

        async with self._connection() as conn:
            if workflow_id is None:
                cursor = await conn.execute(
                    """
                    SELECT id, is_archived
                      FROM workflows
                     WHERE handle = %s
                    """,
                    (handle,),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT id, is_archived
                      FROM workflows
                     WHERE handle = %s
                       AND id != %s::uuid
                    """,
                    (handle, str(workflow_id)),
                )
            rows = await cursor.fetchall()

        for row in rows:
            if not row["is_archived"]:
                msg = f"Workflow handle '{handle}' is already in use."
                raise WorkflowHandleConflictError(msg)

    async def _resolve_workflow_ref_locked(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
    ) -> UUID:
        """Resolve a workflow ref using handle-first semantics."""
        normalized_ref = workflow_ref.strip().lower()
        if not normalized_ref:
            raise WorkflowNotFoundError("workflow ref is empty")

        should_match_uuid = workflow_ref_is_uuid(normalized_ref)
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id
                  FROM workflows
                 WHERE (
                           handle = %s
                       AND (%s OR is_archived = FALSE)
                       )
                    OR (%s AND id = %s)
              ORDER BY
                    CASE
                        WHEN handle = %s AND is_archived = FALSE THEN 0
                        WHEN handle = %s AND is_archived = TRUE THEN 1
                        ELSE 2
                    END,
                    updated_at DESC
                 LIMIT 1
                """,
                (
                    normalized_ref,
                    include_archived,
                    should_match_uuid,
                    normalized_ref,
                    normalized_ref,
                    normalized_ref,
                ),
            )
            row = await cursor.fetchone()
            if row is not None:
                return UUID(row["id"])

        raise WorkflowNotFoundError(normalized_ref)

    async def _get_version_locked(self, version_id: UUID) -> WorkflowVersion:
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT payload FROM workflow_versions WHERE id = %s",
                (str(version_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowVersionNotFoundError(str(version_id))
        payload = row["payload"]
        if isinstance(payload, str):
            return WorkflowVersion.model_validate_json(payload)
        return WorkflowVersion.model_validate(payload)

    async def _get_latest_version_locked(self, workflow_id: UUID) -> WorkflowVersion:
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
            return WorkflowVersion.model_validate_json(payload)
        return WorkflowVersion.model_validate(payload)

    async def _get_run_locked(self, run_id: UUID) -> WorkflowRun:
        async with self._connection() as conn:
            cursor = await conn.execute(
                (
                    "SELECT payload, workflow_id, triggered_by, status "
                    "FROM workflow_runs WHERE id = %s"
                ),
                (str(run_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowRunNotFoundError(str(run_id))
        payload = row["payload"]
        if isinstance(payload, str):
            return WorkflowRun.model_validate_json(payload)
        return WorkflowRun.model_validate(payload)

    async def _create_run_locked(
        self,
        *,
        workflow_id: UUID,
        workflow_version_id: UUID,
        triggered_by: str,
        input_payload: Mapping[str, Any],
        actor: str | None,
        runnable_config: Mapping[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> WorkflowRun:
        version = await self._get_version_locked(workflow_version_id)
        if version.workflow_id != workflow_id:
            raise WorkflowVersionNotFoundError(str(workflow_version_id))

        config_payload: dict[str, Any] | None = None
        if runnable_config:
            if hasattr(runnable_config, "model_dump"):
                config_payload = runnable_config.model_dump(mode="json")  # type: ignore[arg-type]
            elif isinstance(runnable_config, Mapping):  # pragma: no branch
                config_payload = dict(runnable_config)
        merged_config = merge_runnable_configs(version.runnable_config, config_payload)
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
            workflow_version_id=workflow_version_id,
            triggered_by=triggered_by,
            input_payload=dict(input_payload),
            runnable_config=config_payload if isinstance(config_payload, dict) else {},
            tags=tags,
            callbacks=callbacks,
            metadata=metadata,
            run_name=run_name,
        )
        run.record_event(actor=actor or triggered_by, action="run_created")

        async with self._connection() as conn:
            await conn.execute(
                """
                INSERT INTO workflow_runs (
                    id,
                    workflow_id,
                    workflow_version_id,
                    status,
                    triggered_by,
                    payload,
                    created_at,
                    updated_at,
                    tenant_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(run.id),
                    str(workflow_id),
                    str(workflow_version_id),
                    run.status.value,
                    run.triggered_by,
                    self._dump_model(run),
                    run.created_at,
                    run.updated_at,
                    tenant_id,
                ),
            )

        self._trigger_layer.track_run(workflow_id, run.id)
        if triggered_by == "cron":
            self._trigger_layer.register_cron_run(run.id)
        return run


__all__ = ["PostgresPersistenceMixin"]
