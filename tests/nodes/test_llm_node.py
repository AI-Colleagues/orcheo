"""Tests for LLMNode message construction."""

from __future__ import annotations
import contextlib
from typing import Any
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.ai.llm import LLMNode


def _empty_state() -> State:
    return State(
        messages=[],
        inputs={},
        node_results={},
        structured_response=None,
        config=None,
    )


def test_llm_build_messages_formats_user_and_instruction() -> None:
    node = LLMNode(
        name="llm",
        ai_model="test-model",
        draft_reply="  draft reply  ",
        user_message="  user context  ",
        instruction="  Do this  ",
    )
    messages = node._build_messages(_empty_state())
    assert len(messages) == 1
    assert messages[0].content == (
        "Instruction:\n"
        "Do this\n\n"
        "Text:\n"
        "User message:\n"
        "user context\n\n"
        "Draft reply:\n"
        "draft reply"
    )


def test_llm_build_messages_without_user_message_or_instruction() -> None:
    node = LLMNode(
        name="llm",
        ai_model="test-model",
        input_text="  just input  ",
    )
    messages = node._build_messages(_empty_state())
    assert len(messages) == 1
    assert messages[0].content == "just input"


def test_llm_build_messages_returns_empty_when_no_text() -> None:
    node = LLMNode(name="llm", ai_model="test-model")
    assert node._build_messages(_empty_state()) == []


def test_llm_normalize_text_trims_and_handles_none() -> None:
    assert LLMNode._normalize_text("  trimmed  ") == "trimmed"
    assert LLMNode._normalize_text(None) == ""


@pytest.mark.asyncio
async def test_llm_run_returns_empty_when_no_messages(monkeypatch) -> None:
    node = LLMNode(name="llm", ai_model="test-model")
    monkeypatch.setattr(node, "_build_messages", lambda state: [])

    result = await node.run(_empty_state(), RunnableConfig())
    assert result == {"messages": []}


@pytest.mark.asyncio
async def test_llm_run_uses_response_format(monkeypatch) -> None:
    node = LLMNode(
        name="llm",
        ai_model="test-model",
        response_format={"type": "json"},
    )
    monkeypatch.setattr(
        node, "_build_messages", lambda state: [HumanMessage(content="hi")]
    )

    fake_agent = AsyncMock()
    fake_agent.ainvoke.return_value = {"messages": []}

    monkeypatch.setattr(
        "orcheo.nodes.ai.init_chat_model", lambda *args, **kwargs: "model"
    )
    called: dict[str, Any] = {}

    def fake_provider_strategy(fmt: dict[str, Any]) -> str:
        called["format"] = fmt
        return "strategy"

    monkeypatch.setattr("orcheo.nodes.ai.ProviderStrategy", fake_provider_strategy)

    def fake_create_agent(model, tools, system_prompt=None, response_format=None):
        assert response_format == "strategy"
        return fake_agent

    monkeypatch.setattr("orcheo.nodes.ai.create_agent", fake_create_agent)
    monkeypatch.setattr(
        "orcheo.nodes.ai.tool_execution_context", contextlib.nullcontext
    )

    result = await node.run(_empty_state(), RunnableConfig())
    assert result == {"messages": []}
    assert called["format"] == {"type": "json"}
