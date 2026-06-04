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
from orcheo_backend.app.chatkit import server as server_module
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
from unittest.mock import AsyncMock, Mock


class DummyStore:
    """Minimal store implementation used by the helper tests."""

    def __init__(self) -> None:
        self.attachment_service: object | None = None


def create_server() -> tuple[server_module.OrcheoChatKitServer, DummyStore]:
    store = DummyStore()
    repository = Mock()
    server = server_module.OrcheoChatKitServer(
        store=store,
        repository=repository,
        vault_provider=lambda: None,
    )
    return server, store


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


def testextract_reply_from_state_ignores_none_assistant_message() -> None:
    state = {"assistant_message": None, "reply": "Fallback reply"}
    result = extract_reply_from_state(state)
    assert result == "Fallback reply"


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


def testextract_reply_from_state_from_results_none_assistant_message() -> None:
    state = {
        "results": {
            "node_a": {
                "assistant_message": None,
                "reply": "Nested reply",
            }
        }
    }
    result = extract_reply_from_state(state)
    assert result == "Nested reply"


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


def test_chatkit_attachment_module_reexports_store_symbols() -> None:
    from orcheo_backend.app.chatkit import attachments as attachments_module
    from orcheo_backend.app.chatkit_store_postgres import (
        attachment_service as store_module,
    )

    assert attachments_module.AttachmentService is store_module.AttachmentService
    assert (
        attachments_module.AttachmentNotFoundError
        is store_module.AttachmentNotFoundError
    )
    assert (
        attachments_module.build_attachment_scope is store_module.build_attachment_scope
    )
    assert (
        attachments_module.build_scoped_resolver is store_module.build_scoped_resolver
    )


def test_attachment_ids_from_user_item_handles_mixed_payloads() -> None:
    user_item = SimpleNamespace(
        attachments=[
            " atc_string ",
            {"id": " atc_mapping "},
            {"file_id": "atc_file"},
            SimpleNamespace(id=" atc_object "),
            "",
            "   ",
            {"id": None},
            SimpleNamespace(id=None),
        ]
    )

    assert server_module.OrcheoChatKitServer._attachment_ids_from_user_item(
        user_item
    ) == ["atc_string", "atc_mapping", "atc_file", "atc_object"]


def test_attachment_ids_from_user_item_none_returns_empty() -> None:
    assert server_module.OrcheoChatKitServer._attachment_ids_from_user_item(None) == []


@pytest.mark.asyncio
async def test_link_upload_session_handles_direct_link_failure() -> None:
    server, _ = create_server()
    attachment_service = SimpleNamespace(
        link_attachments_to_thread=AsyncMock(side_effect=RuntimeError("boom")),
        resolve_upload_session_id=AsyncMock(return_value="ups-123"),
        link_upload_session_to_thread=AsyncMock(return_value=1),
    )
    server.store.attachment_service = attachment_service

    thread = ThreadMetadata(id="thread-1", created_at=datetime.now(UTC))
    user_item = SimpleNamespace(attachments=["atc-1"])
    context = {"workspace_id": "ws-1", "workflow_id": "wf-1"}

    await server._link_upload_session(context, thread, user_item)

    attachment_service.link_attachments_to_thread.assert_awaited_once_with(
        ["atc-1"], "thread-1", "ws-1"
    )
    attachment_service.resolve_upload_session_id.assert_awaited_once_with(
        ["atc-1"], "ws-1", workflow_id="wf-1"
    )
    attachment_service.link_upload_session_to_thread.assert_awaited_once_with(
        upload_session_id="ups-123",
        thread_id="thread-1",
        workspace_id="ws-1",
    )


@pytest.mark.asyncio
async def test_link_upload_session_returns_when_resolution_fails() -> None:
    server, _ = create_server()
    attachment_service = SimpleNamespace(
        link_attachments_to_thread=AsyncMock(return_value=1),
        resolve_upload_session_id=AsyncMock(side_effect=RuntimeError("boom")),
        link_upload_session_to_thread=AsyncMock(return_value=1),
    )
    server.store.attachment_service = attachment_service

    thread = ThreadMetadata(id="thread-2", created_at=datetime.now(UTC))
    user_item = SimpleNamespace(attachments=["atc-2"])
    context = {"workspace_id": "ws-1", "workflow_id": "wf-1"}

    await server._link_upload_session(context, thread, user_item)

    attachment_service.link_attachments_to_thread.assert_awaited_once_with(
        ["atc-2"], "thread-2", "ws-1"
    )
    attachment_service.resolve_upload_session_id.assert_awaited_once_with(
        ["atc-2"], "ws-1", workflow_id="wf-1"
    )
    attachment_service.link_upload_session_to_thread.assert_not_awaited()
    assert "upload_session_id" not in context


@pytest.mark.asyncio
async def test_link_upload_session_returns_when_service_missing() -> None:
    server, _ = create_server()
    server.store.attachment_service = None

    thread = ThreadMetadata(id="thread-3", created_at=datetime.now(UTC))
    user_item = SimpleNamespace(attachments=["atc-3"])
    context = {
        "workspace_id": "ws-1",
        "workflow_id": "wf-1",
        "upload_session_id": "ups-1",
    }

    await server._link_upload_session(context, thread, user_item)

    assert context["upload_session_id"] == "ups-1"


@pytest.mark.asyncio
async def test_link_upload_session_handles_session_link_failure() -> None:
    server, _ = create_server()
    attachment_service = SimpleNamespace(
        link_attachments_to_thread=AsyncMock(return_value=1),
        resolve_upload_session_id=AsyncMock(return_value="ups-123"),
        link_upload_session_to_thread=AsyncMock(side_effect=RuntimeError("boom")),
    )
    server.store.attachment_service = attachment_service

    thread = ThreadMetadata(id="thread-4", created_at=datetime.now(UTC))
    user_item = SimpleNamespace(attachments=["atc-4"])
    context = {
        "workspace_id": "ws-1",
        "workflow_id": "wf-1",
        "upload_session_id": "ups-123",
    }

    await server._link_upload_session(context, thread, user_item)

    attachment_service.link_attachments_to_thread.assert_awaited_once_with(
        ["atc-4"], "thread-4", "ws-1"
    )
    attachment_service.resolve_upload_session_id.assert_not_awaited()
    attachment_service.link_upload_session_to_thread.assert_awaited_once_with(
        upload_session_id="ups-123",
        thread_id="thread-4",
        workspace_id="ws-1",
    )


@pytest.mark.asyncio
async def test_link_upload_session_skips_debug_when_zero_rows() -> None:
    server, _ = create_server()
    attachment_service = SimpleNamespace(
        link_attachments_to_thread=AsyncMock(return_value=1),
        resolve_upload_session_id=AsyncMock(return_value="ups-123"),
        link_upload_session_to_thread=AsyncMock(return_value=0),
    )
    server.store.attachment_service = attachment_service

    thread = ThreadMetadata(id="thread-5", created_at=datetime.now(UTC))
    user_item = SimpleNamespace(attachments=["atc-5"])
    context = {
        "workspace_id": "ws-1",
        "workflow_id": "wf-1",
        "upload_session_id": "ups-123",
    }

    await server._link_upload_session(context, thread, user_item)

    attachment_service.link_upload_session_to_thread.assert_awaited_once_with(
        upload_session_id="ups-123",
        thread_id="thread-5",
        workspace_id="ws-1",
    )


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


# ---------------------------------------------------------------------------
# _resolve_recent_upload_session_id exception path (server.py lines 799-804)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_recent_upload_session_id_catches_exception() -> None:
    """Exception from attachment_service is swallowed and None is returned (lines 799-804)."""
    server, _ = create_server()
    attachment_service = SimpleNamespace(
        resolve_recent_upload_session_id=AsyncMock(side_effect=RuntimeError("boom"))
    )

    thread = ThreadMetadata(id="thread-exc", created_at=datetime.now(UTC))
    result = await server._resolve_recent_upload_session_id(
        attachment_service=attachment_service,
        thread=thread,
        workspace_id="ws-1",
        workflow_id="wf-1",
        actor_subject="user@example.com",
    )

    assert result is None


# ---------------------------------------------------------------------------
# _resolve_additional_attachments exception path (server.py lines 857-866)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_additional_attachments_catches_exception() -> None:
    """Exception from attachment_service.list_attachment_summaries is swallowed (lines 857-866)."""
    server, store = create_server()
    store.attachment_service = SimpleNamespace(
        list_attachment_summaries=AsyncMock(side_effect=RuntimeError("db-gone"))
    )

    thread = ThreadMetadata(id="thread-exc2", created_at=datetime.now(UTC))
    context = {"workspace_id": "ws-1"}

    result = await server._resolve_additional_attachments(
        thread=thread,
        workflow_id="wf-1",
        context=context,
    )

    assert result == []


@pytest.mark.asyncio
async def test_resolve_additional_attachments_returns_empty_without_service() -> None:
    """Returns [] immediately when attachment_service is None."""
    server, _ = create_server()

    thread = ThreadMetadata(id="thread-no-svc", created_at=datetime.now(UTC))
    context = {"workspace_id": "ws-1"}

    result = await server._resolve_additional_attachments(
        thread=thread,
        workflow_id="wf-1",
        context=context,
    )

    assert result == []
