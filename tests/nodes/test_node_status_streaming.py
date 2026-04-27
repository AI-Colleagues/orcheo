"""Tests for the in-node status streaming API."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.agent_tools.context import (
    emit_node_status,
    get_active_node_status_emitter,
    node_status_context,
    tool_progress_context,
)
from orcheo.nodes.base import AINode, TaskNode


class _StatusReportingTaskNode(TaskNode):
    """Task node that streams optional status updates from ``run``."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        del state, config
        await emit_node_status("starting")
        await emit_node_status(
            "progress",
            payload={"completed": 1, "total": 3},
        )
        await emit_node_status(message="finalising", percent=100)
        return {"value": "done"}


class _StatusReportingAINode(AINode):
    """AI node that streams optional status updates from ``run``."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        del state, config
        await emit_node_status("thinking")
        return {"messages": []}


@pytest.mark.asyncio
async def test_emit_node_status_is_noop_when_no_emitter_bound() -> None:
    assert get_active_node_status_emitter() is None
    # Should not raise even without a configured emitter.
    await emit_node_status("ignored", payload={"x": 1})


@pytest.mark.asyncio
async def test_node_status_context_binds_and_resets_emitter() -> None:
    received: list[Mapping[str, Any]] = []

    async def emitter(body: Mapping[str, Any]) -> None:
        received.append(dict(body))

    with node_status_context(emitter):
        assert get_active_node_status_emitter() is emitter
        await emit_node_status("running", payload={"step": "load"}, batch=4)

    assert get_active_node_status_emitter() is None
    assert received == [{"status": "running", "step": "load", "batch": 4}]


@pytest.mark.asyncio
async def test_emit_node_status_with_no_arguments() -> None:
    received: list[Mapping[str, Any]] = []

    async def emitter(body: Mapping[str, Any]) -> None:
        received.append(dict(body))

    with node_status_context(emitter):
        await emit_node_status()

    assert received == [{}]


@pytest.mark.asyncio
async def test_emit_node_status_preserves_existing_status_key() -> None:
    received: list[Mapping[str, Any]] = []

    async def emitter(body: Mapping[str, Any]) -> None:
        received.append(dict(body))

    with node_status_context(emitter):
        await emit_node_status("ignored", payload={"status": "explicit"})

    assert received == [{"status": "explicit"}]


@pytest.mark.asyncio
async def test_task_node_call_streams_status_through_tool_progress_callback() -> None:
    received: list[Mapping[str, Any]] = []

    async def progress(step: Mapping[str, Any]) -> None:
        received.append(dict(step))

    node = _StatusReportingTaskNode(name="reporter")

    with tool_progress_context(progress):
        result = await node(State({"results": {}}), RunnableConfig())

    assert result == {"results": {"reporter": {"value": "done"}}}
    assert [step["event"] for step in received] == ["node_status"] * 3
    assert all(step["node"] == "reporter" for step in received)
    payloads = [step["payload"] for step in received]
    assert payloads[0] == {"status": "starting"}
    assert payloads[1] == {"status": "progress", "completed": 1, "total": 3}
    assert payloads[2] == {"message": "finalising", "percent": 100}


@pytest.mark.asyncio
async def test_ai_node_call_streams_status_through_tool_progress_callback() -> None:
    received: list[Mapping[str, Any]] = []

    async def progress(step: Mapping[str, Any]) -> None:
        received.append(dict(step))

    node = _StatusReportingAINode(name="responder")

    with tool_progress_context(progress):
        await node(State({"results": {}}), RunnableConfig())

    assert received == [
        {
            "node": "responder",
            "event": "node_status",
            "payload": {"status": "thinking"},
        }
    ]


@pytest.mark.asyncio
async def test_node_call_does_not_emit_when_no_progress_callback_bound() -> None:
    node = _StatusReportingTaskNode(name="reporter")
    # Without a tool progress context, the emitter is None and run() is a no-op
    # for the streaming side effects.
    result = await node(State({"results": {}}), RunnableConfig())
    assert result == {"results": {"reporter": {"value": "done"}}}


@pytest.mark.asyncio
async def test_node_status_context_resets_after_run_exception() -> None:
    received: list[Mapping[str, Any]] = []

    async def progress(step: Mapping[str, Any]) -> None:
        received.append(dict(step))

    class _FailingNode(TaskNode):
        async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            del state, config
            await emit_node_status("about_to_fail")
            raise RuntimeError("boom")

    node = _FailingNode(name="failing")

    with tool_progress_context(progress):
        with pytest.raises(RuntimeError, match="boom"):
            await node(State({"results": {}}), RunnableConfig())
        # Emitter must be cleared even when run() raised.
        assert get_active_node_status_emitter() is None

    assert received == [
        {
            "node": "failing",
            "event": "node_status",
            "payload": {"status": "about_to_fail"},
        }
    ]
