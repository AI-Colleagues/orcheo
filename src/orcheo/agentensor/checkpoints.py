"""Checkpoint models shared across Agentensor training flows."""

from __future__ import annotations
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(tz=UTC)


class AgentensorCheckpoint(BaseModel):
    """Concrete checkpoint record persisted by the trainer.

    The optional ``artifact_url`` can reference external artifacts (e.g., files in
    object storage) produced during training; it remains unset in Milestone 3 while
    metrics/config snapshots are persisted inline.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_id: str = Field(max_length=128, min_length=1)
    tenant_id: str | None = None
    config_version: int = Field(ge=1)
    runnable_config: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    artifact_url: str | None = Field(
        default=None, description="Optional link to external checkpoint artifacts."
    )
    created_at: datetime = Field(default_factory=_utcnow)
    is_best: bool = False


@runtime_checkable
class AgentensorCheckpointStore(Protocol):
    """Persistence interface for trainer checkpoints."""

    async def record_checkpoint(
        self,
        *,
        workflow_id: str,
        runnable_config: Mapping[str, Any],
        metrics: Mapping[str, Any],
        metadata: Mapping[str, Any] | None = None,
        artifact_url: str | None = None,
        is_best: bool = False,
        config_version: int | None = None,
        tenant_id: str | None = None,
    ) -> AgentensorCheckpoint:
        """Persist a checkpoint with an auto-incremented config version."""

    async def list_checkpoints(
        self,
        workflow_id: str,
        *,
        limit: int | None = None,
        tenant_id: str | None = None,
    ) -> list[AgentensorCheckpoint]:
        """Return checkpoints associated with a workflow ordered by version."""

    async def get_checkpoint(self, checkpoint_id: str) -> AgentensorCheckpoint:
        """Return a checkpoint by identifier or raise if missing."""

    async def latest_checkpoint(
        self,
        workflow_id: str,
    ) -> AgentensorCheckpoint | None:
        """Return the most recent checkpoint for the workflow if present."""


class AgentensorCheckpointNotFoundError(RuntimeError):
    """Raised when the requested checkpoint does not exist."""


__all__ = [
    "AgentensorCheckpoint",
    "AgentensorCheckpointNotFoundError",
    "AgentensorCheckpointStore",
]
