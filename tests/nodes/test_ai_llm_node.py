"""LLMNode message construction tests."""

from __future__ import annotations
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.ai.llm import LLMNode


@pytest.mark.asyncio
async def test_llmnode_run_builds_single_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLMNode should wrap input text and instruction into one message."""

    fake_agent = AsyncMock()
    fake_agent.ainvoke.return_value = {"messages": [AIMessage(content="done")]}

    async def fake_prepare_tools(self: LLMNode):  # type: ignore[unused-argument]
        return []

    def fake_init_chat_model(*args, **kwargs):
        return "model"

    def fake_create_agent(model, tools, system_prompt=None, response_format=None):
        return fake_agent

    monkeypatch.setattr("orcheo.nodes.ai.init_chat_model", fake_init_chat_model)
    monkeypatch.setattr("orcheo.nodes.ai.create_agent", fake_create_agent)
    monkeypatch.setattr(LLMNode, "_prepare_tools", fake_prepare_tools)

    node = LLMNode(
        name="llm",
        ai_model="test-model",
        user_message="Bonjour",
        draft_reply="Hello there",
        instruction="Translate to French",
    )
    state = State(inputs={}, node_results={}, structured_response=None)

    result = await node.run(state, {})

    assert result == {"messages": [AIMessage(content="done")]}
    payload = fake_agent.ainvoke.await_args.args[0]
    messages = payload["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == (
        "Instruction:\nTranslate to French\n\nText:\nUser message:\nBonjour\n\n"
        "Draft reply:\nHello there"
    )


def test_llmnode_build_messages_skips_empty_input() -> None:
    node = LLMNode(name="llm", ai_model="test-model", input_text="  ")
    state = State(inputs={}, node_results={}, structured_response=None)
    assert node._build_messages(state) == []


def test_llmnode_build_messages_no_user_message_no_instruction() -> None:
    """input_text without user_message or instruction hits the else
    branches (lines 493, 499).
    """
    node = LLMNode(name="llm", ai_model="test-model", input_text="plain text")
    state = State(inputs={}, node_results={}, structured_response=None)
    messages = node._build_messages(state)
    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "plain text"


@pytest.mark.asyncio
async def test_llmnode_run_returns_empty_when_no_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLMNode.run returns early with empty messages when _build_messages
    yields [] (line 455).
    """

    async def fake_prepare_tools(self: LLMNode):  # type: ignore[unused-argument]
        return []

    monkeypatch.setattr(LLMNode, "_prepare_tools", fake_prepare_tools)

    node = LLMNode(name="llm", ai_model="test-model")
    state = State(inputs={}, node_results={}, structured_response=None)
    result = await node.run(state, {})
    assert result == {"messages": []}


@pytest.mark.asyncio
async def test_llmnode_run_with_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLMNode.run sets response_format_strategy when response_format is
    provided (line 461).
    """
    from pydantic import BaseModel

    class Schema(BaseModel):
        answer: str

    fake_agent = AsyncMock()
    fake_agent.ainvoke.return_value = {"messages": [AIMessage(content="done")]}

    async def fake_prepare_tools(self: LLMNode):  # type: ignore[unused-argument]
        return []

    def fake_init_chat_model(*args, **kwargs):
        return "model"

    captured: dict = {}

    def fake_create_agent(model, tools, system_prompt=None, response_format=None):
        captured["response_format"] = response_format
        return fake_agent

    monkeypatch.setattr("orcheo.nodes.ai.init_chat_model", fake_init_chat_model)
    monkeypatch.setattr("orcheo.nodes.ai.create_agent", fake_create_agent)
    monkeypatch.setattr(LLMNode, "_prepare_tools", fake_prepare_tools)

    node = LLMNode(
        name="llm",
        ai_model="test-model",
        input_text="hello",
        response_format=Schema,
    )
    state = State(inputs={}, node_results={}, structured_response=None)
    result = await node.run(state, {})
    assert result == {"messages": [AIMessage(content="done")]}
    assert captured["response_format"] is not None


@pytest.mark.asyncio
async def test_llmnode_call_attaches_actual_model_trace_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLMNode.__call__ should expose requested and actual model metadata."""

    fake_agent = AsyncMock()
    fake_agent.ainvoke.return_value = {
        "messages": [
            AIMessage(
                content="done",
                response_metadata={"model_name": "gpt-4o-mini-2024-07-18"},
                usage_metadata={
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                },
            )
        ]
    }

    async def fake_prepare_tools(self: LLMNode):  # type: ignore[unused-argument]
        return []

    monkeypatch.setattr("orcheo.nodes.ai.init_chat_model", lambda *args, **kwargs: "m")
    monkeypatch.setattr(
        "orcheo.nodes.ai.create_agent", lambda *args, **kwargs: fake_agent
    )
    monkeypatch.setattr(LLMNode, "_prepare_tools", fake_prepare_tools)

    node = LLMNode(name="llm", ai_model="openai:gpt-4o-mini", input_text="hello")
    state = State(inputs={}, node_results={}, structured_response=None)

    result = await node(state, RunnableConfig())

    assert result["__trace"]["ai"]["kind"] == "llm"
    assert result["__trace"]["ai"]["requested_model"] == "openai:gpt-4o-mini"
    assert result["__trace"]["ai"]["actual_model"] == "gpt-4o-mini-2024-07-18"
    assert result["__trace"]["ai"]["provider"] == "openai"
