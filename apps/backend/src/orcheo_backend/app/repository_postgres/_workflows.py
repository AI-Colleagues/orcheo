"""Workflow CRUD operations for the PostgreSQL repository."""

from __future__ import annotations
from collections.abc import Iterable
from inspect import isawaitable
from typing import Any
from uuid import UUID
from orcheo.models.workflow import (
    ChatKitStartScreenPrompt,
    ChatKitSupportedModel,
    Workflow,
    WorkflowDraftAccess,
)
from orcheo.models.workflow_refs import normalize_workflow_handle
from orcheo_backend.app.repository.chatkit import (
    apply_chatkit_start_screen_prompts_update,
    apply_chatkit_supported_models_update,
)
from orcheo_backend.app.repository.errors import (
    WorkflowNotFoundError,
    WorkflowPublishStateError,
)
from orcheo_backend.app.repository_postgres._persistence import PostgresPersistenceMixin


class WorkflowRepositoryMixin(PostgresPersistenceMixin):
    """Helpers for managing workflow metadata."""

    async def _maybe_disable_listener_subscriptions(
        self,
        workflow_id: UUID,
        *,
        should_disable: bool,
        actor: str,
        conn: Any,
    ) -> None:
        """Disable workflow listeners when archiving transitions them inactive."""
        if not should_disable:
            return
        disable_listener_subscriptions = getattr(
            self,
            "_disable_listener_subscriptions_locked",
            None,
        )
        if disable_listener_subscriptions is None:
            return
        result = disable_listener_subscriptions(
            workflow_id,
            actor=actor,
            conn=conn,
        )
        if isawaitable(result):
            await result

    async def list_workflows(
        self,
        *,
        include_archived: bool = False,
        tenant_id: str | None = None,
    ) -> list[Workflow]:
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                if tenant_id is not None:
                    cursor = await conn.execute(
                        "SELECT payload FROM workflows"
                        " WHERE (tenant_id = %s OR tenant_id IS NULL)"
                        " ORDER BY created_at ASC",
                        (tenant_id,),
                    )
                else:
                    cursor = await conn.execute(
                        "SELECT payload FROM workflows ORDER BY created_at ASC"
                    )
                rows = await cursor.fetchall()
            workflows = [
                self._deserialize_workflow(row["payload"]).model_copy(deep=True)
                for row in rows
            ]
            if include_archived:
                return workflows
            return [wf for wf in workflows if not wf.is_archived]

    async def create_workflow(
        self,
        *,
        name: str,
        handle: str | None = None,
        slug: str | None,
        description: str | None,
        tags: Iterable[str] | None,
        draft_access: WorkflowDraftAccess,
        actor: str,
        tenant_id: str | None = None,
    ) -> Workflow:
        await self._ensure_initialized()
        normalized_handle = normalize_workflow_handle(handle)
        async with self._lock:
            await self._ensure_handle_available_locked(
                normalized_handle,
                workflow_id=None,
                is_archived=False,
            )
            workflow = Workflow(
                name=name,
                handle=normalized_handle,
                slug=slug or "",
                description=description,
                tags=list(tags or []),
                draft_access=draft_access,
            )
            workflow.record_event(actor=actor, action="workflow_created")
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO workflows (
                        id,
                        handle,
                        is_archived,
                        payload,
                        created_at,
                        updated_at,
                        tenant_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(workflow.id),
                        workflow.handle,
                        workflow.is_archived,
                        self._dump_model(workflow),
                        workflow.created_at,
                        workflow.updated_at,
                        tenant_id,
                    ),
                )
            return workflow.model_copy(deep=True)

    async def get_workflow(
        self,
        workflow_id: UUID,
        *,
        tenant_id: str | None = None,
    ) -> Workflow:
        await self._ensure_initialized()
        async with self._lock:
            workflow = await self._get_workflow_locked(workflow_id)
            if tenant_id is not None:
                row_tid = await self._get_workflow_tenant_id_locked(workflow_id)
                if row_tid is not None and row_tid != tenant_id:
                    from orcheo_backend.app.repository.errors import (
                        WorkflowNotFoundError,
                    )

                    raise WorkflowNotFoundError(str(workflow_id))
            return workflow

    async def _get_workflow_tenant_id_locked(self, workflow_id: UUID) -> str | None:
        """Return the tenant_id column for a workflow row (Postgres)."""
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT tenant_id FROM workflows WHERE id = %s",
                (str(workflow_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return row["tenant_id"]

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        tenant_id: str | None = None,
    ) -> UUID:
        await self._ensure_initialized()
        async with self._lock:
            workflow_id = await self._resolve_workflow_ref_locked(
                workflow_ref,
                include_archived=include_archived,
            )
            if tenant_id is not None:
                row_tid = await self._get_workflow_tenant_id_locked(workflow_id)
                if row_tid is not None and row_tid != tenant_id:
                    from orcheo_backend.app.repository.errors import (
                        WorkflowNotFoundError,
                    )

                    raise WorkflowNotFoundError(workflow_ref)
            return workflow_id

    async def update_workflow(
        self,
        workflow_id: UUID,
        *,
        name: str | None,
        handle: str | None = None,
        description: str | None,
        tags: Iterable[str] | None,
        chatkit_start_screen_prompts: list[ChatKitStartScreenPrompt] | None = None,
        chatkit_supported_models: list[ChatKitSupportedModel] | None = None,
        clear_chatkit_start_screen_prompts: bool = False,
        clear_chatkit_supported_models: bool = False,
        draft_access: WorkflowDraftAccess | None = None,
        is_archived: bool | None,
        actor: str,
    ) -> Workflow:
        await self._ensure_initialized()
        normalized_handle = normalize_workflow_handle(handle)
        async with self._lock:
            workflow = await self._get_workflow_locked(workflow_id)

            metadata: dict[str, Any] = {}
            should_disable_listeners = False
            next_is_archived = (
                workflow.is_archived if is_archived is None else is_archived
            )

            if normalized_handle is not None and normalized_handle != workflow.handle:
                await self._ensure_handle_available_locked(
                    normalized_handle,
                    workflow_id=workflow_id,
                    is_archived=next_is_archived,
                )
                metadata["handle"] = {
                    "from": workflow.handle,
                    "to": normalized_handle,
                }
                workflow.handle = normalized_handle

            if name is not None and name != workflow.name:
                metadata["name"] = {"from": workflow.name, "to": name}
                workflow.name = name

            if description is not None and description != workflow.description:
                metadata["description"] = {
                    "from": workflow.description,
                    "to": description,
                }
                workflow.description = description

            if tags is not None:
                normalized_tags = list(tags)
                if normalized_tags != workflow.tags:  # pragma: no branch
                    metadata["tags"] = {
                        "from": workflow.tags,
                        "to": normalized_tags,
                    }
                    workflow.tags = normalized_tags

            apply_chatkit_start_screen_prompts_update(
                workflow,
                metadata,
                chatkit_start_screen_prompts=chatkit_start_screen_prompts,
                clear_chatkit_start_screen_prompts=clear_chatkit_start_screen_prompts,
            )
            apply_chatkit_supported_models_update(
                workflow,
                metadata,
                chatkit_supported_models=chatkit_supported_models,
                clear_chatkit_supported_models=clear_chatkit_supported_models,
            )

            if draft_access is not None and draft_access != workflow.draft_access:
                metadata["draft_access"] = {
                    "from": workflow.draft_access.value,
                    "to": draft_access.value,
                }
                workflow.draft_access = draft_access

            if is_archived is not None and is_archived != workflow.is_archived:
                if is_archived and workflow.is_public:
                    workflow.revoke_publish(actor=actor)
                should_disable_listeners = is_archived
                metadata["is_archived"] = {
                    "from": workflow.is_archived,
                    "to": is_archived,
                }
                workflow.is_archived = is_archived

            workflow.record_event(
                actor=actor,
                action="workflow_updated",
                metadata=metadata,
            )

            async with self._connection() as conn:
                await self._maybe_disable_listener_subscriptions(
                    workflow.id,
                    should_disable=should_disable_listeners,
                    actor=actor,
                    conn=conn,
                )
                await conn.execute(
                    """
                    UPDATE workflows
                       SET handle = %s, is_archived = %s, payload = %s, updated_at = %s
                     WHERE id = %s
                    """,
                    (
                        workflow.handle,
                        workflow.is_archived,
                        self._dump_model(workflow),
                        workflow.updated_at,
                        str(workflow.id),
                    ),
                )
            return workflow.model_copy(deep=True)

    async def archive_workflow(self, workflow_id: UUID, *, actor: str) -> Workflow:
        return await self.update_workflow(
            workflow_id,
            name=None,
            handle=None,
            description=None,
            tags=None,
            draft_access=None,
            is_archived=True,
            actor=actor,
        )

    async def publish_workflow(
        self,
        workflow_id: UUID,
        *,
        require_login: bool,
        actor: str,
    ) -> Workflow:
        await self._ensure_initialized()
        async with self._lock:
            workflow = await self._get_workflow_locked(workflow_id)
            if workflow.is_archived:
                raise WorkflowNotFoundError(str(workflow_id))
            try:
                workflow.publish(
                    require_login=require_login,
                    actor=actor,
                )
            except ValueError as exc:
                raise WorkflowPublishStateError(str(exc)) from exc
            async with self._connection() as conn:
                await conn.execute(
                    """
                    UPDATE workflows
                       SET handle = %s, is_archived = %s, payload = %s, updated_at = %s
                     WHERE id = %s
                    """,
                    (
                        workflow.handle,
                        workflow.is_archived,
                        self._dump_model(workflow),
                        workflow.updated_at,
                        str(workflow.id),
                    ),
                )
            return workflow.model_copy(deep=True)

    async def revoke_publish(self, workflow_id: UUID, *, actor: str) -> Workflow:
        await self._ensure_initialized()
        async with self._lock:
            workflow = await self._get_workflow_locked(workflow_id)
            if workflow.is_archived:
                raise WorkflowNotFoundError(str(workflow_id))
            try:
                workflow.revoke_publish(actor=actor)
            except ValueError as exc:
                raise WorkflowPublishStateError(str(exc)) from exc
            async with self._connection() as conn:
                await conn.execute(
                    """
                    UPDATE workflows
                       SET handle = %s, is_archived = %s, payload = %s, updated_at = %s
                     WHERE id = %s
                    """,
                    (
                        workflow.handle,
                        workflow.is_archived,
                        self._dump_model(workflow),
                        workflow.updated_at,
                        str(workflow.id),
                    ),
                )
            return workflow.model_copy(deep=True)


__all__ = ["WorkflowRepositoryMixin"]
