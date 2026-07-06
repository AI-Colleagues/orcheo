"""Tests for WeCom nodes."""

from __future__ import annotations
import base64
import hashlib
import json
import struct
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
import redis
from Crypto.Cipher import AES
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
import orcheo.nodes.connectors.wecom.crypto as wecom_crypto
from orcheo.nodes.connectors.wecom import (
    CS_MESSAGE_TTL_SECONDS,
    WeComAccessTokenNode,
    WeComCustomerServiceSendNode,
    WeComCustomerServiceSyncNode,
    WeComEventsParserNode,
    WeComGroupPushNode,
    WeComSendMessageNode,
)
from orcheo.nodes.wecom import (
    _build_cs_inbound_entry,
    _build_cs_message_id,
    _build_cs_sync_payload,
    _close_cs_redis_client,
    _coerce_msgtime,
    _compute_message_ttl,
    _create_cs_redis_client,
    _cs_message_index_key,
    _cs_message_key,
    _fetch_cs_customer_username,
    _fetch_cs_message_history,
    _fetch_cs_page,
    _normalize_cs_sync_response,
    _pick_latest_message,
    _process_cs_page,
    _pull_cs_pages,
    _resolve_cs_history,
    _resolve_cs_sync_inputs,
    _select_cs_customer_nickname,
    _store_cs_message,
    decrypt_wecom_message,
    get_access_token_from_state,
    verify_wecom_signature,
)


# Test fixtures and helpers


def _create_aes_key() -> tuple[str, bytes]:
    """Create a valid AES key for testing.

    Returns:
        Tuple of (encoding_aes_key, raw_aes_key).
    """
    # WeCom uses 43-char base64 key that decodes to 32 bytes (256-bit AES)
    raw_key = b"0123456789abcdef0123456789abcdef"  # 32 bytes
    encoding_aes_key = base64.b64encode(raw_key).decode().rstrip("=")
    return encoding_aes_key, raw_key


def _encrypt_message(message: str, aes_key: bytes, corp_id: str) -> str:
    """Encrypt a message using WeCom's encryption format.

    Args:
        message: The message to encrypt.
        aes_key: The raw AES key (32 bytes).
        corp_id: The corp ID to append.

    Returns:
        Base64-encoded encrypted message.
    """
    # Format: random(16) + msg_len(4, big-endian) + msg + receive_id
    random_bytes = b"0123456789abcdef"  # 16 bytes
    msg_bytes = message.encode("utf-8")
    msg_len = struct.pack(">I", len(msg_bytes))
    content = random_bytes + msg_len + msg_bytes + corp_id.encode("utf-8")

    # PKCS7 padding to 32-byte blocks
    block_size = 32
    pad_len = block_size - (len(content) % block_size)
    content += bytes([pad_len] * pad_len)

    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    encrypted = cipher.encrypt(content)
    return base64.b64encode(encrypted).decode()


def _encrypt_raw_content(content: bytes, aes_key: bytes) -> str:
    """Encrypt raw content using AES-CBC with WeCom padding rules."""
    block_size = 32
    pad_len = block_size - (len(content) % block_size)
    padded = content + bytes([pad_len] * pad_len)
    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    encrypted = cipher.encrypt(padded)
    return base64.b64encode(encrypted).decode()


def _encrypt_raw_unpadded(content: bytes, aes_key: bytes) -> str:
    """Encrypt raw content using AES-CBC without padding."""
    if len(content) % 16 != 0:
        msg = "Content length must be a multiple of 16 bytes"
        raise ValueError(msg)
    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    encrypted = cipher.encrypt(content)
    return base64.b64encode(encrypted).decode()


def _sign_wecom(token: str, timestamp: str, nonce: str, data: str) -> str:
    """Create WeCom signature.

    Args:
        token: WeCom token.
        timestamp: Unix timestamp as string.
        nonce: Random nonce.
        data: echostr or encrypted message.

    Returns:
        SHA1 signature.
    """
    items = sorted([token, timestamp, nonce, data])
    return hashlib.sha1("".join(items).encode()).hexdigest()


def _build_state(
    query_params: dict[str, str] | None = None,
    body: str | dict[str, Any] | None = None,
) -> State:
    """Build a State object for testing."""
    inputs: dict[str, Any] = {}
    if query_params:
        inputs["query_params"] = query_params
    if body is not None:
        inputs["body"] = body
    return State(messages=[], inputs=inputs, node_results={})


class FakeRedis:
    """In-memory Redis stand-in for WeCom node tests."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.zsets: dict[str, dict[str, int]] = {}

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        return True

    async def zadd(self, key: str, mapping: dict[str, int]) -> int:
        zset = self.zsets.setdefault(key, {})
        zset.update(mapping)
        return len(mapping)

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def zrange(self, key: str, start: int, end: int) -> list[str]:
        items = sorted(self.zsets.get(key, {}).items(), key=lambda item: item[1])
        members = [member for member, _ in items]
        if end == -1:
            return members[start:]
        return members[start : end + 1]

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.store.get(key) for key in keys]

    async def zrem(self, key: str, *members: str) -> int:
        zset = self.zsets.get(key, {})
        removed = 0
        for member in members:
            if member in zset:
                removed += 1
                zset.pop(member, None)
        return removed

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def patch_redis(fake_redis: FakeRedis) -> FakeRedis:
    with patch("redis.asyncio.from_url", return_value=fake_redis):
        yield fake_redis


# WeComEventsParserNode tests


class TestWeComEventsParserNode:
    """Tests for WeComEventsParserNode."""

    def test_corp_id_defaults_to_vault_placeholder(self) -> None:
        """Test parser node corp_id defaults to the vault placeholder."""
        node = WeComEventsParserNode(name="wecom_parser")

        assert node.corp_id == "[[wecom_corp_id]]"

    def test_verify_signature_success(self) -> None:
        """Test successful signature verification."""
        token = "test_token"
        timestamp = "1234567890"
        nonce = "abc123"
        data = "encrypted_data"
        signature = _sign_wecom(token, timestamp, nonce, data)

        # Should not raise
        verify_wecom_signature(token, timestamp, nonce, data, signature)

    def test_verify_signature_failure(self) -> None:
        """Test signature verification failure."""
        token = "test_token"
        timestamp = "1234567890"
        nonce = "abc123"
        data = "encrypted_data"
        bad_signature = "bad_signature"

        with pytest.raises(ValueError, match="WeCom signature verification failed"):
            verify_wecom_signature(token, timestamp, nonce, data, bad_signature)

    def test_decrypt_message(self) -> None:
        """Test message decryption."""
        encoding_aes_key, raw_key = _create_aes_key()
        corp_id = "corp123"
        original_message = "<xml><Content>Hello</Content></xml>"
        encrypted = _encrypt_message(original_message, raw_key, corp_id)

        decrypted = decrypt_wecom_message(encrypted, encoding_aes_key, corp_id)
        assert decrypted == original_message

    def test_decrypt_message_rejects_mismatched_receive_id(self) -> None:
        """Test message decryption rejects mismatched receive_id."""
        encoding_aes_key, raw_key = _create_aes_key()
        corp_id = "corp123"
        original_message = "<xml><Content>Hello</Content></xml>"
        encrypted = _encrypt_message(original_message, raw_key, corp_id)

        with pytest.raises(ValueError, match="WeCom receive_id validation failed"):
            decrypt_wecom_message(encrypted, encoding_aes_key, "wrong_corp")

    def test_decrypt_message_rejects_invalid_padding(self) -> None:
        """Test message decryption rejects invalid padding values."""
        encoding_aes_key, raw_key = _create_aes_key()
        invalid_padding = b"X" * 31 + b"\x00"
        encrypted = _encrypt_raw_unpadded(invalid_padding, raw_key)

        with pytest.raises(ValueError, match="WeCom payload padding invalid"):
            decrypt_wecom_message(encrypted, encoding_aes_key, "corp123")

    def test_decrypt_message_rejects_too_short_payload(self) -> None:
        """Test message decryption rejects payloads shorter than 20 bytes."""
        encoding_aes_key, raw_key = _create_aes_key()
        short_content = b"A" * 19
        encrypted = _encrypt_raw_content(short_content, raw_key)

        with pytest.raises(ValueError, match="WeCom payload too short"):
            decrypt_wecom_message(encrypted, encoding_aes_key, "corp123")

    def test_decrypt_message_rejects_length_mismatch(self) -> None:
        """Test message decryption rejects mismatched message lengths."""
        encoding_aes_key, raw_key = _create_aes_key()
        random_prefix = b"0123456789abcdef"
        msg_len = struct.pack(">I", 10)
        content = random_prefix + msg_len + b"" + b"abc"
        encrypted = _encrypt_raw_content(content, raw_key)

        with pytest.raises(ValueError, match="WeCom payload length mismatch"):
            decrypt_wecom_message(encrypted, encoding_aes_key, "corp123")

    def test_parse_xml(self) -> None:
        """Test XML parsing."""
        node = WeComEventsParserNode(
            name="wecom_parser",
            token="token",
            encoding_aes_key="key",
            corp_id="corp123",
        )
        xml_str = "<xml><ToUserName>user1</ToUserName><Content>Hello</Content></xml>"
        result = node.parse_xml(xml_str)
        assert result == {"ToUserName": "user1", "Content": "Hello"}

    def test_extract_inputs_falls_back_to_state_dict(self) -> None:
        """Test extract_inputs returns full state when inputs is not a dict."""
        node = WeComEventsParserNode(
            name="wecom_parser",
            token="token",
            encoding_aes_key="key",
            corp_id="corp123",
        )
        state = {"inputs": "not-a-dict", "body": {"raw": "<xml></xml>"}}

        result = node.extract_inputs(state)

        assert result["inputs"] == "not-a-dict"
        assert result["body"] == {"raw": "<xml></xml>"}

    def test_is_direct_message_true(self) -> None:
        """Test direct message detection returns True for valid DM."""
        node = WeComEventsParserNode(
            name="wecom_parser",
            token="token",
            encoding_aes_key="key",
            corp_id="corp123",
        )
        assert node.is_direct_message("text", "", "Hello") is True

    def test_is_direct_message_false_with_chat_id(self) -> None:
        """Test direct message detection returns False for group messages."""
        node = WeComEventsParserNode(
            name="wecom_parser",
            token="token",
            encoding_aes_key="key",
            corp_id="corp123",
        )
        assert node.is_direct_message("text", "chat123", "Hello") is False

    def test_is_direct_message_false_for_non_text(self) -> None:
        """Test direct message detection returns False for non-text messages."""
        node = WeComEventsParserNode(
            name="wecom_parser",
            token="token",
            encoding_aes_key="key",
            corp_id="corp123",
        )
        assert node.is_direct_message("image", "", "Hello") is False

    def test_is_direct_message_false_for_empty_content(self) -> None:
        """Test direct message detection returns False for empty content."""
        node = WeComEventsParserNode(
            name="wecom_parser",
            token="token",
            encoding_aes_key="key",
            corp_id="corp123",
        )
        assert node.is_direct_message("text", "", "   ") is False

    @pytest.mark.asyncio
    async def test_url_verification(self) -> None:
        """Test URL verification flow."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"
        corp_id = "corp123"

        # Encrypt the echostr
        echostr_plain = "echo_string_12345"
        echostr_encrypted = _encrypt_message(echostr_plain, raw_key, corp_id)
        signature = _sign_wecom(token, timestamp, nonce, echostr_encrypted)

        node = WeComEventsParserNode(
            name="wecom_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
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
        assert result["immediate_response"]["status_code"] == 200

    @pytest.mark.asyncio
    async def test_encrypted_message_parsing(self) -> None:
        """Test parsing encrypted message body."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"
        corp_id = "corp123"

        # Create encrypted message content
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

        # Outer XML envelope
        body_xml = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"

        node = WeComEventsParserNode(
            name="wecom_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"raw": body_xml},
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_verification"] is False
        assert result["event_type"] == "text"
        assert result["content"] == "Hello World"
        assert result["user"] == "user456"
        assert result["should_process"] is True

    @pytest.mark.asyncio
    async def test_encrypted_message_parsing_body_string_skips_tolerance(self) -> None:
        """Test parsing when body is a string and timestamp tolerance disabled."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"
        corp_id = "corp123"

        inner_xml = (
            "<xml>"
            "<ToUserName>app123</ToUserName>"
            "<FromUserName>user789</FromUserName>"
            "<MsgType>text</MsgType>"
            "<Content>Hello String</Content>"
            "</xml>"
        )
        encrypted = _encrypt_message(inner_xml, raw_key, corp_id)
        signature = _sign_wecom(token, timestamp, nonce, encrypted)
        body_xml = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"

        node = WeComEventsParserNode(
            name="wecom_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
            timestamp_tolerance_seconds=0,
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body=body_xml,
        )

        result = await node.run(state, RunnableConfig())

        assert result["event_type"] == "text"
        assert result["content"] == "Hello String"
        assert result["should_process"] is True

    @pytest.mark.asyncio
    async def test_encrypted_message_parsing_body_object(self) -> None:
        """Test parsing when body is not a dict or string."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"
        corp_id = "corp123"

        inner_xml = (
            "<xml>"
            "<ToUserName>app123</ToUserName>"
            "<FromUserName>user123</FromUserName>"
            "<MsgType>text</MsgType>"
            "<Content>Hello Object</Content>"
            "</xml>"
        )
        encrypted = _encrypt_message(inner_xml, raw_key, corp_id)
        signature = _sign_wecom(token, timestamp, nonce, encrypted)
        body_xml = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"

        class BodyWrapper:
            def __str__(self) -> str:
                return body_xml

        node = WeComEventsParserNode(
            name="wecom_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
            timestamp_tolerance_seconds=0,
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body=BodyWrapper(),
        )

        result = await node.run(state, RunnableConfig())

        assert result["event_type"] == "text"
        assert result["content"] == "Hello Object"
        assert result["should_process"] is True

    @pytest.mark.asyncio
    async def test_allowlist_rejects_user(self) -> None:
        """Test allowlist blocks non-allowed users."""
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

        node = WeComEventsParserNode(
            name="wecom_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
            allowlist_user_ids=["allowed_user"],
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"raw": body_xml},
        )

        result = await node.run(state, RunnableConfig())

        assert result["should_process"] is False
        assert result["user"] == "user456"

    @pytest.mark.asyncio
    async def test_allowlist_allows_user(self) -> None:
        """Test allowlist allows configured users."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"
        corp_id = "corp123"

        inner_xml = (
            "<xml>"
            "<ToUserName>app123</ToUserName>"
            "<FromUserName>allowed_user</FromUserName>"
            "<MsgType>text</MsgType>"
            "<Content>Hello World</Content>"
            "</xml>"
        )
        encrypted = _encrypt_message(inner_xml, raw_key, corp_id)
        signature = _sign_wecom(token, timestamp, nonce, encrypted)
        body_xml = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"

        node = WeComEventsParserNode(
            name="wecom_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
            allowlist_user_ids=["allowed_user"],
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"raw": body_xml},
        )

        result = await node.run(state, RunnableConfig())

        assert result["should_process"] is True
        assert result["user"] == "allowed_user"

    @pytest.mark.asyncio
    async def test_group_message_ignored(self) -> None:
        """Test that group messages are ignored."""
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
            "<Content>Hello</Content>"
            "<ChatId>group789</ChatId>"
            "</xml>"
        )
        encrypted = _encrypt_message(inner_xml, raw_key, corp_id)
        signature = _sign_wecom(token, timestamp, nonce, encrypted)
        body_xml = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"

        node = WeComEventsParserNode(
            name="wecom_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"raw": body_xml},
        )

        result = await node.run(state, RunnableConfig())

        assert result["should_process"] is False
        assert result["chat_id"] == "group789"

    @pytest.mark.asyncio
    async def test_no_encrypt_element(self) -> None:
        """Test handling of message without Encrypt element."""
        node = WeComEventsParserNode(
            name="wecom_parser",
            token="token",
            encoding_aes_key="key",
            corp_id="corp123",
        )

        state = _build_state(
            query_params={
                "msg_signature": "",
                "timestamp": "",
                "nonce": "",
            },
            body={"raw": "<xml><Empty/></xml>"},
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_verification"] is False
        assert result["event_type"] is None
        assert result["should_process"] is False

    @pytest.mark.asyncio
    async def test_timestamp_tolerance_allows_valid_timestamp(self) -> None:
        """Test that valid timestamps pass tolerance check."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        current_timestamp = str(int(time.time()))
        nonce = "nonce123"
        corp_id = "corp123"

        inner_xml = "<xml><MsgType>text</MsgType><Content>Hi</Content></xml>"
        encrypted = _encrypt_message(inner_xml, raw_key, corp_id)
        signature = _sign_wecom(token, current_timestamp, nonce, encrypted)
        body_xml = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"

        node = WeComEventsParserNode(
            name="wecom_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
            timestamp_tolerance_seconds=300,  # 5 minutes
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": current_timestamp,
                "nonce": nonce,
            },
            body={"raw": body_xml},
        )

        # Should not raise - valid timestamp
        result = await node.run(state, RunnableConfig())
        assert result["event_type"] == "text"

    @pytest.mark.asyncio
    async def test_timestamp_invalid_rejected(self) -> None:
        """Test invalid timestamps are rejected."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = "invalid"
        nonce = "nonce123"
        corp_id = "corp123"

        inner_xml = "<xml><MsgType>text</MsgType><Content>Hi</Content></xml>"
        encrypted = _encrypt_message(inner_xml, raw_key, corp_id)
        signature = _sign_wecom(token, timestamp, nonce, encrypted)
        body_xml = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"

        node = WeComEventsParserNode(
            name="wecom_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
            timestamp_tolerance_seconds=300,
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"raw": body_xml},
        )

        result = await node.run(state, RunnableConfig())

        assert result["should_process"] is False
        assert result["event_type"] is None

    @pytest.mark.asyncio
    async def test_timestamp_outside_tolerance_rejected(self) -> None:
        """Test old timestamps are rejected when outside tolerance."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()) - 1000)
        nonce = "nonce123"
        corp_id = "corp123"

        inner_xml = "<xml><MsgType>text</MsgType><Content>Hi</Content></xml>"
        encrypted = _encrypt_message(inner_xml, raw_key, corp_id)
        signature = _sign_wecom(token, timestamp, nonce, encrypted)
        body_xml = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"

        node = WeComEventsParserNode(
            name="wecom_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
            timestamp_tolerance_seconds=1,
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"raw": body_xml},
        )

        result = await node.run(state, RunnableConfig())

        assert result["should_process"] is False
        assert result["event_type"] is None
        assert result["immediate_response"] is None

    @pytest.mark.asyncio
    async def test_encrypted_message_sync_check_sets_immediate_response(
        self,
    ) -> None:
        """Test sync check with valid message short-circuits with success."""
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"
        corp_id = "corp123"

        inner_xml = (
            "<xml>"
            "<ToUserName>app123</ToUserName>"
            "<FromUserName>user321</FromUserName>"
            "<MsgType>text</MsgType>"
            "<Content>Sync check</Content>"
            "</xml>"
        )
        encrypted = _encrypt_message(inner_xml, raw_key, corp_id)
        signature = _sign_wecom(token, timestamp, nonce, encrypted)
        body_xml = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"

        node = WeComEventsParserNode(
            name="wecom_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"raw": body_xml},
        )

        config = RunnableConfig(
            configurable={"thread_id": "immediate-response-check-xyz"},
        )
        result = await node.run(state, config)

        assert result["should_process"] is True
        assert result["content"] == "Sync check"
        assert result["immediate_response"] == {
            "content": "success",
            "content_type": "text/plain",
            "status_code": 200,
        }

    @pytest.mark.asyncio
    async def test_immediate_response_check(self) -> None:
        """Test immediate response check mode."""
        node = WeComEventsParserNode(
            name="wecom_parser",
            token="token",
            encoding_aes_key="key",
            corp_id="corp123",
        )

        state = _build_state(
            query_params={},
            body={"raw": "<xml><Empty/></xml>"},
        )

        config = RunnableConfig(configurable={"thread_id": "immediate-response-check"})
        result = await node.run(state, config)

        assert result["immediate_response"] == {
            "content": "success",
            "content_type": "text/plain",
            "status_code": 200,
        }


# WeComAccessTokenNode tests


class TestWeComAccessTokenNode:
    """Tests for WeComAccessTokenNode."""

    def test_corp_id_defaults_to_vault_placeholder(self) -> None:
        """Test access token node corp_id defaults to the vault placeholder."""
        node = WeComAccessTokenNode(name="get_token")

        assert node.corp_id == "[[wecom_corp_id]]"

    @pytest.mark.asyncio
    async def test_fetch_access_token_success(self) -> None:
        """Test successful access token fetch."""
        node = WeComAccessTokenNode(
            name="get_token",
            corp_id="corp123",
            app_secret="secret456",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "errcode": 0,
            "errmsg": "ok",
            "access_token": "test_access_token",
            "expires_in": 7200,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run({}, RunnableConfig())

        assert result["access_token"] == "test_access_token"
        assert result["expires_in"] == 7200

        mock_client.get.assert_called_once_with(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": "corp123", "corpsecret": "secret456"},
        )

    @pytest.mark.asyncio
    async def test_fetch_access_token_error(self) -> None:
        """Test access token fetch with API error."""
        node = WeComAccessTokenNode(
            name="get_token",
            corp_id="corp123",
            app_secret="bad_secret",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "errcode": 40001,
            "errmsg": "invalid credential",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(
                ValueError, match="WeCom token error: invalid credential"
            ):
                await node.run({}, RunnableConfig())


# WeComSendMessageNode tests


class TestWeComSendMessageNode:
    """Tests for WeComSendMessageNode."""

    @pytest.mark.asyncio
    async def test_send_text_message_to_user(self) -> None:
        """Test sending text message to user."""
        node = WeComSendMessageNode(
            name="send_msg",
            agent_id=1000001,
            to_user="user123",
            message="Hello, user!",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={"get_access_token": {"access_token": "test_token"}},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        assert result["errcode"] == 0

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[1]["json"]["touser"] == "user123"
        assert call_kwargs[1]["json"]["text"]["content"] == "Hello, user!"

    @pytest.mark.asyncio
    async def test_send_markdown_message(self) -> None:
        """Test sending markdown message."""
        node = WeComSendMessageNode(
            name="send_msg",
            agent_id="1000001",  # String agent_id
            to_user="user123",
            message="**Bold** text",
            msg_type="markdown",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={"get_access_token": {"access_token": "test_token"}},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[1]["json"]["markdown"]["content"] == "**Bold** text"
        assert call_kwargs[1]["json"]["msgtype"] == "markdown"

    @pytest.mark.asyncio
    async def test_send_message_to_chat(self) -> None:
        """Test sending message to group chat."""
        node = WeComSendMessageNode(
            name="send_msg",
            agent_id=1000001,
            chat_id="chat789",
            message="Hello, group!",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={"get_access_token": {"access_token": "test_token"}},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        call_kwargs = mock_client.post.call_args
        assert "chatid" in call_kwargs[1]["json"]
        assert call_kwargs[0][0] == "https://qyapi.weixin.qq.com/cgi-bin/appchat/send"

    @pytest.mark.asyncio
    async def test_send_message_no_access_token(self) -> None:
        """Test sending message without access token."""
        node = WeComSendMessageNode(
            name="send_msg",
            agent_id=1000001,
            to_user="user123",
            message="Hello!",
        )

        state = State(messages=[], inputs={}, node_results={})

        result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert result["error"] == "No access token available"

    @pytest.mark.asyncio
    async def test_send_message_no_recipient(self) -> None:
        """Test sending message without recipient."""
        node = WeComSendMessageNode(
            name="send_msg",
            agent_id=1000001,
            message="Hello!",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={"get_access_token": {"access_token": "test_token"}},
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert "No WeCom chat_id or to_user provided" in result["error"]

    @pytest.mark.asyncio
    async def test_send_message_uses_parser_target_user(self) -> None:
        """Test that send message uses target_user from parser result."""
        node = WeComSendMessageNode(
            name="send_msg",
            agent_id=1000001,
            message="Reply!",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": {"access_token": "test_token"},
                "wecom_events_parser": {"target_user": "parsed_user"},
            },
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[1]["json"]["touser"] == "parsed_user"

    @pytest.mark.asyncio
    async def test_send_message_api_error(self) -> None:
        """Test handling of API error response."""
        node = WeComSendMessageNode(
            name="send_msg",
            agent_id=1000001,
            to_user="user123",
            message="Hello!",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={"get_access_token": {"access_token": "test_token"}},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "errcode": 60011,
            "errmsg": "no privilege to access",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert result["errcode"] == 60011
        assert result["errmsg"] == "no privilege to access"


class TestWeComGroupPushNode:
    """Tests for WeComGroupPushNode."""

    @pytest.mark.asyncio
    async def test_group_push_success_text(self) -> None:
        """Test successful webhook delivery using key-based URL."""
        node = WeComGroupPushNode(
            name="group_push",
            webhook_key="key123",
            content="Daily digest",
        )

        state = State(messages=[], inputs={}, node_results={})

        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "webhook/send?key=key123" in call_kwargs[0][0]
        assert call_kwargs[1]["json"]["text"]["content"] == "Daily digest"

    @pytest.mark.asyncio
    async def test_group_push_markdown_error(self) -> None:
        """Test webhook delivery error response handling."""
        node = WeComGroupPushNode(
            name="group_push",
            webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc",
            msg_type="markdown",
            content="*Digest*",
        )

        state = State(messages=[], inputs={}, node_results={})

        mock_response = MagicMock()
        mock_response.json.return_value = {"errcode": 93000, "errmsg": "invalid"}
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert result["errcode"] == 93000

    @pytest.mark.asyncio
    async def test_group_push_missing_webhook(self) -> None:
        """Test missing webhook configuration results in error."""
        node = WeComGroupPushNode(
            name="group_push",
            webhook_key=None,
            webhook_url=None,
            content="Digest",
        )

        state = State(messages=[], inputs={}, node_results={})
        result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True


# Customer Service Event Detection tests


class TestWeComEventsParserCustomerService:
    """Tests for WeComEventsParserNode Customer Service event detection."""

    def test_is_customer_service_event_true(self) -> None:
        """Test detection of kf_msg_or_event."""
        node = WeComEventsParserNode(
            name="wecom_parser",
            token="token",
            encoding_aes_key="key",
            corp_id="corp123",
        )
        msg_data = {"MsgType": "event", "Event": "kf_msg_or_event"}
        assert node.is_customer_service_event(msg_data) is True

    def test_is_customer_service_event_false_wrong_event(self) -> None:
        """Test non-CS events return False."""
        node = WeComEventsParserNode(
            name="wecom_parser",
            token="token",
            encoding_aes_key="key",
            corp_id="corp123",
        )
        msg_data = {"MsgType": "event", "Event": "subscribe"}
        assert node.is_customer_service_event(msg_data) is False

    def test_is_customer_service_event_false_not_event(self) -> None:
        """Test non-event message types return False."""
        node = WeComEventsParserNode(
            name="wecom_parser",
            token="token",
            encoding_aes_key="key",
            corp_id="corp123",
        )
        msg_data = {"MsgType": "text", "Content": "Hello"}
        assert node.is_customer_service_event(msg_data) is False

    def test_handle_customer_service_event_sync_check_sets_immediate_response(
        self,
    ) -> None:
        """Test sync check short-circuits Customer Service events."""
        node = WeComEventsParserNode(
            name="wecom_parser",
            token="token",
            encoding_aes_key="key",
            corp_id="corp123",
        )
        msg_data = {"OpenKfId": "wkABC123", "Token": "sync_token"}

        result = node.handle_customer_service_event(msg_data, is_sync_check=True)

        assert result["is_customer_service"] is True
        assert result["should_process"] is True
        assert result["immediate_response"] == node.success_response()

    @pytest.mark.asyncio
    async def test_customer_service_event_parsing(self) -> None:
        """Test parsing Customer Service event callback.

        Valid CS events (with open_kf_id and kf_token) set should_process=True
        and immediate_response=None to allow the workflow to continue to CS
        sync and send nodes. The send node will set immediate_response after
        sending, preventing a duplicate async run.
        """
        encoding_aes_key, raw_key = _create_aes_key()
        token = "test_token"
        timestamp = str(int(time.time()))
        nonce = "nonce123"
        corp_id = "corp123"

        # Customer Service event XML
        inner_xml = (
            "<xml>"
            "<ToUserName>corp123</ToUserName>"
            "<CreateTime>1234567890</CreateTime>"
            "<MsgType>event</MsgType>"
            "<Event>kf_msg_or_event</Event>"
            "<Token>sync_token_abc</Token>"
            "<OpenKfId>wkABC123</OpenKfId>"
            "</xml>"
        )
        encrypted = _encrypt_message(inner_xml, raw_key, corp_id)
        signature = _sign_wecom(token, timestamp, nonce, encrypted)
        body_xml = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"

        node = WeComEventsParserNode(
            name="wecom_parser",
            token=token,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
        )

        state = _build_state(
            query_params={
                "msg_signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            body={"raw": body_xml},
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_verification"] is False
        assert result["is_customer_service"] is True
        assert result["event_type"] == "kf_msg_or_event"
        assert result["open_kf_id"] == "wkABC123"
        assert result["kf_token"] == "sync_token_abc"
        # Valid CS events continue to send node; immediate_response=None
        assert result["should_process"] is True
        assert result["immediate_response"] is None


# WeComCustomerServiceSyncNode tests


class TestWeComCustomerServiceSyncNode:
    """Tests for WeComCustomerServiceSyncNode."""

    @pytest.mark.asyncio
    async def test_sync_messages_success(self, patch_redis: FakeRedis) -> None:
        """Test successful message sync."""
        node = WeComCustomerServiceSyncNode(
            name="wecom_cs_sync",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": {
                    "access_token": "test_token",
                    "expires_in": 7200,
                },
                "wecom_events_parser": {
                    "open_kf_id": "wkABC123",
                    "kf_token": "sync_token",
                },
            },
        )

        sync_response = MagicMock()
        sync_response.json.return_value = {
            "errcode": 0,
            "msg_list": [
                {
                    "msgtype": "text",
                    "origin": 3,  # External WeChat user
                    "external_userid": "wmXYZ789",
                    "text": {"content": "Hello from WeChat"},
                }
            ],
            "next_cursor": "cursor123",
            "has_more": 0,
        }
        sync_response.raise_for_status = MagicMock()

        customer_response = MagicMock()
        customer_response.json.return_value = {
            "errcode": 0,
            "errmsg": "ok",
            "customer_list": [
                {
                    "external_userid": "wmXYZ789",
                    "nickname": "张三",
                }
            ],
        }
        customer_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[sync_response, customer_response])
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        assert result["open_kf_id"] == "wkABC123"
        assert result["external_userid"] == "wmXYZ789"
        assert result["external_username"] == "张三"
        assert result["messages"][0]["content"] == "Hello from WeChat"
        assert result["should_process"] is True
        assert result["next_cursor"] == "cursor123"

    @pytest.mark.asyncio
    async def test_sync_messages_no_access_token(self) -> None:
        """Test sync fails without access token."""
        node = WeComCustomerServiceSyncNode(
            name="wecom_cs_sync",
        )

        state = State(messages=[], inputs={}, node_results={})

        result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert "access token" in result["error"]

    @pytest.mark.asyncio
    async def test_sync_messages_no_open_kf_id(self) -> None:
        """Test sync fails without open_kf_id."""
        node = WeComCustomerServiceSyncNode(
            name="wecom_cs_sync",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": {
                    "access_token": "test_token",
                    "expires_in": 7200,
                },
            },
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert "open_kf_id" in result["error"]

    @pytest.mark.asyncio
    async def test_sync_messages_no_external_messages(
        self, patch_redis: FakeRedis
    ) -> None:
        """Test sync with no messages from external users."""
        node = WeComCustomerServiceSyncNode(
            name="wecom_cs_sync",
            open_kf_id="wkABC123",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": {
                    "access_token": "test_token",
                    "expires_in": 7200,
                },
            },
        )

        sync_response = MagicMock()
        sync_response.json.return_value = {
            "errcode": 0,
            "msg_list": [
                {
                    "msgtype": "text",
                    "origin": 5,  # Internal user, not external
                    "text": {"content": "Internal message"},
                }
            ],
            "next_cursor": "",
            "has_more": 0,
        }
        sync_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=sync_response)
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        assert result["external_userid"] == ""
        assert result["external_username"] == ""
        assert result["messages"] == []
        assert result["should_process"] is False

    @pytest.mark.asyncio
    async def test_sync_messages_with_cursor(self, patch_redis: FakeRedis) -> None:
        """Test sync with cursor parameter for pagination."""
        node = WeComCustomerServiceSyncNode(
            name="wecom_cs_sync",
            open_kf_id="wkABC123",
            cursor="previous_cursor_value",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": {
                    "access_token": "test_token",
                    "expires_in": 7200,
                },
            },
        )

        sync_response = MagicMock()
        sync_response.json.return_value = {
            "errcode": 0,
            "msg_list": [
                {
                    "msgtype": "text",
                    "origin": 3,
                    "external_userid": "wmXYZ789",
                    "text": {"content": "Paginated message"},
                }
            ],
            "next_cursor": "next_cursor_value",
            "has_more": 0,
        }
        sync_response.raise_for_status = MagicMock()

        customer_response = MagicMock()
        customer_response.json.return_value = {
            "errcode": 0,
            "errmsg": "ok",
            "customer_list": [
                {
                    "external_userid": "wmXYZ789",
                    "nickname": "Paginated User",
                }
            ],
        }
        customer_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[sync_response, customer_response])
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        # Verify cursor was included in the request payload
        call_kwargs = mock_client.post.call_args_list[0]
        assert call_kwargs[1]["json"]["cursor"] == "previous_cursor_value"

    @pytest.mark.asyncio
    async def test_sync_messages_skips_empty_content(
        self, patch_redis: FakeRedis
    ) -> None:
        """Test sync skips messages with empty content and finds next valid one.

        This test exercises the branch where the loop continues when content is
        empty (line 714->708 branch coverage).
        """
        node = WeComCustomerServiceSyncNode(
            name="wecom_cs_sync",
            open_kf_id="wkABC123",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": {
                    "access_token": "test_token",
                    "expires_in": 7200,
                },
            },
        )

        sync_response = MagicMock()
        sync_response.json.return_value = {
            "errcode": 0,
            "msg_list": [
                # First message (oldest) - has content, will be reached after skipping
                {
                    "msgtype": "text",
                    "origin": 3,
                    "external_userid": "wmOldest",
                    "text": {"content": "Oldest message with content"},
                },
                # Second message (newest) - empty content, processed first due to
                # reverse, should be skipped (this triggers 714->708 branch)
                {
                    "msgtype": "text",
                    "origin": 3,
                    "external_userid": "wmEmpty",
                    "text": {"content": ""},
                },
            ],
            "next_cursor": "",
            "has_more": 0,
        }
        sync_response.raise_for_status = MagicMock()

        customer_response = MagicMock()
        customer_response.json.return_value = {
            "errcode": 0,
            "errmsg": "ok",
            "customer_list": [
                {
                    "external_userid": "wmOldest",
                    "nickname": "Oldest User",
                }
            ],
        }
        customer_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[sync_response, customer_response])
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        # Should skip empty message and get the oldest with content
        assert result["external_userid"] == "wmOldest"
        assert result["external_username"] == "Oldest User"
        assert result["messages"][0]["content"] == "Oldest message with content"
        assert result["should_process"] is True

    @pytest.mark.asyncio
    async def test_sync_messages_api_error(self, patch_redis: FakeRedis) -> None:
        """Test handling of API error response."""
        node = WeComCustomerServiceSyncNode(
            name="wecom_cs_sync",
            open_kf_id="wkABC123",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": {
                    "access_token": "test_token",
                    "expires_in": 7200,
                },
            },
        )

        sync_response = MagicMock()
        sync_response.json.return_value = {
            "errcode": 95011,
            "errmsg": "token is invalid",
        }
        sync_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=sync_response)
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert result["errcode"] == 95011


# WeComCustomerServiceSendNode tests


class TestWeComCustomerServiceSendNode:
    """Tests for WeComCustomerServiceSendNode."""

    @pytest.mark.asyncio
    async def test_send_message_success(self, patch_redis: FakeRedis) -> None:
        """Test successful message send."""
        node = WeComCustomerServiceSendNode(
            name="wecom_cs_send",
            message="Hello, welcome!",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": {
                    "access_token": "test_token",
                    "expires_in": 7200,
                },
                "wecom_cs_sync": {
                    "open_kf_id": "wkABC123",
                    "external_userid": "wmXYZ789",
                },
            },
        )

        send_response = MagicMock()
        send_response.json.return_value = {
            "errcode": 0,
            "errmsg": "ok",
            "msgid": "msg123",
        }
        send_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=send_response)
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        assert result["errcode"] == 0
        assert result["msgid"] == "msg123"

        # Verify the correct API was called
        call_kwargs = mock_client.post.call_args
        assert "kf/send_msg" in call_kwargs[0][0]
        assert call_kwargs[1]["json"]["touser"] == "wmXYZ789"
        assert call_kwargs[1]["json"]["open_kfid"] == "wkABC123"
        assert call_kwargs[1]["json"]["text"]["content"] == "Hello, welcome!"

    @pytest.mark.asyncio
    async def test_send_message_no_access_token(self) -> None:
        """Test send fails without access token."""
        node = WeComCustomerServiceSendNode(
            name="wecom_cs_send",
            message="Hello!",
        )

        state = State(messages=[], inputs={}, node_results={})

        result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert "access token" in result["error"]

    @pytest.mark.asyncio
    async def test_send_message_no_open_kf_id(self) -> None:
        """Test send fails without open_kf_id."""
        node = WeComCustomerServiceSendNode(
            name="wecom_cs_send",
            message="Hello!",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": {
                    "access_token": "test_token",
                    "expires_in": 7200,
                },
            },
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert "open_kf_id" in result["error"]

    @pytest.mark.asyncio
    async def test_send_message_no_external_userid(self) -> None:
        """Test send fails without external_userid."""
        node = WeComCustomerServiceSendNode(
            name="wecom_cs_send",
            open_kf_id="wkABC123",
            message="Hello!",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": {
                    "access_token": "test_token",
                    "expires_in": 7200,
                },
            },
        )

        result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert "external_userid" in result["error"]

    @pytest.mark.asyncio
    async def test_send_message_api_error(self, patch_redis: FakeRedis) -> None:
        """Test handling of API error response."""
        node = WeComCustomerServiceSendNode(
            name="wecom_cs_send",
            open_kf_id="wkABC123",
            external_userid="wmXYZ789",
            message="Hello!",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": {
                    "access_token": "test_token",
                    "expires_in": 7200,
                },
            },
        )

        send_response = MagicMock()
        send_response.json.return_value = {
            "errcode": 95017,
            "errmsg": "invalid external_userid",
        }
        send_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=send_response)
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is True
        assert result["errcode"] == 95017
        assert result["errmsg"] == "invalid external_userid"

    @pytest.mark.asyncio
    async def test_send_message_api_error_raises_when_configured(
        self, patch_redis: FakeRedis
    ) -> None:
        """Opt-in strict mode raises instead of returning an error result."""
        node = WeComCustomerServiceSendNode(
            name="wecom_cs_send",
            open_kf_id="wkABC123",
            external_userid="wmXYZ789",
            message="Hello!",
            raise_on_error=True,
        )

        state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": {
                    "access_token": "test_token",
                    "expires_in": 7200,
                },
            },
        )

        send_response = MagicMock()
        send_response.json.return_value = {
            "errcode": 95017,
            "errmsg": "invalid external_userid",
        }
        send_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=send_response)
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ValueError, match="WeCom customer service send failed"):
                await node.run(state, RunnableConfig())

    @pytest.mark.asyncio
    async def test_send_non_text_message_falls_back_to_text(
        self, patch_redis: FakeRedis
    ) -> None:
        """Test that non-text message types fall back to text format."""
        node = WeComCustomerServiceSendNode(
            name="wecom_cs_send",
            message="Hello from non-text!",
            msg_type="markdown",  # Non-text type
        )

        state = State(
            messages=[],
            inputs={},
            node_results={
                "get_access_token": {
                    "access_token": "test_token",
                    "expires_in": 7200,
                },
                "wecom_cs_sync": {
                    "open_kf_id": "wkABC123",
                    "external_userid": "wmXYZ789",
                },
            },
        )

        send_response = MagicMock()
        send_response.json.return_value = {
            "errcode": 0,
            "errmsg": "ok",
            "msgid": "msg123",
        }
        send_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=send_response)
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        # Even though msg_type is markdown, it should still use text format
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[1]["json"]["text"]["content"] == "Hello from non-text!"

    @pytest.mark.asyncio
    async def test_send_message_generates_fallback_msgid(
        self, patch_redis: FakeRedis
    ) -> None:
        """Fallback to hashed msgid when API response omits msgid."""
        node = WeComCustomerServiceSendNode(
            name="wecom_cs_send",
            open_kf_id="wkABC123",
            external_userid="wmXYZ789",
            message="Fallback ID",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={"get_access_token": {"access_token": "test_token"}},
        )

        send_response = MagicMock()
        send_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        send_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=send_response)
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            with patch("orcheo.nodes.wecom.hashlib.sha1") as mock_sha1:
                mock_sha1.return_value.hexdigest.return_value = "fallback-hash"
                result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        mock_sha1.assert_called_once()
        assert result["msgid"] is None

    @pytest.mark.asyncio
    async def test_send_message_redis_write_failure_logged(
        self, patch_redis: FakeRedis
    ) -> None:
        """Redis write failures are caught and do not block API success."""
        node = WeComCustomerServiceSendNode(
            name="wecom_cs_send",
            open_kf_id="wkABC123",
            external_userid="wmXYZ789",
            message="Hello!",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={"get_access_token": {"access_token": "test_token"}},
        )

        send_response = MagicMock()
        send_response.json.return_value = {
            "errcode": 0,
            "errmsg": "ok",
            "msgid": "msg123",
        }
        send_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=send_response)
        mock_client.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            with patch(
                "orcheo.nodes.wecom._store_cs_message",
                new=AsyncMock(side_effect=redis.RedisError("write failure")),
            ):
                result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False

    @pytest.mark.asyncio
    async def test_send_message_closes_redis_client(self) -> None:
        node = WeComCustomerServiceSendNode(
            name="wecom_cs_send",
            open_kf_id="wkABC123",
            external_userid="wmXYZ789",
            message="Close Redis!",
        )

        state = State(
            messages=[],
            inputs={},
            node_results={"get_access_token": {"access_token": "test_token"}},
        )

        send_response = MagicMock()
        send_response.json.return_value = {
            "errcode": 0,
            "errmsg": "ok",
            "msgid": "msg123",
        }
        send_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=send_response)
        mock_client.aclose = AsyncMock()

        fake_redis = AsyncMock()
        fake_redis.aclose = AsyncMock()

        with patch("orcheo.nodes.wecom.httpx.AsyncClient", return_value=mock_client):
            with patch("redis.asyncio.from_url", return_value=fake_redis):
                result = await node.run(state, RunnableConfig())

        assert result["is_error"] is False
        fake_redis.aclose.assert_awaited_once()


# get_access_token_from_state tests


class TestGetAccessTokenFromState:
    """Tests for get_access_token_from_state helper function."""

    def test_returns_token_from_get_access_token(self) -> None:
        """Test extracting token from get_access_token key."""
        results = {"get_access_token": {"access_token": "token123"}}
        assert get_access_token_from_state(results) == "token123"

    def test_returns_token_from_get_cs_access_token(self) -> None:
        """Test extracting token from get_cs_access_token key."""
        results = {"get_cs_access_token": {"access_token": "cs_token456"}}
        assert get_access_token_from_state(results) == "cs_token456"

    def test_returns_none_when_no_token(self) -> None:
        """Test returns None when no token in results."""
        results = {"some_other_key": {"data": "value"}}
        assert get_access_token_from_state(results) is None

    def test_returns_none_when_token_result_not_dict(self) -> None:
        """Test returns None when token result is not a dict."""
        results = {"get_access_token": "not_a_dict"}
        assert get_access_token_from_state(results) is None

    def test_returns_none_when_token_is_empty(self) -> None:
        """Test returns None when access_token is empty string."""
        results = {"get_access_token": {"access_token": ""}}
        assert get_access_token_from_state(results) is None


class TestWeComNodeCallBehaviors:
    """Tests for TaskNode __call__ message merging."""

    @pytest.mark.asyncio
    async def test_events_parser_call_merges_agent_messages(self) -> None:
        node = WeComEventsParserNode(
            name="wecom_parser",
            token="token",
            encoding_aes_key="key",
            corp_id="corp123",
        )
        run_result = {"agent_messages": [{"role": "user", "content": "hi"}]}

        with patch.object(
            WeComEventsParserNode,
            "run",
            new=AsyncMock(return_value=run_result),
        ):
            output = await node.__call__(
                State(messages=[], inputs={}, node_results={}), RunnableConfig()
            )

        assert "agent_messages" not in output["node_results"]["wecom_parser"]
        assert output["messages"] == run_result["agent_messages"]

    @pytest.mark.asyncio
    async def test_customer_service_sync_call_merges_agent_messages(self) -> None:
        node = WeComCustomerServiceSyncNode(name="wecom_cs_sync")
        run_result = {"agent_messages": [{"role": "assistant", "content": "hi"}]}

        with patch.object(
            WeComCustomerServiceSyncNode,
            "run",
            new=AsyncMock(return_value=run_result),
        ):
            output = await node.__call__(
                State(messages=[], inputs={}, node_results={}), RunnableConfig()
            )

        assert "agent_messages" not in output["node_results"]["wecom_cs_sync"]
        assert output["messages"] == run_result["agent_messages"]


class TestWeComHelperFunctions:
    """Tests for WeCom helper utilities."""

    def test_coerce_msgtime_handles_various_inputs(self, monkeypatch):
        monkeypatch.setattr("orcheo.nodes.wecom.time.time", lambda: 1000)
        assert _coerce_msgtime(None) == 1000
        assert _coerce_msgtime(0) == 1000
        assert _coerce_msgtime(1_600_000_000) == 1_600_000_000
        assert _coerce_msgtime(1_500_000_000_000) == 1_500_000_000_000 // 1000

    def test_compute_message_ttl_uses_constant(self, monkeypatch):
        monkeypatch.setattr("orcheo.nodes.wecom.time.time", lambda: 1_000_000)
        expected = max(0, 1_000_000 + CS_MESSAGE_TTL_SECONDS - 1_000_000)
        assert _compute_message_ttl(1_000_000) == expected

    def test_build_cs_message_id_prefers_existing_id(self):
        assert _build_cs_message_id({"msgid": "abc"}) == "abc"

    def test_build_cs_message_id_hashes_payload(self):
        payload = {"z": 1, "a": 2}
        expected = hashlib.sha1(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert _build_cs_message_id(payload) == expected

    @pytest.mark.asyncio
    async def test_fetch_cs_message_history_returns_empty_when_index_missing(
        self, fake_redis
    ):
        history = await _fetch_cs_message_history(fake_redis, "user")
        assert history == []

    @pytest.mark.asyncio
    async def test_store_cs_message_detects_existing_key(self, fake_redis):
        message_key = _cs_message_key("user", "msg")
        fake_redis.store[message_key] = "value"
        result = await _store_cs_message(
            fake_redis,
            message_key,
            _cs_message_index_key("user"),
            int(time.time()),
            {"content": "value"},
        )
        assert result == (False, True)

    @pytest.mark.asyncio
    async def test_store_cs_message_handles_expired_ttl(self, fake_redis, monkeypatch):
        msgtime = 1_000
        monkeypatch.setattr(
            "orcheo.nodes.wecom.time.time",
            lambda: msgtime + CS_MESSAGE_TTL_SECONDS + 5,
        )
        message_key = _cs_message_key("user", "expired")
        result = await _store_cs_message(
            fake_redis,
            message_key,
            _cs_message_index_key("user"),
            msgtime,
            {"content": "dead"},
        )
        assert result == (False, False)

    @pytest.mark.asyncio
    async def test_store_cs_message_success(self, fake_redis, monkeypatch):
        msgtime = 1_000_000
        monkeypatch.setattr("orcheo.nodes.wecom.time.time", lambda: msgtime)
        message_key = _cs_message_key("user", "fresh")
        result = await _store_cs_message(
            fake_redis,
            message_key,
            _cs_message_index_key("user"),
            msgtime,
            {"content": "alive"},
        )
        assert result == (True, False)
        assert fake_redis.store[message_key] == json.dumps({"content": "alive"})

    @pytest.mark.asyncio
    async def test_fetch_cs_message_history_filters_stale_entries(self, fake_redis):
        index_key = _cs_message_index_key("user")
        keys = [
            _cs_message_key("user", "one"),
            _cs_message_key("user", "two"),
            _cs_message_key("user", "three"),
        ]
        fake_redis.zsets[index_key] = {keys[0]: 1, keys[1]: 2, keys[2]: 3}
        fake_redis.store[keys[0]] = '{"content":"v1"}'
        fake_redis.store[keys[2]] = "not json"

        history = await _fetch_cs_message_history(fake_redis, "user")

        assert history == [{"content": "v1"}]
        assert keys[1] not in fake_redis.zsets[index_key]
        assert keys[2] not in fake_redis.zsets[index_key]

    def test_build_cs_sync_payload_adds_cursor(self):
        base = {"open_kfid": "open"}
        payload = _build_cs_sync_payload(base, "cursor123")
        assert payload["cursor"] == "cursor123"
        assert base == {"open_kfid": "open"}

    def test_normalize_cs_sync_response_converts_flags(self):
        data = {
            "errcode": 1,
            "errmsg": "bad",
            "msg_list": [{"msg": "value"}],
            "next_cursor": "next",
            "has_more": 1,
        }
        errcode, errmsg, msg_list, next_cursor, has_more = _normalize_cs_sync_response(
            data
        )
        assert errcode == 1
        assert errmsg == "bad"
        assert next_cursor == "next"
        assert has_more is True
        assert msg_list == [{"msg": "value"}]

    def test_build_cs_inbound_entry_validation(self):
        empty_origin = _build_cs_inbound_entry("open", {"origin": 2, "msgtype": "text"})
        assert empty_origin is None
        empty_data = _build_cs_inbound_entry(
            "open",
            {
                "origin": 3,
                "msgtype": "text",
                "external_userid": "",
                "text": {"content": "hi"},
            },
        )
        assert empty_data is None

    def test_build_cs_inbound_entry_returns_entry(self):
        msg = {
            "origin": 3,
            "msgtype": "text",
            "external_userid": "user",
            "text": {"content": "hello"},
            "msgtime": 1_234,
            "msgid": "abc123",
        }
        entry = _build_cs_inbound_entry("open", msg)
        assert entry is not None
        assert entry["id"] == "abc123"
        assert entry["content"] == "hello"

    def test_pick_latest_message_prefers_newer(self):
        current = {"msgtime": 10}
        candidate = {"msgtime": 20}
        assert _pick_latest_message(current, candidate) == candidate
        assert _pick_latest_message(None, candidate) == candidate
        assert _pick_latest_message(current, None) == current

    @pytest.mark.asyncio
    async def test_process_cs_page_without_redis_client(self):
        msg = {
            "origin": 3,
            "msgtype": "text",
            "external_userid": "user",
            "text": {"content": "hello"},
            "msgtime": 1,
            "msgid": "id1",
        }
        new_messages, latest, saw_existing, redis_failed = await _process_cs_page(
            [msg], "open", None
        )
        assert len(new_messages) == 1
        assert latest["external_userid"] == "user"
        assert saw_existing is False
        assert redis_failed is False

    @pytest.mark.asyncio
    async def test_process_cs_page_handles_redis_write_error(self, fake_redis):
        msg = {
            "origin": 3,
            "msgtype": "text",
            "external_userid": "user",
            "text": {"content": "hello"},
            "msgtime": 1,
            "msgid": "id1",
        }
        with patch(
            "orcheo.nodes.wecom._store_cs_entry",
            new=AsyncMock(side_effect=redis.RedisError("boom")),
        ):
            new_messages, latest, saw_existing, redis_failed = await _process_cs_page(
                [msg], "open", fake_redis
            )
        assert new_messages
        assert latest is not None
        assert saw_existing is False
        assert redis_failed is True

    @pytest.mark.asyncio
    async def test_process_cs_page_detects_existing_entry(self, fake_redis):
        msg = {
            "origin": 3,
            "msgtype": "text",
            "external_userid": "user",
            "text": {"content": "hello"},
            "msgtime": 1,
            "msgid": "id_existing",
        }
        with patch(
            "orcheo.nodes.wecom._store_cs_entry",
            new=AsyncMock(return_value=(False, True)),
        ):
            new_messages, latest, saw_existing, redis_failed = await _process_cs_page(
                [msg], "open", fake_redis
            )
        assert new_messages == []
        assert saw_existing is True
        assert redis_failed is False

    def test_resolve_cs_sync_inputs_prefers_explicit_values(self):
        direct = _resolve_cs_sync_inputs("open", "token", {"open_kf_id": "parser"})
        assert direct == ("open", "token")
        fallback = _resolve_cs_sync_inputs(
            None, None, {"open_kf_id": "parser", "kf_token": "abc"}
        )
        assert fallback == ("parser", "abc")

    def test_create_cs_redis_client_handles_unavailable(self):
        with patch(
            "redis.asyncio.from_url",
            side_effect=redis.RedisError("unavailable"),
        ):
            assert _create_cs_redis_client() is None

    @pytest.mark.asyncio
    async def test_close_cs_redis_client_ignores_errors(self):
        class Dummy:
            async def aclose(self) -> None:
                raise redis.RedisError("boom")

        await _close_cs_redis_client(Dummy())

    @pytest.mark.asyncio
    async def test_close_cs_redis_client_handles_none(self):
        await _close_cs_redis_client(None)

    @pytest.mark.asyncio
    async def test_fetch_cs_page_includes_cursor(self):
        client = AsyncMock()
        response = MagicMock()
        response.json.return_value = {"errcode": 0}
        response.raise_for_status = MagicMock()
        client.post = AsyncMock(return_value=response)
        payload = {"open_kfid": "open"}
        result = await _fetch_cs_page(
            client, "url", {"access_token": "token"}, payload, "cursor123"
        )
        client.post.assert_awaited_once()
        assert result == {"errcode": 0}
        assert client.post.call_args[1]["json"]["cursor"] == "cursor123"

    @pytest.mark.asyncio
    async def test_pull_cs_pages_handles_api_error(self):
        client = AsyncMock()
        with patch(
            "orcheo.nodes.wecom._fetch_cs_page",
            new=AsyncMock(return_value={"errcode": 123, "errmsg": "bad"}),
        ):
            result = await _pull_cs_pages(client, None, "url", {}, {}, None)
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_pull_cs_pages_closes_redis_on_failure(self, fake_redis):
        client = AsyncMock()
        normalized = MagicMock(return_value=(0, "", [], "", False))
        processed = AsyncMock(
            return_value=([], {"external_userid": "user"}, True, True)
        )
        closer = AsyncMock()
        with (
            patch(
                "orcheo.nodes.wecom._fetch_cs_page",
                new=AsyncMock(
                    return_value={
                        "errcode": 0,
                        "msg_list": [],
                        "next_cursor": "",
                        "has_more": 0,
                    }
                ),
            ),
            patch(
                "orcheo.nodes.wecom._normalize_cs_sync_response",
                new=normalized,
            ),
            patch(
                "orcheo.nodes.wecom._process_cs_page",
                new=processed,
            ),
            patch(
                "orcheo.nodes.wecom._close_cs_redis_client",
                new=closer,
            ),
        ):
            result = await _pull_cs_pages(
                client,
                fake_redis,
                "url",
                {"access_token": "token"},
                {"open_kfid": "open"},
                None,
            )
        closer.assert_awaited_once_with(fake_redis)
        assert result["redis_client"] is None

    @pytest.mark.asyncio
    async def test_pull_cs_pages_uses_cursor_for_pagination(self):
        client = AsyncMock()
        fetch = AsyncMock(
            side_effect=[
                {"errcode": 0, "msg_list": [], "next_cursor": "next123", "has_more": 1},
                {"errcode": 0, "msg_list": [], "next_cursor": "", "has_more": 0},
            ]
        )
        normalized = MagicMock(
            side_effect=[(0, "", [], "next123", True), (0, "", [], "", False)]
        )
        processed = AsyncMock(return_value=([], None, False, False))
        with (
            patch("orcheo.nodes.wecom._fetch_cs_page", new=fetch),
            patch("orcheo.nodes.wecom._normalize_cs_sync_response", new=normalized),
            patch("orcheo.nodes.wecom._process_cs_page", new=processed),
        ):
            result = await _pull_cs_pages(
                client,
                None,
                "url",
                {"access_token": "token"},
                {"open_kfid": "open"},
                None,
            )
        assert fetch.call_count == 2
        assert fetch.call_args_list[1][0][4] == "next123"
        assert result["has_more"] is False

    @pytest.mark.asyncio
    async def test_resolve_cs_history_returns_empty_without_latest(self):
        external_userid, history = await _resolve_cs_history(None, None, [])
        assert external_userid == ""
        assert history == []

    @pytest.mark.asyncio
    async def test_resolve_cs_history_falls_back_to_new_messages(self):
        latest = {"external_userid": "user"}
        new_messages = [
            {"external_userid": "user", "msgtime": 200},
            {"external_userid": "user", "msgtime": 100},
        ]
        external_userid, history = await _resolve_cs_history(None, latest, new_messages)
        assert external_userid == "user"
        assert history[0]["msgtime"] == 100

    @pytest.mark.asyncio
    async def test_resolve_cs_history_handles_missing_external_userid(self):
        latest = {"external_userid": ""}
        external_userid, history = await _resolve_cs_history(None, latest, [])
        assert external_userid == ""
        assert history == []

    @pytest.mark.asyncio
    async def test_resolve_cs_history_prefers_redis_history(self, fake_redis):
        latest = {"external_userid": "user"}
        history_payload = [{"external_userid": "user", "msgtime": 5}]
        with patch(
            "orcheo.nodes.wecom._fetch_cs_message_history",
            new=AsyncMock(return_value=history_payload),
        ):
            external_userid, history = await _resolve_cs_history(fake_redis, latest, [])
        assert external_userid == "user"
        assert history == history_payload

    @pytest.mark.asyncio
    async def test_resolve_cs_history_handles_redis_error(self, fake_redis):
        latest = {"external_userid": "user"}
        fallback_messages = [{"external_userid": "user", "msgtime": 1}]
        with patch(
            "orcheo.nodes.wecom._fetch_cs_message_history",
            new=AsyncMock(side_effect=redis.RedisError("boom")),
        ):
            external_userid, history = await _resolve_cs_history(
                fake_redis, latest, fallback_messages
            )
        assert external_userid == "user"
        assert history == fallback_messages

    def test_select_cs_customer_nickname_handles_various(self):
        assert _select_cs_customer_nickname("notalist", "user") == ""
        matches = [
            {"external_userid": "user", "nickname": "Nick"},
            {"external_userid": "other", "nickname": "Other"},
        ]
        assert _select_cs_customer_nickname(matches, "user") == "Nick"
        fallback = [{"external_userid": "none", "name": "Name"}]
        assert _select_cs_customer_nickname(fallback, "user") == "Name"

    def test_select_cs_customer_nickname_skips_non_mapping_entries(self):
        customers = [
            "invalid",
            {"external_userid": "user", "nickname": "Nick"},
        ]
        assert _select_cs_customer_nickname(customers, "user") == "Nick"

    def test_select_cs_customer_nickname_returns_empty_when_no_mapping(self):
        customers = ["invalid", 123]
        assert _select_cs_customer_nickname(customers, "user") == ""

    @pytest.mark.asyncio
    async def test_fetch_cs_customer_username_returns_empty_on_missing_userid(self):
        client = AsyncMock()
        result = await _fetch_cs_customer_username(client, "token", "")
        assert result == ""

    @pytest.mark.asyncio
    async def test_fetch_cs_customer_username_handles_http_error(self):
        client = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.HTTPError("fail"))
        result = await _fetch_cs_customer_username(client, "token", "user")
        assert result == ""

    @pytest.mark.asyncio
    async def test_fetch_cs_customer_username_handles_errcode(self):
        client = AsyncMock()
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {"errcode": 1, "errmsg": "bad"}
        client.post = AsyncMock(return_value=response)
        result = await _fetch_cs_customer_username(client, "token", "user")
        assert result == ""

    @pytest.mark.asyncio
    async def test_fetch_cs_customer_username_returns_nickname(self):
        client = AsyncMock()
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "errcode": 0,
            "customer_list": [{"external_userid": "user", "nickname": "Display"}],
        }
        client.post = AsyncMock(return_value=response)
        result = await _fetch_cs_customer_username(client, "token", "user")
        assert result == "Display"

    def test_build_agent_messages_filters_roles(self):
        history = [
            {"role": "user", "content": " hi "},
            {"role": "ai", "content": "assistant"},
            {"role": "assistant", "content": " "},
            "invalid",
        ]
        messages = WeComCustomerServiceSyncNode._build_agent_messages(history)
        assert messages == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "assistant"},
        ]

    def test_build_agent_messages_handles_non_list(self):
        assert WeComCustomerServiceSyncNode._build_agent_messages(None) == []

    def test_build_agent_messages_skips_non_mapping_items(self):
        history = [
            "bad",
            {"role": "user", "content": "hello"},
        ]
        messages = WeComCustomerServiceSyncNode._build_agent_messages(history)
        assert messages == [{"role": "user", "content": "hello"}]


def test_normalize_optional_runtime_value_template_returns_none() -> None:
    """Returns None when value contains template syntax {{...}} (line 56)."""
    from orcheo.nodes.wecom import _normalize_optional_runtime_value

    assert _normalize_optional_runtime_value("{{node_results.token}}") is None
    assert _normalize_optional_runtime_value("  {{some.value}}  ") is None


def test_wecom_crypto_module_reexports_helpers() -> None:
    assert wecom_crypto.decrypt_wecom_message is decrypt_wecom_message
    assert wecom_crypto.verify_wecom_signature is verify_wecom_signature
