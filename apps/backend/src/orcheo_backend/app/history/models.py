"""Shared models and protocol for workflow execution history."""

from __future__ import annotations
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(tz=UTC)


class RunHistoryError(RuntimeError):
    """Base error raised for run history store issues."""


class RunHistoryNotFoundError(RunHistoryError):
    """Raised when requesting history for an unknown execution."""


class RunHistoryStep(BaseModel):
    """Single step captured during workflow execution."""

    model_config = ConfigDict(extra="forbid")

    index: int
    at: datetime = Field(default_factory=_utcnow)
    payload: dict[str, Any]


class RunHistoryRecord(BaseModel):
    """Complete history for a workflow execution."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    workflow_id: str
    execution_id: str
    tenant_id: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    runnable_config: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    callbacks: list[Any] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    run_name: str | None = None
    status: str = "running"
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    error: str | None = None
    steps: list[RunHistoryStep] = Field(default_factory=list)
    trace_id: str | None = None
    trace_started_at: datetime | None = None
    trace_completed_at: datetime | None = None
    trace_last_span_at: datetime | None = None

    def append_step(
        self,
        payload: Mapping[str, Any],
        *,
        at: datetime | None = None,
    ) -> RunHistoryStep:
        """Append a step to the history with an auto-incremented index."""
        step = RunHistoryStep(
            index=len(self.steps),
            at=at or _utcnow(),
            payload=dict(payload),
        )
        self.steps.append(step)
        self.trace_last_span_at = step.at
        if self.trace_started_at is None:
            self.trace_started_at = step.at
        return step

    def mark_completed(self) -> None:
        """Mark the execution as successfully completed."""
        self.status = "completed"
        self.completed_at = _utcnow()
        self.error = None
        self.trace_completed_at = self.completed_at

    def mark_failed(self, error: str) -> None:
        """Mark the execution as failed with the provided error."""
        self.status = "error"
        self.completed_at = _utcnow()
        self.error = error
        self.trace_completed_at = self.completed_at

    def mark_cancelled(self, *, reason: str | None = None) -> None:
        """Mark the execution as cancelled with an optional reason."""
        self.status = "cancelled"
        self.completed_at = _utcnow()
        self.error = reason
        self.trace_completed_at = self.completed_at


class RunHistoryStore(Protocol):
    """Protocol describing history store behaviours."""

    async def start_run(
        self,
        *,
        workflow_id: str,
        execution_id: str,
        inputs: Mapping[str, Any] | None = None,
        runnable_config: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        callbacks: list[Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        run_name: str | None = None,
        trace_id: str | None = None,
        trace_started_at: datetime | None = None,
        tenant_id: str | None = None,
    ) -> RunHistoryRecord:
        """Initialise a history record for the provided execution."""

    async def append_step(
        self,
        execution_id: str,
        payload: Mapping[str, Any],
    ) -> RunHistoryStep:
        """Append a step for the execution."""

    async def mark_completed(self, execution_id: str) -> RunHistoryRecord:
        """Mark the execution as completed."""

    async def mark_failed(
        self,
        execution_id: str,
        error: str,
    ) -> RunHistoryRecord:
        """Mark the execution as failed with the specified error."""

    async def mark_cancelled(
        self,
        execution_id: str,
        *,
        reason: str | None = None,
    ) -> RunHistoryRecord:
        """Mark the execution as cancelled."""

    async def get_history(self, execution_id: str) -> RunHistoryRecord:
        """Return a deep copy of the execution history."""

    async def clear(self) -> None:
        """Clear all stored histories. Intended for testing only."""

    async def list_histories(
        self,
        workflow_id: str,
        *,
        limit: int | None = None,
        tenant_id: str | None = None,
    ) -> list[RunHistoryRecord]:
        """Return histories associated with the provided workflow."""


__all__ = [
    "RunHistoryError",
    "RunHistoryNotFoundError",
    "RunHistoryRecord",
    "RunHistoryStep",
    "RunHistoryStore",
]
