"""Tests covering workflow execution streaming behaviour."""

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import WebSocket
from orcheo.graph.ingestion import LANGGRAPH_SCRIPT_FORMAT
from orcheo_backend.app import execute_workflow
from orcheo_backend.app.history import InMemoryRunHistoryStore


@pytest.mark.asyncio
async def test_execute_workflow() -> None:
    """Workflows stream step payloads to the websocket and history store."""

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_graph = MagicMock()

    workflow_id = "test-workflow"
    graph_config = {"nodes": []}
    inputs = {"input": "test"}
    execution_id = "test-execution"
    runnable_config = {"tags": ["demo"], "metadata": {"experiment": "m1"}}

    steps = [
        {"status": "running", "data": "test"},
        {"status": "completed", "data": "done"},
    ]

    async def mock_astream(*args, **kwargs):
        for step in steps:
            yield step

    async def mock_aget_state(*args, **kwargs):
        return MagicMock(values={"messages": [], "results": {}, "inputs": inputs})

    mock_compiled_graph = MagicMock()
    mock_compiled_graph.astream = mock_astream
    mock_compiled_graph.aget_state = mock_aget_state
    mock_graph.compile.return_value = mock_compiled_graph

    mock_checkpointer = object()
    mock_store = object()

    @asynccontextmanager
    async def fake_checkpointer(_settings):
        yield mock_checkpointer

    @asynccontextmanager
    async def fake_graph_store(_settings):
        yield mock_store

    history_store = InMemoryRunHistoryStore()

    with (
        patch("orcheo_backend.app.create_checkpointer", fake_checkpointer),
        patch("orcheo_backend.app.create_graph_store", fake_graph_store),
        patch("orcheo_backend.app.build_graph", return_value=mock_graph),
        patch("orcheo_backend.app._history_store_ref", {"store": history_store}),
        patch(
            "orcheo_backend.app.workflow_execution.close_browser_sessions_for_scope",
            new_callable=AsyncMock,
        ) as mock_browser_cleanup,
    ):
        await execute_workflow(
            workflow_id,
            graph_config,
            inputs,
            execution_id,
            mock_websocket,
            runnable_config=runnable_config,
        )
    mock_browser_cleanup.assert_awaited_once_with(execution_id)

    mock_graph.compile.assert_called_once_with(
        checkpointer=mock_checkpointer,
        store=mock_store,
    )
    mock_websocket.send_json.assert_any_call(steps[0])
    mock_websocket.send_json.assert_any_call(steps[1])

    history = await history_store.get_history(execution_id)
    assert history.status == "completed"
    assert [step.payload for step in history.steps[:-1]] == steps
    assert history.steps[-1].payload == {"status": "completed"}
    assert history.tags == ["demo"]
    assert history.runnable_config["configurable"]["thread_id"] == execution_id

    trace_messages = [
        call.args[0]
        for call in mock_websocket.send_json.call_args_list
        if call.args
        and isinstance(call.args[0], dict)
        and call.args[0].get("type") == "trace:update"
    ]
    assert trace_messages, "expected websocket trace updates"
    assert trace_messages[0]["spans"][0]["name"] == "workflow.execution"
    assert trace_messages[-1]["complete"] is True


@pytest.mark.asyncio
async def test_execute_workflow_langgraph_script_uses_raw_inputs() -> None:
    """LangGraph script executions pass the incoming inputs unchanged."""

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_graph = MagicMock()

    graph_config = {"format": LANGGRAPH_SCRIPT_FORMAT}
    inputs: dict[str, str] = {"input": "raw"}
    execution_id = "script-exec"

    steps = [{"status": "completed"}]
    captured_state: Any | None = None

    async def mock_astream(state: Any, *args: Any, **kwargs: Any):
        nonlocal captured_state
        captured_state = state
        for step in steps:
            yield step

    async def mock_aget_state(*args: Any, **kwargs: Any):
        return MagicMock(values=inputs)

    mock_compiled_graph = MagicMock()
    mock_compiled_graph.astream = mock_astream
    mock_compiled_graph.aget_state = mock_aget_state
    mock_graph.compile.return_value = mock_compiled_graph

    @asynccontextmanager
    async def fake_checkpointer(_settings):
        yield object()

    @asynccontextmanager
    async def fake_graph_store(_settings):
        yield object()

    history_store = InMemoryRunHistoryStore()

    with (
        patch("orcheo_backend.app.create_checkpointer", fake_checkpointer),
        patch("orcheo_backend.app.create_graph_store", fake_graph_store),
        patch("orcheo_backend.app.build_graph", return_value=mock_graph),
        patch("orcheo_backend.app._history_store_ref", {"store": history_store}),
        patch(
            "orcheo_backend.app.workflow_execution.close_browser_sessions_for_scope",
            new_callable=AsyncMock,
        ) as mock_browser_cleanup,
    ):
        await execute_workflow(
            "langgraph-workflow",
            graph_config,
            inputs,
            execution_id,
            mock_websocket,
        )
    mock_browser_cleanup.assert_awaited_once_with(execution_id)

    assert isinstance(captured_state, dict)
    assert captured_state["input"] == "raw"
    assert captured_state["config"]["configurable"]["thread_id"] == execution_id

    history = await history_store.get_history(execution_id)
    assert history.inputs == inputs
    assert history.steps[-1].payload == {"status": "completed"}


@pytest.mark.asyncio
async def test_execute_workflow_failure_records_error() -> None:
    """Failures during execution are captured within the history store."""

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_graph = MagicMock()

    class _FailingStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("boom")

    def failing_astream(*args, **kwargs):
        return _FailingStream()

    mock_compiled_graph = MagicMock()
    mock_compiled_graph.astream = failing_astream
    mock_graph.compile.return_value = mock_compiled_graph

    @asynccontextmanager
    async def fake_checkpointer(_settings):
        yield object()

    @asynccontextmanager
    async def fake_graph_store(_settings):
        yield object()

    history_store = InMemoryRunHistoryStore()

    with (
        patch("orcheo_backend.app.create_checkpointer", fake_checkpointer),
        patch("orcheo_backend.app.create_graph_store", fake_graph_store),
        patch("orcheo_backend.app.build_graph", return_value=mock_graph),
        patch("orcheo_backend.app._history_store_ref", {"store": history_store}),
        patch(
            "orcheo_backend.app.workflow_execution.close_browser_sessions_for_scope",
            new_callable=AsyncMock,
        ) as mock_browser_cleanup,
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await execute_workflow(
                "wf",
                {"nodes": []},
                {"input": "data"},
                "exec-1",
                mock_websocket,
            )
    mock_browser_cleanup.assert_awaited_once_with("exec-1")

    history = await history_store.get_history("exec-1")
    assert history.status == "error"
    assert history.error == "boom"
    assert history.steps[-1].payload == {"status": "error", "error": "boom"}


@pytest.mark.asyncio
async def test_execute_workflow_cancelled_records_reason() -> None:
    """Cancellations propagate the reason and update execution history."""

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_graph = MagicMock()
    cancellation_reason = "client requested stop"

    class _CancellingStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise asyncio.CancelledError(cancellation_reason)

    def cancelling_astream(*args, **kwargs):
        return _CancellingStream()

    mock_compiled_graph = MagicMock()
    mock_compiled_graph.astream = cancelling_astream
    mock_graph.compile.return_value = mock_compiled_graph

    @asynccontextmanager
    async def fake_checkpointer(_settings):
        yield object()

    @asynccontextmanager
    async def fake_graph_store(_settings):
        yield object()

    history_store = InMemoryRunHistoryStore()

    with (
        patch("orcheo_backend.app.create_checkpointer", fake_checkpointer),
        patch("orcheo_backend.app.create_graph_store", fake_graph_store),
        patch("orcheo_backend.app.build_graph", return_value=mock_graph),
        patch("orcheo_backend.app._history_store_ref", {"store": history_store}),
        patch(
            "orcheo_backend.app.workflow_execution.close_browser_sessions_for_scope",
            new_callable=AsyncMock,
        ) as mock_browser_cleanup,
    ):
        with pytest.raises(asyncio.CancelledError):
            await execute_workflow(
                "wf-cancel",
                {"nodes": []},
                {},
                "exec-cancel",
                mock_websocket,
            )
    mock_browser_cleanup.assert_awaited_once_with("exec-cancel")

    history = await history_store.get_history("exec-cancel")
    assert history.status == "cancelled"
    assert history.error == cancellation_reason
    assert len(history.steps) == 1
    assert history.steps[0].payload == {
        "status": "cancelled",
        "reason": cancellation_reason,
    }
