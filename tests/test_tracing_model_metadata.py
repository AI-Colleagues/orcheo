"""Unit tests for orcheo.tracing.model_metadata helpers."""

from __future__ import annotations
from types import SimpleNamespace
from orcheo.tracing.model_metadata import (
    NAMESPACE_METADATA_KEY,
    TRACE_METADATA_KEY,
    _extract_model_name_from_mapping,
    encode_step_namespace,
    extract_ai_trace_attributes,
    extract_step_namespace,
    infer_chat_result_model_name,
    infer_model_name_from_instance,
    split_model_identifier,
)


# ---------------------------------------------------------------------------
# split_model_identifier
# ---------------------------------------------------------------------------


def test_split_model_identifier_returns_none_for_non_string() -> None:
    """Non-string input returns (None, None) (line 14)."""
    assert split_model_identifier(None) == (None, None)
    assert split_model_identifier(42) == (None, None)  # type: ignore[arg-type]


def test_split_model_identifier_returns_none_for_empty_string() -> None:
    """Blank string returns (None, None) (line 17)."""
    assert split_model_identifier("") == (None, None)
    assert split_model_identifier("   ") == (None, None)


def test_split_model_identifier_no_colon_returns_model_only() -> None:
    assert split_model_identifier("gpt-4") == (None, "gpt-4")


def test_split_model_identifier_with_provider() -> None:
    assert split_model_identifier("openai:gpt-4") == ("openai", "gpt-4")


def test_split_model_identifier_strips_whitespace_components() -> None:
    assert split_model_identifier(" : model ") == (None, "model")


# ---------------------------------------------------------------------------
# extract_ai_trace_attributes
# ---------------------------------------------------------------------------


def test_extract_ai_trace_attributes_returns_empty_for_missing_trace() -> None:
    assert extract_ai_trace_attributes({}) == {}
    assert extract_ai_trace_attributes({"other": "data"}) == {}


def test_extract_ai_trace_attributes_returns_empty_for_non_mapping_ai() -> None:
    """ai_payload not a Mapping returns {} (line 60)."""
    payload = {TRACE_METADATA_KEY: {"ai": "not-a-mapping"}}
    assert extract_ai_trace_attributes(payload) == {}


def test_extract_ai_trace_attributes_covers_null_branches() -> None:
    """ai_payload with no kind/requested_model/actual_model/provider (lines 64->66, 67->69, 70->72, 73->75)."""  # noqa: E501
    payload = {TRACE_METADATA_KEY: {"ai": {}}}
    result = extract_ai_trace_attributes(payload)
    assert result == {}


def test_extract_ai_trace_attributes_includes_operation() -> None:
    """operation field is included in attributes (line 77)."""
    payload = {
        TRACE_METADATA_KEY: {
            "ai": {
                "kind": "embedding",
                "requested_model": "openai:text-embedding-3",
                "operation": "embed",
            }
        }
    }
    result = extract_ai_trace_attributes(payload)
    assert result["orcheo.ai.embedding.operation"] == "embed"


def test_extract_ai_trace_attributes_includes_vector_size() -> None:
    """vector_size field is cast to int and included (lines 80-81)."""
    payload = {
        TRACE_METADATA_KEY: {
            "ai": {
                "kind": "embedding",
                "requested_model": "openai:text-embedding-3",
                "vector_size": 1536,
            }
        }
    }
    result = extract_ai_trace_attributes(payload)
    assert result["orcheo.ai.embedding.vector_size"] == 1536


def test_extract_ai_trace_attributes_full_payload() -> None:
    """All fields present: kind, requested_model, actual_model, provider (true branches)."""  # noqa: E501
    payload = {
        TRACE_METADATA_KEY: {
            "ai": {
                "kind": "llm",
                "requested_model": "openai:gpt-4",
                "actual_model": "gpt-4-0613",
                "provider": "openai",
            }
        }
    }
    result = extract_ai_trace_attributes(payload)
    assert result["orcheo.ai.kind"] == "llm"
    assert result["orcheo.ai.model.requested"] == "openai:gpt-4"
    assert result["orcheo.ai.model.actual"] == "gpt-4-0613"
    assert result["orcheo.ai.provider"] == "openai"


# ---------------------------------------------------------------------------
# infer_chat_result_model_name
# ---------------------------------------------------------------------------


def test_infer_chat_result_model_name_returns_from_direct_response_metadata() -> None:
    """response_metadata with a model key returns immediately (line 94)."""
    payload = {"response_metadata": {"model_name": "gpt-4"}}
    result = infer_chat_result_model_name(payload)
    assert result == "gpt-4"


def test_infer_chat_result_model_name_falls_through_empty_response_metadata() -> None:
    """response_metadata present but without a model name falls through to messages (lines 92-93)."""  # noqa: E501
    payload = {
        "response_metadata": {"other_key": "value"},
        "messages": [{"response_metadata": {"model": "gpt-4"}}],
    }
    result = infer_chat_result_model_name(payload)
    assert result == "gpt-4"


def test_infer_chat_result_model_name_returns_none_for_non_sequence_messages() -> None:
    payload: dict[str, object] = {"messages": "not-a-sequence"}
    assert infer_chat_result_model_name(payload) is None


def test_infer_chat_result_model_name_skips_messages_without_metadata() -> None:
    """Messages without response_metadata are skipped."""
    payload = {"messages": [{"content": "hello"}]}
    assert infer_chat_result_model_name(payload) is None


def test_infer_chat_result_model_name_reads_from_object_message() -> None:
    """Non-Mapping messages use getattr for response_metadata."""
    msg = SimpleNamespace(response_metadata={"model_name": "claude-3"})
    payload = {"messages": [msg]}
    result = infer_chat_result_model_name(payload)
    assert result == "claude-3"


# ---------------------------------------------------------------------------
# infer_model_name_from_instance
# ---------------------------------------------------------------------------


def test_infer_model_name_from_instance_skips_empty_string_attributes() -> None:
    """Attribute value that is an empty string is skipped (line 127->116)."""
    instance = SimpleNamespace(model_name="", model="gpt-4")
    result = infer_model_name_from_instance(instance)
    assert result == "gpt-4"


def test_infer_model_name_from_instance_returns_none_when_all_missing() -> None:
    result = infer_model_name_from_instance(SimpleNamespace())
    assert result is None


# ---------------------------------------------------------------------------
# _extract_model_name_from_mapping (internal helper)
# ---------------------------------------------------------------------------


def test_extract_model_name_from_mapping_skips_empty_string_values() -> None:
    """Candidate that strips to empty is skipped in favour of next key (line 152->147)."""  # noqa: E501
    payload = {"model_name": "  ", "model": "gpt-4"}
    result = _extract_model_name_from_mapping(payload)
    assert result == "gpt-4"


def test_extract_model_name_from_mapping_returns_none_for_no_match() -> None:
    assert _extract_model_name_from_mapping({"other": "x"}) is None


# ---------------------------------------------------------------------------
# encode_step_namespace / extract_step_namespace
# ---------------------------------------------------------------------------


def test_encode_step_namespace_tags_step_with_namespace() -> None:
    step = {"quote_selector": {"status": "success"}}

    encoded = encode_step_namespace(step, ("generate_report:task-1",))

    assert encoded[NAMESPACE_METADATA_KEY] == ["generate_report:task-1"]
    assert encoded["quote_selector"] == {"status": "success"}
    # original mapping is untouched
    assert NAMESPACE_METADATA_KEY not in step


def test_encode_step_namespace_omits_key_for_empty_namespace() -> None:
    step = {"ingest": {"status": "success"}}

    encoded = encode_step_namespace(step, ())

    assert NAMESPACE_METADATA_KEY not in encoded


def test_extract_step_namespace_round_trips_encoded_step() -> None:
    step = encode_step_namespace(
        {"inner": {}}, ("generate_report:task-1", "child:task-2")
    )

    assert extract_step_namespace(step) == ("generate_report:task-1", "child:task-2")


def test_extract_step_namespace_returns_empty_tuple_when_absent() -> None:
    assert extract_step_namespace({"ingest": {}}) == ()


def test_extract_step_namespace_ignores_non_sequence_value() -> None:
    assert extract_step_namespace({NAMESPACE_METADATA_KEY: "not-a-list"}) == ()
