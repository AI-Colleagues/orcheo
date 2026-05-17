"""Tests for WeCom AI bot nodes."""

from __future__ import annotations
import base64
import hashlib
import json
import struct
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from Crypto.Cipher import AES
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.connectors.wecom import (
    WeComAIBotEventsParserNode,
    WeComAIBotPassiveReplyNode,
    WeComAIBotResponseNode,
)


def _create_aes_key() -> tuple[str, bytes]:
    """Create a valid AES key for testing."""
    raw_key = b"0123456789abcdef0123456789abcdef"
    encoding_aes_key = base64.b64encode(raw_key).decode().rstrip("=")
    return encoding_aes_key, raw_key


def _encrypt_message(message: str, aes_key: bytes, receive_id: str) -> str:
    """Encrypt a message using WeCom's encryption format."""
    random_bytes = b"0123456789abcdef"
    msg_bytes = message.encode("utf-8")
    msg_len = struct.pack(">I", len(msg_bytes))
    content = random_bytes + msg_len + msg_bytes + receive_id.encode("utf-8")

    block_size = 32
    pad_len = block_size - (len(content) % block_size)
    content += bytes([pad_len] * pad_len)

    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    encrypted = cipher.encrypt(content)
    return base64.b64encode(encrypted).decode()


def _sign_wecom(token: str, timestamp: str, nonce: str, data: str) -> str:
    """Create WeCom signature."""
    items = sorted([token, timestamp, nonce, data])
    return hashlib.sha1("".join(items).encode()).hexdigest()


def _build_state(
    query_params: dict[str, str] | None = None,
    body: str | dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> State:
    """Build a State object for testing."""
    inputs: dict[str, Any] = {}
    if query_params:
        inputs["query_params"] = query_params
    if body is not None:
        inputs["body"] = body
    return State(
        messages=[],
        inputs=inputs,
        results={},
        config=config or {},
        structured_response=None,
    )


class TestWeComAIBotEventsParserNode:
    """Tests for WeComAIBotEventsParserNode."""

    @pytest.mark.asyncio
    async def test_url_verification(self) -> None:
        """Test AI bot URL verification flow."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"

        echostr_plain = "echo_string_123"
        echostr_encrypted = _encrypt_message(echostr_plain, raw_key, "")
        signature = _sign_wecom(token, timestamp, nonce, echostr_encrypted)

        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            receive_id="",
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
                "echostr": echostr_encrypted,
            }
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_verification"] is True
        assert result["should_process"] is False
        assert result["immediate_response"]["content"] == echostr_plain

    @pytest.mark.asyncio
    async def test_encrypted_message_parsing(self) -> None:
        """Test parsing encrypted AI bot JSON payload."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"

        msg_payload = {
            "msgid": "msg123",
            "aibotid": "bot123",
            "chattype": "single",
            "response_url": "https://example.com/response",
            "msgtype": "text",
            "from": {"userid": "user456"},
            "text": {"content": "Hello AI"},
        }
        encrypted = _encrypt_message(json.dumps(msg_payload), raw_key, "")
        signature = _sign_wecom(token, timestamp, nonce, encrypted)

        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            receive_id="",
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"encrypt": encrypted},
        )

        result = await node.run(state, RunnableConfig())

        assert result["msg_type"] == "text"
        assert result["chat_type"] == "single"
        assert result["response_url"] == "https://example.com/response"
        assert result["user"] == "user456"
        assert result["content"] == "Hello AI"
        assert result["should_process"] is True
        assert result["immediate_response"] is None

    @pytest.mark.asyncio
    async def test_encrypted_message_immediate_response_check_active_reply(
        self,
    ) -> None:
        """Test immediate-response-check with active reply returns success for async."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"

        msg_payload = {
            "msgid": "msg123",
            "aibotid": "bot123",
            "chattype": "single",
            "response_url": "https://example.com/response",
            "msgtype": "text",
            "from": {"userid": "user456"},
            "text": {"content": "Hello AI"},
        }
        encrypted = _encrypt_message(json.dumps(msg_payload), raw_key, "")
        signature = _sign_wecom(token, timestamp, nonce, encrypted)

        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            receive_id="",
        )

        # Active reply mode: use_passive_reply is False
        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"encrypt": encrypted},
            config={"configurable": {"use_passive_reply": False}},
        )

        # Simulate immediate-response-check execution
        config = RunnableConfig(
            configurable={"thread_id": "immediate-response-check-abc123"}
        )
        result = await node.run(state, config)

        # Active reply mode: parser returns success immediately, queues async run
        assert result["should_process"] is True
        assert result["immediate_response"] is not None
        assert result["immediate_response"]["content"] == "success"
        assert result["immediate_response"]["status_code"] == 200

    @pytest.mark.asyncio
    async def test_encrypted_message_immediate_response_check_passive_reply(
        self,
    ) -> None:
        """Test immediate-response-check with passive reply does not short-circuit."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"

        msg_payload = {
            "msgid": "msg123",
            "aibotid": "bot123",
            "chattype": "single",
            "response_url": "https://example.com/response",
            "msgtype": "text",
            "from": {"userid": "user456"},
            "text": {"content": "Hello AI"},
        }
        encrypted = _encrypt_message(json.dumps(msg_payload), raw_key, "")
        signature = _sign_wecom(token, timestamp, nonce, encrypted)

        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            receive_id="",
        )

        # Passive reply mode: use_passive_reply is True
        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"encrypt": encrypted},
            config={"configurable": {"use_passive_reply": True}},
        )

        # Simulate immediate-response-check execution
        config = RunnableConfig(
            configurable={"thread_id": "immediate-response-check-abc123"}
        )
        result = await node.run(state, config)

        # Passive reply mode: parser doesn't set immediate_response,
        # lets workflow continue to passive_reply node
        assert result["should_process"] is True
        assert result["immediate_response"] is None
        assert result["msg_type"] == "text"
        assert result["content"] == "Hello AI"

    @pytest.mark.asyncio
    async def test_missing_encrypt_returns_invalid(self) -> None:
        """Test missing encrypt field returns invalid payload response with ack."""
        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token="token",
            encoding_aes_key="key",
            receive_id="",
        )

        state = _build_state(
            query_params={
                "msg_signature": "",
                "timestamp": "",
                "nonce": "",
            },
            body={"missing": "encrypt"},
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_verification"] is False
        assert result["should_process"] is False
        # Invalid payloads return a success response to ack WeCom and prevent retries
        assert result["immediate_response"] == {
            "content": "success",
            "content_type": "text/plain",
            "status_code": 200,
        }


class TestWeComAIBotPassiveReplyNode:
    """Tests for WeComAIBotPassiveReplyNode."""

    @pytest.mark.asyncio
    async def test_passive_reply_encrypts_payload(self) -> None:
        """Test passive reply encryption and signature."""
        encoding_aes_key, _ = _create_aes_key()
        token = "test_token"

        node = WeComAIBotPassiveReplyNode(
            name="passive_reply",
            token=token,
            encoding_aes_key=encoding_aes_key,
            msg_type="markdown",
            content="Hello AI",
            receive_id="",
        )

        result = await node.run(
            State(
                messages=[],
                inputs={},
                results={},
                config={},
                structured_response=None,
            ),
            RunnableConfig(),
        )

        assert result["is_error"] is False
        response_body = json.loads(result["immediate_response"]["content"])
        signature = _sign_wecom(
            token,
            str(response_body["timestamp"]),
            response_body["nonce"],
            response_body["encrypt"],
        )
        assert response_body["msgsignature"] == signature

        from orcheo.nodes.wecom import decrypt_wecom_message

        decrypted = decrypt_wecom_message(
            response_body["encrypt"],
            encoding_aes_key,
            None,
        )
        payload = json.loads(decrypted)
        assert payload["msgtype"] == "markdown"
        assert payload["markdown"]["content"] == "Hello AI"


class TestWeComAIBotResponseNode:
    """Tests for WeComAIBotResponseNode."""

    @pytest.mark.asyncio
    async def test_response_url_delivery_success(self) -> None:
        """Test active reply delivery to response_url."""
        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url="https://example.com/response",
            msg_type="markdown",
            content="Thanks!",
        )

        state = State(
            messages=[], inputs={}, results={}, config={}, structured_response=None
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[0][0] == "https://example.com/response"
        assert call_kwargs[1]["json"]["markdown"]["content"] == "Thanks!"

    @pytest.mark.asyncio
    async def test_response_url_from_parser_result(self) -> None:
        """Test response_url extraction from parser result."""
        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url=None,
            msg_type="text",
            content="Hello!",
        )

        state = State(
            messages=[],
            inputs={},
            results={
                "wecom_ai_bot_events_parser": {
                    "response_url": "https://example.com/from-parser"
                }
            },
            config={},
            structured_response=None,
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[0][0] == "https://example.com/from-parser"

    @pytest.mark.asyncio
    async def test_response_url_from_aibot_response_url_key(self) -> None:
        """Test response_url extraction from aibot_response_url key (line 868)."""
        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url=None,
            msg_type="text",
            content="Hello!",
        )

        state = State(
            messages=[],
            inputs={},
            results={
                "wecom_ai_bot_events_parser": {
                    "aibot_response_url": "https://example.com/from-aibot-key"
                }
            },
            config={},
            structured_response=None,
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[0][0] == "https://example.com/from-aibot-key"

    @pytest.mark.asyncio
    async def test_missing_response_url_returns_error(self) -> None:
        """Test missing response_url returns error (lines 958-966)."""
        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url=None,
            msg_type="text",
            content="Hello!",
        )

        state = State(
            messages=[], inputs={}, results={}, config={}, structured_response=None
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert result["error"] == "No response_url available"

    @pytest.mark.asyncio
    async def test_invalid_payload_returns_error(self) -> None:
        """Test invalid payload returns error (lines 970-977)."""
        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url="https://example.com/response",
            msg_type="text",
            content=None,  # No content
        )

        state = State(
            messages=[], inputs={}, results={}, config={}, structured_response=None
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert result["error"] == "Invalid reply payload"

    @pytest.mark.asyncio
    async def test_template_card_payload_missing_returns_error(self) -> None:
        """Test template_card without card data returns error (lines 875-877)."""
        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url="https://example.com/response",
            msg_type="template_card",
            template_card=None,  # No template_card
        )

        state = State(
            messages=[], inputs={}, results={}, config={}, structured_response=None
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert result["error"] == "Invalid reply payload"

    @pytest.mark.asyncio
    async def test_template_card_payload_success(self) -> None:
        """Test template_card with valid card data (lines 875-877)."""
        template_card_data = {
            "card_type": "text_notice",
            "main_title": {"title": "Test Card"},
        }
        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url="https://example.com/response",
            msg_type="template_card",
            template_card=template_card_data,
        )

        state = State(
            messages=[], inputs={}, results={}, config={}, structured_response=None
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[1]["json"]["msgtype"] == "template_card"
        assert call_kwargs[1]["json"]["template_card"] == template_card_data

    @pytest.mark.asyncio
    async def test_text_msg_type_payload(self) -> None:
        """Test text msg_type builds correct payload (lines 882-883)."""
        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url="https://example.com/response",
            msg_type="text",
            content="Plain text message",
        )

        state = State(
            messages=[], inputs={}, results={}, config={}, structured_response=None
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[1]["json"]["msgtype"] == "text"
        assert call_kwargs[1]["json"]["text"]["content"] == "Plain text message"

    @pytest.mark.asyncio
    async def test_unknown_msg_type_returns_none_payload(self) -> None:
        """Test unknown msg_type returns None payload (line 884)."""
        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url="https://example.com/response",
            msg_type="unknown_type",
            content="Some content",
        )

        state = State(
            messages=[], inputs={}, results={}, config={}, structured_response=None
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert result["error"] == "Invalid reply payload"

    @pytest.mark.asyncio
    async def test_timeout_exception(self) -> None:
        """Test handling of timeout exception (lines 912-921)."""
        import httpx

        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url="https://example.com/response",
            msg_type="text",
            content="Hello!",
        )

        state = State(
            messages=[], inputs={}, results={}, config={}, structured_response=None
        )

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert result["error"] == "Request timed out"

    @pytest.mark.asyncio
    async def test_http_status_error(self) -> None:
        """Test handling of HTTP status error (lines 922-935)."""
        import httpx

        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url="https://example.com/response",
            msg_type="text",
            content="Hello!",
        )

        state = State(
            messages=[], inputs={}, results={}, config={}, structured_response=None
        )

        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = MagicMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Server Error",
                request=MagicMock(),
                response=mock_response,
            )
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert "HTTP error: 500" in result["error"]
        assert result["status_code"] == 500

    @pytest.mark.asyncio
    async def test_request_error(self) -> None:
        """Test handling of general request error (lines 936-945)."""
        import httpx

        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url="https://example.com/response",
            msg_type="text",
            content="Hello!",
        )

        state = State(
            messages=[], inputs={}, results={}, config={}, structured_response=None
        )

        mock_client = MagicMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.RequestError("Connection failed")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert "Request failed:" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_json_response(self) -> None:
        """Test handling of invalid JSON response (lines 899-911)."""
        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url="https://example.com/response",
            msg_type="text",
            content="Hello!",
        )

        state = State(
            messages=[], inputs={}, results={}, config={}, structured_response=None
        )

        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert result["error"] == "Invalid JSON response"
        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_wecom_api_error_response(self) -> None:
        """Test handling of WeCom API error response (lines 986-999)."""
        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url="https://example.com/response",
            msg_type="text",
            content="Hello!",
        )

        state = State(
            messages=[], inputs={}, results={}, config={}, structured_response=None
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "errcode": 40001,
            "errmsg": "invalid credential",
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert result["errcode"] == 40001
        assert result["errmsg"] == "invalid credential"

    @pytest.mark.asyncio
    async def test_parser_result_not_dict(self) -> None:
        """Test handling of non-dict parser result (line 954)."""
        node = WeComAIBotResponseNode(
            name="aibot_response",
            response_url=None,
            msg_type="text",
            content="Hello!",
        )

        state = State(
            messages=[],
            inputs={},
            results={"wecom_ai_bot_events_parser": "not_a_dict"},
            config={},
            structured_response=None,
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert result["error"] == "No response_url available"


class TestWeComAIBotEventsParserNodeEdgeCases:
    """Additional tests for WeComAIBotEventsParserNode edge cases."""

    @pytest.mark.asyncio
    async def test_extract_inputs_returns_state_dict(self) -> None:
        """Test extract_inputs returns state_dict when inputs is not dict (line 513)."""
        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token="token",
            encoding_aes_key="key",
            receive_id="",
        )

        # State with non-dict inputs
        state = State(
            messages=[],
            inputs="not_a_dict",  # type: ignore[typeddict-item]
            results={},
            config={},
            structured_response=None,
        )

        inputs = node.extract_inputs(state)
        # Should return the state dict which contains inputs="not_a_dict"
        assert "inputs" in inputs

    @pytest.mark.asyncio
    async def test_extract_body_dict_with_raw_string(self) -> None:
        """Test extract_body_dict with raw string in body dict (lines 518-519)."""
        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token="token",
            encoding_aes_key="key",
            receive_id="",
        )

        inputs = {"body": {"raw": '{"encrypt": "test123"}'}}
        result = node.extract_body_dict(inputs)

        assert result == {"encrypt": "test123"}

    @pytest.mark.asyncio
    async def test_extract_body_dict_with_string_body(self) -> None:
        """Test extract_body_dict with string body (lines 520-527)."""
        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token="token",
            encoding_aes_key="key",
            receive_id="",
        )

        inputs = {"body": '{"encrypt": "test456"}'}
        result = node.extract_body_dict(inputs)

        assert result == {"encrypt": "test456"}

    @pytest.mark.asyncio
    async def test_extract_body_dict_with_invalid_json(self) -> None:
        """Test extract_body_dict with invalid JSON string (lines 523-524)."""
        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token="token",
            encoding_aes_key="key",
            receive_id="",
        )

        inputs = {"body": "not valid json"}
        result = node.extract_body_dict(inputs)

        assert result == {}

    @pytest.mark.asyncio
    async def test_extract_body_dict_with_non_dict_json(self) -> None:
        """Test extract_body_dict with non-dict JSON (lines 525-527)."""
        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token="token",
            encoding_aes_key="key",
            receive_id="",
        )

        inputs = {"body": '["array", "not", "dict"]'}
        result = node.extract_body_dict(inputs)

        assert result == {}

    @pytest.mark.asyncio
    async def test_extract_body_dict_with_non_dict_body(self) -> None:
        """Test extract_body_dict with non-dict/non-string body (line 530)."""
        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token="token",
            encoding_aes_key="key",
            receive_id="",
        )

        inputs = {"body": 12345}
        result = node.extract_body_dict(inputs)

        assert result == {}

    @pytest.mark.asyncio
    async def test_validate_timestamp_disabled(self) -> None:
        """Test validate_timestamp when tolerance is 0 (line 535)."""
        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token="token",
            encoding_aes_key="key",
            receive_id="",
            timestamp_tolerance_seconds=0,
        )

        result = node.validate_timestamp("any_timestamp")
        assert result is True

    @pytest.mark.asyncio
    async def test_validate_timestamp_invalid_format(self) -> None:
        """Test validate_timestamp with invalid timestamp format (lines 538-547)."""
        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token="token",
            encoding_aes_key="key",
            receive_id="",
            timestamp_tolerance_seconds=300,
        )

        result = node.validate_timestamp("not_a_number")
        assert result is False

    @pytest.mark.asyncio
    async def test_validate_timestamp_outside_tolerance(self) -> None:
        """Test validate_timestamp with timestamp outside tolerance (lines 550-559)."""
        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token="token",
            encoding_aes_key="key",
            receive_id="",
            timestamp_tolerance_seconds=300,
        )

        old_timestamp = str(int(time.time()) - 600)  # 10 minutes ago
        result = node.validate_timestamp(old_timestamp)
        assert result is False

    @pytest.mark.asyncio
    async def test_validate_timestamp_returns_invalid_response(self) -> None:
        """Test that invalid timestamp returns invalid payload response (line 707)."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        old_timestamp = str(int(time.time()) - 600)  # 10 minutes ago
        nonce = "nonce123"

        msg_payload = {"msgid": "msg123", "msgtype": "text"}
        encrypted = _encrypt_message(json.dumps(msg_payload), raw_key, "")
        signature = _sign_wecom(token, old_timestamp, nonce, encrypted)

        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            receive_id="",
            timestamp_tolerance_seconds=300,
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": old_timestamp,
                "nonce": nonce,
            },
            body={"encrypt": encrypted},
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_verification"] is False
        assert result["should_process"] is False
        assert result["immediate_response"]["content"] == "success"

    @pytest.mark.asyncio
    async def test_json_decode_error_returns_invalid(self) -> None:
        """Test JSON decode error in message parsing (lines 722-730)."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"

        # Encrypt invalid JSON
        encrypted = _encrypt_message("not valid json {", raw_key, "")
        signature = _sign_wecom(token, timestamp, nonce, encrypted)

        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            receive_id="",
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"encrypt": encrypted},
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_verification"] is False
        assert result["should_process"] is False
        assert result["immediate_response"]["content"] == "success"

    @pytest.mark.asyncio
    async def test_non_dict_decrypted_payload(self) -> None:
        """Test non-dict decrypted payload (line 732)."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"

        # Encrypt a JSON array instead of object
        encrypted = _encrypt_message('["not", "a", "dict"]', raw_key, "")
        signature = _sign_wecom(token, timestamp, nonce, encrypted)

        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            receive_id="",
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"encrypt": encrypted},
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_verification"] is False
        assert result["should_process"] is False

    @pytest.mark.asyncio
    async def test_get_use_passive_reply_non_dict_config(self) -> None:
        """Test get_use_passive_reply with non-dict config (line 675)."""
        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token="token",
            encoding_aes_key="key",
            receive_id="",
        )

        # State with non-dict config
        state = State(
            messages=[],
            inputs={},
            results={},
            config="not_a_dict",  # type: ignore[typeddict-item]
            structured_response=None,
        )
        result = node.get_use_passive_reply(state)
        assert result is False

        # State with config but non-dict configurable
        state2 = State(
            messages=[],
            inputs={},
            results={},
            config={"configurable": "not_a_dict"},
            structured_response=None,
        )
        result2 = node.get_use_passive_reply(state2)
        assert result2 is False

    @pytest.mark.asyncio
    async def test_parse_message_missing_from_dict(self) -> None:
        """Test parse_message with missing from dict (lines 637-639)."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"

        # Message without "from" field
        msg_payload = {
            "msgid": "msg123",
            "msgtype": "text",
            "text": {"content": "Hello"},
        }
        encrypted = _encrypt_message(json.dumps(msg_payload), raw_key, "")
        signature = _sign_wecom(token, timestamp, nonce, encrypted)

        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            receive_id="",
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"encrypt": encrypted},
        )

        result = await node.run(state, RunnableConfig())

        assert result["user"] == ""
        assert result["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_parse_message_missing_text_dict(self) -> None:
        """Test parse_message with missing text dict (lines 641-646)."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"

        # Message without "text" field
        msg_payload = {
            "msgid": "msg123",
            "msgtype": "image",
            "from": {"userid": "user123"},
        }
        encrypted = _encrypt_message(json.dumps(msg_payload), raw_key, "")
        signature = _sign_wecom(token, timestamp, nonce, encrypted)

        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            receive_id="",
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"encrypt": encrypted},
        )

        result = await node.run(state, RunnableConfig())

        assert result["user"] == "user123"
        assert result["content"] == ""

    @pytest.mark.asyncio
    async def test_parse_message_from_not_dict(self) -> None:
        """Test parse_message with non-dict from field (line 637->639)."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"

        # Message with non-dict "from" field
        msg_payload = {
            "msgid": "msg123",
            "msgtype": "text",
            "from": "not_a_dict",
            "text": {"content": "Hello"},
        }
        encrypted = _encrypt_message(json.dumps(msg_payload), raw_key, "")
        signature = _sign_wecom(token, timestamp, nonce, encrypted)

        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            receive_id="",
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"encrypt": encrypted},
        )

        result = await node.run(state, RunnableConfig())

        assert result["user"] == ""

    @pytest.mark.asyncio
    async def test_parse_message_text_not_dict(self) -> None:
        """Test parse_message with non-dict text field (line 641->646)."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"

        # Message with non-dict "text" field
        msg_payload = {
            "msgid": "msg123",
            "msgtype": "text",
            "from": {"userid": "user123"},
            "text": "not_a_dict",
        }
        encrypted = _encrypt_message(json.dumps(msg_payload), raw_key, "")
        signature = _sign_wecom(token, timestamp, nonce, encrypted)

        node = WeComAIBotEventsParserNode(
            name="wecom_ai_bot_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            receive_id="",
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"encrypt": encrypted},
        )

        result = await node.run(state, RunnableConfig())

        assert result["content"] == ""


class TestWeComAIBotPassiveReplyNodeEdgeCases:
    """Additional tests for WeComAIBotPassiveReplyNode edge cases."""

    @pytest.mark.asyncio
    async def test_passive_reply_missing_content(self) -> None:
        """Test passive reply with missing content (lines 778, 792-799)."""
        encoding_aes_key, _ = _create_aes_key()
        token = "test_token"

        node = WeComAIBotPassiveReplyNode(
            name="passive_reply",
            token=token,
            encoding_aes_key=encoding_aes_key,
            msg_type="markdown",
            content=None,  # No content
            receive_id="",
        )

        result = await node.run(
            State(
                messages=[],
                inputs={},
                results={},
                config={},
                structured_response=None,
            ),
            RunnableConfig(),
        )

        assert result["is_error"] is True
        assert result["error"] == "Invalid reply payload"
        assert result["immediate_response"] is None

    @pytest.mark.asyncio
    async def test_passive_reply_template_card_missing(self) -> None:
        """Test passive reply with template_card but no card data (lines 774-776)."""
        encoding_aes_key, _ = _create_aes_key()
        token = "test_token"

        node = WeComAIBotPassiveReplyNode(
            name="passive_reply",
            token=token,
            encoding_aes_key=encoding_aes_key,
            msg_type="template_card",
            template_card=None,  # No template_card
            receive_id="",
        )

        result = await node.run(
            State(
                messages=[],
                inputs={},
                results={},
                config={},
                structured_response=None,
            ),
            RunnableConfig(),
        )

        assert result["is_error"] is True
        assert result["error"] == "Invalid reply payload"

    @pytest.mark.asyncio
    async def test_passive_reply_template_card_success(self) -> None:
        """Test passive reply with valid template_card (lines 774-776)."""
        encoding_aes_key, _ = _create_aes_key()
        token = "test_token"

        template_card_data = {
            "card_type": "text_notice",
            "main_title": {"title": "Test Card"},
        }
        node = WeComAIBotPassiveReplyNode(
            name="passive_reply",
            token=token,
            encoding_aes_key=encoding_aes_key,
            msg_type="template_card",
            template_card=template_card_data,
            receive_id="",
        )

        result = await node.run(
            State(
                messages=[],
                inputs={},
                results={},
                config={},
                structured_response=None,
            ),
            RunnableConfig(),
        )

        assert result["is_error"] is False
        response_body = json.loads(result["immediate_response"]["content"])
        assert "encrypt" in response_body

        from orcheo.nodes.wecom import decrypt_wecom_message

        decrypted = decrypt_wecom_message(
            response_body["encrypt"],
            encoding_aes_key,
            None,
        )
        payload = json.loads(decrypted)
        assert payload["msgtype"] == "template_card"
        assert payload["template_card"] == template_card_data

    @pytest.mark.asyncio
    async def test_passive_reply_text_type(self) -> None:
        """Test passive reply with text type (line 781)."""
        encoding_aes_key, _ = _create_aes_key()
        token = "test_token"

        node = WeComAIBotPassiveReplyNode(
            name="passive_reply",
            token=token,
            encoding_aes_key=encoding_aes_key,
            msg_type="text",
            content="Plain text",
            receive_id="",
        )

        result = await node.run(
            State(
                messages=[],
                inputs={},
                results={},
                config={},
                structured_response=None,
            ),
            RunnableConfig(),
        )

        assert result["is_error"] is False
        response_body = json.loads(result["immediate_response"]["content"])

        from orcheo.nodes.wecom import decrypt_wecom_message

        decrypted = decrypt_wecom_message(
            response_body["encrypt"],
            encoding_aes_key,
            None,
        )
        payload = json.loads(decrypted)
        assert payload["msgtype"] == "text"
        assert payload["text"]["content"] == "Plain text"
