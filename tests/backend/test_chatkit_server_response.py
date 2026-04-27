"""Tests covering ChatKit server responses and history building."""

from __future__ import annotations
from datetime import UTC, datetime
from unittest.mock import AsyncMock
import pytest
from chatkit.errors import CustomStreamError
from chatkit.types import (
    AssistantMessageContent,
    AssistantMessageItem,
    InferenceOptions,
    ProgressUpdateEvent,
    ThreadMetadata,
    UserMessageItem,
    UserMessageTextContent,
)
from orcheo_backend.app.chatkit import ChatKitRequestContext
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    WorkflowNotFoundError,
)
from tests.backend.chatkit_test_utils import (
    create_chatkit_test_server,
    create_workflow_with_graph,
)


def _build_thread(metadata: dict[str, str] | None = None) -> ThreadMetadata:
    return ThreadMetadata(
        id="thr_test",
        created_at=datetime.now(UTC),
        metadata=metadata or {},
    )


def _build_user_item(thread_id: str, text: str = "Ping") -> UserMessageItem:
    return UserMessageItem(
        id="msg_user",
        thread_id=thread_id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text=text)],
        attachments=[],
        quoted_text=None,
        inference_options=InferenceOptions(),
    )


@pytest.mark.asyncio
async def test_chatkit_server_emits_assistant_reply() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    workflow_id = str(workflow.id)

    server = create_chatkit_test_server(repository)
    server._run_workflow = AsyncMock(  # type: ignore[attr-defined]
        return_value=("Echo: Ping", {}, None)
    )

    thread = _build_thread({"workflow_id": workflow_id})
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = _build_user_item(thread.id, "Ping")
    await server.store.add_thread_item(thread.id, user_item, context)

    events = [event async for event in server.respond(thread, user_item, context)]
    non_progress = [e for e in events if not isinstance(e, ProgressUpdateEvent)]
    assert len(non_progress) == 1
    event = non_progress[0]
    assert isinstance(event.item, AssistantMessageItem)
    assert "Ping" in event.item.content[0].text


@pytest.mark.asyncio
async def test_chatkit_server_emits_progress_updates() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    workflow_id = str(workflow.id)

    server = create_chatkit_test_server(repository)

    async def fake_run(_workflow_uuid, _inputs, progress_callback=None, **_kwargs):
        if progress_callback is not None:
            await progress_callback({})
            await progress_callback({"node_a": {"value": 1}})
        return ("Reply", {}, None)

    server._run_workflow = AsyncMock(side_effect=fake_run)  # type: ignore[attr-defined]

    thread = _build_thread({"workflow_id": workflow_id})
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = _build_user_item(thread.id, "Ping")
    await server.store.add_thread_item(thread.id, user_item, context)

    events = [event async for event in server.respond(thread, user_item, context)]

    progress_events = [
        event for event in events if isinstance(event, ProgressUpdateEvent)
    ]
    assert len(progress_events) == 2
    assert progress_events[0].text == "Agent is working..."
    assert progress_events[1].text == "Running node_a"


@pytest.mark.asyncio
async def test_chatkit_server_emits_node_event_progress_updates() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    workflow_id = str(workflow.id)

    server = create_chatkit_test_server(repository)

    async def fake_run(_workflow_uuid, _inputs, progress_callback=None, **_kwargs):
        if progress_callback is not None:
            await progress_callback({"node": "indexer", "event": "on_chain_start"})
            await progress_callback({"node": "indexer", "event": "on_chain_end"})
        return ("Reply", {}, None)

    server._run_workflow = AsyncMock(side_effect=fake_run)  # type: ignore[attr-defined]

    thread = _build_thread({"workflow_id": workflow_id})
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = _build_user_item(thread.id, "Ping")
    await server.store.add_thread_item(thread.id, user_item, context)

    events = [event async for event in server.respond(thread, user_item, context)]

    progress_texts = [
        event.text for event in events if isinstance(event, ProgressUpdateEvent)
    ]
    assert any("indexer" in text and "starting" in text for text in progress_texts)
    assert any("indexer" in text and "completed" in text for text in progress_texts)


@pytest.mark.asyncio
async def test_chatkit_server_requires_workflow_metadata() -> None:
    repository = InMemoryWorkflowRepository()
    server = create_chatkit_test_server(repository)

    thread = _build_thread(metadata={})
    context: ChatKitRequestContext = {}
    user_item = _build_user_item(thread.id, "Hello")

    await server.store.save_thread(thread, context)
    await server.store.add_thread_item(thread.id, user_item, context)

    with pytest.raises(CustomStreamError):
        _ = [event async for event in server.respond(thread, user_item, context)]


@pytest.mark.asyncio
async def test_chatkit_server_invalid_workflow_id() -> None:
    repository = InMemoryWorkflowRepository()
    server = create_chatkit_test_server(repository)

    thread = _build_thread(metadata={"workflow_id": "not-a-uuid"})
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = _build_user_item(thread.id)
    await server.store.add_thread_item(thread.id, user_item, context)

    with pytest.raises(CustomStreamError, match="invalid"):
        _ = [event async for event in server.respond(thread, user_item, context)]


@pytest.mark.asyncio
async def test_chatkit_server_translates_workflow_not_found() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    workflow_id = str(workflow.id)

    server = create_chatkit_test_server(repository)
    server._run_workflow = AsyncMock(
        side_effect=WorkflowNotFoundError("missing"),  # type: ignore[attr-defined]
    )

    thread = _build_thread({"workflow_id": workflow_id})
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = _build_user_item(thread.id, "Ping")
    await server.store.add_thread_item(thread.id, user_item, context)

    with pytest.raises(CustomStreamError, match="missing"):
        _ = [event async for event in server.respond(thread, user_item, context)]


@pytest.mark.asyncio
async def test_chatkit_server_resolve_user_item_from_history() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    workflow_id = str(workflow.id)

    server = create_chatkit_test_server(repository)
    server._run_workflow = AsyncMock(return_value=("Reply", {}, None))  # type: ignore[attr-defined]

    thread = _build_thread({"workflow_id": workflow_id})
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = _build_user_item(thread.id, "Hello")
    await server.store.add_thread_item(thread.id, user_item, context)

    events = [event async for event in server.respond(thread, None, context)]
    non_progress = [e for e in events if not isinstance(e, ProgressUpdateEvent)]
    assert len(non_progress) == 1


@pytest.mark.asyncio
async def test_chatkit_server_resolve_user_item_not_found() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    workflow_id = str(workflow.id)

    server = create_chatkit_test_server(repository)

    thread = _build_thread({"workflow_id": workflow_id})
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    with pytest.raises(CustomStreamError, match="Unable to locate"):
        _ = [event async for event in server.respond(thread, None, context)]


@pytest.mark.asyncio
async def test_chatkit_server_builds_history() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    workflow_id = str(workflow.id)

    server = create_chatkit_test_server(repository)

    captured_inputs: dict[str, object] = {}

    async def mock_run(_workflow_uuid, inputs, _actor="chatkit", **_: object):
        captured_inputs.update(inputs)
        return ("Reply", {}, None)

    server._run_workflow = AsyncMock(side_effect=mock_run)  # type: ignore[attr-defined]

    thread = _build_thread({"workflow_id": workflow_id})
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item1 = _build_user_item(thread.id, "First message")
    await server.store.add_thread_item(thread.id, user_item1, context)

    assistant_item = AssistantMessageItem(
        id="msg_assistant",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[AssistantMessageContent(text="First response")],
    )
    await server.store.add_thread_item(thread.id, assistant_item, context)

    user_item2 = _build_user_item(thread.id, "Second message")
    await server.store.add_thread_item(thread.id, user_item2, context)

    _ = [event async for event in server.respond(thread, user_item2, context)]

    history = captured_inputs["history"]
    assert isinstance(history, list)
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
    assert history[2]["content"] == "Second message"


@pytest.mark.asyncio
async def test_chatkit_server_history_with_unknown_item_type() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    workflow_id = str(workflow.id)

    server = create_chatkit_test_server(repository)

    thread = _build_thread({"workflow_id": workflow_id})
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = _build_user_item(thread.id, "Hello")
    await server.store.add_thread_item(thread.id, user_item, context)

    class UnknownItem:
        id = "msg_unknown"
        thread_id = thread.id
        created_at = datetime.now(UTC)
        type = "unknown"

        def model_copy(self, *, deep: bool = True):
            return self

    state = server.store._state_for(thread.id)
    state.items.append(UnknownItem())

    history = await server._history(thread, context)
    assert len(history) == 1
    assert history[0]["role"] == "user"


@pytest.mark.asyncio
async def test_chatkit_server_resolve_user_item_with_assistant_as_most_recent() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    workflow_id = str(workflow.id)

    server = create_chatkit_test_server(repository)

    thread = _build_thread({"workflow_id": workflow_id})
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    assistant_item = AssistantMessageItem(
        id="msg_assistant",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[AssistantMessageContent(text="Assistant response")],
    )
    await server.store.add_thread_item(thread.id, assistant_item, context)

    with pytest.raises(CustomStreamError, match="Unable to locate"):
        await server._resolve_user_item(thread, None, context)


def test_format_node_event_update_on_chain_error_with_message() -> None:
    from orcheo_backend.app.chatkit.server import _format_node_event_update

    result = _format_node_event_update("indexer", "on_chain_error", {"error": "boom"})
    assert result == "! indexer error: boom"


def test_format_node_event_update_on_chain_error_without_message() -> None:
    from orcheo_backend.app.chatkit.server import _format_node_event_update

    result = _format_node_event_update("indexer", "on_chain_error", {})
    assert result == "! indexer error"


def test_format_node_event_update_on_chain_error_non_mapping_payload() -> None:
    from orcheo_backend.app.chatkit.server import _format_node_event_update

    result = _format_node_event_update("indexer", "on_chain_error", "not a dict")
    assert result == "! indexer error"


def test_format_node_event_update_unknown_event() -> None:
    from orcheo_backend.app.chatkit.server import _format_node_event_update

    result = _format_node_event_update("indexer", "on_tool_start", None)
    assert result == "[on_tool_start] indexer"


def test_format_node_event_update_node_status_with_message() -> None:
    from orcheo_backend.app.chatkit.server import _format_node_event_update

    result = _format_node_event_update(
        "indexer",
        "node_status",
        {"message": "fetching documents"},
    )
    assert result == "indexer: fetching documents"


def test_format_node_event_update_node_status_with_text_field() -> None:
    from orcheo_backend.app.chatkit.server import _format_node_event_update

    result = _format_node_event_update(
        "indexer",
        "node_status",
        {"text": "loading"},
    )
    assert result == "indexer: loading"


def test_format_node_event_update_node_status_with_status_field() -> None:
    from orcheo_backend.app.chatkit.server import _format_node_event_update

    result = _format_node_event_update("indexer", "node_status", {"status": "ready"})
    assert result == "indexer: ready"


def test_format_node_event_update_node_status_string_payload() -> None:
    from orcheo_backend.app.chatkit.server import _format_node_event_update

    result = _format_node_event_update("indexer", "node_status", "in flight")
    assert result == "indexer: in flight"


def test_format_node_event_update_node_status_default_label() -> None:
    from orcheo_backend.app.chatkit.server import _format_node_event_update

    result = _format_node_event_update("indexer", "node_status", {})
    assert result == "indexer: status update"


def test_progress_texts_for_step_skips_none_key() -> None:
    from orcheo_backend.app.chatkit.server import _progress_texts_for_step

    texts = _progress_texts_for_step({None: "value", "node_a": "data"})  # type: ignore[dict-item]
    assert texts == ["Running node_a"]


def test_progress_texts_for_step_skips_empty_key() -> None:
    from orcheo_backend.app.chatkit.server import _progress_texts_for_step

    texts = _progress_texts_for_step({"  ": "value", "node_a": "data"})
    assert texts == ["Running node_a"]
