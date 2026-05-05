"""Helpers for assembling workflow trace payloads."""

from __future__ import annotations
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from hashlib import blake2b
from typing import Any, TypedDict
from orcheo.tracing import workflow as tracing_workflow
from orcheo.tracing.model_metadata import (
    TRACE_METADATA_KEY,
    extract_ai_trace_attributes,
)
from orcheo_backend.app.history import RunHistoryRecord, RunHistoryStep
from orcheo_backend.app.schemas.traces import (
    TraceExecutionMetadata,
    TracePageInfo,
    TraceResponse,
    TraceSpanEvent,
    TraceSpanResponse,
    TraceSpanStatus,
    TraceTokenUsage,
    TraceUpdateMessage,
)


_STATE_MAX_DEPTH = 6
_STATE_MAX_COLLECTION_ITEMS = 64
_STATE_MAX_STRING_LENGTH = 2048
_STATE_REDACTED_MARKER = "[REDACTED]"
_STATE_TRUNCATED_MARKER = "[TRUNCATED]"
_SENSITIVE_STATE_KEY_PATTERN = re.compile(
    r"(?i)(password|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key)"
)
_NON_SENSITIVE_STATE_KEYS = frozenset(
    {
        "token_usage",
        "usage",
        "usage_metadata",
        "prompt_tokens_details",
        "completion_tokens_details",
        "input_token_details",
        "output_token_details",
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "audio_tokens",
        "reasoning_tokens",
        "accepted_prediction_tokens",
        "rejected_prediction_tokens",
        "cache_read",
        "cache_write",
        "audio",
        "text",
        "reasoning",
    }
)


class _WorkflowStateSnapshot(TypedDict):
    before: dict[str, Any]
    after: dict[str, Any]
    redacted: bool
    truncated: bool


def build_trace_response(record: RunHistoryRecord) -> TraceResponse:
    """Convert a history record into a trace response."""
    root_span_id = _derive_root_span_id(record.trace_id, record.execution_id)
    runtime_thread_id = _extract_runtime_thread_id(record)
    root_span = _build_root_span(record, root_span_id, runtime_thread_id)
    spans: list[TraceSpanResponse] = [root_span]
    state_snapshots = _build_workflow_state_snapshots(record)

    total_input = 0
    total_output = 0
    for step in record.steps:
        node_spans = _build_spans_for_step(
            record,
            step,
            root_span_id,
            state_snapshots=state_snapshots,
        )
        spans.extend(node_spans)
        for span in node_spans:
            total_input += int(span.attributes.get("orcheo.token.input", 0))
            total_output += int(span.attributes.get("orcheo.token.output", 0))

    execution_metadata = TraceExecutionMetadata(
        id=record.execution_id,
        status=record.status,
        thread_id=runtime_thread_id,
        started_at=record.trace_started_at or record.started_at,
        finished_at=record.trace_completed_at or record.completed_at,
        trace_id=record.trace_id or root_span_id,
        token_usage=TraceTokenUsage(input=total_input, output=total_output),
    )

    return TraceResponse(
        execution=execution_metadata,
        spans=spans,
        page_info=TracePageInfo(has_next_page=False, cursor=None),
    )


def build_trace_update(
    record: RunHistoryRecord,
    *,
    step: RunHistoryStep | None = None,
    include_root: bool = False,
    complete: bool = False,
) -> TraceUpdateMessage | None:
    """Assemble a websocket trace update message."""
    root_span_id = _derive_root_span_id(record.trace_id, record.execution_id)
    runtime_thread_id = _extract_runtime_thread_id(record)
    state_snapshots = _build_workflow_state_snapshots(record)
    spans: list[TraceSpanResponse] = []
    if include_root:
        spans.append(_build_root_span(record, root_span_id, runtime_thread_id))
    if step is not None:
        spans.extend(
            _build_spans_for_step(
                record,
                step,
                root_span_id,
                state_snapshots=state_snapshots,
            )
        )

    if not spans and not complete:
        return None

    trace_identifier = record.trace_id or root_span_id
    return TraceUpdateMessage(
        execution_id=record.execution_id,
        trace_id=trace_identifier,
        spans=spans,
        complete=complete,
    )


def _derive_root_span_id(trace_id: str | None, execution_id: str) -> str:
    if trace_id:
        sanitized = trace_id.replace("-", "")
        if len(sanitized) >= 16:
            return sanitized[:16]
    digest = blake2b(f"{execution_id}:root".encode(), digest_size=8)
    return digest.hexdigest()


def _derive_child_span_id(execution_id: str, step_index: int, node_key: str) -> str:
    digest = blake2b(f"{execution_id}:{step_index}:{node_key}".encode(), digest_size=8)
    return digest.hexdigest()


def _build_root_span(
    record: RunHistoryRecord,
    span_id: str,
    runtime_thread_id: str | None,
) -> TraceSpanResponse:
    status = _status_from_history(record)
    attributes: dict[str, Any] = {
        "orcheo.execution.id": record.execution_id,
        "orcheo.workflow.id": record.workflow_id,
    }
    if record.tenant_id:
        attributes["orcheo.tenant.id"] = record.tenant_id
    if runtime_thread_id:
        attributes["orcheo.execution.thread_id"] = runtime_thread_id
    if record.tags:
        attributes["orcheo.execution.tags"] = list(record.tags)
        attributes["orcheo.execution.tag_count"] = len(record.tags)
    if record.run_name:
        attributes["orcheo.execution.run_name"] = record.run_name
    if record.metadata:
        attributes["orcheo.execution.metadata_keys"] = sorted(record.metadata.keys())
    if record.callbacks:
        attributes["orcheo.execution.callbacks.count"] = len(record.callbacks)
    recursion_limit = record.runnable_config.get("recursion_limit")
    if recursion_limit is not None:
        attributes["orcheo.execution.recursion_limit"] = recursion_limit
    max_concurrency = record.runnable_config.get("max_concurrency")
    if max_concurrency is not None:
        attributes["orcheo.execution.max_concurrency"] = max_concurrency
    prompts = record.runnable_config.get("prompts")
    if isinstance(prompts, Mapping):
        attributes["orcheo.execution.prompts.count"] = len(prompts)
    return TraceSpanResponse(
        span_id=span_id,
        parent_span_id=None,
        name="workflow.execution",
        start_time=record.trace_started_at or record.started_at,
        end_time=record.trace_completed_at or record.completed_at,
        attributes=attributes,
        status=status,
    )


def _extract_runtime_thread_id(record: RunHistoryRecord) -> str | None:
    configurable = record.runnable_config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    runtime_thread_id = configurable.get("thread_id")
    if not isinstance(runtime_thread_id, str):
        return None
    normalized = runtime_thread_id.strip()
    return normalized or None


def _build_spans_for_step(
    record: RunHistoryRecord,
    step: RunHistoryStep,
    root_span_id: str,
    *,
    state_snapshots: Mapping[tuple[int, str], _WorkflowStateSnapshot] | None = None,
) -> list[TraceSpanResponse]:
    spans: list[TraceSpanResponse] = []
    for node_key, payload in step.payload.items():
        if not isinstance(payload, Mapping):
            continue
        state_snapshot = None
        if state_snapshots is not None:
            state_snapshot = state_snapshots.get((step.index, node_key))
        span = _build_node_span(
            record,
            step,
            node_key,
            payload,
            root_span_id,
            state_snapshot=state_snapshot,
        )
        if span is not None:
            spans.append(span)
    return spans


def _build_node_span(
    record: RunHistoryRecord,
    step: RunHistoryStep,
    node_key: str,
    payload: Mapping[str, Any],
    parent_id: str,
    *,
    state_snapshot: _WorkflowStateSnapshot | None = None,
) -> TraceSpanResponse | None:
    attributes = _node_attributes(node_key, payload, tenant_id=record.tenant_id)
    span_id = _derive_child_span_id(record.execution_id, step.index, node_key)
    name = attributes.get("orcheo.node.display_name", node_key)
    start_time = step.at
    end_time = _compute_end_time(start_time, payload)
    token_input, token_output = _extract_token_usage(payload)
    if token_input is not None:
        attributes["orcheo.token.input"] = token_input
    if token_output is not None:
        attributes["orcheo.token.output"] = token_output
    artifact_ids = _extract_artifact_ids(payload)
    if artifact_ids:
        attributes["orcheo.artifact.ids"] = artifact_ids
    if state_snapshot is not None:  # pragma: no branch
        attributes["orcheo.workflow.state.before"] = state_snapshot["before"]
        attributes["orcheo.workflow.state.after"] = state_snapshot["after"]
        if state_snapshot["redacted"]:
            attributes["orcheo.workflow.state.redacted"] = True
        if state_snapshot["truncated"]:
            attributes["orcheo.workflow.state.truncated"] = True
    events = list(_collect_message_events(payload, start_time))
    status = _status_from_payload(payload)
    return TraceSpanResponse(
        span_id=span_id,
        parent_span_id=parent_id,
        name=str(name),
        start_time=start_time,
        end_time=end_time,
        attributes=attributes,
        events=events,
        status=status,
    )


def _build_workflow_state_snapshots(
    record: RunHistoryRecord,
) -> dict[tuple[int, str], _WorkflowStateSnapshot]:
    state = _initial_workflow_state(record.inputs)
    snapshots: dict[tuple[int, str], _WorkflowStateSnapshot] = {}

    for step in record.steps:
        for node_key, payload in step.payload.items():
            if not isinstance(payload, Mapping):
                continue
            before_state = _clone_json_like(state)
            state = _merge_workflow_state(state, payload)
            after_state = _clone_json_like(state)
            sanitized_before, before_redacted, before_truncated = (
                _sanitize_state_snapshot(before_state)
            )
            sanitized_after, after_redacted, after_truncated = _sanitize_state_snapshot(
                after_state
            )
            snapshots[(step.index, node_key)] = {
                "before": sanitized_before,
                "after": sanitized_after,
                "redacted": before_redacted or after_redacted,
                "truncated": before_truncated or after_truncated,
            }

    return snapshots


def _initial_workflow_state(inputs: Mapping[str, Any]) -> dict[str, Any]:
    state = _clone_json_like(inputs)
    state.setdefault("inputs", _clone_json_like(inputs))
    state.setdefault("results", {})
    state.setdefault("messages", [])
    return state


def _merge_workflow_state(
    current_state: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    merged = _clone_json_like(current_state)
    for key, value in payload.items():
        key_str = str(key)
        if key_str == TRACE_METADATA_KEY:
            continue
        existing = merged.get(key_str)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key_str] = _merge_workflow_state(existing, value)
            continue
        merged[key_str] = _clone_json_like(value)
    return merged


def _clone_json_like(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _clone_json_like(val) for key, val in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_clone_json_like(item) for item in value]
    if isinstance(value, bytes | bytearray):
        return value.decode("utf-8", errors="replace")
    return value


def _sanitize_state_snapshot(
    value: Any,
    *,
    depth: int = 0,
) -> tuple[dict[str, Any], bool, bool]:
    sanitized, redacted, truncated = _sanitize_value(value, depth=depth)
    if isinstance(sanitized, Mapping):
        return dict(sanitized), redacted, truncated
    return {"value": sanitized}, redacted, truncated


def _sanitize_value(
    value: Any,
    *,
    depth: int,
) -> tuple[Any, bool, bool]:
    if depth >= _STATE_MAX_DEPTH:
        return _STATE_TRUNCATED_MARKER, False, True

    if isinstance(value, Mapping):
        return _sanitize_mapping_value(value, depth=depth)

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return _sanitize_sequence_value(value, depth=depth)

    if isinstance(value, bytes | bytearray):
        value = value.decode("utf-8", errors="replace")

    if isinstance(value, str) and len(value) > _STATE_MAX_STRING_LENGTH:
        return value[:_STATE_MAX_STRING_LENGTH] + "…", False, True

    return value, False, False


def _sanitize_mapping_value(
    value: Mapping[Any, Any],
    *,
    depth: int,
) -> tuple[dict[str, Any], bool, bool]:
    redacted = False
    truncated = False
    sanitized: dict[str, Any] = {}
    items = list(value.items())
    for index, (raw_key, raw_value) in enumerate(items):
        if index >= _STATE_MAX_COLLECTION_ITEMS:
            sanitized["__truncated_items__"] = len(items) - index
            truncated = True
            break

        key = str(raw_key)
        if _is_sensitive_state_key(key):
            sanitized[key] = _STATE_REDACTED_MARKER
            redacted = True
            continue

        child_value, child_redacted, child_truncated = _sanitize_value(
            raw_value,
            depth=depth + 1,
        )
        sanitized[key] = child_value
        redacted = redacted or child_redacted
        truncated = truncated or child_truncated

    return sanitized, redacted, truncated


def _is_sensitive_state_key(key: str) -> bool:
    """Return whether a state snapshot key should be redacted."""
    return (
        key.strip().lower() not in _NON_SENSITIVE_STATE_KEYS
        and _SENSITIVE_STATE_KEY_PATTERN.search(key) is not None
    )


def _sanitize_sequence_value(
    value: Sequence[Any],
    *,
    depth: int,
) -> tuple[list[Any], bool, bool]:
    redacted = False
    truncated = False
    sanitized_list: list[Any] = []
    for index, item in enumerate(value):
        if index >= _STATE_MAX_COLLECTION_ITEMS:
            sanitized_list.append(_STATE_TRUNCATED_MARKER)
            truncated = True
            break
        child_value, child_redacted, child_truncated = _sanitize_value(
            item,
            depth=depth + 1,
        )
        sanitized_list.append(child_value)
        redacted = redacted or child_redacted
        truncated = truncated or child_truncated
    return sanitized_list, redacted, truncated


def _compute_end_time(
    start_time: datetime, payload: Mapping[str, Any]
) -> datetime | None:
    latency = _extract_latency(payload)
    if latency is None:
        return None
    return start_time + timedelta(milliseconds=latency)


def _node_attributes(
    node_key: str,
    payload: Mapping[str, Any],
    *,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    display_name = payload.get("display_name") or payload.get("name") or node_key
    attributes: dict[str, Any] = {
        "orcheo.node.id": str(payload.get("id", node_key)),
        "orcheo.node.display_name": str(display_name),
    }
    if tenant_id is None:
        payload_tenant_id = payload.get("tenant_id")
        if isinstance(payload_tenant_id, str) and payload_tenant_id.strip():
            tenant_id = payload_tenant_id.strip()
    if tenant_id is not None:
        attributes["orcheo.tenant.id"] = tenant_id
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


def _extract_artifact_ids(payload: Mapping[str, Any]) -> list[str]:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, Sequence):
        return []
    identifiers: list[str] = []
    for artifact in artifacts:
        if isinstance(artifact, Mapping) and artifact.get("id") is not None:
            identifiers.append(str(artifact.get("id")))
        else:
            identifiers.append(str(artifact))
    return identifiers


def _collect_message_events(
    payload: Mapping[str, Any],
    default_time: datetime,
) -> Sequence[TraceSpanEvent]:
    events: list[TraceSpanEvent] = []
    for key in ("prompts", "prompt"):
        if key in payload:
            events.extend(_build_text_events("prompt", payload[key], default_time))
    for key in ("responses", "response"):
        if key in payload:
            events.extend(_build_text_events("response", payload[key], default_time))
    messages = payload.get("messages")
    if isinstance(messages, Sequence) and not isinstance(messages, str | bytes):
        for message in messages:
            if isinstance(message, Mapping):
                role = str(message.get("role", "message"))
                preview = _preview_text(message.get("content"))
            else:
                role = "message"
                preview = _preview_text(message)
            events.append(
                TraceSpanEvent(
                    name="message",
                    time=default_time,
                    attributes={"role": role, "preview": preview},
                )
            )
    return events


def _build_text_events(
    name: str,
    value: Any,
    default_time: datetime,
) -> list[TraceSpanEvent]:
    if isinstance(value, Mapping):
        return [
            TraceSpanEvent(
                name=name,
                time=default_time,
                attributes={key: _preview_text(val) for key, val in value.items()},
            )
        ]
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [
            TraceSpanEvent(
                name=name,
                time=default_time,
                attributes={"preview": _preview_text(item)},
            )
            for item in value
        ]
    return [
        TraceSpanEvent(
            name=name,
            time=default_time,
            attributes={"preview": _preview_text(value)},
        )
    ]


def _status_from_history(record: RunHistoryRecord) -> TraceSpanStatus:
    status = record.status.lower()
    if status in {"completed", "success", "succeeded"}:
        return TraceSpanStatus(code="OK")
    if status in {"error", "failed"}:
        return TraceSpanStatus(code="ERROR", message=record.error)
    if status in {"cancelled", "canceled"}:
        return TraceSpanStatus(code="ERROR", message=record.error or "cancelled")
    return TraceSpanStatus(code="UNSET")


def _status_from_payload(payload: Mapping[str, Any]) -> TraceSpanStatus:
    status = _coalesce_status(payload)
    if not status:
        return TraceSpanStatus(code="OK")
    lowered = status.lower()
    if lowered in {"completed", "success", "succeeded"}:
        return TraceSpanStatus(code="OK")
    if lowered in {"error", "failed"}:
        message = _extract_error_message(payload)
        return TraceSpanStatus(code="ERROR", message=message)
    if lowered in {"cancelled", "canceled"}:
        reason = payload.get("reason") or payload.get("error") or "cancelled"
        return TraceSpanStatus(code="ERROR", message=str(reason))
    return TraceSpanStatus(code="UNSET")


def _extract_error_message(payload: Mapping[str, Any]) -> str | None:
    error_value = payload.get("error")
    if isinstance(error_value, Mapping):
        message = error_value.get("message")
        if message is not None:
            return str(message)
    if error_value is not None:
        return str(error_value)
    return None


def _preview_text(value: Any) -> str:
    return tracing_workflow._preview_text(value)  # type: ignore[attr-defined]  # noqa: SLF001


def _coalesce_status(payload: Mapping[str, Any]) -> str | None:
    return tracing_workflow._coalesce_status(payload)  # type: ignore[attr-defined]  # noqa: SLF001


def _extract_token_usage(payload: Mapping[str, Any]) -> tuple[int | None, int | None]:
    return tracing_workflow._extract_token_usage(payload)  # type: ignore[attr-defined]  # noqa: SLF001


def _extract_latency(payload: Mapping[str, Any]) -> int | None:
    return tracing_workflow._extract_latency(payload)  # type: ignore[attr-defined]  # noqa: SLF001


__all__ = [
    "build_trace_response",
    "build_trace_update",
]
