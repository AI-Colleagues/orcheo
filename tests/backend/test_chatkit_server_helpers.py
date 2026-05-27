"""Tests for helper functions in the ChatKit service module."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
import pytest
from chatkit.types import (
    AssistantMessageContent,
    ThreadMetadata,
    UserMessageTextContent,
)
from langchain_core.messages import AIMessage, HumanMessage
from orcheo.graph.ingestion import LANGGRAPH_SCRIPT_FORMAT
from orcheo.models import Workflow, WorkflowChatKitConfig
from orcheo_backend.app.chatkit import message_utils as message_utils_module
from orcheo_backend.app.chatkit.message_utils import (
    build_action_inputs_payload,
    build_initial_state,
    collect_text_from_assistant_content,
    collect_text_from_user_content,
    extract_reply_from_state,
    stringify_langchain_message,
)
from orcheo_backend.app.chatkit.model_selection import (
    apply_chatkit_selected_model,
    resolve_chatkit_selected_model,
)


def teststringify_langchain_message_with_base_message() -> None:
    msg = HumanMessage(content="Hello world")
    result = stringify_langchain_message(msg)
    assert result == "Hello world"


def teststringify_langchain_message_with_mapping() -> None:
    msg = {"content": "Test content"}
    result = stringify_langchain_message(msg)
    assert result == "Test content"

    msg_with_text = {"text": "Test text"}
    result = stringify_langchain_message(msg_with_text)
    assert result == "Test text"


def teststringify_langchain_message_with_list() -> None:
    msg = HumanMessage(content=["Hello", "world"])
    result = stringify_langchain_message(msg)
    assert result == "Hello world"


def teststringify_langchain_message_with_nested_list() -> None:
    msg = {"content": [{"text": "Part 1"}, {"text": "Part 2"}]}
    result = stringify_langchain_message(msg)
    assert "Part 1" in result
    assert "Part 2" in result


def teststringify_langchain_message_with_object() -> None:
    class CustomMessage:
        content = "Custom content"

    msg = CustomMessage()
    result = stringify_langchain_message(msg)
    assert result == "Custom content"


def teststringify_langchain_message_with_plain_string() -> None:
    result = stringify_langchain_message("plain string")
    assert result == "plain string"


def teststringify_langchain_message_with_none_content() -> None:
    class EmptyMessage:
        pass

    msg = EmptyMessage()
    result = stringify_langchain_message(msg)
    assert result is not None


def teststringify_langchain_message_with_empty_list_entries() -> None:
    msg = {"content": ["", {"text": ""}, {"content": "Valid"}, None]}
    result = stringify_langchain_message(msg)
    assert "Valid" in result


def test_build_initial_state_langgraph_format() -> None:
    graph_config = {"format": LANGGRAPH_SCRIPT_FORMAT}
    inputs = {"message": "Hello", "metadata": {"key": "value"}}
    result = build_initial_state(graph_config, inputs)
    assert result["inputs"] == inputs
    assert result["message"] == "Hello"
    assert result["metadata"] == {"key": "value"}
    assert result["messages"] == []
    assert result["results"] == {}
    assert result["config"] == {}


def test_build_initial_state_standard_format() -> None:
    graph_config = {"format": "standard"}
    inputs = {"message": "Hello"}
    result = build_initial_state(graph_config, inputs)

    assert "messages" in result
    assert "results" in result
    assert "inputs" in result
    assert "config" in result
    assert result["inputs"] == inputs


def test_collect_text_from_user_content_multiple_parts() -> None:
    content = [
        UserMessageTextContent(type="input_text", text="Part 1"),
        UserMessageTextContent(type="input_text", text="Part 2"),
    ]
    result = collect_text_from_user_content(content)
    assert result == "Part 1 Part 2"


def test_collect_text_from_assistant_content_multiple_parts() -> None:
    content = [
        AssistantMessageContent(text="Response 1"),
        AssistantMessageContent(text="Response 2"),
    ]
    result = collect_text_from_assistant_content(content)
    assert result == "Response 1 Response 2"


def test_collect_text_from_user_content_with_no_text() -> None:
    class ContentWithoutText:
        pass

    content = [ContentWithoutText()]
    result = collect_text_from_user_content(content)
    assert result == ""


def test_collect_text_from_assistant_content_with_no_text() -> None:
    content = [AssistantMessageContent(text="")]
    result = collect_text_from_assistant_content(content)
    assert result == ""


def testextract_reply_from_state_with_reply_key() -> None:
    state = {"reply": "Direct reply"}
    result = extract_reply_from_state(state)
    assert result == "Direct reply"


def testextract_reply_from_state_prefers_assistant_message() -> None:
    state = {
        "assistant_message": "User-facing reply",
        "reply": "Fallback reply",
    }
    result = extract_reply_from_state(state)
    assert result == "User-facing reply"


def testextract_reply_from_state_with_none_reply() -> None:
    state = {"reply": None, "messages": [{"content": "Message content"}]}
    result = extract_reply_from_state(state)
    assert result is not None


def testextract_reply_from_state_from_results_dict() -> None:
    state = {"results": {"node_a": {"reply": "Reply from results"}}}
    result = extract_reply_from_state(state)
    assert result == "Reply from results"


def testextract_reply_from_state_from_results_assistant_message() -> None:
    state = {
        "results": {
            "node_a": {
                "phase": "awaiting_objective",
                "assistant_message": "Assistant reply from results",
            }
        }
    }
    result = extract_reply_from_state(state)
    assert result == "Assistant reply from results"


def testextract_reply_from_state_from_results_string() -> None:
    state = {"results": {"node_a": "String result"}}
    result = extract_reply_from_state(state)
    assert result == "String result"


def testextract_reply_from_state_from_messages() -> None:
    state = {"messages": [AIMessage(content="AI response")]}
    result = extract_reply_from_state(state)
    assert result == "AI response"


def testextract_reply_from_state_returns_none() -> None:
    state = {"unrelated": "data"}
    result = extract_reply_from_state(state)
    assert result is None


def testextract_reply_from_state_with_results_non_string_value() -> None:
    state = {"results": {"node_a": {"other": "value"}}}
    result = extract_reply_from_state(state)
    assert result is None


def testextract_reply_from_state_with_empty_messages() -> None:
    state = {"messages": []}
    result = extract_reply_from_state(state)
    assert result is None


def testextract_reply_from_state_with_none_reply_in_results() -> None:
    state = {"results": {"node_a": {"reply": None}, "node_b": "fallback"}}
    result = extract_reply_from_state(state)
    assert result == "fallback"


class ModelAction:
    def model_dump(self) -> dict[str, object]:
        return {"type": "model", "payload": {"flag": True}}


class AttributeAction:
    type = "attribute"
    payload = "value"
    handler = "handler"
    loadingBehavior = "loading"  # noqa: N802, N815


class WidgetWithDump:
    def model_dump(self, exclude_none: bool = True) -> dict[str, object]:
        return {"type": "Card", "title": "widget"}


@dataclass
class WidgetItemStub:
    id: str
    widget: object


def test_dump_action_prefers_model_dump() -> None:
    result = message_utils_module._dump_action(ModelAction())
    assert result == {"type": "model", "payload": {"flag": True}}


def test_dump_action_handles_mapping() -> None:
    mapping_action = {"type": "map", "payload": {"value": 1}}
    result = message_utils_module._dump_action(mapping_action)
    assert result == mapping_action


def test_dump_action_handles_attribute_based_action() -> None:
    result = message_utils_module._dump_action(AttributeAction())
    assert result == {
        "type": "attribute",
        "payload": "value",
        "handler": "handler",
        "loadingBehavior": "loading",
    }


def test_stringify_action_handles_string_payload() -> None:
    result = message_utils_module._stringify_action(AttributeAction())
    assert result == "[action:attribute] value"


def test_stringify_action_handles_none_payload() -> None:
    class ZeroPayload:
        type = "none"

    assert message_utils_module._stringify_action(ZeroPayload()) == "[action:none]"


def test_stringify_action_handles_json_dump_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ComplexPayload:
        type = "complex"
        payload = {"value": 1}

    def raise_type_error(*args: object, **kwargs: object) -> str:
        raise TypeError("boom")

    monkeypatch.setattr(
        message_utils_module.json,
        "dumps",
        raise_type_error,
    )
    result = message_utils_module._stringify_action(ComplexPayload())
    assert "[action:complex]" in result
    assert "1" in result


def test_stringify_action_serializes_json_payload() -> None:
    class DataPayload:
        type = "data"
        payload = {"value": 1}

    result = message_utils_module._stringify_action(DataPayload())
    assert result.startswith("[action:data]")
    assert '"value": 1' in result


def test_dump_widget_handles_model_dump() -> None:
    widget = WidgetWithDump()
    result = message_utils_module._dump_widget(widget)
    assert result == {"type": "Card", "title": "widget"}


def test_dump_widget_handles_mapping() -> None:
    widget = {"type": "Card", "title": "map"}
    assert message_utils_module._dump_widget(widget) == widget


def test_dump_widget_handles_generic_object() -> None:
    widget = SimpleNamespace(label="test")
    assert message_utils_module._dump_widget(widget) == {"widget": widget}


def test_build_action_inputs_payload_includes_widget_and_metadata() -> None:
    thread = ThreadMetadata(
        id="thread",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "wf", "chatkit_model": "openai:gpt-5"},
    )
    history = [{"role": "assistant", "content": "hello"}]
    widget_item = WidgetItemStub(id="widget-id", widget=WidgetWithDump())
    result = build_action_inputs_payload(
        thread, AttributeAction(), history, widget_item
    )
    assert result["thread_id"] == "thread"
    assert result["session_id"] == "thread"
    assert result["history"] == history
    assert result["metadata"] == {
        "workflow_id": "wf",
        "chatkit_model": "openai:gpt-5",
    }
    assert result["model"] == "openai:gpt-5"
    assert result["action"]["type"] == "attribute"
    assert result["widget_item_id"] == "widget-id"
    assert result["widget"] == {"type": "Card", "title": "widget"}


def test_dump_action_handles_non_mapping_model_dump() -> None:
    class SequenceModelAction:
        def model_dump(self) -> list[str]:
            return ["not", "a", "mapping"]

        type = "sequence"
        payload = {"value": 1}

    result = message_utils_module._dump_action(SequenceModelAction())
    assert result["type"] == "sequence"
    assert result["payload"] == {"value": 1}


def test_apply_chatkit_selected_model_ignores_unconfigured_model() -> None:
    workflow = Workflow(name="No picker")
    inputs = {"message": "hello", "model": "openai:gpt-5"}

    selected_model = apply_chatkit_selected_model(inputs, workflow)

    assert selected_model is None
    assert inputs == {"message": "hello"}


def test_apply_chatkit_selected_model_enforces_workflow_allowlist() -> None:
    workflow = Workflow(
        name="Picker",
        chatkit=WorkflowChatKitConfig(
            supported_models=[
                {"id": "openai:gpt-5", "label": "GPT-5"},
            ]
        ),
    )
    inputs = {"message": "hello", "model": "openai:gpt-5"}

    selected_model = apply_chatkit_selected_model(inputs, workflow)

    assert selected_model == "openai:gpt-5"
    assert inputs["model"] == "openai:gpt-5"


def test_apply_chatkit_selected_model_defaults_to_configured_model() -> None:
    workflow = Workflow(
        name="Picker",
        chatkit=WorkflowChatKitConfig(
            supported_models=[
                {"id": "openai:gpt-5-mini", "label": "GPT-5 Mini"},
                {"id": "openai:gpt-5", "label": "GPT-5", "default": True},
            ]
        ),
    )
    inputs = {"message": "hello"}

    selected_model = apply_chatkit_selected_model(inputs, workflow)

    assert selected_model == "openai:gpt-5"
    assert inputs["model"] == "openai:gpt-5"


def test_resolve_chatkit_selected_model_uses_default_when_candidate_blank() -> None:
    workflow = Workflow(
        name="Picker",
        chatkit=WorkflowChatKitConfig(
            supported_models=[
                {"id": "openai:gpt-5", "label": "GPT-5", "default": True},
                {"id": "openai:gpt-5-mini", "label": "GPT-5 Mini"},
            ]
        ),
    )

    result = resolve_chatkit_selected_model(workflow, "   ")

    assert result == "openai:gpt-5"


def test_resolve_chatkit_selected_model_rejects_unknown_candidate() -> None:
    workflow = Workflow(
        name="Picker",
        chatkit=WorkflowChatKitConfig(
            supported_models=[
                {"id": "openai:gpt-5", "label": "GPT-5", "default": True},
                {"id": "openai:gpt-5-mini", "label": "GPT-5 Mini"},
            ]
        ),
    )

    result = resolve_chatkit_selected_model(workflow, "openai:gpt-6")

    assert result == "openai:gpt-5"


def test_apply_chatkit_selected_model_drops_all_disabled_models() -> None:
    workflow = Workflow(
        name="Picker",
        chatkit=WorkflowChatKitConfig(
            supported_models=[
                {"id": "openai:gpt-5", "disabled": True},
                {"id": "openai:gpt-5-mini", "disabled": True},
            ]
        ),
    )
    inputs = {"message": "hello", "model": "openai:gpt-5"}

    selected_model = apply_chatkit_selected_model(inputs, workflow)

    assert selected_model is None
    assert inputs == {"message": "hello"}
