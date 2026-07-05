"""Tests for private listener nodes."""

from __future__ import annotations
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.listeners import (
    DiscordBotListenerNode,
    QQBotListenerNode,
    TelegramBotListenerNode,
)


@pytest.mark.asyncio
async def test_telegram_listener_node_returns_normalized_payload() -> None:
    node = TelegramBotListenerNode(name="telegram_listener")
    state = State(
        {
            "inputs": {
                "listener": {
                    "platform": "telegram",
                    "event_type": "message",
                    "bot_identity": "telegram:[[bot]]",
                    "dedupe_key": "telegram:1",
                    "message": {
                        "chat_id": "123",
                        "user_id": "456",
                        "message_id": "789",
                        "text": "hello",
                    },
                    "reply_target": {"chat_id": "123"},
                    "raw_event": {"update_id": 1},
                    "metadata": {"node_name": "telegram_listener"},
                }
            },
            "node_results": {},
        }
    )

    result = await node.run(state, RunnableConfig())
    assert result["should_process"] is True
    assert result["chat_id"] == "123"
    assert result["text"] == "hello"
    assert result["bot_identity"] == "telegram:[[bot]]"


@pytest.mark.asyncio
async def test_listener_node_skips_mismatched_platform() -> None:
    node = TelegramBotListenerNode(name="telegram_listener")
    state = State({"inputs": {"listener": {"platform": "discord"}}, "node_results": {}})

    result = await node.run(state, RunnableConfig())
    assert result["should_process"] is False
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_listener_node_processes_direct_platform_inputs() -> None:
    node = TelegramBotListenerNode(name="telegram_listener")
    state = State(
        {
            "inputs": {
                "platform": "telegram",
                "event_type": "message",
                "chat_id": "555",
                "message": {"chat_id": "555", "text": "hi"},
            },
            "node_results": {},
        }
    )

    result = await node.run(state, RunnableConfig())
    assert result["should_process"] is True
    assert result["platform"] == "telegram"


@pytest.mark.asyncio
async def test_listener_node_skips_when_inputs_do_not_contain_listener_payload() -> (
    None
):
    node = TelegramBotListenerNode(name="telegram_listener")
    state = State({"inputs": ["invalid-shape"], "node_results": {}})

    result = await node.run(state, RunnableConfig())
    assert result["platform"] == "telegram"
    assert result["should_process"] is False
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_listener_node_platform_mismatch_without_listener_dict() -> None:
    node = TelegramBotListenerNode(name="telegram_listener")
    state = State(
        {
            "inputs": {
                "listener": "invalid-shape",
                "platform": "discord",
            },
            "node_results": {},
        }
    )

    result = await node.run(state, RunnableConfig())
    assert result["platform"] == "telegram"
    assert result["should_process"] is False
    assert result["skipped"] is True


def test_discord_listener_node_defaults_include_dm_intent() -> None:
    node = DiscordBotListenerNode(name="discord_listener")
    assert "direct_messages" in node.intents
    assert node.include_direct_messages is True


def test_qq_listener_node_defaults_cover_message_scenes() -> None:
    node = QQBotListenerNode(name="qq_listener")
    assert node.platform == "qq"
    assert node.allowed_events == [
        "C2C_MESSAGE_CREATE",
        "GROUP_AT_MESSAGE_CREATE",
        "AT_MESSAGE_CREATE",
    ]
    assert node.allowed_scene_types == ["c2c", "group", "channel"]
