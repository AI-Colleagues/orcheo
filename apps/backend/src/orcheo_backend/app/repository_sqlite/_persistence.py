"""Low-level SQLite persistence helpers."""

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
from orcheo_backend.app.repository_sqlite._base import SqliteRepositoryBase


class SqlitePersistenceMixin(SqliteRepositoryBase):
    """Expose locked fetch helpers shared across mixins."""

    @staticmethod
    def _deserialize_workflow(payload_json: str) -> Workflow:
        """Return a Workflow instance while stripping deprecated fields."""
        payload = json.loads(payload_json)
        payload.pop("publish_token_hash", None)
        payload.pop("publish_token_rotated_at", None)
        return Workflow.model_validate(payload)

    async def _get_workflow_locked(self, workflow_id: UUID) -> Workflow:
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT payload FROM workflows WHERE id = ?", (str(workflow_id),)
            )
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowNotFoundError(str(workflow_id))
        return self._deserialize_workflow(row["payload"])

    async def _workflow_exists_locked(self, workflow_id: UUID) -> bool:
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM workflows WHERE id = ?",
                (str(workflow_id),),
            )
            row = await cursor.fetchone()
        return row is not None

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

        workflow_id_str = str(workflow_id) if workflow_id is not None else None
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, is_archived
                  FROM workflows
                 WHERE handle = ?
                   AND (? IS NULL OR id != ?)
                """,
                (handle, workflow_id_str, workflow_id_str),
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

        should_match_uuid = int(workflow_ref_is_uuid(normalized_ref))
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id
                  FROM workflows
                 WHERE (
                           handle = ?
                       AND (? = 1 OR is_archived = 0)
                       )
                    OR (? = 1 AND id = ?)
              ORDER BY
                    CASE
                        WHEN handle = ? AND is_archived = 0 THEN 0
                        WHEN handle = ? AND is_archived = 1 THEN 1
                        ELSE 2
                    END,
                    updated_at DESC
                 LIMIT 1
                """,
                (
                    normalized_ref,
                    int(include_archived),
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
                "SELECT payload FROM workflow_versions WHERE id = ?",
                (str(version_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowVersionNotFoundError(str(version_id))
        return WorkflowVersion.model_validate_json(row["payload"])

    async def _get_latest_version_locked(self, workflow_id: UUID) -> WorkflowVersion:
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT payload
                  FROM workflow_versions
                 WHERE workflow_id = ?
              ORDER BY version DESC
                 LIMIT 1
                """,
                (str(workflow_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowVersionNotFoundError("latest")
        return WorkflowVersion.model_validate_json(row["payload"])

    async def _get_run_locked(self, run_id: UUID) -> WorkflowRun:
        async with self._connection() as conn:
            cursor = await conn.execute(
                (
                    "SELECT payload, workflow_id, triggered_by, status "
                    "FROM workflow_runs WHERE id = ?"
                ),
                (str(run_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowRunNotFoundError(str(run_id))
        return WorkflowRun.model_validate_json(row["payload"])

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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run.id),
                    str(workflow_id),
                    str(workflow_version_id),
                    run.status.value,
                    run.triggered_by,
                    self._dump_model(run),
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                    tenant_id,
                ),
            )

        self._trigger_layer.track_run(workflow_id, run.id)
        if triggered_by == "cron":
            self._trigger_layer.register_cron_run(run.id)
        return run


__all__ = ["SqlitePersistenceMixin"]
