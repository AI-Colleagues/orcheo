"""Integration tests for WeCom nodes."""

from __future__ import annotations
import base64
import hashlib
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from Crypto.Cipher import AES
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.connectors.wecom import (
    WeComAccessTokenNode,
    WeComEventsParserNode,
    WeComSendMessageNode,
)


def _create_aes_key() -> tuple[str, bytes]:
    """Create a valid AES key for testing."""
    raw_key = b"0123456789abcdef0123456789abcdef"
    encoding_aes_key = base64.b64encode(raw_key).decode().rstrip("=")
    return encoding_aes_key, raw_key


def _encrypt_message(message: str, aes_key: bytes, corp_id: str) -> str:
    """Encrypt a message using WeCom's encryption format."""
    random_bytes = b"0123456789abcdef"
    msg_bytes = message.encode("utf-8")
    msg_len = struct.pack(">I", len(msg_bytes))
    content = random_bytes + msg_len + msg_bytes + corp_id.encode("utf-8")

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


@pytest.mark.asyncio
async def test_message_delivery_integration() -> None:
    """Test parser + token + send message flow with mocked WeCom APIs."""
    encoding_aes_key, raw_key = _create_aes_key()
    token = "test_token"
    timestamp = str(int(time.time()))
    nonce = "nonce123"
    corp_id = "corp123"

    inner_xml = (
        "<xml>"
        "<ToUserName>app123</ToUserName>"
        "<FromUserName>user456</FromUserName>"
        "<MsgType>text</MsgType>"
        "<Content>Hello World</Content>"
        "</xml>"
    )
    encrypted = _encrypt_message(inner_xml, raw_key, corp_id)
    signature = _sign_wecom(token, timestamp, nonce, encrypted)
    body_xml = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"

    parser_node = WeComEventsParserNode(
        name="wecom_events_parser",
        token=token,
        encoding_aes_key=encoding_aes_key,
        corp_id=corp_id,
    )
    parser_state = State(
        messages=[],
        inputs={
            "query_params": {
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            "body": {"raw": body_xml},
        },
        node_results={},
    )
    parser_result = await parser_node.run(parser_state, RunnableConfig())
    assert parser_result["should_process"] is True

    access_token_node = WeComAccessTokenNode(
        name="get_access_token",
        corp_id=corp_id,
        app_secret="secret456",
    )
    send_node = WeComSendMessageNode(
        name="send_message",
        agent_id=1000001,
        message="Reply!",
    )

    token_response = MagicMock()
    token_response.json.return_value = {
        "errcode": 0,
        "errmsg": "ok",
        "access_token": "test_access_token",
        "expires_in": 7200,
    }
    token_response.raise_for_status = MagicMock()
    token_client = AsyncMock()
    token_client.get = AsyncMock(return_value=token_response)
    token_client.aclose = AsyncMock()

    send_response = MagicMock()
    send_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
    send_response.raise_for_status = MagicMock()
    send_client = AsyncMock()
    send_client.post = AsyncMock(return_value=send_response)
    send_client.aclose = AsyncMock()

    with patch(
        "orcheo.nodes.wecom.httpx.AsyncClient",
        side_effect=[token_client, send_client],
    ):
        token_result = await access_token_node.run(
            State(messages=[], inputs={}, node_results={}),
            RunnableConfig(),
        )
        send_state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": token_result,
                "wecom_events_parser": parser_result,
            },
        )
        send_result = await send_node.run(send_state, RunnableConfig())

    assert send_result["is_error"] is False
    token_client.get.assert_called_once_with(
        "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
        params={"corpid": corp_id, "corpsecret": "secret456"},
    )
    send_client.post.assert_called_once()
    call_kwargs = send_client.post.call_args
    assert call_kwargs[1]["json"]["touser"] == "user456"
