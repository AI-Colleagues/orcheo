"""Workflow-specific tracing helpers."""

from __future__ import annotations
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from opentelemetry.trace import Span, Status, StatusCode, Tracer
from orcheo.config import get_settings
from orcheo.runtime.runnable_config import RunnableConfigModel
from orcheo.tracing.model_metadata import (
    NAMESPACE_METADATA_KEY,
    extract_ai_trace_attributes,
    extract_step_namespace,
)


_DEFAULT_MAX_PREVIEW_LENGTH = 512
_DEFAULT_HIGH_USAGE_THRESHOLD = 1000
_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"),
    re.compile(r"\b(?:\d[ -.]?){13,16}\b"),
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?P<label>(?:api|secret|token|key)[\w-]*)\s*(?P<sep>[:=])\s*(?P<value>[A-Z0-9\-_=]{8,})"
)


@dataclass(slots=True)
class WorkflowSpanContext:
    """Lightweight container describing an active workflow span."""

    span: Span
    started_at: datetime

    @property
    def trace_id(self) -> str:
        """Return the current span's trace identifier as a hex string."""
        return f"{self.span.get_span_context().trace_id:032x}"


@contextmanager
def workflow_span(
    tracer: Tracer,
    *,
    workflow_id: str,
    execution_id: str,
    inputs: Mapping[str, Any] | None = None,
    runnable_config: RunnableConfigModel | None = None,
) -> Iterator[WorkflowSpanContext]:
    """Create a root span encapsulating a workflow execution."""
    input_keys: Sequence[str] = tuple(sorted(inputs.keys())) if inputs else ()
    attributes: dict[str, Any] = {
        "orcheo.execution.id": execution_id,
        "orcheo.workflow.id": workflow_id,
    }
    if input_keys:
        attributes["orcheo.execution.input_keys"] = list(input_keys)
        attributes["orcheo.execution.input_count"] = len(input_keys)
    if runnable_config is not None:
        if runnable_config.tags:
            attributes["orcheo.execution.tags"] = list(runnable_config.tags)
            attributes["orcheo.execution.tag_count"] = len(runnable_config.tags)
        if runnable_config.run_name:
            attributes["orcheo.execution.run_name"] = runnable_config.run_name
        if runnable_config.metadata:
            attributes["orcheo.execution.metadata_keys"] = sorted(
                runnable_config.metadata.keys()
            )
        if runnable_config.callbacks:
            attributes["orcheo.execution.callbacks.count"] = len(
                runnable_config.callbacks
            )
        if runnable_config.recursion_limit is not None:
            attributes["orcheo.execution.recursion_limit"] = (
                runnable_config.recursion_limit
            )
        if runnable_config.max_concurrency is not None:
            attributes["orcheo.execution.max_concurrency"] = (
                runnable_config.max_concurrency
            )
        prompts = runnable_config.prompts
        if prompts:
            attributes["orcheo.execution.prompts.count"] = len(prompts)
    started_at = datetime.now(tz=UTC)
    with tracer.start_as_current_span(
        "workflow.execution",
        attributes=attributes,
    ) as span:
        yield WorkflowSpanContext(span=span, started_at=started_at)


def split_subgraph_update(chunk: Any) -> tuple[tuple[str, ...], Mapping[str, Any]]:
    """Split a ``subgraphs=True`` stream chunk into its namespace and update.

    With ``stream_mode="updates"`` and ``subgraphs=True``, LangGraph yields
    ``(namespace, update)`` tuples where ``namespace`` is the path of
    ``"<node_name>:<task_id>"`` segments leading to the subgraph the update
    happened in (empty for top-level nodes). Falls back to treating the chunk
    as a bare update mapping for callers that stream without subgraphs.
    """
    if (
        isinstance(chunk, tuple)
        and len(chunk) == 2
        and isinstance(chunk[0], tuple)
        and isinstance(chunk[1], Mapping)
    ):
        namespace, update = chunk
        return tuple(str(segment) for segment in namespace), update
    if isinstance(chunk, Mapping):
        return (), chunk
    msg = f"Unexpected stream chunk shape: {type(chunk)!r}"
    raise TypeError(msg)


def record_workflow_step(tracer: Tracer, step: Mapping[str, Any]) -> None:
    """Emit child spans that represent node executions within a step payload."""
    namespace = extract_step_namespace(step)
    for node_name, payload in step.items():
        if node_name == NAMESPACE_METADATA_KEY:
            continue
        if not isinstance(payload, Mapping):
            continue
        attributes = _node_attributes(node_name, payload)
        if namespace:
            attributes["orcheo.node.namespace"] = ":".join(namespace)
        span_name = attributes.get("orcheo.node.display_name", node_name)
        with tracer.start_as_current_span(
            str(span_name),
            attributes=attributes,
        ) as span:
            _apply_token_metrics(span, payload)
            _apply_artifact_attributes(span, payload)
            _apply_message_events(span, payload)
            _apply_status(span, payload)


def record_workflow_completion(span: Span, *, status: str = "completed") -> None:
    """Mark the root span as successfully completed."""
    span.add_event("workflow.completed", {"status": status})
    span.set_status(Status(StatusCode.OK))


def record_workflow_failure(span: Span, error: Exception) -> None:
    """Mark the root span as failed with the provided exception."""
    span.record_exception(error)
    span.set_status(Status(StatusCode.ERROR, str(error)))
    span.add_event(
        "workflow.failed",
        {
            "exception.type": error.__class__.__name__,
            "exception.message": str(error),
        },
    )


def record_workflow_cancellation(span: Span, *, reason: str | None = None) -> None:
    """Mark the root span as cancelled."""
    span.set_status(Status(StatusCode.ERROR, reason or "cancelled"))
    span.add_event("workflow.cancelled", {"reason": reason or "cancelled"})


def _node_attributes(node_name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    display_name = str(payload.get("display_name", node_name))
    attributes: dict[str, Any] = {
        "orcheo.node.id": str(payload.get("id", node_name)),
        "orcheo.node.display_name": display_name,
    }
    kind = payload.get("kind") or payload.get("type")
    if kind is not None:
        attributes["orcheo.node.kind"] = str(kind)
    status = _coalesce_status(payload)
    if status:
        attributes["orcheo.node.status"] = status
    latency = _extract_latency(payload)
    if latency is not None:
        attributes["orcheo.node.latency_ms"] = latency
    attributes.update(extract_ai_trace_attributes(payload))
    return attributes


def _apply_token_metrics(span: Span, payload: Mapping[str, Any]) -> None:
    input_tokens, output_tokens = _extract_token_usage(payload)
    if input_tokens is not None:
        span.set_attribute("orcheo.token.input", input_tokens)
    if output_tokens is not None:
        span.set_attribute("orcheo.token.output", output_tokens)
    threshold = _token_usage_threshold()
    if (input_tokens or 0) > threshold or (output_tokens or 0) > threshold:
        span.add_event(
            "token.chunk",
            {
                "input": input_tokens or 0,
                "output": output_tokens or 0,
                "reason": "high_usage",
            },
        )


def _apply_artifact_attributes(span: Span, payload: Mapping[str, Any]) -> None:
    artifacts = payload.get("artifacts")
    if not artifacts:
        return
    artifact_ids = [
        str(artifact.get("id"))
        if isinstance(artifact, Mapping) and artifact.get("id") is not None
        else str(artifact)
        for artifact in artifacts
    ]
    span.set_attribute("orcheo.artifact.ids", artifact_ids)


def _apply_message_events(span: Span, payload: Mapping[str, Any]) -> None:
    for key in ("prompts", "prompt"):
        if key in payload:
            _add_text_events(span, "prompt", payload[key])
    for key in ("responses", "response"):
        if key in payload:
            _add_text_events(span, "response", payload[key])
    if "messages" in payload:
        messages = payload["messages"]
        if isinstance(messages, Sequence):
            for message in messages:
                if isinstance(message, Mapping):
                    role = str(message.get("role", "unknown"))
                    preview = _preview_text(message.get("content"))
                else:
                    role = "message"
                    preview = _preview_text(message)
                span.add_event(
                    "message",
                    {
                        "role": role,
                        "preview": preview,
                    },
                )


def _apply_status(span: Span, payload: Mapping[str, Any]) -> None:
    status = _coalesce_status(payload)
    if status is None:
        span.set_status(Status(StatusCode.OK))
        return
    if status.lower() == "error":
        error_obj = payload.get("error")
        error_code = None
        error_message = None
        if isinstance(error_obj, Mapping):
            error_code = error_obj.get("code")
            error_message = error_obj.get("message")
        elif isinstance(error_obj, str):
            error_message = error_obj
        if error_code:
            span.set_attribute("orcheo.error.code", str(error_code))
        if error_message:
            span.add_event("error.detail", {"message": _preview_text(error_message)})
        span.set_status(Status(StatusCode.ERROR, error_message or "error"))
    else:
        span.set_status(Status(StatusCode.OK))


def _coalesce_status(payload: Mapping[str, Any]) -> str | None:
    for key in ("status", "state", "result"):
        value = payload.get(key)
        if isinstance(value, str):
            return value.lower()
    return None


def _extract_latency(payload: Mapping[str, Any]) -> int | None:
    for key in ("latency_ms", "duration_ms", "elapsed_ms"):
        candidate = payload.get(key)
        if candidate is None:
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            continue
    return None


def _extract_token_usage(payload: Mapping[str, Any]) -> tuple[int | None, int | None]:
    token_sources: Iterable[Any] = (
        payload.get("token_usage"),
        payload.get("usage"),
        payload.get("usage_metadata"),
    )
    input_tokens: int | None = None
    output_tokens: int | None = None
    for source in token_sources:
        if not isinstance(source, Mapping):
            continue
        if input_tokens is None:
            input_tokens = _extract_token_count(
                source,
                ("input", "input_tokens", "prompt", "prompt_tokens"),
            )
        if output_tokens is None:
            output_tokens = _extract_token_count(
                source,
                ("output", "output_tokens", "completion", "completion_tokens"),
            )
    if input_tokens is None:
        input_tokens = _extract_token_count(payload, ("input_tokens", "prompt_tokens"))
    if output_tokens is None:
        output_tokens = _extract_token_count(
            payload, ("output_tokens", "completion_tokens")
        )
    return input_tokens, output_tokens


def _extract_token_count(
    payload: Mapping[str, Any],
    keys: Iterable[str],
) -> int | None:
    for key in keys:
        candidate = payload.get(key)
        if candidate is None:
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            continue
    return None


def _add_text_events(span: Span, event_name: str, value: Any) -> None:
    if isinstance(value, Mapping):
        span.add_event(event_name, {k: _preview_text(v) for k, v in value.items()})
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for item in value:
            span.add_event(event_name, {"preview": _preview_text(item)})
        return
    span.add_event(event_name, {"preview": _preview_text(value)})


def _preview_text(value: Any) -> str:
    text = "" if value is None else str(value)
    sanitized = _sanitize_text(text)
    max_length = _preview_max_length()
    if len(sanitized) <= max_length:
        return sanitized
    return sanitized[: max_length - 1] + "…"


def _token_usage_threshold() -> int:
    settings = get_settings()
    value = settings.get("TRACING_HIGH_TOKEN_THRESHOLD", _DEFAULT_HIGH_USAGE_THRESHOLD)
    try:
        threshold = int(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return _DEFAULT_HIGH_USAGE_THRESHOLD
    return threshold if threshold > 0 else _DEFAULT_HIGH_USAGE_THRESHOLD


def _preview_max_length() -> int:
    settings = get_settings()
    value = settings.get("TRACING_PREVIEW_MAX_LENGTH", _DEFAULT_MAX_PREVIEW_LENGTH)
    try:
        length = int(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return _DEFAULT_MAX_PREVIEW_LENGTH
    return length if length > 0 else _DEFAULT_MAX_PREVIEW_LENGTH


def _sanitize_text(text: str) -> str:
    sanitized = text
    for pattern in _SENSITIVE_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)

    def _replace_secret(match: re.Match[str]) -> str:
        label = match.group("label")
        separator = match.group("sep")
        return f"{label}{separator} [REDACTED]"

    sanitized = _SECRET_ASSIGNMENT_PATTERN.sub(_replace_secret, sanitized)
    return sanitized


__all__ = [
    "WorkflowSpanContext",
    "record_workflow_cancellation",
    "record_workflow_completion",
    "record_workflow_failure",
    "record_workflow_step",
    "split_subgraph_update",
    "workflow_span",
]
