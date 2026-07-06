"""Tests for Telegram node."""

import json
from unittest.mock import AsyncMock, patch
import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel
from telegram import Message
from orcheo.graph.state import State
from orcheo.nodes.connectors.telegram import (
    MessageTelegram,
    TelegramEventsParserNode,
    escape_markdown,
)


def test_escape_markdown():
    """Test markdown escaping function."""
    text = "Hello! This is a *bold* _italic_ [text](http://example.com)"
    escaped = escape_markdown(text)
    assert (
        escaped
        == "Hello\\! This is a \\*bold\\* \\_italic\\_ \\[text\\]\\(http://example\\.com\\)"
    )


@pytest.fixture
def telegram_node():
    return MessageTelegram(
        name="telegram_node",
        token="test_token",
        chat_id="123456",
        message="Test message!",
    )


@pytest.mark.asyncio
async def test_telegram_node_send_message(telegram_node):
    mock_message = AsyncMock(spec=Message)
    mock_message.message_id = 42

    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(return_value=mock_message)

    with patch("orcheo.nodes.telegram.Bot", return_value=mock_bot):
        result = await telegram_node.run(State(), None)

        assert result == {"message_id": 42, "status": "sent"}
        mock_bot.send_message.assert_called_once_with(
            chat_id="123456", text="Test message!", parse_mode=None
        )


@pytest.mark.asyncio
async def test_telegram_node_error_handling(telegram_node):
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(
        side_effect=Exception("Bad Request: message text is empty")
    )

    with patch("orcheo.nodes.telegram.Bot", return_value=mock_bot):
        with pytest.raises(
            ValueError, match="Telegram API error: Bad Request: message text is empty"
        ):
            await telegram_node.run(State(), None)


@pytest.mark.asyncio
async def test_telegram_node_send_message_with_parse_mode():
    telegram_node = MessageTelegram(
        name="telegram_node",
        token="test_token",
        chat_id="123456",
        message="Test message!",
        parse_mode="MarkdownV2",
    )
    mock_message = AsyncMock(spec=Message)
    mock_message.message_id = 42

    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(return_value=mock_message)

    with patch("orcheo.nodes.telegram.Bot", return_value=mock_bot):
        result = await telegram_node.run(State(), None)

        assert result == {"message_id": 42, "status": "sent"}
        mock_bot.send_message.assert_called_once_with(
            chat_id="123456",
            text="Test message!",
            parse_mode="MarkdownV2",
        )


@pytest.mark.asyncio
async def test_telegram_node_uses_latest_ai_message_when_message_missing() -> None:
    telegram_node = MessageTelegram(
        name="telegram_node",
        token="test_token",
        chat_id="123456",
    )
    mock_message = AsyncMock(spec=Message)
    mock_message.message_id = 99

    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(return_value=mock_message)
    state = State(
        {
            "messages": [{"role": "assistant", "content": "From agent"}],
            "node_results": {},
            "inputs": {},
        }
    )

    with patch("orcheo.nodes.telegram.Bot", return_value=mock_bot):
        result = await telegram_node.run(state, None)

    assert result == {"message_id": 99, "status": "sent"}
    mock_bot.send_message.assert_called_once_with(
        chat_id="123456", text="From agent", parse_mode=None
    )


def test_message_from_state_handles_ai_messages() -> None:
    state = State({"messages": [AIMessage(content="  trimmed  ")]})
    assert MessageTelegram._message_from_state(state) == "  trimmed  "


def test_message_from_state_casts_non_str_content() -> None:
    state = State({"messages": [AIMessage(content=[{"k": "v"}])]})
    assert MessageTelegram._message_from_state(state) == "[{'k': 'v'}]"


@pytest.mark.asyncio
async def test_telegram_node_tool_run(telegram_node):
    mock_message = AsyncMock(spec=Message)
    mock_message.message_id = 42

    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(return_value=mock_message)

    with patch("orcheo.nodes.telegram.Bot", return_value=mock_bot):
        with patch("asyncio.run") as mock_asyncio_run:
            # Mock asyncio.run to directly call the async function
            async def mock_run(coro):
                return await coro

            mock_asyncio_run.side_effect = mock_run

            result = await telegram_node.tool_arun("123456", "Test message!")
    assert result == {"message_id": 42, "status": "sent"}
    mock_bot.send_message.assert_called_once_with(
        chat_id="123456", text="Test message!", parse_mode=None
    )


@pytest.mark.asyncio
async def test_telegram_node_requires_message_content() -> None:
    telegram_node = MessageTelegram(
        name="telegram_node",
        token="test_token",
        chat_id="123456",
    )
    with pytest.raises(ValueError, match="Telegram message content is required"):
        await telegram_node.run(State({"node_results": {}}), None)


# ---------------------------------------------------------------------------
# TelegramEventsParserNode helpers
# ---------------------------------------------------------------------------


def _make_parser(**kwargs) -> TelegramEventsParserNode:
    return TelegramEventsParserNode(name="parser", **kwargs)


def _message_state(body: object, headers: dict | None = None) -> State:
    return State(
        {"inputs": {"headers": headers or {}, "body": body}, "node_results": {}}
    )


# ---------------------------------------------------------------------------
# _extract_inputs
# ---------------------------------------------------------------------------


def test_parser_extract_inputs_basemodel_state() -> None:
    """BaseModel state uses model_dump() and returns the inputs mapping."""

    class FakeState(BaseModel):
        inputs: dict = {}

    node = _make_parser()
    result = node._extract_inputs(FakeState(inputs={"body": {}, "x": 1}))  # type: ignore[arg-type]
    assert result == {"body": {}, "x": 1}


def test_parser_extract_inputs_non_mapping_state() -> None:
    """A non-Mapping, non-BaseModel state returns an empty dict."""
    node = _make_parser()
    assert node._extract_inputs(42) == {}  # type: ignore[arg-type]


def test_parser_extract_inputs_non_mapping_inputs() -> None:
    """When the inputs value is not a Mapping the whole state dict is returned."""
    node = _make_parser()
    state = {"inputs": "flat_string", "other": 1}
    result = node._extract_inputs(state)  # type: ignore[arg-type]
    assert result == {"inputs": "flat_string", "other": 1}


# ---------------------------------------------------------------------------
# _verify_secret_token
# ---------------------------------------------------------------------------


def test_parser_verify_secret_token_no_secret() -> None:
    """No secret configured – any headers pass without raising."""
    node = _make_parser()
    node._verify_secret_token({"X-Telegram-Bot-Api-Secret-Token": "anything"})


def test_parser_verify_secret_token_matching() -> None:
    """Correct token in headers does not raise."""
    node = _make_parser(secret_token="s3cr3t")
    node._verify_secret_token({"x-telegram-bot-api-secret-token": "s3cr3t"})


def test_parser_verify_secret_token_mismatch() -> None:
    """Wrong token raises ValueError."""
    node = _make_parser(secret_token="s3cr3t")
    with pytest.raises(ValueError, match="secret token verification failed"):
        node._verify_secret_token({"x-telegram-bot-api-secret-token": "wrong"})


# ---------------------------------------------------------------------------
# _detect_update_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update_type",
    [
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "callback_query",
        "inline_query",
        "my_chat_member",
        "chat_member",
        "chat_join_request",
    ],
)
def test_parser_detect_update_type_known(update_type: str) -> None:
    node = _make_parser()
    assert node._detect_update_type({update_type: {}}) == update_type


def test_parser_detect_update_type_unknown() -> None:
    node = _make_parser()
    assert node._detect_update_type({"unknown_key": {}}) is None


# ---------------------------------------------------------------------------
# _parse_body
# ---------------------------------------------------------------------------


def test_parser_parse_body_dict() -> None:
    node = _make_parser()
    body = {"update_id": 1}
    assert node._parse_body({"body": body}) == body


def test_parser_parse_body_valid_json_string() -> None:
    node = _make_parser()
    assert node._parse_body({"body": '{"update_id": 1}'}) == {"update_id": 1}


def test_parser_parse_body_invalid_json_string() -> None:
    node = _make_parser()
    assert node._parse_body({"body": "not json {"}) == {}


def test_parser_parse_body_non_dict_non_string() -> None:
    """A list body (valid JSON type but not a dict) returns {}."""
    node = _make_parser()
    assert node._parse_body({"body": [1, 2, 3]}) == {}


# ---------------------------------------------------------------------------
# _extract_update_details
# ---------------------------------------------------------------------------


def test_parser_extract_update_details_non_dict_msg() -> None:
    """When the update value is not a dict, msg/chat/sender default to {}."""
    node = _make_parser()
    msg, chat, sender, text = node._extract_update_details(
        {"message": "not_a_dict"}, "message"
    )
    assert msg == {}
    assert chat == {}
    assert sender == {}
    assert text == ""


def test_parser_extract_update_details_non_dict_fields() -> None:
    """Non-dict chat/sender and non-str text are coerced to safe defaults."""
    node = _make_parser()
    payload: dict = {"message": {"chat": "bad_chat", "from": 99, "text": 42}}
    msg, chat, sender, text = node._extract_update_details(payload, "message")
    assert chat == {}
    assert sender == {}
    assert text == "42"


def test_parser_extract_update_details_callback_query() -> None:
    """callback_query extracts chat from nested message and text from data."""
    node = _make_parser()
    payload: dict = {
        "callback_query": {
            "message": {"chat": {"id": 7, "type": "private"}},
            "from": {"id": 1, "first_name": "Alice"},
            "data": "btn_click",
        }
    }
    msg, chat, sender, text = node._extract_update_details(payload, "callback_query")
    assert chat == {"id": 7, "type": "private"}
    assert text == "btn_click"


def test_parser_extract_update_details_callback_non_dict_callback_msg() -> None:
    """When the callback_query's nested message is not a dict, chat defaults to {}."""
    node = _make_parser()
    payload: dict = {
        "callback_query": {
            "message": "not_a_dict",
            "from": {"id": 1},
            "data": "click",
        }
    }
    msg, chat, sender, text = node._extract_update_details(payload, "callback_query")
    assert chat == {}
    assert text == "click"


# ---------------------------------------------------------------------------
# run() integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parser_run_valid_message() -> None:
    """Happy path: a private message is fully parsed and should_process=True."""
    state = _message_state(
        body={
            "update_id": 1,
            "message": {
                "message_id": 10,
                "from": {"id": 100, "first_name": "Alice"},
                "chat": {"id": -1, "type": "private"},
                "text": "Hello",
            },
        }
    )
    node = _make_parser()
    result = await node(state, None)
    payload = result["node_results"]["parser"]

    assert payload["should_process"] is True
    assert payload["update_type"] == "message"
    assert payload["chat_id"] == "-1"
    assert payload["username"] == "Alice"
    assert payload["text"] == "Hello"
    assert payload["message_id"] == 10


@pytest.mark.asyncio
async def test_parser_run_no_update_type() -> None:
    """Empty body produces should_process=False with update_type=None."""
    state = _message_state(body={})
    node = _make_parser()
    result = await node(state, None)
    payload = result["node_results"]["parser"]

    assert payload["should_process"] is False
    assert payload["update_type"] is None
    assert payload["chat_id"] is None


@pytest.mark.asyncio
async def test_parser_run_disallowed_update_type() -> None:
    """Update type present but not in allowed list → should_process=False."""
    state = _message_state(body={"channel_post": {"text": "news"}})
    node = _make_parser(allowed_update_types=["message"])
    result = await node(state, None)
    payload = result["node_results"]["parser"]

    assert payload["should_process"] is False
    assert payload["update_type"] == "channel_post"


@pytest.mark.asyncio
async def test_parser_run_disallowed_chat_type() -> None:
    """Chat type not in allowed list → should_process=False with chat_id set."""
    state = _message_state(
        body={
            "message": {
                "message_id": 5,
                "from": {"id": 1},
                "chat": {"id": 99, "type": "channel"},
                "text": "hi",
            }
        }
    )
    node = _make_parser(allowed_chat_types=["private"])
    result = await node(state, None)
    payload = result["node_results"]["parser"]

    assert payload["should_process"] is False
    assert payload["chat_id"] == "99"
    assert payload["username"] is None


@pytest.mark.asyncio
async def test_parser_run_callback_query() -> None:
    """callback_query update is parsed correctly."""
    state = _message_state(
        body={
            "callback_query": {
                "message": {"chat": {"id": 5, "type": "private"}},
                "from": {"id": 2, "first_name": "Bob"},
                "data": "btn",
            }
        },
    )
    node = _make_parser(allowed_update_types=["callback_query"])
    result = await node(state, None)
    payload = result["node_results"]["parser"]

    assert payload["should_process"] is True
    assert payload["update_type"] == "callback_query"
    assert payload["text"] == "btn"
    assert payload["username"] == "Bob"


@pytest.mark.asyncio
async def test_parser_run_secret_token_mismatch() -> None:
    """Mismatching secret token raises ValueError during run()."""
    state = _message_state(
        body={},
        headers={"x-telegram-bot-api-secret-token": "wrong"},
    )
    node = _make_parser(secret_token="correct")
    with pytest.raises(ValueError, match="secret token verification failed"):
        await node(state, None)


@pytest.mark.asyncio
async def test_parser_run_body_as_json_string() -> None:
    """Body delivered as a JSON string is parsed transparently."""
    body = {
        "update_id": 2,
        "message": {
            "message_id": 3,
            "from": {"id": 1, "first_name": "Eve"},
            "chat": {"id": 10, "type": "private"},
            "text": "Hi",
        },
    }
    state = _message_state(body=json.dumps(body))
    node = _make_parser()
    result = await node(state, None)
    payload = result["node_results"]["parser"]

    assert payload["should_process"] is True
    assert payload["text"] == "Hi"


@pytest.mark.asyncio
async def test_parser_run_invalid_json_body() -> None:
    """An invalid JSON body string results in an empty body → should_process=False."""
    state = _message_state(body="not json {")
    node = _make_parser()
    result = await node(state, None)
    payload = result["node_results"]["parser"]

    assert payload["should_process"] is False
    assert payload["update_type"] is None


@pytest.mark.asyncio
async def test_parser_run_body_non_dict_non_string() -> None:
    """A list body (non-dict, non-string) is treated as an empty payload."""
    state = _message_state(body=[1, 2, 3])
    node = _make_parser()
    result = await node(state, None)
    payload = result["node_results"]["parser"]

    assert payload["should_process"] is False
    assert payload["update_type"] is None


@pytest.mark.asyncio
async def test_parser_run_non_dict_headers() -> None:
    """Non-dict headers value skips secret-token verification entirely."""
    state = State({"inputs": {"headers": "not_a_dict", "body": {}}, "node_results": {}})
    node = _make_parser(secret_token="s3cr3t")
    # No ValueError raised despite secret_token being set
    result = await node(state, None)
    assert result["node_results"]["parser"]["should_process"] is False


@pytest.mark.asyncio
async def test_parser_run_should_process_false_when_no_text() -> None:
    """should_process is False when message text is empty."""
    state = _message_state(
        body={
            "message": {
                "message_id": 1,
                "from": {"id": 1, "first_name": "Alice"},
                "chat": {"id": 5, "type": "private"},
                "text": "",
            }
        }
    )
    node = _make_parser()
    result = await node(state, None)
    payload = result["node_results"]["parser"]

    assert payload["should_process"] is False
    assert payload["text"] == ""


@pytest.mark.asyncio
async def test_parser_run_username_fallback_to_username_field() -> None:
    """Falls back to sender.username when first_name is absent."""
    state = _message_state(
        body={
            "message": {
                "message_id": 1,
                "from": {"id": 9, "username": "alice_bot"},
                "chat": {"id": 3, "type": "private"},
                "text": "hello",
            }
        }
    )
    node = _make_parser()
    result = await node(state, None)
    payload = result["node_results"]["parser"]

    assert payload["username"] == "alice_bot"


@pytest.mark.asyncio
async def test_parser_run_whitespace_text_does_not_inject_message() -> None:
    """should_process=True but whitespace-only text skips HumanMessage injection."""
    # Whitespace text is truthy so bool(chat_id and text) is True, but text.strip()
    # returns "" which is falsy – the 'messages' key must NOT appear in the output.
    state = _message_state(
        body={
            "message": {
                "message_id": 1,
                "from": {"id": 1, "first_name": "Alice"},
                "chat": {"id": 5, "type": "private"},
                "text": "   ",
            }
        }
    )
    node = _make_parser()
    result = await node(state, None)

    assert "messages" not in result
    assert result["node_results"]["parser"]["should_process"] is True
