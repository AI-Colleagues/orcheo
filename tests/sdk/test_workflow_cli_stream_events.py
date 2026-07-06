"""Tests for workflow CLI event handling helpers."""

from __future__ import annotations
import json
from typing import Any
import pytest
from orcheo_sdk.cli.state import CLIState
from orcheo_sdk.cli.workflow import (
    _handle_node_event,
    _handle_status_update,
    _process_stream_messages,
    _render_node_output,
)
from tests.sdk.workflow_cli_test_utils import make_state


@pytest.mark.asyncio()
async def test_process_stream_messages_returns_final_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = make_state()
    updates: list[dict[str, Any]] = []

    def fake_handle(state_arg: CLIState, update: dict[str, Any]) -> str:
        updates.append(update)
        return "completed"

    monkeypatch.setattr("orcheo_sdk.cli.workflow._handle_status_update", fake_handle)

    class FakeWebSocket:
        def __init__(self) -> None:
            self._messages = iter([json.dumps({"status": "completed"})])

        def __aiter__(self) -> FakeWebSocket:
            return self

        async def __anext__(self) -> str:
            try:
                return next(self._messages)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    result = await _process_stream_messages(state, FakeWebSocket())
    assert result == "completed"
    assert updates[0]["status"] == "completed"


@pytest.mark.asyncio()
async def test_process_stream_messages_handles_node_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = make_state()
    handled_events: list[dict[str, Any]] = []

    def fake_status(state_arg: CLIState, update: dict[str, Any]) -> None:
        handled_events.append({"status_checked": update})

    def fake_node_event(state_arg: CLIState, update: dict[str, Any]) -> None:
        handled_events.append(update)

    monkeypatch.setattr("orcheo_sdk.cli.workflow._handle_status_update", fake_status)
    monkeypatch.setattr("orcheo_sdk.cli.workflow._handle_node_event", fake_node_event)

    messages = [
        json.dumps({"status": "running"}),
        json.dumps({"node": "demo", "event": "custom", "payload": {"ok": True}}),
    ]

    class FakeWebSocket:
        def __init__(self, payloads: list[str]) -> None:
            self._payloads = iter(payloads)

        def __aiter__(self) -> FakeWebSocket:
            return self

        async def __anext__(self) -> str:
            try:
                return next(self._payloads)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    result = await _process_stream_messages(state, FakeWebSocket(messages))
    assert result == "completed"
    assert any(event.get("event") == "custom" for event in handled_events)


@pytest.mark.asyncio()
async def test_process_stream_messages_handles_trace_updates() -> None:
    state = make_state()

    messages = [
        json.dumps(
            {
                "type": "trace:update",
                "spans": [{"name": "document_loader", "status": {"code": "OK"}}],
                "complete": False,
            }
        ),
        json.dumps({"status": "completed"}),
    ]

    class FakeWebSocket:
        def __init__(self, payloads: list[str]) -> None:
            self._payloads = iter(payloads)

        def __aiter__(self) -> FakeWebSocket:
            return self

        async def __anext__(self) -> str:
            try:
                return next(self._payloads)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    result = await _process_stream_messages(state, FakeWebSocket(messages))
    assert result == "completed"
    assert any("Trace update" in msg for msg in state.console.messages)


@pytest.mark.asyncio()
async def test_process_stream_messages_handles_generic_updates() -> None:
    state = make_state()

    messages = [
        json.dumps(
            {"document_loader": {"node_results": {"document_loader": {"ok": True}}}}
        ),
        json.dumps({"status": "completed"}),
    ]

    class FakeWebSocket:
        def __init__(self, payloads: list[str]) -> None:
            self._payloads = iter(payloads)

        def __aiter__(self) -> FakeWebSocket:
            return self

        async def __anext__(self) -> str:
            try:
                return next(self._payloads)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    result = await _process_stream_messages(state, FakeWebSocket(messages))
    assert result == "completed"
    assert any("document_loader" in msg for msg in state.console.messages)


@pytest.mark.asyncio()
async def test_process_stream_messages_machine_verbose_prints_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = make_state(verbose_results=True)
    state.human = False
    captured: list[dict[str, Any]] = []

    def fake_print_json(data: Any) -> None:
        assert isinstance(data, dict)
        captured.append(data)

    monkeypatch.setattr("orcheo_sdk.cli.workflow.streaming.print_json", fake_print_json)

    messages = [
        json.dumps({"type": "trace:update", "spans": [{"name": "loader"}]}),
        json.dumps({"node": "loader", "event": "on_chain_end", "payload": {"ok": 1}}),
        json.dumps({"status": "completed"}),
    ]

    class FakeWebSocket:
        def __init__(self, payloads: list[str]) -> None:
            self._payloads = iter(payloads)

        def __aiter__(self) -> FakeWebSocket:
            return self

        async def __anext__(self) -> str:
            try:
                return next(self._payloads)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    result = await _process_stream_messages(state, FakeWebSocket(messages))
    assert result == "completed"
    assert [entry.get("status") for entry in captured][-1] == "completed"
    assert not state.console.messages


@pytest.mark.parametrize(
    ("status", "expected", "fragment"),
    [
        ("error", "error", "Error"),
        ("cancelled", "cancelled", "Cancelled"),
        ("completed", "completed", "completed successfully"),
        ("running", None, "Status"),
    ],
)
def test_handle_status_update_variants(
    status: str, expected: str | None, fragment: str
) -> None:
    state = make_state()
    update = {"status": status, "error": "boom", "reason": "user aborted"}
    result = _handle_status_update(state, update)
    if expected is None:
        assert result is None
    else:
        assert result == expected
    assert any(fragment in msg for msg in state.console.messages)


def test_handle_node_event_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state()
    outputs: list[Any] = []

    def capture_render(state_arg: CLIState, data: Any) -> None:
        outputs.append(data)

    monkeypatch.setattr("orcheo_sdk.cli.workflow._render_node_output", capture_render)

    _handle_node_event(state, {"node": "A", "event": "on_chain_start"})
    _handle_node_event(
        state,
        {"node": "B", "event": "on_chain_end", "payload": {"value": 1}},
    )
    _handle_node_event(
        state,
        {
            "node": "C",
            "event": "on_chain_error",
            "payload": {"error": "boom"},
        },
    )
    _handle_node_event(
        state,
        {"node": "D", "event": "custom", "payload": {"info": True}},
    )
    _handle_node_event(state, {"node": None, "event": "custom"})

    assert outputs == [{"value": 1}]
    assert any("starting" in msg for msg in state.console.messages)
    assert any("boom" in msg for msg in state.console.messages)
    assert any("[custom]" in msg for msg in state.console.messages)


def test_handle_node_event_on_chain_end_without_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = make_state()
    called = False

    def fail_render(*_: Any, **__: Any) -> None:
        nonlocal called
        called = True
        raise AssertionError("render should not be called")

    monkeypatch.setattr("orcheo_sdk.cli.workflow._render_node_output", fail_render)

    _handle_node_event(state, {"node": "B", "event": "on_chain_end"})

    assert not called
    assert any("✓ B" in msg for msg in state.console.messages)


def test_render_node_output_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state()
    rendered: list[Any] = []

    def fake_render_json(console: Any, data: Any, title: Any = None) -> None:
        rendered.append((data, title))

    monkeypatch.setattr("orcheo_sdk.cli.workflow.render_json", fake_render_json)

    _render_node_output(state, None)
    _render_node_output(state, {"a": "b", "c": 1})
    _render_node_output(state, {"a": [1, 2, 3]})
    _render_node_output(state, "short text")
    _render_node_output(state, [1, 2, 3])

    assert any("a='b'" in msg for msg in state.console.messages)
    assert rendered[0][0] == {"a": [1, 2, 3]}
    assert any("[dim]" in msg and "[" in msg for msg in state.console.messages)
