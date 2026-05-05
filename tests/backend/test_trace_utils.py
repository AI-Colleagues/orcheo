"""Unit tests for trace utilities."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from hashlib import blake2b
from typing import Any
import pytest
from orcheo_backend.app import trace_utils
from orcheo_backend.app.history.models import RunHistoryRecord
from orcheo_backend.app.schemas.traces import TraceSpanResponse
from orcheo_backend.app.trace_utils import build_trace_response, build_trace_update


def _timestamp(offset_seconds: int = 0) -> datetime:
    return datetime(2024, 1, 1, 12, 0, offset_seconds, tzinfo=UTC)


def test_build_trace_response_emits_span_metadata() -> None:
    """build_trace_response should return root and child span details."""

    record = RunHistoryRecord(
        workflow_id="wf-1",
        execution_id="exec-1",
        status="completed",
        tenant_id="tenant-1",
        runnable_config={"configurable": {"thread_id": "thread-123"}},
    )
    record.trace_started_at = _timestamp()
    record.trace_completed_at = _timestamp(5)
    record.append_step(
        {
            "ai_model": {
                "id": "node-1",
                "display_name": "Draft",
                "kind": "ai_model",
                "status": "completed",
                "latency_ms": 42,
                "token_usage": {"input": 5, "output": 7},
                "artifacts": [{"id": "artifact-1"}],
                "prompts": {"text": "Hello"},
                "responses": ["World"],
                "messages": [
                    {"role": "assistant", "content": "Hi there"},
                    "plain message",
                ],
                "__trace": {
                    "ai": {
                        "kind": "llm",
                        "requested_model": "openai:gpt-4o-mini",
                        "actual_model": "gpt-4o-mini-2024-07-18",
                        "provider": "openai",
                    }
                },
            },
            "status": "ignored",
        },
        at=_timestamp(1),
    )

    response = build_trace_response(record)
    assert response.execution.id == "exec-1"
    assert response.execution.thread_id == "thread-123"
    assert response.execution.token_usage.input == 5
    assert response.execution.token_usage.output == 7

    assert len(response.spans) == 2
    root_span, node_span = response.spans
    assert root_span.parent_span_id is None
    assert root_span.attributes["orcheo.execution.id"] == "exec-1"
    assert root_span.attributes["orcheo.execution.thread_id"] == "thread-123"
    assert root_span.attributes["orcheo.tenant.id"] == "tenant-1"
    assert len(node_span.events) == 4  # prompt, response, two message events
    assert node_span.attributes["orcheo.node.kind"] == "ai_model"
    assert node_span.attributes["orcheo.ai.kind"] == "llm"
    assert node_span.attributes["orcheo.ai.model.requested"] == "openai:gpt-4o-mini"
    assert node_span.attributes["orcheo.ai.model.actual"] == "gpt-4o-mini-2024-07-18"
    assert node_span.attributes["orcheo.token.output"] == 7
    assert node_span.attributes["orcheo.artifact.ids"] == ["artifact-1"]
    assert node_span.attributes["orcheo.tenant.id"] == "tenant-1"
    assert "orcheo.workflow.state.before" in node_span.attributes
    assert "orcheo.workflow.state.after" in node_span.attributes
    assert node_span.attributes["orcheo.workflow.state.before"]["inputs"] == {}
    assert node_span.attributes["orcheo.workflow.state.after"]["id"] == "node-1"
    assert "__trace" not in node_span.attributes["orcheo.workflow.state.after"]
    assert node_span.status.code == "OK"


def test_build_trace_response_uses_root_span_id_when_trace_id_missing() -> None:
    """Trace response metadata should expose a fallback identifier."""

    record = RunHistoryRecord(
        workflow_id="wf-no-trace-id",
        execution_id="exec-no-trace-id",
        status="completed",
    )

    response = build_trace_response(record)
    expected_trace_id = blake2b(
        f"{record.execution_id}:root".encode(), digest_size=8
    ).hexdigest()

    assert response.execution.trace_id == expected_trace_id
    assert response.spans[0].span_id == expected_trace_id


def test_build_trace_update_returns_none_for_non_node_payload() -> None:
    """build_trace_update should return None when no spans are generated."""

    record = RunHistoryRecord(workflow_id="wf", execution_id="exec", status="running")
    step = record.append_step({"status": "running"}, at=_timestamp())

    update = build_trace_update(record, step=step)
    assert update is None


def test_build_trace_update_includes_root_and_error_status() -> None:
    """build_trace_update should include root span and child error metadata."""

    record = RunHistoryRecord(
        workflow_id="wf-err",
        execution_id="exec-err",
        status="error",
        trace_id="12345678-90ab-cdef-1234-567890abcdef",
    )
    record.trace_started_at = _timestamp()
    step = record.append_step(
        {
            "node": {
                "status": "error",
                "error": {"message": "boom"},
            }
        },
        at=_timestamp(2),
    )
    record.error = "boom"

    update = build_trace_update(record, step=step, include_root=True, complete=True)
    assert update is not None
    assert update.complete is True
    assert update.trace_id == record.trace_id
    assert len(update.spans) == 2
    root_span, child_span = update.spans
    assert root_span.span_id == record.trace_id.replace("-", "")[:16]
    assert child_span.status.code == "ERROR"
    assert child_span.status.message == "boom"


def test_build_trace_update_complete_without_spans_emits_message() -> None:
    """Completion updates should emit even when no span changes occurred."""

    record = RunHistoryRecord(
        workflow_id="wf-complete",
        execution_id="exec-complete",
        status="completed",
    )
    record.trace_started_at = _timestamp()
    record.trace_completed_at = _timestamp(10)

    update = build_trace_update(record, include_root=False, complete=True)

    assert update is not None
    assert update.complete is True
    assert update.spans == []

    expected_trace_id = blake2b(
        f"{record.execution_id}:root".encode(), digest_size=8
    ).hexdigest()
    assert update.trace_id == expected_trace_id


def test_trace_update_root_span_uses_digest_when_missing_trace_id() -> None:
    """Trace updates should derive a deterministic root ID when trace_id is absent."""

    record = RunHistoryRecord(
        workflow_id="wf-fallback",
        execution_id="exec-fallback",
        status="running",
    )
    record.trace_started_at = _timestamp()

    update = build_trace_update(record, include_root=True)

    assert update is not None
    assert len(update.spans) == 1

    root_span = update.spans[0]
    expected_root_id = blake2b(
        f"{record.execution_id}:root".encode(), digest_size=8
    ).hexdigest()
    assert root_span.span_id == expected_root_id
    assert update.trace_id == expected_root_id


def test_trace_update_short_trace_id_still_generates_digest_span() -> None:
    """Trace updates should fall back to digest spans when trace_id is too short."""

    record = RunHistoryRecord(
        workflow_id="wf-short",
        execution_id="exec-short",
        status="running",
        trace_id="abcd-1234",
    )
    record.trace_started_at = _timestamp()

    update = build_trace_update(record, include_root=True)

    assert update is not None
    assert len(update.spans) == 1
    expected_root_id = blake2b(
        f"{record.execution_id}:root".encode(), digest_size=8
    ).hexdigest()
    assert update.spans[0].span_id == expected_root_id
    assert update.trace_id == record.trace_id


def test_node_attributes_include_status_and_latency() -> None:
    """_node_attributes should surface normalized status and latency metadata."""

    payload = {"id": "node", "status": "SUCCESS", "latency_ms": 37}

    attributes = trace_utils._node_attributes("node", payload)

    assert attributes["orcheo.node.status"] == "success"
    assert attributes["orcheo.node.latency_ms"] == 37


def test_extract_artifact_ids_handles_non_mapping_entries() -> None:
    """_extract_artifact_ids should coerce non-mapping artifacts to strings."""

    payload = {"artifacts": ["artifact-a", {"id": "artifact-b"}]}

    artifact_ids = trace_utils._extract_artifact_ids(payload)

    assert artifact_ids == ["artifact-a", "artifact-b"]


def test_build_text_events_for_sequence_values() -> None:
    """_build_text_events should emit an event per list entry."""

    default_time = _timestamp()

    events = trace_utils._build_text_events(
        "response",
        ["first", "second"],
        default_time,
    )

    assert [event.attributes["preview"] for event in events] == ["first", "second"]


def test_status_from_payload_handles_missing_status() -> None:
    """_status_from_payload should return OK when no status metadata is present."""

    status = trace_utils._status_from_payload({})

    assert status.code == "OK"
    assert status.message is None


def test_status_from_payload_handles_cancellation_reason() -> None:
    """_status_from_payload should surface cancellation reasons in status message."""

    status = trace_utils._status_from_payload(
        {"status": "cancelled", "reason": "user request"}
    )

    assert status.code == "ERROR"
    assert status.message == "user request"


def test_extract_error_message_handles_primitives_and_missing() -> None:
    """Should stringify primitives and return None when absent."""

    assert trace_utils._extract_error_message({"error": 404}) == "404"
    assert trace_utils._extract_error_message({}) is None


def test_build_spans_for_step_skips_none_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_build_spans_for_step should ignore nodes that returned no span."""

    record = RunHistoryRecord(workflow_id="wf", execution_id="exec", status="running")
    step = record.append_step({"node-a": {}, "node-b": {}}, at=_timestamp())

    def fake_build_node_span(
        record_arg: RunHistoryRecord,
        step_arg: Any,
        node_key: str,
        payload: dict[str, Any],
        parent_id: str,
        *,
        state_snapshot: Any = None,
    ) -> TraceSpanResponse | None:
        if node_key == "node-a":
            return None
        return TraceSpanResponse(
            span_id="child",
            parent_span_id=parent_id,
            name=node_key,
        )

    monkeypatch.setattr(trace_utils, "_build_node_span", fake_build_node_span)

    spans = trace_utils._build_spans_for_step(record, step, "root")

    assert [span.name for span in spans] == ["node-b"]


def test_node_attributes_omits_optional_metadata() -> None:
    """_node_attributes should skip status/latency keys when unavailable."""

    attributes = trace_utils._node_attributes("node", {})

    assert "orcheo.node.status" not in attributes
    assert "orcheo.node.latency_ms" not in attributes


def test_build_text_events_handles_scalars() -> None:
    """_build_text_events should cover scalar fallbacks for preview strings."""

    default_time = _timestamp()

    events = trace_utils._build_text_events("prompt", "answer", default_time)

    assert len(events) == 1
    assert events[0].attributes["preview"] == "answer"


def test_status_from_payload_returns_unset_for_unknown_states() -> None:
    """_status_from_payload should default to UNSET for non terminal states."""

    status = trace_utils._status_from_payload({"status": "pending"})

    assert status.code == "UNSET"


def test_extract_error_message_prefers_mapping_message() -> None:
    """_extract_error_message should prefer nested mapping messages."""

    message = trace_utils._extract_error_message(
        {"error": {"message": "detailed boom", "code": 500}}
    )

    assert message == "detailed boom"


def test_extract_error_message_falls_back_to_mapping_repr() -> None:
    """_extract_error_message should stringify mapping payloads without message."""

    message = trace_utils._extract_error_message({"error": {"code": "MISSING"}})

    assert message == "{'code': 'MISSING'}"


def test_build_trace_response_includes_execution_attributes() -> None:
    """The root span should surface tags, metadata, and runtime hints."""

    start_time = datetime(2024, 1, 1, tzinfo=UTC)
    end_time = start_time + timedelta(seconds=5)
    record = RunHistoryRecord(
        workflow_id="workflow-1",
        execution_id="exec-1",
        started_at=start_time,
        completed_at=end_time,
        trace_started_at=start_time,
        trace_completed_at=end_time,
        status="success",
        tags=["alpha"],
        callbacks=["cb"],
        metadata={"key": "value"},
        run_name="run-name",
        runnable_config={
            "recursion_limit": 7,
            "max_concurrency": 2,
            "prompts": {"primary": {"step": "first"}},
        },
    )

    response = build_trace_response(record)
    root_span = response.spans[0]
    attributes = root_span.attributes

    assert attributes["orcheo.execution.tags"] == ["alpha"]
    assert attributes["orcheo.execution.tag_count"] == 1
    assert attributes["orcheo.execution.run_name"] == "run-name"
    assert attributes["orcheo.execution.metadata_keys"] == ["key"]
    assert attributes["orcheo.execution.callbacks.count"] == 1
    assert attributes["orcheo.execution.recursion_limit"] == 7
    assert attributes["orcheo.execution.max_concurrency"] == 2
    assert attributes["orcheo.execution.prompts.count"] == 1


def test_build_trace_response_redacts_and_truncates_workflow_state() -> None:
    """Workflow snapshots redact sensitive keys and truncate long values."""

    record = RunHistoryRecord(
        workflow_id="wf-privacy",
        execution_id="exec-privacy",
        status="completed",
        inputs={"api_key": "secret-value", "query": "hello"},
    )
    record.append_step(
        {
            "node": {
                "result": "x" * 3000,
                "nested": {
                    "session_token": "token-123",
                },
            }
        },
        at=_timestamp(),
    )

    response = build_trace_response(record)
    node_span = response.spans[1]

    before_state = node_span.attributes["orcheo.workflow.state.before"]
    after_state = node_span.attributes["orcheo.workflow.state.after"]
    assert before_state["api_key"] == "[REDACTED]"
    assert after_state["nested"]["session_token"] == "[REDACTED]"
    assert node_span.attributes["orcheo.workflow.state.redacted"] is True
    assert node_span.attributes["orcheo.workflow.state.truncated"] is True
    assert isinstance(after_state["result"], str)
    assert len(after_state["result"]) <= 2049


def test_extract_runtime_thread_id_requires_string() -> None:
    """Non-string runtime thread IDs should be ignored."""

    record = RunHistoryRecord(
        workflow_id="wf-thread",
        execution_id="exec-thread",
        status="running",
        runnable_config={"configurable": {"thread_id": 123}},
    )

    assert trace_utils._extract_runtime_thread_id(record) is None


def test_merge_workflow_state_recursively_merges_nested_mappings() -> None:
    """Nested mapping payloads should merge recursively into existing state."""

    current_state = {"nested": {"a": 1, "b": 2}}
    payload = {"nested": {"b": 3, "c": 4}}

    merged = trace_utils._merge_workflow_state(current_state, payload)

    assert merged["nested"] == {"a": 1, "b": 3, "c": 4}


def test_sanitize_mapping_value_preserves_token_usage_metrics() -> None:
    """Operational token usage keys should not be redacted as secrets."""

    value = {
        "token_usage": {"input": 5, "output": 7},
        "usage_metadata": {"prompt_tokens": 5, "total_tokens": 12},
        "prompt_tokens_details": {"cached_tokens": 4, "audio_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 3},
        "input_token_details": {"cache_read": 4, "audio": 0},
        "output_token_details": {"reasoning": 3, "text": 9},
        "session_token": "secret-token",
    }

    sanitized, redacted, truncated = trace_utils._sanitize_mapping_value(value, depth=0)

    assert sanitized["token_usage"] == {"input": 5, "output": 7}
    assert sanitized["usage_metadata"] == {"prompt_tokens": 5, "total_tokens": 12}
    assert sanitized["prompt_tokens_details"] == {
        "cached_tokens": 4,
        "audio_tokens": 0,
    }
    assert sanitized["completion_tokens_details"] == {"reasoning_tokens": 3}
    assert sanitized["input_token_details"] == {"cache_read": 4, "audio": 0}
    assert sanitized["output_token_details"] == {"reasoning": 3, "text": 9}
    assert sanitized["session_token"] == "[REDACTED]"
    assert redacted is True
    assert truncated is False


def test_clone_json_like_decodes_bytes_values() -> None:
    """Bytes values should be UTF-8 decoded during clone."""

    cloned = trace_utils._clone_json_like({"payload": b"hello"})

    assert cloned == {"payload": "hello"}


def test_sanitize_state_snapshot_wraps_non_mapping_values() -> None:
    """Non-mapping snapshots should be wrapped under a value key."""

    sanitized, redacted, truncated = trace_utils._sanitize_state_snapshot("plain-state")

    assert sanitized == {"value": "plain-state"}
    assert redacted is False
    assert truncated is False


def test_sanitize_value_truncates_when_depth_limit_reached() -> None:
    """Values at max depth should be replaced by the truncation marker."""

    sanitized, redacted, truncated = trace_utils._sanitize_value(
        {"nested": "value"},
        depth=trace_utils._STATE_MAX_DEPTH,
    )

    assert sanitized == trace_utils._STATE_TRUNCATED_MARKER
    assert redacted is False
    assert truncated is True


def test_sanitize_value_decodes_bytes_payload() -> None:
    """Byte payloads should be decoded instead of returned as bytes."""

    sanitized, redacted, truncated = trace_utils._sanitize_value(b"hello", depth=0)

    assert sanitized == "hello"
    assert redacted is False
    assert truncated is False


def test_sanitize_mapping_value_marks_truncated_items() -> None:
    """Mappings larger than max collection items should include truncation metadata."""

    value = {f"k{i}": i for i in range(trace_utils._STATE_MAX_COLLECTION_ITEMS + 1)}

    sanitized, redacted, truncated = trace_utils._sanitize_mapping_value(value, depth=0)

    assert sanitized["__truncated_items__"] == 1
    assert redacted is False
    assert truncated is True


def test_sanitize_sequence_value_truncates_long_sequences() -> None:
    """Long sequences should append a truncation marker at the collection boundary."""

    value = list(range(trace_utils._STATE_MAX_COLLECTION_ITEMS + 1))

    sanitized, redacted, truncated = trace_utils._sanitize_sequence_value(
        value, depth=0
    )

    assert sanitized[-1] == trace_utils._STATE_TRUNCATED_MARKER
    assert len(sanitized) == trace_utils._STATE_MAX_COLLECTION_ITEMS + 1
    assert redacted is False
    assert truncated is True
