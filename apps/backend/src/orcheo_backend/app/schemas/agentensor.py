"""Schemas for Agentensor checkpoint APIs."""

from __future__ import annotations
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field
from orcheo.agentensor.checkpoints import AgentensorCheckpoint
from orcheo.agentensor.evaluation import EvaluationRequest
from orcheo.runtime.runnable_config import RunnableConfigModel


class WorkflowEvaluationRequest(BaseModel):
    """Evaluate a stored workflow version within a bounded request lifetime."""

    evaluation: EvaluationRequest
    inputs: dict[str, Any] = Field(default_factory=dict)
    version: int | None = Field(default=None, ge=1)
    runnable_config: RunnableConfigModel | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=300)


class WorkflowEvaluationResponse(BaseModel):
    """Summary of an evaluation whose full events are stored in run history."""

    execution_id: str
    status: str
    result: dict[str, Any] | None = None


class AgentensorCheckpointResponse(BaseModel):
    """Response payload for persisted training checkpoints."""

    id: str
    workflow_id: str
    config_version: int
    runnable_config: dict[str, Any]
    metrics: dict[str, Any]
    metadata: dict[str, Any]
    artifact_url: str | None = None
    created_at: datetime
    is_best: bool = False

    @classmethod
    def from_domain(
        cls, checkpoint: AgentensorCheckpoint
    ) -> AgentensorCheckpointResponse:
        """Build a response payload from a checkpoint domain model."""
        return cls(
            id=checkpoint.id,
            workflow_id=checkpoint.workflow_id,
            config_version=checkpoint.config_version,
            runnable_config=dict(checkpoint.runnable_config),
            metrics=dict(checkpoint.metrics),
            metadata=dict(checkpoint.metadata),
            artifact_url=checkpoint.artifact_url,
            created_at=checkpoint.created_at,
            is_best=checkpoint.is_best,
        )


__all__ = ["AgentensorCheckpointResponse"]
