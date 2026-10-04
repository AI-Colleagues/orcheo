"""Manual trigger request and dispatch utilities."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from orcheo.runtime.runnable_config import RunnableConfigModel


class ManualDispatchValidationError(ValueError):
    """Raised when manual dispatch payloads fail validation."""


class ManualTriggerValidationError(ValueError):
    """Raised when manual trigger configuration is invalid."""


class ManualTriggerConfig(BaseModel):
    """Configuration describing how manual triggers behave."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(
        default="manual",
        description="Label used to identify manual trigger executions.",
    )
    allowed_actors: list[str] = Field(
        default_factory=list,
        description=(
            "Optional list of actors permitted to dispatch manual runs. Empty allows"
            " any authenticated user."
        ),
    )
    require_comment: bool = Field(
        default=False,
        description="Whether dispatchers must provide a comment when triggering runs.",
    )
    default_payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Default payload merged into manual dispatch requests.",
    )
    cooldown_seconds: int = Field(
        default=0,
        ge=0,
        description=(
            "Minimum number of seconds between manual dispatches. Zero disables the"
            " cooldown."
        ),
    )
    last_dispatched_at: datetime | None = Field(
        default=None,
        description=(
            "Timestamp of the last manual dispatch. Managed by the runtime; should"
            " not be set manually."
        ),
    )

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "label must be a non-empty string"
            raise ManualTriggerValidationError(msg)
        return normalized

    @field_validator("allowed_actors", mode="after")
    @classmethod
    def _normalize_actors(cls, value: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for actor in value:
            normalized = actor.strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(normalized)
        return deduped

    @model_validator(mode="after")
    def _validate_cooldown(self) -> ManualTriggerConfig:
        if self.cooldown_seconds and self.last_dispatched_at:
            now = datetime.now(UTC)
            elapsed = (now - self.last_dispatched_at).total_seconds()
            if elapsed < 0:
                msg = "last_dispatched_at cannot be in the future"
                raise ManualTriggerValidationError(msg)
        return self


@dataclass(slots=True)
class ManualDispatchRun:
    """Resolved manual dispatch entry targeting a workflow version."""

    workflow_version_id: UUID
    input_payload: dict[str, Any]
    runnable_config: dict[str, Any] | None = None


class ManualDispatchItem(BaseModel):
    """Single run request within a manual dispatch."""

    model_config = ConfigDict(extra="forbid")

    workflow_version_id: UUID | None = Field(
        default=None,
        description=(
            "Optional workflow version identifier. When omitted the latest version "
            "will be used."
        ),
    )
    input_payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Payload supplied as the run's input context.",
    )
    runnable_config: RunnableConfigModel | None = Field(
        default=None,
        description="Per-run overrides merged with the stored version configuration.",
    )


class ManualDispatchRequest(BaseModel):
    """Batch of manual run dispatches for a workflow."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: UUID = Field(description="Identifier of the workflow to execute.")
    actor: str = Field(
        default="manual",
        description="Actor recorded in the workflow run audit trail.",
    )
    runs: list[ManualDispatchItem] = Field(
        default_factory=list,
        min_length=1,
        max_length=100,
        description="Collection of run requests to dispatch in order.",
    )
    label: str | None = Field(
        default=None,
        description=(
            "Optional label describing the trigger source. When omitted a sensible "
            "default is derived based on run count."
        ),
    )

    @field_validator("actor")
    @classmethod
    def _normalize_actor(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "actor must be a non-empty string"
            raise ManualDispatchValidationError(msg)
        return normalized

    @field_validator("label")
    @classmethod
    def _normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            msg = "label must not be empty when provided"
            raise ManualDispatchValidationError(msg)
        return normalized

    @model_validator(mode="after")
    def _enforce_run_limit(self) -> ManualDispatchRequest:
        if not self.runs:
            msg = "at least one run must be provided"
            raise ManualDispatchValidationError(msg)
        return self

    def trigger_label(self) -> str:
        """Return the label recorded on dispatched runs."""
        if self.label:
            return self.label
        return "manual" if len(self.runs) == 1 else "manual_batch"

    def resolve_runs(
        self, *, default_workflow_version_id: UUID
    ) -> list[ManualDispatchRun]:
        """Resolve each request against the provided default version."""
        resolved: list[ManualDispatchRun] = []
        for item in self.runs:
            version_id = item.workflow_version_id or default_workflow_version_id
            resolved.append(
                ManualDispatchRun(
                    workflow_version_id=version_id,
                    input_payload=dict(item.input_payload),
                    runnable_config=item.runnable_config.model_dump(
                        mode="json", exclude_unset=True
                    )
                    if item.runnable_config is not None
                    else None,
                )
            )
        return resolved


__all__ = [
    "ManualDispatchItem",
    "ManualDispatchRequest",
    "ManualDispatchRun",
    "ManualDispatchValidationError",
    "ManualTriggerConfig",
    "ManualTriggerValidationError",
]
