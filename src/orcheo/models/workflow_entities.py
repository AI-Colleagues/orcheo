"""Workflow-centric entities and supporting utilities."""

from __future__ import annotations
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any, NotRequired, TypedDict
from uuid import UUID, uuid4
from pydantic import Field, field_validator, model_validator
from orcheo.models.base import OrcheoBaseModel, TimestampedAuditModel, _utcnow
from orcheo.models.workflow_refs import normalize_workflow_handle


__all__ = [
    "WorkflowChatKitConfig",
    "ChatKitSupportedModel",
    "ChatKitStartScreenPrompt",
    "WorkflowDraftAccess",
    "Workflow",
    "WorkflowRun",
    "WorkflowRunStatus",
    "WorkflowVersion",
]


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    """Convert the provided value into a workflow-safe slug."""
    normalized = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return normalized or value.strip().lower() or str(uuid4())


class WorkflowPublishMetadata(TypedDict):
    """Audit metadata for workflow publish events."""

    require_login: bool


class WorkflowUnpublishedMetadata(TypedDict):
    """Audit metadata for workflow unpublish events."""

    require_login: bool


class WorkflowRunFailedMetadata(TypedDict):
    """Audit metadata captured when a workflow run fails."""

    error: str


class WorkflowRunCancelledMetadata(TypedDict, total=False):
    """Audit metadata captured when a workflow run is cancelled."""

    reason: NotRequired[str]


class WorkflowDraftAccess(str, Enum):
    """Draft-access modes for unpublished workflows."""

    PERSONAL = "personal"
    AUTHENTICATED = "authenticated"
    WORKSPACE = "workspace"


class ChatKitStartScreenPrompt(OrcheoBaseModel):
    """Configurable prompt shown on the public ChatKit start screen."""

    label: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=2000)
    icon: str | None = Field(default=None, max_length=64)

    @field_validator("label", "prompt", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: object) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            msg = "ChatKit prompt fields must not be empty."
            raise ValueError(msg)
        return candidate

    @field_validator("icon", mode="before")
    @classmethod
    def _normalize_icon(cls, value: object) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None


class ChatKitSupportedModel(OrcheoBaseModel):
    """Configurable model option shown in the ChatKit composer."""

    id: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=240)
    disabled: bool = False
    default: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            msg = "ChatKit model id must not be empty."
            raise ValueError(msg)
        return candidate

    @field_validator("label", "description", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None


class WorkflowChatKitConfig(OrcheoBaseModel):
    """Typed configuration for the published ChatKit surface."""

    start_screen_prompts: list[ChatKitStartScreenPrompt] | None = None
    supported_models: list[ChatKitSupportedModel] | None = None


class Workflow(TimestampedAuditModel):
    """Represents a workflow container with metadata and audit trail."""

    name: str = Field(min_length=1, max_length=128)
    handle: str | None = Field(default=None, max_length=64)
    workspace_id: str | None = None
    slug: str = ""
    description: str | None = Field(default=None, max_length=1024)
    tags: list[str] = Field(default_factory=list)
    is_archived: bool = False
    draft_access: WorkflowDraftAccess = WorkflowDraftAccess.PERSONAL
    is_public: bool = False
    published_at: datetime | None = None
    published_by: str | None = None
    require_login: bool = False
    share_url: str | None = None
    chatkit: WorkflowChatKitConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _backfill_draft_access(cls, data: object) -> object:
        """Infer legacy draft access from workspace tags when absent."""
        if not isinstance(data, Mapping):
            return data
        if data.get("draft_access") is not None:
            return data
        tags = data.get("tags")
        if not isinstance(tags, list):
            return data
        if any(
            isinstance(tag, str) and tag.lower().startswith("workspace:") and ":" in tag
            for tag in tags
        ):
            return {**data, "draft_access": WorkflowDraftAccess.WORKSPACE}
        return data

    @model_validator(mode="before")
    @classmethod
    def _backfill_chatkit_config(cls, data: object) -> object:
        """Fold legacy top-level ChatKit fields into the typed config object."""
        if not isinstance(data, Mapping):
            return data
        normalized = dict(data)
        if normalized.get("chatkit") is not None:
            normalized.pop("chatkit_start_screen_prompts", None)
            normalized.pop("chatkit_supported_models", None)
            return normalized

        chatkit_payload: dict[str, Any] = {}
        if "chatkit_start_screen_prompts" in normalized:
            chatkit_payload["start_screen_prompts"] = normalized.pop(
                "chatkit_start_screen_prompts"
            )
        if "chatkit_supported_models" in normalized:
            chatkit_payload["supported_models"] = normalized.pop(
                "chatkit_supported_models"
            )
        if not chatkit_payload:
            return normalized
        normalized["chatkit"] = chatkit_payload
        return normalized

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            msg = "Workflow name must not be empty."
            raise ValueError(msg)
        return candidate

    @field_validator("handle", mode="before")
    @classmethod
    def _normalize_handle(cls, value: object) -> str | None:
        if value is None:
            return None
        return normalize_workflow_handle(str(value))

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: object) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("tags", mode="after")
    @classmethod
    def _dedupe_tags(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for tag in value:
            normalized = tag.strip()
            key = normalized.lower()
            if key and key not in seen:
                seen.add(key)
                deduped.append(key)
        return deduped

    @model_validator(mode="after")
    def _populate_slug(self) -> Workflow:
        slug_source = self.slug or self.name
        if not slug_source:
            msg = "Workflow requires a name or slug to be provided."
            raise ValueError(msg)
        object.__setattr__(self, "slug", _slugify(slug_source))
        return self

    @property
    def chatkit_start_screen_prompts(self) -> list[ChatKitStartScreenPrompt] | None:
        """Compatibility accessor for the typed ChatKit config."""
        if self.chatkit is None:
            return None
        return self.chatkit.start_screen_prompts

    @chatkit_start_screen_prompts.setter
    def chatkit_start_screen_prompts(
        self,
        value: list[ChatKitStartScreenPrompt] | None,
    ) -> None:
        """Compatibility setter for ChatKit starter prompts."""
        self._set_chatkit_field("start_screen_prompts", value)

    @property
    def chatkit_supported_models(self) -> list[ChatKitSupportedModel] | None:
        """Compatibility accessor for the typed ChatKit config."""
        if self.chatkit is None:
            return None
        return self.chatkit.supported_models

    @chatkit_supported_models.setter
    def chatkit_supported_models(
        self,
        value: list[ChatKitSupportedModel] | None,
    ) -> None:
        """Compatibility setter for ChatKit supported models."""
        self._set_chatkit_field("supported_models", value)

    def _set_chatkit_field(
        self,
        field_name: str,
        value: list[ChatKitStartScreenPrompt] | list[ChatKitSupportedModel] | None,
    ) -> None:
        """Assign a single ChatKit field while collapsing empty config objects."""
        chatkit = self.chatkit
        if chatkit is None:
            if value is None:
                return
            chatkit = WorkflowChatKitConfig()
            object.__setattr__(self, "chatkit", chatkit)

        setattr(chatkit, field_name, value)
        if chatkit.start_screen_prompts is None and chatkit.supported_models is None:
            object.__setattr__(self, "chatkit", None)

    # -- Publish management -------------------------------------------------
    def publish(
        self,
        *,
        require_login: bool,
        actor: str,
    ) -> None:
        """Mark the workflow as publicly accessible."""
        if self.is_public:
            msg = "Workflow is already published."
            raise ValueError(msg)

        now = _utcnow()
        metadata: WorkflowPublishMetadata = {
            "require_login": require_login,
        }
        self.is_public = True
        self.published_at = now
        self.published_by = actor
        self.require_login = require_login
        self.record_event(
            actor=actor,
            action="workflow_published",
            metadata=metadata,
        )

    def revoke_publish(self, *, actor: str) -> None:
        """Revoke public access to the workflow."""
        if not self.is_public:
            msg = "Workflow is not currently published."
            raise ValueError(msg)

        metadata: WorkflowUnpublishedMetadata = {
            "require_login": self.require_login,
        }
        self.is_public = False
        self.published_at = None
        self.published_by = None
        self.require_login = False
        self.record_event(
            actor=actor,
            action="workflow_unpublished",
            metadata=metadata,
        )


class WorkflowVersion(TimestampedAuditModel):
    """Versioned definition of a workflow graph."""

    workflow_id: UUID
    workspace_id: str | None = None
    version: int = Field(gt=0)
    graph: dict[str, Any] = Field(default_factory=dict)
    mermaid: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    runnable_config: dict[str, Any] | None = None
    created_by: str
    notes: str | None = None

    def compute_checksum(self) -> str:
        """Return a deterministic checksum for the graph definition."""
        serialized = json.dumps(self.graph, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class WorkflowRunStatus(str, Enum):
    """Possible states for an individual workflow execution run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """Return whether the status represents a terminal state."""
        return self in {
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
        }


class WorkflowRun(TimestampedAuditModel):
    """Runtime record for a workflow execution."""

    workspace_id: str | None = None
    workflow_version_id: UUID
    status: WorkflowRunStatus = Field(default=WorkflowRunStatus.PENDING)
    triggered_by: str
    input_payload: dict[str, Any] = Field(default_factory=dict)
    runnable_config: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    callbacks: list[Any] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    run_name: str | None = None
    output_payload: dict[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None

    def mark_started(self, *, actor: str) -> None:
        """Transition the run into the running state."""
        if self.status is not WorkflowRunStatus.PENDING:
            msg = "Only pending runs can be started."
            raise ValueError(msg)
        self.status = WorkflowRunStatus.RUNNING
        self.started_at = _utcnow()
        self.record_event(actor=actor, action="run_started")

    def mark_succeeded(
        self,
        *,
        actor: str,
        output: Mapping[str, Any] | None = None,
    ) -> None:
        """Mark the run as successfully completed."""
        if self.status is not WorkflowRunStatus.RUNNING:
            msg = "Only running runs can be marked as succeeded."
            raise ValueError(msg)
        self.status = WorkflowRunStatus.SUCCEEDED
        self.completed_at = _utcnow()
        self.output_payload = dict(output or {})
        self.error = None
        self.record_event(actor=actor, action="run_succeeded")

    def mark_failed(self, *, actor: str, error: str) -> None:
        """Mark the run as failed with the provided error message."""
        if self.status not in {WorkflowRunStatus.PENDING, WorkflowRunStatus.RUNNING}:
            msg = "Only pending or running runs can be marked as failed."
            raise ValueError(msg)
        self.status = WorkflowRunStatus.FAILED
        self.completed_at = _utcnow()
        self.error = error
        metadata: WorkflowRunFailedMetadata = {"error": error}
        self.record_event(actor=actor, action="run_failed", metadata=metadata)

    def mark_cancelled(self, *, actor: str, reason: str | None = None) -> None:
        """Cancel the run from a non-terminal state."""
        if self.status.is_terminal:
            msg = "Cannot cancel a run that is already completed."
            raise ValueError(msg)
        self.status = WorkflowRunStatus.CANCELLED
        self.completed_at = _utcnow()
        self.error = reason
        metadata: WorkflowRunCancelledMetadata = {}
        if reason:
            metadata["reason"] = reason
        self.record_event(actor=actor, action="run_cancelled", metadata=metadata)
