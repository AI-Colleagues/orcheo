"""Schemas describing workflow tracing responses."""

from __future__ import annotations
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


class TraceSpanStatus(BaseModel):
    """Status metadata attached to an OpenTelemetry span."""

    code: Literal["OK", "ERROR", "UNSET"]
    message: str | None = None


class TraceSpanEvent(BaseModel):
    """Event emitted within the lifecycle of a span."""

    name: str
    time: datetime | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class TraceSpanResponse(BaseModel):
    """Serializable representation of a span."""

    span_id: str
    parent_span_id: str | None = None
    name: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[TraceSpanEvent] = Field(default_factory=list)
    status: TraceSpanStatus = Field(
        default_factory=lambda: TraceSpanStatus(code="UNSET")
    )
    links: list[dict[str, Any]] = Field(default_factory=list)


class TraceTokenUsage(BaseModel):
    """Aggregated token usage for an execution."""

    input: int = 0
    output: int = 0


class TraceExecutionMetadata(BaseModel):
    """High-level metadata associated with an execution trace."""

    id: str
    status: str
    thread_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    trace_id: str | None = None
    token_usage: TraceTokenUsage = Field(default_factory=TraceTokenUsage)


class TracePageInfo(BaseModel):
    """Cursor metadata for paginated trace responses."""

    has_next_page: bool = False
    cursor: str | None = None


class TraceResponse(BaseModel):
    """Response envelope returned by the trace endpoint."""

    execution: TraceExecutionMetadata
    spans: list[TraceSpanResponse] = Field(default_factory=list)
    page_info: TracePageInfo = Field(default_factory=TracePageInfo)


class TraceSpanStateResponse(BaseModel):
    """Workflow state captured immediately before and after a node span ran."""

    span_id: str
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    redacted: bool = False
    truncated: bool = False


class TraceUpdateMessage(BaseModel):
    """Realtime websocket payload describing trace changes."""

    type: Literal["trace:update"] = "trace:update"
    execution_id: str
    trace_id: str
    spans: list[TraceSpanResponse] = Field(default_factory=list)
    complete: bool = False
