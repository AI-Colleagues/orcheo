"""Tests for the connector listener base implementation."""

from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableConfig

from orcheo.graph.state import State
from orcheo.nodes.connectors.listener_base import ListenerNode


class _TestListenerNode(ListenerNode):
    """Minimal concrete listener node used to exercise the base implementation."""

    platform: str = "test-platform"


def test_extract_listener_payload_returns_empty_for_non_mapping_state() -> None:
    node = _TestListenerNode(name="listener")

    assert node._extract_listener_payload(["invalid-shape"]) == {}


def test_extract_listener_payload_prefers_nested_listener_mapping() -> None:
    node = _TestListenerNode(name="listener")
    state = State(
        {
            "inputs": {
                "listener": {
                    "platform": "test-platform",
                    "event_type": "message",
                    "dedupe_key": "nested-1",
                }
            },
            "node_results": {},
        }
    )

    assert node._extract_listener_payload(state) == {
        "platform": "test-platform",
        "event_type": "message",
        "dedupe_key": "nested-1",
    }


def test_extract_listener_payload_uses_direct_platform_inputs() -> None:
    node = _TestListenerNode(name="listener")
    state = State(
        {
            "inputs": {
                "platform": "test-platform",
                "event_type": "message",
                "chat_id": "direct-1",
            },
            "node_results": {},
        }
    )

    assert node._extract_listener_payload(state) == {
        "platform": "test-platform",
        "event_type": "message",
        "chat_id": "direct-1",
    }


def test_extract_listener_payload_returns_empty_for_non_matching_inputs() -> None:
    node = _TestListenerNode(name="listener")
    state = State(
        {
            "inputs": {
                "listener": "invalid-shape",
                "platform": "other-platform",
            },
            "node_results": {},
        }
    )

    assert node._extract_listener_payload(state) == {}


def test_extract_listener_payload_returns_empty_for_non_mapping_inputs() -> None:
    node = _TestListenerNode(name="listener")
    state = State({"inputs": ["invalid-shape"], "node_results": {}})

    assert node._extract_listener_payload(state) == {}


@pytest.mark.asyncio
async def test_listener_node_run_skips_when_platform_does_not_match() -> None:
    node = _TestListenerNode(name="listener", bot_identity_key="fallback-bot")
    state = State(
        {
            "inputs": {"listener": {"platform": "other-platform"}},
            "node_results": {},
        }
    )

    result = await node.run(state, RunnableConfig())

    assert result == {
        "platform": "test-platform",
        "should_process": False,
        "skipped": True,
        "bot_identity": "fallback-bot",
    }


@pytest.mark.asyncio
async def test_listener_node_run_normalizes_mapping_payload() -> None:
    node = _TestListenerNode(name="listener", bot_identity_key="fallback-bot")
    state = State(
        {
            "inputs": {
                "listener": {
                    "platform": "test-platform",
                    "event_type": "message",
                    "bot_identity": "bot-123",
                    "dedupe_key": "dedupe-1",
                    "message": {
                        "chat_id": "chat-1",
                        "user_id": "user-1",
                        "message_id": "msg-1",
                        "text": "hello",
                    },
                    "reply_target": {"chat_id": "reply-1"},
                    "raw_event": {"update_id": 1},
                    "metadata": {"source": "tests"},
                }
            },
            "node_results": {},
        }
    )

    result = await node.run(state, RunnableConfig())

    assert result == {
        "platform": "test-platform",
        "event_type": "message",
        "should_process": True,
        "bot_identity": "bot-123",
        "message": {
            "chat_id": "chat-1",
            "user_id": "user-1",
            "message_id": "msg-1",
            "text": "hello",
        },
        "reply_target": {"chat_id": "reply-1"},
        "raw_event": {"update_id": 1},
        "metadata": {"source": "tests"},
        "dedupe_key": "dedupe-1",
        "chat_id": "reply-1",
        "text": "hello",
        "user_id": "user-1",
        "message_id": "msg-1",
    }


@pytest.mark.asyncio
async def test_listener_node_run_handles_non_mapping_message_fields() -> None:
    node = _TestListenerNode(name="listener")
    state = State(
        {
            "inputs": {
                "listener": {
                    "platform": "test-platform",
                    "event_type": "message",
                    "message": "not-a-mapping",
                    "reply_target": "also-not-a-mapping",
                    "raw_event": {"update_id": 2},
                    "metadata": {"source": "tests"},
                }
            },
            "node_results": {},
        }
    )

    result = await node.run(state, RunnableConfig())

    assert result["platform"] == "test-platform"
    assert result["should_process"] is False
    assert result["message"] == {}
    assert result["reply_target"] == {}
    assert result["raw_event"] == {"update_id": 2}
    assert result["metadata"] == {"source": "tests"}
    assert result["chat_id"] is None
    assert result["text"] is None
    assert result["user_id"] is None
    assert result["message_id"] is None
