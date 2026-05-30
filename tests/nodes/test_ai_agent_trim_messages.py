"""Tests for AgentNode._trim_messages tool-pair-aware history trimming.

Regression coverage for the OpenAI 400 "messages with role 'tool' must be a
response to a preceeding message with 'tool_calls'" error: a plain tail slice of
the conversation could keep a ToolMessage whose parent AIMessage was trimmed off,
or keep a trailing AIMessage whose tool_calls had no following ToolMessage.
"""

from __future__ import annotations

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from orcheo.nodes.ai import AgentNode


def _make_node(max_messages: int) -> AgentNode:
    return AgentNode(name="agent", ai_model="gpt-4o-mini", max_messages=max_messages)


def _ai_with_tool_call(call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "do", "args": {}, "id": call_id}],
    )


def test_trim_drops_leading_orphan_tool_message() -> None:
    """A window that would start on a ToolMessage drops the orphan."""
    node = _make_node(max_messages=3)
    messages: list[BaseMessage] = [
        _ai_with_tool_call("c1"),
        ToolMessage(content="result", tool_call_id="c1"),
        HumanMessage(content="next"),
        AIMessage(content="reply"),
    ]

    # Plain tail slice would be [ToolMessage, Human, AIMessage] -> orphan at front.
    trimmed = node._trim_messages(messages)

    assert not isinstance(trimmed[0], ToolMessage)
    assert [type(m).__name__ for m in trimmed] == ["HumanMessage", "AIMessage"]


def test_trim_drops_trailing_dangling_tool_call() -> None:
    """A trailing AIMessage with unanswered tool_calls is dropped."""
    node = _make_node(max_messages=5)
    messages: list[BaseMessage] = [
        HumanMessage(content="hi"),
        _ai_with_tool_call("c2"),
    ]

    trimmed = node._trim_messages(messages)

    assert [type(m).__name__ for m in trimmed] == ["HumanMessage"]


def test_trim_keeps_complete_tool_pair() -> None:
    """A complete AIMessage/ToolMessage pair inside the window is preserved."""
    node = _make_node(max_messages=4)
    ai = _ai_with_tool_call("c3")
    tool = ToolMessage(content="ok", tool_call_id="c3")
    messages: list[BaseMessage] = [
        SystemMessage(content="sys"),
        HumanMessage(content="u1"),
        ai,
        tool,
        HumanMessage(content="u2"),
    ]

    trimmed = node._trim_messages(messages)

    # Last 4 = [u1, ai, tool, u2]; pair is intact, no leading/trailing orphan.
    assert trimmed == messages[-4:]
    assert not isinstance(trimmed[0], ToolMessage)


def test_trim_no_op_under_limit() -> None:
    """Histories at or under the limit are returned unchanged."""
    node = _make_node(max_messages=10)
    messages: list[BaseMessage] = [
        HumanMessage(content="u1"),
        AIMessage(content="a1"),
    ]

    assert node._trim_messages(messages) == messages
