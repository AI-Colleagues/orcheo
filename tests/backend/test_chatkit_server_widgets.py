"""Tests covering ChatKit widget serialization and action handling."""

from __future__ import annotations
import warnings
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
import pytest
from chatkit.types import (
    AssistantMessageItem,
    InferenceOptions,
    NoticeEvent,
    ThreadItemDoneEvent,
    ThreadMetadata,
    UserMessageItem,
    UserMessageTextContent,
    WidgetItem,
)
from chatkit.widgets import DynamicWidgetRoot
from langchain_core.messages import ToolMessage
from pydantic import BaseModel, TypeAdapter, ValidationError
from orcheo_backend.app.chatkit import server as server_mod
from orcheo_backend.app.chatkit import ChatKitRequestContext
from orcheo_backend.app.chatkit.server import _MAX_WIDGET_PAYLOAD_BYTES
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from tests.backend.chatkit_test_utils import (
    create_chatkit_test_server,
    create_workflow_with_graph,
)


warnings.filterwarnings(
    "ignore",
    message=".*named widget classes is deprecated.*",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message=".*named action classes is deprecated.*",
    category=DeprecationWarning,
)


def _sample_widget_root() -> dict[str, object]:
    return {
        "type": "Card",
        "children": [
            {"type": "Text", "value": "Example widget"},
        ],
    }


@pytest.mark.asyncio
async def test_respond_hydrates_widget_toolmessage() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(
        id="thr_widgets",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": str(workflow.id)},
    )
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = UserMessageItem(
        id="msg_user",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Hello")],
        attachments=[],
        quoted_text=None,
        inference_options=InferenceOptions(),
    )
    await server.store.add_thread_item(thread.id, user_item, context)

    tool_message = ToolMessage(
        content=[{"type": "text", "text": "widget payload"}],
        tool_call_id="call-1",
        name="widget_tool",
        artifact={"structured_content": _sample_widget_root()},
    )
    server._run_workflow = AsyncMock(  # type: ignore[attr-defined]
        return_value=("Reply", {"messages": [tool_message]}, None)
    )

    events = [event async for event in server.respond(thread, user_item, context)]

    widget_events = [
        event
        for event in events
        if isinstance(event, ThreadItemDoneEvent) and isinstance(event.item, WidgetItem)
    ]
    assert len(widget_events) == 1
    assert widget_events[0].item.widget.type == "Card"

    stored_items = await server.store.load_thread_items(
        thread.id, after=None, limit=10, order="asc", context=context
    )
    assert any(isinstance(item, WidgetItem) for item in stored_items.data)
    assert any(isinstance(item, AssistantMessageItem) for item in stored_items.data)


@pytest.mark.asyncio
async def test_respond_does_not_replay_previous_widgets() -> None:
    """A follow-up turn must not re-emit widgets produced in earlier turns.

    The checkpointed workflow state carries the full message history, so the
    first turn's widget ``ToolMessage`` reappears on the second turn. The server
    tracks hydrated ``tool_call_id``s on the thread so only genuinely new widgets
    are emitted.
    """
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(
        id="thr_widget_replay",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": str(workflow.id)},
    )
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    def _user_item(item_id: str, text: str) -> UserMessageItem:
        return UserMessageItem(
            id=item_id,
            thread_id=thread.id,
            created_at=datetime.now(UTC),
            content=[UserMessageTextContent(type="input_text", text=text)],
            attachments=[],
            quoted_text=None,
            inference_options=InferenceOptions(),
        )

    def _widget_tool_message(tool_call_id: str) -> ToolMessage:
        return ToolMessage(
            content=[{"type": "text", "text": "widget payload"}],
            tool_call_id=tool_call_id,
            name="widget_tool",
            artifact={"structured_content": _sample_widget_root()},
        )

    # Turn 1: the agent emits a widget (tool call "call-1").
    first_user = _user_item("msg_user_1", "Generate a widget")
    await server.store.add_thread_item(thread.id, first_user, context)
    server._run_workflow = AsyncMock(  # type: ignore[attr-defined]
        return_value=("Reply", {"messages": [_widget_tool_message("call-1")]}, None)
    )
    first_events = [
        event async for event in server.respond(thread, first_user, context)
    ]
    first_widgets = [
        event
        for event in first_events
        if isinstance(event, ThreadItemDoneEvent) and isinstance(event.item, WidgetItem)
    ]
    assert len(first_widgets) == 1

    # Turn 2: a plain text reply, but the checkpointed history still includes
    # the "call-1" widget ToolMessage. It must not be re-emitted.
    second_user = _user_item("msg_user_2", "Thanks")
    await server.store.add_thread_item(thread.id, second_user, context)
    server._run_workflow = AsyncMock(  # type: ignore[attr-defined]
        return_value=(
            "You're welcome",
            {"messages": [_widget_tool_message("call-1")]},
            None,
        )
    )
    second_events = [
        event async for event in server.respond(thread, second_user, context)
    ]
    second_widgets = [
        event
        for event in second_events
        if isinstance(event, ThreadItemDoneEvent) and isinstance(event.item, WidgetItem)
    ]
    assert second_widgets == []


@pytest.mark.asyncio
async def test_widget_hydration_emits_notice_on_invalid_payload() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(
        id="thr_widgets_invalid",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": str(workflow.id)},
    )
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = UserMessageItem(
        id="msg_user",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Hello")],
        attachments=[],
        quoted_text=None,
        inference_options=InferenceOptions(),
    )
    await server.store.add_thread_item(thread.id, user_item, context)

    tool_message = ToolMessage(
        content=[{"type": "text", "text": '{"type": "Card"}'}],
        tool_call_id="call-1",
        name="widget_tool",
        artifact={"structured_content": {"unexpected": "value"}},
    )
    server._run_workflow = AsyncMock(  # type: ignore[attr-defined]
        return_value=("Reply", {"messages": [tool_message]}, None)
    )

    events = [event async for event in server.respond(thread, user_item, context)]

    notices = [event for event in events if isinstance(event, NoticeEvent)]
    assert len(notices) == 1
    assert "Widget" in notices[0].title

    widget_events = [
        event
        for event in events
        if isinstance(event, ThreadItemDoneEvent) and isinstance(event.item, WidgetItem)
    ]
    assert not widget_events

    stored_items = await server.store.load_thread_items(
        thread.id, after=None, limit=10, order="asc", context=context
    )
    assert not any(isinstance(item, WidgetItem) for item in stored_items.data)


@pytest.mark.asyncio
async def test_widget_hydration_logs_thread_and_workflow(caplog) -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(
        id="thr_widgets_invalid_logging",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": str(workflow.id)},
    )
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = UserMessageItem(
        id="msg_user",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Hello")],
        attachments=[],
        quoted_text=None,
        inference_options=InferenceOptions(),
    )
    await server.store.add_thread_item(thread.id, user_item, context)

    tool_message = ToolMessage(
        content=[{"type": "text", "text": '{"type": "Card"}'}],
        tool_call_id="call-1",
        name="widget_tool",
        artifact={"structured_content": {"unexpected": "value"}},
    )
    server._run_workflow = AsyncMock(  # type: ignore[attr-defined]
        return_value=("Reply", {"messages": [tool_message]}, None)
    )

    with caplog.at_level("WARNING", logger="orcheo_backend.app.chatkit.server"):
        events = [event async for event in server.respond(thread, user_item, context)]

    notices = [event for event in events if isinstance(event, NoticeEvent)]
    assert notices

    log_record = next(
        record
        for record in caplog.records
        if "Skipping widget payload" in record.message
    )
    assert log_record.thread_id == str(thread.id)
    assert log_record.workflow_id == str(workflow.id)
    assert "unexpected" in log_record.message


@pytest.mark.asyncio
async def test_widget_hydration_enforces_size_limit() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(
        id="thr_widgets_large",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": str(workflow.id)},
    )
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = UserMessageItem(
        id="msg_user",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Hello")],
        attachments=[],
        quoted_text=None,
        inference_options=InferenceOptions(),
    )
    await server.store.add_thread_item(thread.id, user_item, context)

    oversized_text = "x" * (_MAX_WIDGET_PAYLOAD_BYTES + 5_000)
    oversized_widget = {
        "type": "Card",
        "children": [{"type": "Text", "value": oversized_text}],
    }
    tool_message = ToolMessage(
        content=[{"type": "text", "text": "too large"}],
        tool_call_id="call-1",
        name="widget_tool",
        artifact={"structured_content": oversized_widget},
    )
    server._run_workflow = AsyncMock(  # type: ignore[attr-defined]
        return_value=("Reply", {"messages": [tool_message]}, None)
    )

    events = [event async for event in server.respond(thread, user_item, context)]

    notices = [event for event in events if isinstance(event, NoticeEvent)]
    assert len(notices) == 1
    assert "large" in notices[0].message.lower()

    widget_events = [
        event
        for event in events
        if isinstance(event, ThreadItemDoneEvent) and isinstance(event.item, WidgetItem)
    ]
    assert not widget_events


@pytest.mark.asyncio
async def test_action_updates_existing_widget_root() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(
        id="thr_widget_update",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": str(workflow.id)},
    )
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    original_root = TypeAdapter(DynamicWidgetRoot).validate_python(
        _sample_widget_root()
    )
    sender = WidgetItem(
        id="widget_to_update",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        widget=original_root,
    )
    await server.store.add_thread_item(thread.id, sender, context)

    updated_root = {
        "type": "Card",
        "children": [{"type": "Text", "value": "Updated choice"}],
    }
    tool_message = ToolMessage(
        content=[{"type": "text", "text": "new widget"}],
        tool_call_id="call-update",
        name="widget_tool",
        artifact={"structured_content": updated_root},
    )
    server._run_workflow = AsyncMock(  # type: ignore[attr-defined]
        return_value=("Reply", {"messages": [tool_message]}, None)
    )

    events = [
        event
        async for event in server.action(thread, {"type": "submit"}, sender, context)
    ]

    widget_done_events = [
        event
        for event in events
        if isinstance(event, ThreadItemDoneEvent) and isinstance(event.item, WidgetItem)
    ]
    assert not widget_done_events
    update_events = [event for event in events if event.type == "thread.item.updated"]
    assert update_events

    stored_item = await server.store.load_item(thread.id, sender.id, context=context)
    assert isinstance(stored_item, WidgetItem)
    assert stored_item.widget.children[0].value == "Updated choice"


@pytest.mark.asyncio
async def test_action_freezes_submitted_selection_without_reemit() -> None:
    """A submit must persist the user's selection into the stored widget root.

    ChatKit form controls are uncontrolled, so unless the submitted selection is
    written back into the widget root it snaps back to the original defaults on
    the next render. When the workflow returns no replacement widget, the server
    freezes the submitted form values into the sender so follow-up messages keep
    the selection.
    """
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(
        id="thr_widget_freeze",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": str(workflow.id)},
    )
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    multi_select_root = {
        "type": "Card",
        "asForm": True,
        "children": [
            {
                "type": "Checkbox",
                "name": "choices.opt1",
                "label": "Option 1",
                "defaultChecked": False,
            },
            {
                "type": "Checkbox",
                "name": "choices.opt2",
                "label": "Option 2",
                "defaultChecked": True,
            },
            {
                "type": "Checkbox",
                "name": "choices.opt3",
                "label": "Option 3",
                "defaultChecked": False,
            },
        ],
    }
    sender = WidgetItem(
        id="widget_multiselect",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        widget=TypeAdapter(DynamicWidgetRoot).validate_python(multi_select_root),
    )
    await server.store.add_thread_item(thread.id, sender, context)

    # The workflow only acknowledges the submission with text; it does not
    # re-emit the widget.
    server._run_workflow = AsyncMock(  # type: ignore[attr-defined]
        return_value=("Got your picks", {"messages": []}, None)
    )

    # User checked opt1 and opt3, unchecked opt2.
    action: dict[str, object] = {
        "type": "submit",
        "payload": {"choices": {"opt1": True, "opt3": True}},
    }

    events = [event async for event in server.action(thread, action, sender, context)]

    update_events = [event for event in events if event.type == "thread.item.updated"]
    assert update_events, "Submitted selection should update the widget in place"

    stored_item = await server.store.load_item(thread.id, sender.id, context=context)
    assert isinstance(stored_item, WidgetItem)
    checkbox_state = {
        child.name: child.defaultChecked for child in stored_item.widget.children
    }
    assert checkbox_state == {
        "choices.opt1": True,
        "choices.opt2": False,
        "choices.opt3": True,
    }


@pytest.mark.asyncio
async def test_action_logs_failures_with_ids(caplog) -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(
        id="thr_widgets_action_failure",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": str(workflow.id)},
    )
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    widget_root = TypeAdapter(DynamicWidgetRoot).validate_python(_sample_widget_root())
    widget_item = WidgetItem(
        id="widget_sender_failure",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        widget=widget_root,
    )

    action: dict[str, object] = {"type": "submit", "payload": {"value": "ok"}}
    server._run_workflow = AsyncMock(  # type: ignore[attr-defined]
        side_effect=RuntimeError("workflow error")
    )

    with caplog.at_level("ERROR", logger="orcheo_backend.app.chatkit.server"):
        with pytest.raises(RuntimeError):
            async for _ in server.action(thread, action, widget_item, context):
                pass

    log_record = next(
        record for record in caplog.records if "Widget action failed" in record.message
    )
    assert log_record.thread_id == str(thread.id)
    assert log_record.workflow_id == str(workflow.id)
    assert log_record.widget_action_type == action["type"]


@pytest.mark.asyncio
async def test_action_skips_unsupported_type(caplog) -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(
        id="thr_widgets_action_reject",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": str(workflow.id)},
    )
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    widget_root = TypeAdapter(DynamicWidgetRoot).validate_python(_sample_widget_root())
    widget_item = WidgetItem(
        id="widget_sender_reject",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        widget=widget_root,
    )

    action: dict[str, object] = {
        "type": "link_click",
        "payload": {"href": "https://example.com"},
    }
    server._run_workflow = AsyncMock()  # type: ignore[attr-defined]

    with caplog.at_level("WARNING", logger="orcheo_backend.app.chatkit.server"):
        events = [
            event async for event in server.action(thread, action, widget_item, context)
        ]

    server._run_workflow.assert_not_awaited()
    assert events == []

    log_record = next(
        record
        for record in caplog.records
        if "Ignoring widget action" in record.message
    )
    assert log_record.widget_action_type == action["type"]
    assert "submit" in str(log_record.allowed_widget_action_types)


@pytest.mark.asyncio
async def test_action_routes_widget_payload_to_workflow() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(
        id="thr_widgets_action",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": str(workflow.id)},
    )
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    widget_root = TypeAdapter(DynamicWidgetRoot).validate_python(_sample_widget_root())
    widget_item = WidgetItem(
        id="widget_sender",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        widget=widget_root,
    )

    captured_inputs: dict[str, object] = {}

    async def fake_run(workflow_id, inputs, actor="chatkit", **_kwargs):
        captured_inputs.update(inputs)
        return ("Action reply", {"messages": []}, None)

    server._run_workflow = AsyncMock(side_effect=fake_run)  # type: ignore[attr-defined]

    action: dict[str, object] = {"type": "submit", "payload": {"value": "ok"}}

    events = [
        event async for event in server.action(thread, action, widget_item, context)
    ]

    assert captured_inputs["action"] == action
    assert captured_inputs["widget_item_id"] == widget_item.id
    assert isinstance(captured_inputs["widget"], dict)
    assert captured_inputs["widget"]["type"] == "Card"

    assistant_events = [
        event
        for event in events
        if isinstance(event, ThreadItemDoneEvent)
        and isinstance(event.item, AssistantMessageItem)
    ]
    assert assistant_events, "Assistant reply should still be emitted"


@pytest.mark.asyncio
async def test_action_emits_progress_updates() -> None:
    from chatkit.types import ProgressUpdateEvent

    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(
        id="thr_action_progress",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": str(workflow.id)},
    )
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    widget_root = TypeAdapter(DynamicWidgetRoot).validate_python(_sample_widget_root())
    widget_item = WidgetItem(
        id="widget_progress",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        widget=widget_root,
    )

    async def fake_run(_workflow_id, _inputs, progress_callback=None, **_kwargs):
        if progress_callback is not None:
            await progress_callback({"node_a": {"value": 1}})
        return ("Action reply", {"messages": []}, None)

    server._run_workflow = AsyncMock(side_effect=fake_run)  # type: ignore[attr-defined]

    action: dict[str, object] = {"type": "submit", "payload": {"value": "ok"}}

    events = [
        event async for event in server.action(thread, action, widget_item, context)
    ]

    progress_events = [
        event for event in events if isinstance(event, ProgressUpdateEvent)
    ]
    assert progress_events
    assert any("node_a" in e.text for e in progress_events)


# ---------------------------------------------------------------------------
# _freeze_widget_selection (server.py lines 830, 843, 846-847)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_freeze_widget_selection_returns_none_for_none_payload() -> None:
    """Covers line 830: _action_payload_mapping returns None → early return."""
    repository = InMemoryWorkflowRepository()
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(id="thr-freeze-none-payload", created_at=datetime.now(UTC))
    context: ChatKitRequestContext = {}

    widget_root = TypeAdapter(DynamicWidgetRoot).validate_python(_sample_widget_root())
    sender = WidgetItem(
        id="widget-freeze-none-payload",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        widget=widget_root,
    )

    # Action with no "payload" key → _action_payload_mapping returns None → line 830
    action: dict[str, object] = {"type": "submit"}
    result = await server._freeze_widget_selection(thread, sender, action, context)
    assert result is None


@pytest.mark.asyncio
async def test_freeze_widget_selection_returns_none_when_values_unchanged() -> None:
    """Covers line 843: _apply_submitted_form_values returns changed=False."""
    repository = InMemoryWorkflowRepository()
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(id="thr-freeze-unchanged", created_at=datetime.now(UTC))
    context: ChatKitRequestContext = {}

    # Checkbox widget with defaultChecked=True already.
    checkbox_root = {
        "type": "Card",
        "asForm": True,
        "children": [
            {"type": "Checkbox", "name": "opt", "defaultChecked": True},
        ],
    }
    widget_root = TypeAdapter(DynamicWidgetRoot).validate_python(checkbox_root)
    sender = WidgetItem(
        id="widget-freeze-unchanged",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        widget=widget_root,
    )

    # Submit the same value (True) that's already stored → no change → line 843
    action: dict[str, object] = {"type": "submit", "payload": {"opt": True}}
    result = await server._freeze_widget_selection(thread, sender, action, context)
    assert result is None


@pytest.mark.asyncio
async def test_freeze_widget_selection_returns_none_on_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Covers lines 846-847: ValidationError from _WIDGET_ROOT_ADAPTER is caught."""
    repository = InMemoryWorkflowRepository()
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(id="thr-freeze-valerr", created_at=datetime.now(UTC))
    context: ChatKitRequestContext = {}

    # Widget with a checkbox so the payload triggers a real change
    checkbox_root = {
        "type": "Card",
        "asForm": True,
        "children": [
            {"type": "Checkbox", "name": "opt", "defaultChecked": False},
        ],
    }
    widget_root = TypeAdapter(DynamicWidgetRoot).validate_python(checkbox_root)
    sender = WidgetItem(
        id="widget-freeze-valerr",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        widget=widget_root,
    )

    # Produce a real ValidationError instance to use as side_effect.
    class _M(BaseModel):
        x: int

    try:
        _M.model_validate({"x": "not-an-int"})
    except ValidationError as _e:
        fake_ve = _e

    monkeypatch.setattr(
        server_mod._WIDGET_ROOT_ADAPTER,
        "validate_python",
        Mock(side_effect=fake_ve),
    )

    # Submit True → defaultChecked was False → changed=True → validate_python raises
    action: dict[str, object] = {"type": "submit", "payload": {"opt": True}}
    result = await server._freeze_widget_selection(thread, sender, action, context)
    assert result is None
