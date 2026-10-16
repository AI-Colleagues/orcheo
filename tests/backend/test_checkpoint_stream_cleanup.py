"""Checkpoint cleanup must wait for LangGraph streams to finish closing."""

from __future__ import annotations
import asyncio
import importlib
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, nullcontext
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from opentelemetry.trace import INVALID_SPAN, get_tracer
from orcheo_backend.app import _workflow_execution_module as execution
from orcheo_backend.app.chatkit import workflow_executor as chatkit
from orcheo_backend.worker.tasks import _run_to_final_state


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "cancel"])
@pytest.mark.parametrize(
    "runner",
    ["worker", "websocket", "chatkit_run", "chatkit_thread", "chatkit_deleted_thread"],
)
async def test_consumer_failure_closes_stream_before_checkpoint_cleanup(
    monkeypatch: pytest.MonkeyPatch, runner: str, failure: str
) -> None:
    """Fail while handling a yielded update, when the graph is still suspended."""
    saver = InMemorySaver()
    graph = StateGraph(dict)
    graph.add_node("first", lambda state: {"reply": "first"})
    graph.add_node("second", lambda state: {"reply": "second"})
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)
    compiled = graph.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "exec"}}
    streams: list[Any] = []
    original_astream = compiled.astream

    def astream(*args: Any, **kwargs: Any) -> Any:
        stream = original_astream(*args, **kwargs)
        # Keep a reference so garbage collection cannot mask missing aclose().
        streams.append(stream)
        return stream

    monkeypatch.setattr(compiled, "astream", astream)
    monkeypatch.setattr(graph, "compile", lambda **kwargs: compiled)
    checkpoints_at_cleanup: list[int] = []
    original_delete = saver.adelete_thread

    async def delete(thread_id: str) -> None:
        checkpoints_at_cleanup.append(len(list(saver.list(config))))
        await original_delete(thread_id)

    async def compact(*args: Any, **kwargs: Any) -> None:
        checkpoints_at_cleanup.append(len(list(saver.list(config))))

    monkeypatch.setattr(saver, "adelete_thread", delete)
    monkeypatch.setattr(saver, "aprune", AsyncMock(side_effect=compact))
    consuming = asyncio.Event()

    async def consume(*args: Any) -> None:
        step = args[-1]
        if isinstance(step, Mapping) and "first" in step:
            consuming.set()
            if failure == "error":
                raise RuntimeError("consumer failed")
            await asyncio.Future()

    history = AsyncMock()
    history.append_step.side_effect = consume

    @asynccontextmanager
    async def checkpointer_context(*args: Any) -> AsyncIterator[Any]:
        yield saver

    @asynccontextmanager
    async def store_context(*args: Any) -> AsyncIterator[None]:
        yield None

    if runner == "worker":
        run = _run_to_final_state(
            compiled=compiled,
            checkpointer=saver,
            state={},
            runtime_config=config,
            history_store=history,
            execution_id="exec",
            history_error_cls=ValueError,
        )
    elif runner == "websocket":
        backend_app = importlib.import_module("orcheo_backend.app")

        monkeypatch.setattr(backend_app, "build_graph", lambda config: graph)
        monkeypatch.setattr(backend_app, "create_checkpointer", checkpointer_context)
        monkeypatch.setattr(backend_app, "create_graph_store", store_context)
        monkeypatch.setattr(execution, "credential_resolution", lambda _: nullcontext())
        monkeypatch.setattr(execution, "_build_initial_state", lambda *args: {})
        monkeypatch.setattr(execution, "_emit_trace_update", AsyncMock())
        run = execution._execute_trusted_workflow_in_worker(
            settings={},
            graph_config={},
            inputs={},
            runtime_config=config,
            state_config={},
            history_store=history,
            websocket=AsyncMock(),
            execution_id="exec",
            tracer=get_tracer(__name__),
            span=INVALID_SPAN,
            resolver=object(),
            workspace_id=None,
        )
    else:
        monkeypatch.setattr(chatkit, "get_settings", lambda: {})
        monkeypatch.setattr(chatkit, "build_graph", lambda config: graph)
        monkeypatch.setattr(chatkit, "create_checkpointer", checkpointer_context)
        monkeypatch.setattr(chatkit, "create_graph_store", store_context)
        monkeypatch.setattr(chatkit, "build_initial_state", lambda *args, **kwargs: {})
        monkeypatch.setattr(chatkit, "CredentialResolver", lambda *args, **kwargs: None)
        monkeypatch.setattr(chatkit, "credential_resolution", lambda _: nullcontext())
        exists = AsyncMock(return_value=runner != "chatkit_deleted_thread")
        executor = chatkit.WorkflowExecutor(
            repository=MagicMock(), vault_provider=lambda: None, thread_exists=exists
        )
        run = executor._execute_graph(
            workflow_id=UUID(int=0),
            graph_config={},
            inputs={} if runner == "chatkit_run" else {"thread_id": "exec"},
            config=config,
            state_config={},
            step_callback=consume,
        )

    task = asyncio.create_task(run)
    try:
        await asyncio.wait_for(consuming.wait(), timeout=5)
        if failure == "cancel":
            task.cancel()
        with pytest.raises(
            asyncio.CancelledError if failure == "cancel" else RuntimeError,
            match=None if failure == "cancel" else "consumer failed",
        ):
            await task

        # Exit durability writes exactly one checkpoint during stream closure.
        assert checkpoints_at_cleanup == [1]
        assert len(list(saver.list(config))) == (1 if runner == "chatkit_thread" else 0)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for stream in streams:
            await stream.aclose()
