"""Workflow-related request/response schemas."""

from __future__ import annotations
from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator
from orcheo.graph.ingestion import DEFAULT_SCRIPT_SIZE_LIMIT
from orcheo.models.workflow import (
    Workflow,
    WorkflowChatKitConfig,
    WorkflowDraftAccess,
    WorkflowVersion,
)
from orcheo.models.workflow_refs import normalize_workflow_handle
from orcheo.runtime.runnable_config import RunnableConfigModel


class WorkflowCreateRequest(BaseModel):
    """Payload for creating a new workflow."""

    name: str
    handle: str | None = None
    slug: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    draft_access: WorkflowDraftAccess | None = None
    actor: str = Field(default="system")

    @field_validator("handle", mode="before")
    @classmethod
    def _normalize_handle(cls, value: object) -> str | None:
        if value is None:
            return None
        return normalize_workflow_handle(str(value))


class WorkflowUpdateRequest(BaseModel):
    """Payload for updating an existing workflow."""

    name: str | None = None
    handle: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    draft_access: WorkflowDraftAccess | None = None
    is_archived: bool | None = None
    chatkit: WorkflowChatKitConfig | None = None
    clear_chatkit_start_screen_prompts: bool = False
    clear_chatkit_supported_models: bool = False
    actor: str = Field(default="system")

    @field_validator("handle", mode="before")
    @classmethod
    def _normalize_handle(cls, value: object) -> str | None:
        if value is None:
            return None
        return normalize_workflow_handle(str(value))

    @model_validator(mode="after")
    def _validate_chatkit_prompt_update(self) -> WorkflowUpdateRequest:
        chatkit_fields = (
            self.chatkit.model_fields_set if self.chatkit is not None else set()
        )
        if (
            self.clear_chatkit_start_screen_prompts
            and "start_screen_prompts" in chatkit_fields
        ):
            msg = (
                "Provide either chatkit.start_screen_prompts or "
                "clear_chatkit_start_screen_prompts, not both."
            )
            raise ValueError(msg)
        if self.clear_chatkit_supported_models and "supported_models" in chatkit_fields:
            msg = (
                "Provide either chatkit.supported_models or "
                "clear_chatkit_supported_models, not both."
            )
            raise ValueError(msg)
        return self


class WorkflowVersionIngestRequest(BaseModel):
    """Payload for ingesting a LangGraph Python script."""

    script: str
    entrypoint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    runnable_config: RunnableConfigModel | None = None
    notes: str | None = None
    created_by: str

    @field_validator("script")
    @classmethod
    def _enforce_script_size(cls, value: str) -> str:
        size = len(value.encode("utf-8"))
        if size > DEFAULT_SCRIPT_SIZE_LIMIT:
            msg = (
                "LangGraph script exceeds the maximum allowed size of "
                f"{DEFAULT_SCRIPT_SIZE_LIMIT} bytes"
            )
            raise ValueError(msg)
        return value


class WorkflowVersionRunnableConfigUpdateRequest(BaseModel):
    """Payload for updating workflow-version runnable config only."""

    runnable_config: RunnableConfigModel | None = None
    actor: str = Field(default="system")


class WorkflowRunCreateRequest(BaseModel):
    """Payload for creating a new workflow execution run."""

    workflow_version_id: UUID
    triggered_by: str
    input_payload: dict[str, Any] = Field(default_factory=dict)
    runnable_config: RunnableConfigModel | None = None


class WorkflowVersionDiffResponse(BaseModel):
    """Response payload for workflow version diffs."""

    base_version: int
    target_version: int
    diff: list[str]


class WorkflowPublishRequest(BaseModel):
    """Payload for publishing a workflow."""

    require_login: bool = False
    actor: str = Field(default="system")


class WorkflowPublishRevokeRequest(BaseModel):
    """Payload for revoking workflow publication."""

    actor: str = Field(default="system")


class WorkflowPublishResponse(BaseModel):
    """Response payload for publish actions."""

    workflow: Workflow
    message: str | None = None
    share_url: str | None = None


class PublicWorkflow(BaseModel):
    """Public workflow metadata returned without authentication."""

    id: UUID
    handle: str | None = None
    name: str
    description: str | None = None
    is_public: bool
    require_login: bool
    share_url: str | None = None
    chatkit: WorkflowChatKitConfig | None = None


class WorkflowListItem(Workflow):
    """Workflow list item enriched with listing-friendly summary fields."""

    latest_version: WorkflowVersion | None = None
    is_scheduled: bool = False


class WorkflowCanvasVersionSummary(BaseModel):
    """Compact workflow-version payload used when opening Canvas."""

    id: UUID
    workflow_id: UUID
    version: int
    mermaid: str | None = None
    has_cron_trigger: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    runnable_config: dict[str, Any] | None = None
    notes: str | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class WorkflowCanvasPayload(BaseModel):
    """Workflow payload optimized for opening Canvas."""

    workflow: Workflow
    versions: list[WorkflowCanvasVersionSummary] = Field(default_factory=list)
