"""Workflow level operations for the in-memory repository."""

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
from orcheo_backend.app.repository.in_memory.state import InMemoryRepositoryState


class WorkflowCrudMixin(InMemoryRepositoryState):
    """Implements workflow management helpers."""

    async def _maybe_disable_listener_subscriptions(
        self,
        workflow_id: UUID,
        *,
        should_disable: bool,
        actor: str,
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
        result = disable_listener_subscriptions(workflow_id, actor=actor)
        if isawaitable(result):
            await result

    async def list_workflows(
        self,
        *,
        include_archived: bool = False,
        tenant_id: str | None = None,
    ) -> list[Workflow]:
        """Return workflows stored within the repository."""
        async with self._lock:
            return [
                workflow.model_copy(deep=True)
                for workflow in self._workflows.values()
                if (include_archived or not workflow.is_archived)
                and (
                    tenant_id is None
                    or self._workflow_tenants.get(workflow.id) is None
                    or self._workflow_tenants.get(workflow.id) == tenant_id
                )
            ]

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
        """Persist a new workflow and return the created instance."""
        normalized_handle = normalize_workflow_handle(handle)
        async with self._lock:
            self._ensure_handle_available_locked(
                normalized_handle,
                workflow_id=None,
                is_archived=False,
            )
            workflow = Workflow(
                name=name,
                handle=normalized_handle,
                tenant_id=tenant_id,
                slug=slug or "",
                description=description,
                tags=list(tags or []),
                draft_access=draft_access,
            )
            workflow.record_event(actor=actor, action="workflow_created")
            self._workflows[workflow.id] = workflow
            if tenant_id is not None:
                self._workflow_tenants[workflow.id] = tenant_id
            self._rebuild_handle_indexes_locked()
            self._workflow_versions.setdefault(workflow.id, [])
            return workflow.model_copy(deep=True)

    async def get_workflow(
        self,
        workflow_id: UUID,
        *,
        tenant_id: str | None = None,
    ) -> Workflow:
        """Retrieve a workflow by its identifier."""
        async with self._lock:
            workflow = self._workflows.get(workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError(str(workflow_id))
            stored_tid = self._workflow_tenants.get(workflow_id)
            if (
                tenant_id is not None
                and stored_tid is not None
                and stored_tid != tenant_id
            ):
                raise WorkflowNotFoundError(str(workflow_id))
            return workflow.model_copy(deep=True)

    async def get_workflow_tenant_id(self, workflow_id: UUID) -> str | None:
        """Return the tenant_id for the workflow, or None if unscoped."""
        async with self._lock:
            return self._workflow_tenants.get(workflow_id)

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        tenant_id: str | None = None,
    ) -> UUID:
        """Resolve a workflow ref using handle-first semantics."""
        return await super().resolve_workflow_ref(
            workflow_ref,
            include_archived=include_archived,
            tenant_id=tenant_id,
        )

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
        """Update workflow metadata and record an audit event."""
        normalized_handle = normalize_workflow_handle(handle)
        async with self._lock:
            workflow = self._workflows.get(workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError(str(workflow_id))

            metadata: dict[str, Any] = {}
            should_disable_listeners = False
            next_is_archived = (
                workflow.is_archived if is_archived is None else is_archived
            )

            if normalized_handle is not None and normalized_handle != workflow.handle:
                self._ensure_handle_available_locked(
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
                if normalized_tags != workflow.tags:
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
            await self._maybe_disable_listener_subscriptions(
                workflow.id,
                should_disable=should_disable_listeners,
                actor=actor,
            )
            self._rebuild_handle_indexes_locked()
            return workflow.model_copy(deep=True)

    async def archive_workflow(self, workflow_id: UUID, *, actor: str) -> Workflow:
        """Archive a workflow by delegating to the update helper."""
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
        """Mark the workflow as public."""
        async with self._lock:
            workflow = self._workflows.get(workflow_id)
            if workflow is None or workflow.is_archived:
                raise WorkflowNotFoundError(str(workflow_id))
            try:
                workflow.publish(
                    require_login=require_login,
                    actor=actor,
                )
            except ValueError as exc:
                raise WorkflowPublishStateError(str(exc)) from exc
            return workflow.model_copy(deep=True)

    async def revoke_publish(self, workflow_id: UUID, *, actor: str) -> Workflow:
        """Revoke public access for the workflow."""
        async with self._lock:
            workflow = self._workflows.get(workflow_id)
            if workflow is None or workflow.is_archived:
                raise WorkflowNotFoundError(str(workflow_id))
            try:
                workflow.revoke_publish(actor=actor)
            except ValueError as exc:
                raise WorkflowPublishStateError(str(exc)) from exc
            return workflow.model_copy(deep=True)


__all__ = ["WorkflowCrudMixin"]
