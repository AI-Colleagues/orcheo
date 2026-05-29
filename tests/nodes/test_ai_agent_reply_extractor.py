"""Tests for AgentReplyExtractorNode (lines 417-429 of ai.py)."""

from __future__ import annotations
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentReplyExtractorNode


@pytest.mark.asyncio
async def test_extractor_returns_dict_assistant_content() -> None:
    """Dict-style assistant message: returns its content string (lines 419-423)."""
    node = AgentReplyExtractorNode(name="extractor")
    state: State = {
        "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ],
        "inputs": {},
        "results": {},
        "structured_response": None,
    }
    result = await node.run(state, RunnableConfig())
    assert result == {"agent_reply": "Hi there!"}


@pytest.mark.asyncio
async def test_extractor_skips_dict_assistant_with_empty_content() -> None:
    """Dict assistant message with empty content is skipped; fallback is
    returned (line 422 false branch).
    """
    node = AgentReplyExtractorNode(name="extractor")
    state: State = {
        "messages": [{"role": "assistant", "content": ""}],
        "inputs": {},
        "results": {},
        "structured_response": None,
    }
    result = await node.run(state, RunnableConfig())
    assert result == {"agent_reply": node.fallback_message}


@pytest.mark.asyncio
async def test_extractor_skips_dict_non_assistant_role() -> None:
    """Dict message with non-assistant role is not extracted (line 420 false branch)."""
    node = AgentReplyExtractorNode(name="extractor")
    state: State = {
        "messages": [{"role": "user", "content": "Question"}],
        "inputs": {},
        "results": {},
        "structured_response": None,
    }
    result = await node.run(state, RunnableConfig())
    assert result == {"agent_reply": node.fallback_message}


@pytest.mark.asyncio
async def test_extractor_returns_ai_message_string_content() -> None:
    """AIMessage with string content is returned directly (lines 424-428,
    isinstance str True).
    """
    node = AgentReplyExtractorNode(name="extractor")
    state: State = {
        "messages": [HumanMessage(content="Q"), AIMessage(content="Answer")],
        "inputs": {},
        "results": {},
        "structured_response": None,
    }
    result = await node.run(state, RunnableConfig())
    assert result == {"agent_reply": "Answer"}


@pytest.mark.asyncio
async def test_extractor_stringifies_ai_message_non_string_content() -> None:
    """AIMessage with non-string content is converted via str() (line 427
    str() branch).
    """
    node = AgentReplyExtractorNode(name="extractor")
    content = [{"type": "text", "text": "structured"}]
    state: State = {
        "messages": [AIMessage(content=content)],  # type: ignore[list-item]
        "inputs": {},
        "results": {},
        "structured_response": None,
    }
    result = await node.run(state, RunnableConfig())
    assert result == {"agent_reply": str(content)}


@pytest.mark.asyncio
async def test_extractor_returns_fallback_when_no_assistant_message() -> None:
    """Fallback message returned when messages list has no assistant turn (line 429)."""
    node = AgentReplyExtractorNode(
        name="extractor", fallback_message="No reply available"
    )
    state: State = {
        "messages": [HumanMessage(content="Question")],
        "inputs": {},
        "results": {},
        "structured_response": None,
    }
    result = await node.run(state, RunnableConfig())
    assert result == {"agent_reply": "No reply available"}


@pytest.mark.asyncio
async def test_extractor_returns_fallback_for_empty_messages() -> None:
    """Fallback returned when messages list is empty (line 417-418, 429)."""
    node = AgentReplyExtractorNode(name="extractor")
    state: State = {
        "messages": [],
        "inputs": {},
        "results": {},
        "structured_response": None,
    }
    result = await node.run(state, RunnableConfig())
    assert result["agent_reply"] == node.fallback_message


@pytest.mark.asyncio
async def test_extractor_returns_trailing_tool_message() -> None:
    """A turn ending on a ToolMessage (return_direct) surfaces its output verbatim."""
    node = AgentReplyExtractorNode(name="extractor")
    state: State = {
        "messages": [
            HumanMessage(content="run it"),
            AIMessage(content="", tool_calls=[]),
            ToolMessage(content="# Codebook\n...", tool_call_id="c1", name="gen"),
        ],
        "inputs": {},
        "results": {},
        "structured_response": None,
    }
    result = await node.run(state, RunnableConfig())
    assert result == {"agent_reply": "# Codebook\n..."}


@pytest.mark.asyncio
async def test_extractor_prefers_trailing_ai_over_earlier_tool() -> None:
    """Normal turns ending on an AI message keep the assistant reply (unchanged)."""
    node = AgentReplyExtractorNode(name="extractor")
    state: State = {
        "messages": [
            HumanMessage(content="q"),
            ToolMessage(content="raw tool output", tool_call_id="c1", name="t"),
            AIMessage(content="Here is my summary."),
        ],
        "inputs": {},
        "results": {},
        "structured_response": None,
    }
    result = await node.run(state, RunnableConfig())
    assert result == {"agent_reply": "Here is my summary."}


@pytest.mark.asyncio
async def test_extractor_returns_trailing_dict_tool_message() -> None:
    """Dict-style trailing tool message also surfaces verbatim."""
    node = AgentReplyExtractorNode(name="extractor")
    state: State = {
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "tool", "content": "report text"},
        ],
        "inputs": {},
        "results": {},
        "structured_response": None,
    }
    result = await node.run(state, RunnableConfig())
    assert result == {"agent_reply": "report text"}
