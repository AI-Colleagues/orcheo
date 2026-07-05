"""WeCom integration nodes for Orcheo.

This module provides nodes for integrating with WeCom (企业微信) APIs:
- WeComEventsParserNode: Validates signatures and parses callback payloads
- WeComAccessTokenNode: Fetches WeCom access tokens
- WeComSendMessageNode: Sends messages to WeCom chats
- WeComCustomerServiceSyncNode: Syncs messages from Customer Service (微信客服)
- WeComCustomerServiceSendNode: Sends messages via Customer Service API
"""

from __future__ import annotations
import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree
import httpx
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


if TYPE_CHECKING:
    import redis.asyncio as redis


logger = logging.getLogger(__name__)

# WeCom encryption constants
AES_BLOCK_SIZE = 32
PKCS7_PAD_MIN = 1
PKCS7_PAD_MAX = 32
RANDOM_PREFIX_LEN = 16
MSG_LEN_BYTES = 4
MIN_DECRYPTED_LEN = RANDOM_PREFIX_LEN + MSG_LEN_BYTES
CS_MESSAGE_TTL_SECONDS = 3 * 24 * 60 * 60
CS_REDIS_PREFIX = "wecom:cs"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DEFAULT_TIMEOUT = 10.0


def _normalize_optional_runtime_value(value: str | None) -> str | None:
    """Return a usable runtime value or ``None`` for blanks/templates."""
    if not value:
        return None
    stripped = value.strip()
    if not stripped or TaskNode.contains_template(stripped):
        return None
    return stripped


def verify_wecom_signature(
    token: str,
    timestamp: str,
    nonce: str,
    encrypted_msg: str,
    signature: str,
    log_prefix: str = "WeCom",
) -> None:
    """Verify WeCom message signature.

    Args:
        token: WeCom callback token.
        timestamp: Request timestamp.
        nonce: Request nonce.
        encrypted_msg: Encrypted message or echostr.
        signature: Expected signature.
        log_prefix: Prefix for log messages (e.g., "WeCom", "WeCom AI bot").

    Raises:
        ValueError: If signature verification fails.
    """
    items = sorted([token, timestamp, nonce, encrypted_msg])
    sha1 = hashlib.sha1("".join(items).encode()).hexdigest()
    if not hmac.compare_digest(sha1, signature):
        logger.warning(
            "%s signature verification failed",
            log_prefix,
            extra={
                "event": f"{log_prefix.lower().replace(' ', '_')}_signature_validation",
                "status": "failed",
            },
        )
        msg = f"{log_prefix} signature verification failed"
        raise ValueError(msg)


def decrypt_wecom_message(
    encrypt: str,
    encoding_aes_key: str,
    expected_receive_id: str | None = None,
    log_prefix: str = "WeCom",
) -> str:
    """Decrypt WeCom encrypted message.

    Args:
        encrypt: Base64-encoded encrypted message.
        encoding_aes_key: WeCom AES key (43 chars, will be padded).
        expected_receive_id: Optional receive_id/corp_id to validate.
        log_prefix: Prefix for log messages (e.g., "WeCom", "WeCom AI bot").

    Returns:
        Decrypted message string.

    Raises:
        ValueError: If decryption or validation fails.
    """
    from Crypto.Cipher import AES

    aes_key = base64.b64decode(encoding_aes_key + "=")
    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    decrypted = cipher.decrypt(base64.b64decode(encrypt))

    # Remove PKCS7 padding
    pad_len = decrypted[-1]
    if pad_len < PKCS7_PAD_MIN or pad_len > PKCS7_PAD_MAX:
        msg = f"{log_prefix} payload padding invalid"
        raise ValueError(msg)
    content = decrypted[:-pad_len]

    # Parse format: random(16) + msg_len(4) + msg + receive_id
    if len(content) < MIN_DECRYPTED_LEN:
        msg = f"{log_prefix} payload too short"
        raise ValueError(msg)
    msg_len = struct.unpack(">I", content[RANDOM_PREFIX_LEN:MIN_DECRYPTED_LEN])[0]
    msg_end = MIN_DECRYPTED_LEN + msg_len
    if msg_end > len(content):
        msg = f"{log_prefix} payload length mismatch"
        raise ValueError(msg)
    msg = content[MIN_DECRYPTED_LEN:msg_end].decode("utf-8")
    receive_id = content[msg_end:].decode("utf-8")
    if expected_receive_id and receive_id != expected_receive_id:
        event_name = f"{log_prefix.lower().replace(' ', '_')}_receive_id_validation"
        logger.warning(
            "%s receive_id validation failed",
            log_prefix,
            extra={
                "event": event_name,
                "status": "failed",
                "receive_id": receive_id,
                "expected_receive_id": expected_receive_id,
            },
        )
        msg = f"{log_prefix} receive_id validation failed"
        raise ValueError(msg)
    return msg


def encrypt_wecom_message(message: str, encoding_aes_key: str, receive_id: str) -> str:
    """Encrypt a reply payload using WeCom format.

    Args:
        message: Message content to encrypt.
        encoding_aes_key: WeCom AES key (43 chars, will be padded).
        receive_id: Receive ID to append to payload.

    Returns:
        Base64-encoded encrypted message.
    """
    from Crypto.Cipher import AES

    aes_key = base64.b64decode(encoding_aes_key + "=")
    random_bytes = os.urandom(RANDOM_PREFIX_LEN)
    msg_bytes = message.encode("utf-8")
    msg_len = struct.pack(">I", len(msg_bytes))
    payload = random_bytes + msg_len + msg_bytes + receive_id.encode("utf-8")

    pad_len = AES_BLOCK_SIZE - (len(payload) % AES_BLOCK_SIZE)
    payload += bytes([pad_len] * pad_len)

    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    encrypted = cipher.encrypt(payload)
    return base64.b64encode(encrypted).decode()


@registry.register(
    NodeMetadata(
        name="WeComEventsParserNode",
        description="Validate WeCom signatures and parse callback payloads",
        category="wecom",
    )
)
class WeComEventsParserNode(TaskNode):
    """Validate WeCom signatures and parse callback payloads.

    Handles:
    - URL verification (GET with echostr)
    - Message decryption for encrypted callbacks
    - Signature validation using Token
    - Filtering for direct messages only
    """

    token: str = "[[wecom_token]]"
    """WeCom callback token for signature validation (from Orcheo vault)."""
    encoding_aes_key: str = "[[wecom_encoding_aes_key]]"
    """WeCom AES key for payload decryption (from Orcheo vault)."""
    corp_id: str = Field(
        default="[[wecom_corp_id]]",
        description="WeCom corp ID for decryption validation",
    )
    timestamp_tolerance_seconds: int = Field(
        default=300,
        description="Maximum age for WeCom signature timestamps",
    )
    allowlist_user_ids: list[str] | None = Field(
        default=None,
        description="Optional allowlist of WeCom user IDs",
    )

    def parse_xml(self, xml_str: str) -> dict[str, str]:
        """Parse WeCom XML payload."""
        root = ElementTree.fromstring(xml_str)
        return {child.tag: (child.text or "") for child in root}

    def extract_inputs(self, state: State) -> dict[str, Any]:
        """Extract inputs from state."""
        state_dict = dict(state)
        raw_inputs = state_dict.get("inputs")
        if isinstance(raw_inputs, dict):
            return dict(raw_inputs)
        return state_dict

    def is_immediate_response_check(self, config: RunnableConfig) -> bool:
        """Check if this is a synchronous immediate-response check execution."""
        configurable = config.get("configurable", {})
        thread_id = configurable.get("thread_id", "")
        return thread_id.startswith("immediate-response-check")

    def success_response(self) -> dict[str, Any]:
        """Return a success immediate_response dict."""
        return {
            "content": "success",
            "content_type": "text/plain",
            "status_code": 200,
        }

    def add_immediate_response(
        self, result: dict[str, Any], is_sync_check: bool
    ) -> dict[str, Any]:
        """Add immediate_response to result if this is a sync check."""
        if is_sync_check:
            result["immediate_response"] = self.success_response()
        else:
            result.setdefault("immediate_response", None)
        return result

    def is_direct_message(self, msg_type: str, chat_id: str, content: str) -> bool:
        """Return whether the message is a direct message to the app."""
        if chat_id:
            return False
        if msg_type != "text":
            return False
        return bool(content.strip())

    def is_customer_service_event(self, msg_data: dict[str, str]) -> bool:
        """Return whether this is a Customer Service (微信客服) event.

        External WeChat users send messages through Customer Service,
        which triggers a kf_msg_or_event callback. This callback only signals
        that new messages are available; actual messages must be fetched
        via the sync_msg API.
        """
        msg_type = msg_data.get("MsgType", "").strip().lower()
        event = msg_data.get("Event", "").strip().lower()
        return msg_type == "event" and event == "kf_msg_or_event"

    def is_allowlisted_user(self, user_id: str) -> bool:
        """Return whether the user is allowed to send messages."""
        if not self.allowlist_user_ids:
            return True
        return user_id in self.allowlist_user_ids

    def validate_timestamp(self, timestamp: str) -> bool:
        """Validate that the timestamp is within the tolerance window.

        Returns True if valid, False if invalid or outside tolerance.
        """
        if not self.timestamp_tolerance_seconds:
            return True
        try:
            ts = int(timestamp)
        except ValueError:
            logger.warning(
                "WeCom request timestamp invalid",
                extra={
                    "event": "wecom_timestamp_validation",
                    "status": "failed",
                    "timestamp": timestamp,
                },
            )
            return False
        now = int(time.time())
        if abs(now - ts) > self.timestamp_tolerance_seconds:
            logger.warning(
                "WeCom request timestamp outside tolerance window",
                extra={
                    "event": "wecom_timestamp_validation",
                    "status": "failed",
                    "timestamp": timestamp,
                    "tolerance_seconds": self.timestamp_tolerance_seconds,
                },
            )
            return False
        return True

    def handle_internal_message(
        self, msg_data: dict[str, str], is_sync_check: bool
    ) -> dict[str, Any]:
        """Handle internal WeCom user message."""
        msg_type = msg_data.get("MsgType", "").strip().lower()
        chat_id = msg_data.get("ChatId", "")
        content = msg_data.get("Content", "")
        user_id = msg_data.get("FromUserName", "")

        # Ignore group chat messages; only respond to direct messages.
        if chat_id:
            logger.info(
                "WeCom group message ignored",
                extra={
                    "event": "wecom_message_filter",
                    "status": "ignored",
                    "chat_id": chat_id,
                },
            )
            return self.add_immediate_response(
                {
                    "is_verification": False,
                    "is_customer_service": False,
                    "event_type": msg_type,
                    "chat_id": chat_id,
                    "should_process": False,
                },
                is_sync_check,
            )

        # Direct message detection (no ChatId).
        is_direct_message = self.is_direct_message(msg_type, chat_id, content)
        is_allowlisted = self.is_allowlisted_user(user_id)
        if is_direct_message and not is_allowlisted:
            logger.warning(
                "WeCom user rejected by allowlist",
                extra={
                    "event": "wecom_allowlist_validation",
                    "status": "failed",
                    "user_id": user_id,
                },
            )

        should_continue = is_direct_message and is_allowlisted

        # For sync checks, return immediate success to avoid WeCom retries.
        agent_messages: list[dict[str, str]] = []
        content_stripped = content.strip()
        if should_continue and content_stripped:
            agent_messages = [{"role": "user", "content": content_stripped}]

        result: dict[str, Any] = {
            "is_verification": False,
            "is_customer_service": False,
            "event_type": msg_type,
            "chat_id": chat_id,
            "user": user_id,
            "content": content,
            "target_user": user_id,
            "should_process": should_continue,
            "agent_messages": agent_messages,
        }
        if is_sync_check:
            result["immediate_response"] = self.success_response()
        else:
            result["immediate_response"] = None
        return result

    def handle_customer_service_event(
        self, msg_data: dict[str, str], is_sync_check: bool
    ) -> dict[str, Any]:
        """Handle Customer Service (微信客服) event from external WeChat users.

        For CS events, don't set immediate_response in the parser. This allows
        the workflow to continue to CS sync and send during the sync check.
        The send node will set immediate_response after sending.
        """
        open_kf_id = msg_data.get("OpenKfId", "")
        kf_token = msg_data.get("Token", "")
        should_continue = bool(open_kf_id and kf_token)
        logger.info(
            "WeCom Customer Service event received",
            extra={
                "event": "wecom_customer_service",
                "status": "received",
                "open_kf_id": open_kf_id,
            },
        )
        result: dict[str, Any] = {
            "is_verification": False,
            "is_customer_service": True,
            "event_type": "kf_msg_or_event",
            "open_kf_id": open_kf_id,
            "kf_token": kf_token,
            "should_process": should_continue,
            "agent_messages": [],
        }
        if is_sync_check:
            result["immediate_response"] = self.success_response()
        else:
            result["immediate_response"] = None
        return result

    def handle_url_verification(
        self, timestamp: str, nonce: str, echostr: str, msg_signature: str
    ) -> dict[str, Any]:
        """Handle URL verification request (GET with echostr)."""
        verify_wecom_signature(
            self.token, timestamp, nonce, echostr, msg_signature, log_prefix="WeCom"
        )
        decrypted_echostr = decrypt_wecom_message(
            echostr, self.encoding_aes_key, self.corp_id, log_prefix="WeCom"
        )
        return {
            "is_verification": True,
            "should_process": False,
            "immediate_response": {
                "content": decrypted_echostr,
                "content_type": "text/plain",
                "status_code": 200,
            },
            "agent_messages": [],
        }

    def extract_body_string(self, inputs: dict[str, Any]) -> str:
        """Extract body string from inputs."""
        body = inputs.get("body", {})
        if isinstance(body, dict) and "raw" in body:
            return body["raw"]
        if isinstance(body, str):
            return body
        return str(body)

    def make_invalid_payload_response(self, is_sync_check: bool) -> dict[str, Any]:
        """Return a response for invalid/missing payload."""
        return self.add_immediate_response(
            {
                "is_verification": False,
                "event_type": None,
                "should_process": False,
                "agent_messages": [],
            },
            is_sync_check,
        )

    def decrypt_and_parse_message(
        self, encrypt: str, timestamp: str, nonce: str, msg_signature: str
    ) -> dict[str, str]:
        """Verify signature, validate timestamp, decrypt and parse message."""
        verify_wecom_signature(
            self.token, timestamp, nonce, encrypt, msg_signature, log_prefix="WeCom"
        )
        decrypted_xml = decrypt_wecom_message(
            encrypt, self.encoding_aes_key, self.corp_id, log_prefix="WeCom"
        )
        return self.parse_xml(decrypted_xml)

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Parse the WeCom callback payload and validate signatures."""
        is_sync_check = self.is_immediate_response_check(config)
        inputs = self.extract_inputs(state)
        query_params = inputs.get("query_params", {})

        msg_signature = query_params.get("msg_signature", "")
        timestamp = query_params.get("timestamp", "")
        nonce = query_params.get("nonce", "")
        echostr = query_params.get("echostr")

        # URL verification request (GET with echostr)
        if echostr:
            return self.handle_url_verification(
                timestamp, nonce, echostr, msg_signature
            )

        # Parse encrypted message body
        body_str = self.extract_body_string(inputs)
        xml_data = self.parse_xml(body_str)
        encrypt = xml_data.get("Encrypt", "")

        if not encrypt:
            logger.warning(
                "WeCom payload missing Encrypt element",
                extra={
                    "event": "wecom_payload_validation",
                    "status": "failed",
                },
            )
            return self.make_invalid_payload_response(is_sync_check)

        # Check timestamp tolerance before decryption
        if not self.validate_timestamp(timestamp):
            return self.make_invalid_payload_response(is_sync_check)

        # Decrypt and parse message
        msg_data = self.decrypt_and_parse_message(
            encrypt, timestamp, nonce, msg_signature
        )

        # Route to appropriate handler based on message type
        if self.is_customer_service_event(msg_data):
            return self.handle_customer_service_event(msg_data, is_sync_check)

        return self.handle_internal_message(msg_data, is_sync_check)

    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Execute the node and merge agent messages into state."""
        runnable = self.resolved_for_run(state, config=config)
        result = await runnable.run(state, config)
        serialized = runnable._serialize_result(result)
        output: dict[str, Any] = {"results": {self.name: serialized}}
        if isinstance(serialized, dict):  # pragma: no branch
            agent_messages = serialized.pop("agent_messages", None)
            if isinstance(agent_messages, list):
                output["messages"] = agent_messages
        return output


@registry.register(
    NodeMetadata(
        name="WeComAIBotEventsParserNode",
        description="Validate WeCom AI bot signatures and parse callbacks",
        category="wecom",
    )
)
class WeComAIBotEventsParserNode(TaskNode):
    """Validate WeCom AI bot signatures and parse callback payloads."""

    token: str = "[[wecom_aibot_token]]"
    """WeCom AI bot callback token (from Orcheo vault)."""
    encoding_aes_key: str = "[[wecom_aibot_encoding_aes_key]]"
    """WeCom AI bot AES key for payload decryption (from Orcheo vault)."""
    receive_id: str | None = Field(
        default=None,
        description="Optional receive_id for AI bot decryption validation",
    )
    timestamp_tolerance_seconds: int = Field(
        default=300,
        description="Maximum age for WeCom signature timestamps",
    )

    def extract_inputs(self, state: State) -> dict[str, Any]:
        """Extract inputs from state."""
        state_dict = dict(state)
        raw_inputs = state_dict.get("inputs")
        if isinstance(raw_inputs, dict):
            return dict(raw_inputs)
        return state_dict

    def extract_body_dict(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Extract JSON body as a dict."""
        body = inputs.get("body", {})
        if isinstance(body, dict) and "raw" in body:
            body = body.get("raw")
        if isinstance(body, str):
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, dict):
                return parsed
            return {}
        if isinstance(body, dict):
            return body
        return {}

    def validate_timestamp(self, timestamp: str) -> bool:
        """Validate that the timestamp is within the tolerance window."""
        if not self.timestamp_tolerance_seconds:
            return True
        try:
            ts = int(timestamp)
        except ValueError:
            logger.warning(
                "WeCom AI bot request timestamp invalid",
                extra={
                    "event": "wecom_aibot_timestamp_validation",
                    "status": "failed",
                    "timestamp": timestamp,
                },
            )
            return False
        now = int(time.time())
        if abs(now - ts) > self.timestamp_tolerance_seconds:
            logger.warning(
                "WeCom AI bot request timestamp outside tolerance window",
                extra={
                    "event": "wecom_aibot_timestamp_validation",
                    "status": "failed",
                    "timestamp": timestamp,
                    "tolerance_seconds": self.timestamp_tolerance_seconds,
                },
            )
            return False
        return True

    def is_immediate_response_check(self, config: RunnableConfig) -> bool:
        """Check if this is a synchronous immediate-response check execution."""
        configurable = config.get("configurable", {})
        thread_id = configurable.get("thread_id", "")
        return thread_id.startswith("immediate-response-check")

    def success_response(self) -> dict[str, Any]:
        """Return a success immediate_response dict."""
        return {
            "content": "success",
            "content_type": "text/plain",
            "status_code": 200,
        }

    def handle_url_verification(
        self, timestamp: str, nonce: str, echostr: str, msg_signature: str
    ) -> dict[str, Any]:
        """Handle AI bot URL verification request."""
        verify_wecom_signature(
            self.token,
            timestamp,
            nonce,
            echostr,
            msg_signature,
            log_prefix="WeCom AI bot",
        )
        decrypted_echostr = decrypt_wecom_message(
            echostr, self.encoding_aes_key, self.receive_id, log_prefix="WeCom AI bot"
        )
        return {
            "is_verification": True,
            "should_process": False,
            "immediate_response": {
                "content": decrypted_echostr,
                "content_type": "text/plain",
                "status_code": 200,
            },
        }

    def make_invalid_payload_response(self) -> dict[str, Any]:
        """Return a response for invalid/missing payload.

        Returns a success immediate_response to acknowledge the request to WeCom.
        This prevents WeCom from retrying the request while ensuring no workflow
        processing occurs for invalid payloads.
        """
        return {
            "is_verification": False,
            "should_process": False,
            "immediate_response": self.success_response(),
        }

    def parse_message(
        self, msg_data: dict[str, Any], is_sync_check: bool, use_passive_reply: bool
    ) -> dict[str, Any]:
        """Extract AI bot message fields.

        Args:
            msg_data: Decrypted message payload from WeCom.
            is_sync_check: True if this is an immediate-response-check execution.
            use_passive_reply: True if workflow uses passive reply mode.

        Behavior:
            - Passive reply mode: Don't set immediate_response. Let workflow continue
              to passive_reply node which will encrypt and return the actual reply.
            - Active reply mode + sync check: Set immediate_response to ack WeCom
              quickly, and should_process=True to queue async run.
            - Active reply mode + async run: No immediate_response, workflow
              continues to active_reply node to POST to response_url.
        """
        msg_type = str(msg_data.get("msgtype", "")).lower()
        chat_type = str(msg_data.get("chattype", ""))
        response_url = str(msg_data.get("response_url", ""))
        from_data = msg_data.get("from", {})
        user_id = ""
        if isinstance(from_data, dict):
            user_id = str(from_data.get("userid", ""))
        text_content = ""
        text_data = msg_data.get("text", {})
        if isinstance(text_data, dict):
            text_content = str(text_data.get("content", ""))

        # For passive reply: don't set immediate_response here, let passive_reply
        # node handle it. For active reply + sync check: ack quickly and queue async.
        if use_passive_reply:
            immediate_response = None
            should_process = True  # Let workflow continue to passive_reply node
        elif is_sync_check:
            immediate_response = self.success_response()
            should_process = True  # Queue async run for active_reply
        else:
            immediate_response = None
            should_process = True

        return {
            "is_verification": False,
            "should_process": should_process,
            "immediate_response": immediate_response,
            "msg_type": msg_type,
            "chat_type": chat_type,
            "response_url": response_url,
            "user": user_id,
            "content": text_content,
            "message": msg_data,
        }

    def get_use_passive_reply(self, state: State) -> bool:
        """Check if workflow is configured for passive reply mode."""
        state_config = state.get("config", {})
        if isinstance(state_config, dict):
            configurable = state_config.get("configurable", {})
            if isinstance(configurable, dict):
                return bool(configurable.get("use_passive_reply", False))
        return False

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Parse the WeCom AI bot callback payload and validate signatures."""
        is_sync_check = self.is_immediate_response_check(config)
        use_passive_reply = self.get_use_passive_reply(state)
        inputs = self.extract_inputs(state)
        query_params = inputs.get("query_params", {})

        msg_signature = query_params.get("msg_signature", "")
        timestamp = query_params.get("timestamp", "")
        nonce = query_params.get("nonce", "")
        echostr = query_params.get("echostr")

        if echostr:
            return self.handle_url_verification(
                timestamp, nonce, echostr, msg_signature
            )

        body = self.extract_body_dict(inputs)
        encrypt = body.get("encrypt", "")
        if not encrypt:
            logger.warning(
                "WeCom AI bot payload missing encrypt field",
                extra={
                    "event": "wecom_aibot_payload_validation",
                    "status": "failed",
                },
            )
            return self.make_invalid_payload_response()

        if not self.validate_timestamp(timestamp):
            return self.make_invalid_payload_response()

        verify_wecom_signature(
            self.token,
            timestamp,
            nonce,
            encrypt,
            msg_signature,
            log_prefix="WeCom AI bot",
        )
        decrypted_json = decrypt_wecom_message(
            encrypt, self.encoding_aes_key, self.receive_id, log_prefix="WeCom AI bot"
        )
        try:
            msg_data = json.loads(decrypted_json)
        except json.JSONDecodeError:
            logger.warning(
                "WeCom AI bot payload JSON decode failed",
                extra={
                    "event": "wecom_aibot_payload_validation",
                    "status": "failed",
                },
            )
            return self.make_invalid_payload_response()
        if not isinstance(msg_data, dict):
            return self.make_invalid_payload_response()

        return self.parse_message(msg_data, is_sync_check, use_passive_reply)


@registry.register(
    NodeMetadata(
        name="WeComAIBotPassiveReplyNode",
        description="Encrypt and return passive AI bot replies",
        category="wecom",
    )
)
class WeComAIBotPassiveReplyNode(TaskNode):
    """Encrypt and return passive AI bot replies."""

    token: str = "[[wecom_aibot_token]]"
    """WeCom AI bot callback token (from Orcheo vault)."""
    encoding_aes_key: str = "[[wecom_aibot_encoding_aes_key]]"
    """WeCom AI bot AES key for payload encryption (from Orcheo vault)."""
    receive_id: str | None = Field(
        default=None,
        description="Optional receive_id for encryption",
    )
    msg_type: str = Field(default="markdown", description="Reply message type")
    content: str | None = Field(default=None, description="Reply content")
    template_card: dict[str, Any] | None = Field(
        default=None,
        description="Template card payload for template_card replies",
    )
    nonce: str | None = Field(
        default=None,
        description="Optional nonce override for signature",
    )
    timestamp: int | None = Field(
        default=None,
        description="Optional timestamp override for signature",
    )

    def build_payload(self) -> dict[str, Any] | None:
        """Build the reply payload for the AI bot."""
        reply_msg_type = self.msg_type
        if reply_msg_type == "template_card":
            if not self.template_card:
                return None
            return {"msgtype": "template_card", "template_card": self.template_card}
        if not self.content:
            return None
        if reply_msg_type == "markdown":
            return {"msgtype": "markdown", "markdown": {"content": self.content}}
        return {"msgtype": "text", "text": {"content": self.content}}

    def sign_message(self, timestamp: str, nonce: str, encrypt: str) -> str:
        """Sign an encrypted AI bot message."""
        items = sorted([self.token, timestamp, nonce, encrypt])
        return hashlib.sha1("".join(items).encode()).hexdigest()

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return an encrypted passive reply response."""
        payload = self.build_payload()
        if not payload:
            logger.warning(
                "WeCom AI bot reply payload invalid",
                extra={
                    "event": "wecom_aibot_passive_reply",
                    "status": "failed",
                },
            )
            return {
                "is_error": True,
                "error": "Invalid reply payload",
                "immediate_response": None,
            }

        timestamp = self.timestamp or int(time.time())
        nonce = self.nonce or base64.b64encode(os.urandom(8)).decode().rstrip("=")
        payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        encrypt = encrypt_wecom_message(
            payload_json, self.encoding_aes_key, self.receive_id or ""
        )
        signature = self.sign_message(str(timestamp), nonce, encrypt)
        response_body = {
            "encrypt": encrypt,
            "msgsignature": signature,
            "timestamp": timestamp,
            "nonce": nonce,
        }

        return {
            "is_error": False,
            "encrypt": encrypt,
            "msgsignature": signature,
            "timestamp": timestamp,
            "nonce": nonce,
            "immediate_response": {
                "content": json.dumps(response_body, ensure_ascii=True),
                "content_type": "application/json",
                "status_code": 200,
            },
        }


@registry.register(
    NodeMetadata(
        name="WeComAIBotResponseNode",
        description="Send active replies to WeCom AI bot response_url",
        category="wecom",
    )
)
class WeComAIBotResponseNode(TaskNode):
    """Send active replies to WeCom AI bot response_url."""

    response_url: str | None = Field(
        default=None,
        description="AI bot response_url for active replies",
    )
    msg_type: str = Field(
        default="markdown",
        description="Reply message type (markdown, text, template_card)",
    )
    content: str | None = Field(default=None, description="Reply content")
    template_card: dict[str, Any] | None = Field(
        default=None,
        description="Template card payload for template_card replies",
    )
    timeout: float | None = Field(
        default=DEFAULT_TIMEOUT,
        description="Timeout in seconds for the response_url request",
    )

    def get_response_url(self, parser_result: dict[str, Any]) -> str | None:
        """Extract response_url from node config or parser result.

        Checks self.response_url first, then falls back to parser_result keys.
        """
        if self.response_url:
            return self.response_url
        return parser_result.get("response_url") or parser_result.get(
            "aibot_response_url"
        )

    def build_payload(self) -> dict[str, Any] | None:
        """Build the reply payload."""
        if self.msg_type == "template_card":
            if not self.template_card:
                return None
            return {"msgtype": "template_card", "template_card": self.template_card}
        if not self.content:
            return None
        if self.msg_type == "markdown":
            return {"msgtype": "markdown", "markdown": {"content": self.content}}
        if self.msg_type == "text":
            return {"msgtype": "text", "text": {"content": self.content}}
        return None

    async def _send_request(
        self, response_url: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Send HTTP request and handle errors.

        Returns a result dict with is_error and response data or error info.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(response_url, json=payload)
                response.raise_for_status()
                try:
                    data = response.json()
                except ValueError:
                    logger.warning(
                        "WeCom AI bot response_url returned invalid JSON",
                        extra={
                            "event": "wecom_aibot_active_reply",
                            "status": "failed",
                        },
                    )
                    return {
                        "is_error": True,
                        "error": "Invalid JSON response",
                        "status_code": response.status_code,
                    }
        except httpx.TimeoutException:
            logger.warning(
                "WeCom AI bot response_url request timed out",
                extra={
                    "event": "wecom_aibot_active_reply",
                    "status": "failed",
                    "reason": "timeout",
                },
            )
            return {"is_error": True, "error": "Request timed out"}
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "WeCom AI bot response_url HTTP error",
                extra={
                    "event": "wecom_aibot_active_reply",
                    "status": "failed",
                    "status_code": exc.response.status_code,
                },
            )
            return {
                "is_error": True,
                "error": f"HTTP error: {exc.response.status_code}",
                "status_code": exc.response.status_code,
            }
        except httpx.RequestError as exc:
            logger.warning(
                "WeCom AI bot response_url request failed",
                extra={
                    "event": "wecom_aibot_active_reply",
                    "status": "failed",
                    "error": str(exc),
                },
            )
            return {"is_error": True, "error": f"Request failed: {exc}"}

        return {"is_error": False, "data": data}

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Send a reply to the AI bot response_url."""
        results = state.get("results", {})
        parser_result = results.get("wecom_ai_bot_events_parser", {})
        if not isinstance(parser_result, dict):
            parser_result = {}

        response_url = self.get_response_url(parser_result)
        if not response_url:
            logger.warning(
                "WeCom AI bot response_url missing",
                extra={
                    "event": "wecom_aibot_active_reply",
                    "status": "failed",
                    "reason": "missing_response_url",
                },
            )
            return {"is_error": True, "error": "No response_url available"}

        payload = self.build_payload()
        if not payload:
            logger.warning(
                "WeCom AI bot reply payload invalid",
                extra={
                    "event": "wecom_aibot_active_reply",
                    "status": "failed",
                },
            )
            return {"is_error": True, "error": "Invalid reply payload"}

        result = await self._send_request(response_url, payload)
        if result["is_error"]:
            return result

        data = result["data"]
        errcode = data.get("errcode", 0)
        if errcode != 0:
            logger.warning(
                "WeCom AI bot response_url delivery failed",
                extra={
                    "event": "wecom_aibot_active_reply",
                    "status": "failed",
                    "errcode": errcode,
                    "errmsg": data.get("errmsg", "Unknown error"),
                },
            )
            return {
                "is_error": True,
                "errcode": errcode,
                "errmsg": data.get("errmsg", "Unknown error"),
            }

        logger.info(
            "WeCom AI bot response_url delivered",
            extra={
                "event": "wecom_aibot_active_reply",
                "status": "success",
                "errcode": 0,
            },
        )
        return {
            "is_error": False,
            "errcode": 0,
            "errmsg": data.get("errmsg", "ok"),
        }


@registry.register(
    NodeMetadata(
        name="WeComAccessTokenNode",
        description="Fetch and cache WeCom access token",
        category="wecom",
    )
)
class WeComAccessTokenNode(TaskNode):
    """Fetch and cache WeCom access token."""

    corp_id: str = Field(
        default="[[wecom_corp_id]]",
        description="WeCom corp ID",
    )
    app_secret: str = "[[wecom_app_secret]]"
    """WeCom app secret (from Orcheo vault)."""
    timeout: float | None = Field(
        default=DEFAULT_TIMEOUT,
        description="Timeout in seconds for the access token request",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Fetch access token from WeCom API."""
        url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
        params = {"corpid": self.corp_id, "corpsecret": self.app_secret}

        client = httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        finally:
            await client.aclose()

        if data.get("errcode", 0) != 0:
            msg = f"WeCom token error: {data.get('errmsg', 'Unknown error')}"
            raise ValueError(msg)

        return {
            "access_token": data.get("access_token"),
            "expires_in": data.get("expires_in"),
        }


@registry.register(
    NodeMetadata(
        name="WeComSendMessageNode",
        description="Send messages to WeCom chat",
        category="wecom",
    )
)
class WeComSendMessageNode(TaskNode):
    """Send messages to WeCom chat."""

    agent_id: int | str = Field(description="WeCom app agent ID")
    chat_id: str | None = Field(
        default=None,
        description="WeCom chat ID for group message delivery",
    )
    to_user: str | None = Field(
        default=None,
        description="WeCom user ID for direct message delivery",
    )
    message: str = Field(description="Message content to send")
    msg_type: str = Field(default="text", description="Message type (text or markdown)")
    timeout: float | None = Field(
        default=DEFAULT_TIMEOUT,
        description="Timeout in seconds for the send message request",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Send message to WeCom chat."""
        # Get access token from previous node result
        results = state.get("results", {})
        token_result = results.get("get_access_token", {})
        access_token = token_result.get("access_token")

        if not access_token:
            logger.warning(
                "WeCom message delivery failed: missing access token",
                extra={
                    "event": "wecom_message_delivery",
                    "status": "failed",
                    "reason": "missing_access_token",
                },
            )
            return {"is_error": True, "error": "No access token available"}

        target_user = _normalize_optional_runtime_value(self.to_user)
        if not target_user and isinstance(results, dict):
            parser_result = results.get("wecom_events_parser")
            if isinstance(parser_result, dict):
                target_user = _normalize_optional_runtime_value(
                    parser_result.get("target_user")
                )
        target_chat = _normalize_optional_runtime_value(self.chat_id)
        if not target_user and not target_chat:
            logger.warning(
                "WeCom message delivery failed: missing recipient",
                extra={
                    "event": "wecom_message_delivery",
                    "status": "failed",
                    "reason": "missing_recipient",
                },
            )
            return {
                "is_error": True,
                "error": "No WeCom chat_id or to_user provided",
            }

        url = (
            "https://qyapi.weixin.qq.com/cgi-bin/message/send"
            if target_user
            else "https://qyapi.weixin.qq.com/cgi-bin/appchat/send"
        )
        params = {"access_token": access_token}

        # Convert agent_id to int if string
        agent_id = self.agent_id
        if isinstance(agent_id, str):
            agent_id = int(agent_id)

        payload: dict[str, Any] = {
            "msgtype": self.msg_type,
            "agentid": agent_id,
            "safe": 0,
        }
        if target_user:
            payload["touser"] = target_user
        else:
            payload["chatid"] = target_chat

        if self.msg_type == "markdown":
            payload["markdown"] = {"content": self.message}
        else:
            payload["text"] = {"content": self.message}

        client = httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(url, params=params, json=payload)
            response.raise_for_status()
            data = response.json()
        finally:
            await client.aclose()

        errcode = data.get("errcode", 0)
        if errcode != 0:
            logger.warning(
                "WeCom message delivery failed",
                extra={
                    "event": "wecom_message_delivery",
                    "status": "failed",
                    "errcode": errcode,
                    "errmsg": data.get("errmsg", "Unknown error"),
                    "target_user": target_user,
                    "target_chat": target_chat,
                },
            )
            return {
                "is_error": True,
                "errcode": errcode,
                "errmsg": data.get("errmsg", "Unknown error"),
            }

        logger.info(
            "WeCom message delivered",
            extra={
                "event": "wecom_message_delivery",
                "status": "success",
                "errcode": 0,
                "target_user": target_user,
                "target_chat": target_chat,
            },
        )
        return {
            "is_error": False,
            "errcode": 0,
            "errmsg": "ok",
            # Set immediate_response to ack WeCom after sending.
            # Set should_process=False to prevent queuing a duplicate async run.
            "immediate_response": {
                "content": "success",
                "content_type": "text/plain",
                "status_code": 200,
            },
            "should_process": False,
        }


@registry.register(
    NodeMetadata(
        name="WeComGroupPushNode",
        description="Send messages to WeCom group via webhook",
        category="wecom",
    )
)
class WeComGroupPushNode(TaskNode):
    """Send messages to a WeCom group via webhook."""

    webhook_key: str | None = Field(
        default="[[wecom_group_webhook_key]]",
        description="WeCom group webhook key (from Orcheo vault)",
    )
    webhook_url: str | None = Field(
        default=None,
        description="Optional full webhook URL override",
    )
    msg_type: str = Field(
        default="text",
        description="Message type (text or markdown)",
    )
    content: str = Field(description="Message content to send")
    timeout: float | None = Field(
        default=DEFAULT_TIMEOUT,
        description="Timeout in seconds for the webhook request",
    )

    def build_webhook_url(self) -> str | None:
        """Return the webhook URL from key or explicit URL."""
        if self.webhook_url:
            return self.webhook_url
        if self.webhook_key:
            return (
                "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
                f"{self.webhook_key}"
            )
        return None

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Send the webhook payload to WeCom."""
        url = self.build_webhook_url()
        if not url:
            logger.warning(
                "WeCom group webhook missing URL/key",
                extra={
                    "event": "wecom_group_webhook",
                    "status": "failed",
                    "reason": "missing_webhook",
                },
            )
            return {
                "is_error": True,
                "error": "No webhook URL or key provided",
            }

        payload: dict[str, Any] = {"msgtype": self.msg_type}
        if self.msg_type == "markdown":
            payload["markdown"] = {"content": self.content}
        else:
            payload["text"] = {"content": self.content}

        client = httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        finally:
            await client.aclose()

        errcode = data.get("errcode", 0)
        if errcode != 0:
            logger.warning(
                "WeCom group webhook delivery failed",
                extra={
                    "event": "wecom_group_webhook",
                    "status": "failed",
                    "errcode": errcode,
                    "errmsg": data.get("errmsg", "Unknown error"),
                },
            )
            return {
                "is_error": True,
                "errcode": errcode,
                "errmsg": data.get("errmsg", "Unknown error"),
                "status_code": response.status_code,
            }

        logger.info(
            "WeCom group webhook delivered",
            extra={
                "event": "wecom_group_webhook",
                "status": "success",
                "errcode": 0,
            },
        )
        return {
            "is_error": False,
            "errcode": 0,
            "errmsg": data.get("errmsg", "ok"),
            "status_code": response.status_code,
        }


def get_access_token_from_state(results: dict[str, Any]) -> str | None:
    """Extract access token from workflow state results.

    Looks for access_token in common node result keys.
    """
    for key in ("get_access_token", "get_cs_access_token"):
        token_result = results.get(key, {})
        if isinstance(token_result, dict):
            token = token_result.get("access_token")
            if token:
                return token
    return None


def _coerce_msgtime(raw_value: Any) -> int:
    """Normalize WeCom message timestamp to epoch seconds."""
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return int(time.time())
    if value <= 0:
        return int(time.time())
    if value > 1_000_000_000_000:
        return value // 1000
    return value


def _compute_message_ttl(msgtime_seconds: int) -> int:
    """Compute TTL seconds from a message timestamp."""
    return max(0, (msgtime_seconds + CS_MESSAGE_TTL_SECONDS) - int(time.time()))


def _cs_message_index_key(external_userid: str) -> str:
    return f"{CS_REDIS_PREFIX}:messages:{external_userid}"


def _cs_message_key(external_userid: str, message_id: str) -> str:
    return f"{CS_REDIS_PREFIX}:message:{external_userid}:{message_id}"


def _build_cs_message_id(message: dict[str, Any]) -> str:
    msgid = str(message.get("msgid") or message.get("msg_id") or "").strip()
    if msgid:
        return msgid
    payload = json.dumps(message, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


async def _store_cs_message(
    redis_client: redis.Redis,
    message_key: str,
    index_key: str,
    msgtime: int,
    payload: dict[str, Any],
) -> tuple[bool, bool]:
    """Store a CS message in Redis and update its index."""
    exists = await redis_client.exists(message_key)
    if exists:
        return False, True
    ttl_seconds = _compute_message_ttl(msgtime)
    if ttl_seconds <= 0:
        return False, False
    await redis_client.set(message_key, json.dumps(payload), ex=ttl_seconds)
    await redis_client.zadd(index_key, {message_key: msgtime})
    await redis_client.expire(index_key, max(ttl_seconds, 1))
    return True, False


async def _fetch_cs_message_history(
    redis_client: redis.Redis,
    external_userid: str,
) -> list[dict[str, Any]]:
    """Fetch stored CS message history in chronological order."""
    index_key = _cs_message_index_key(external_userid)
    keys = await redis_client.zrange(index_key, 0, -1)
    if not keys:
        return []
    values = await redis_client.mget(keys)
    history: list[dict[str, Any]] = []
    stale_keys: list[str] = []
    for key, value in zip(keys, values, strict=False):
        if value is None:
            stale_keys.append(key)
            continue
        try:
            history.append(json.loads(value))
        except json.JSONDecodeError:
            stale_keys.append(key)
    if stale_keys:
        await redis_client.zrem(index_key, *stale_keys)
    return history


def _build_cs_sync_payload(
    base_payload: dict[str, Any], cursor: str | None
) -> dict[str, Any]:
    payload = dict(base_payload)
    if cursor:
        payload["cursor"] = cursor
    return payload


def _normalize_cs_sync_response(
    data: dict[str, Any],
) -> tuple[int, str, list[dict[str, Any]], str, bool]:
    errcode = data.get("errcode", 0)
    errmsg = data.get("errmsg", "Unknown error")
    msg_list = data.get("msg_list", [])
    next_cursor = data.get("next_cursor", "")
    has_more = data.get("has_more", 0) == 1
    return errcode, errmsg, msg_list, next_cursor, has_more


def _build_cs_inbound_entry(
    open_kf_id: str, msg: dict[str, Any]
) -> dict[str, Any] | None:
    if msg.get("origin") != 3 or msg.get("msgtype") != "text":
        return None
    external_userid = msg.get("external_userid", "")
    text_data = msg.get("text", {})
    content = text_data.get("content", "")
    if not external_userid or not content:
        return None
    msgtime = _coerce_msgtime(msg.get("msgtime"))
    message_id = _build_cs_message_id(msg)
    return {
        "id": message_id,
        "external_userid": external_userid,
        "open_kf_id": open_kf_id,
        "direction": "inbound",
        "role": "user",
        "msgtype": msg.get("msgtype", "text"),
        "content": content,
        "msgtime": msgtime,
    }


def _pick_latest_message(
    current: dict[str, Any] | None, candidate: dict[str, Any] | None
) -> dict[str, Any] | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return (
        candidate
        if candidate.get("msgtime", 0) >= current.get("msgtime", 0)
        else current
    )


async def _store_cs_entry(
    redis_client: redis.Redis, entry: dict[str, Any]
) -> tuple[bool, bool]:
    message_key = _cs_message_key(entry["external_userid"], entry["id"])
    index_key = _cs_message_index_key(entry["external_userid"])
    return await _store_cs_message(
        redis_client,
        message_key,
        index_key,
        entry["msgtime"],
        entry,
    )


async def _process_cs_page(
    msg_list: list[dict[str, Any]],
    open_kf_id: str,
    redis_client: redis.Redis | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, bool, bool]:
    import redis.asyncio as redis

    new_messages: list[dict[str, Any]] = []
    latest_message: dict[str, Any] | None = None
    saw_existing = False
    redis_failed = False
    for msg in msg_list:
        entry = _build_cs_inbound_entry(open_kf_id, msg)
        if entry is None:
            continue
        latest_message = _pick_latest_message(latest_message, entry)
        if redis_client is None:
            new_messages.append(entry)
            continue
        try:
            stored, already_exists = await _store_cs_entry(redis_client, entry)
        except redis.RedisError as exc:
            logger.warning(
                "WeCom CS sync: Redis write failed",
                extra={
                    "event": "wecom_cs_sync",
                    "status": "degraded",
                    "reason": "redis_write_failed",
                    "error": str(exc),
                },
            )
            redis_failed = True
            new_messages.append(entry)
            continue
        if already_exists:
            saw_existing = True
        elif stored:  # pragma: no branch
            new_messages.append(entry)
    return new_messages, latest_message, saw_existing, redis_failed


def _resolve_cs_sync_inputs(
    open_kf_id: str | None, kf_token: str | None, parser_result: dict[str, Any]
) -> tuple[str, str]:
    resolved_open_kf_id = open_kf_id or parser_result.get("open_kf_id", "")
    resolved_kf_token = kf_token or parser_result.get("kf_token", "")
    return resolved_open_kf_id, resolved_kf_token


def _create_cs_redis_client() -> redis.Redis | None:
    import redis.asyncio as redis

    try:
        return redis.from_url(REDIS_URL, decode_responses=True)
    except redis.RedisError as exc:
        logger.warning(
            "WeCom CS sync: Redis unavailable",
            extra={
                "event": "wecom_cs_sync",
                "status": "degraded",
                "reason": "redis_unavailable",
                "error": str(exc),
            },
        )
        return None


async def _close_cs_redis_client(redis_client: redis.Redis | None) -> None:
    import redis.asyncio as redis

    if redis_client is None:
        return
    try:
        await redis_client.aclose()
    except redis.RedisError:
        return


async def _fetch_cs_page(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str],
    base_payload: dict[str, Any],
    cursor: str | None,
) -> dict[str, Any]:
    payload = _build_cs_sync_payload(base_payload, cursor)
    response = await client.post(url, params=params, json=payload)
    response.raise_for_status()
    return response.json()


async def _pull_cs_pages(
    client: httpx.AsyncClient,
    redis_client: redis.Redis | None,
    url: str,
    params: dict[str, str],
    base_payload: dict[str, Any],
    cursor: str | None,
) -> dict[str, Any]:
    total_message_count = 0
    next_cursor = ""
    has_more = False
    saw_existing = False
    new_user_messages: list[dict[str, Any]] = []
    latest_message: dict[str, Any] | None = None

    while True:
        data = await _fetch_cs_page(client, url, params, base_payload, cursor)
        errcode, errmsg, msg_list, next_cursor, has_more = _normalize_cs_sync_response(
            data
        )
        if errcode != 0:
            logger.warning(
                "WeCom CS sync failed",
                extra={
                    "event": "wecom_cs_sync",
                    "status": "failed",
                    "errcode": errcode,
                    "errmsg": errmsg,
                },
            )
            return {"is_error": True, "errcode": errcode, "errmsg": errmsg}

        total_message_count += len(msg_list)
        (
            page_new_messages,
            page_latest_message,
            page_saw_existing,
            redis_failed,
        ) = await _process_cs_page(msg_list, base_payload["open_kfid"], redis_client)

        new_user_messages.extend(page_new_messages)
        latest_message = _pick_latest_message(latest_message, page_latest_message)

        if redis_failed and redis_client is not None:
            await _close_cs_redis_client(redis_client)
            redis_client = None

        if page_saw_existing:
            saw_existing = True
        if not has_more or saw_existing:
            if saw_existing:
                has_more = False
            break
        cursor = next_cursor

    return {
        "is_error": False,
        "new_user_messages": new_user_messages,
        "latest_message": latest_message,
        "message_count": total_message_count,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "redis_client": redis_client,
    }


async def _resolve_cs_history(
    redis_client: redis.Redis | None,
    latest_message: dict[str, Any] | None,
    new_user_messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    import redis.asyncio as redis

    if latest_message is None:
        return "", []
    external_userid = latest_message.get("external_userid", "")
    if not external_userid:
        return "", []
    history_messages: list[dict[str, Any]] = []
    if redis_client is not None:
        try:
            history_messages = await _fetch_cs_message_history(
                redis_client, external_userid
            )
        except redis.RedisError as exc:
            logger.warning(
                "WeCom CS sync: Redis read failed",
                extra={
                    "event": "wecom_cs_sync",
                    "status": "degraded",
                    "reason": "redis_read_failed",
                    "error": str(exc),
                },
            )
            history_messages = []

    if not history_messages:
        history_messages = sorted(
            [
                message
                for message in new_user_messages
                if message.get("external_userid") == external_userid
            ],
            key=lambda item: item.get("msgtime", 0),
        )
    return external_userid, history_messages


def _select_cs_customer_nickname(customer_list: Any, external_userid: str) -> str:
    """Pick a display name from a CS customer list response."""
    if not isinstance(customer_list, list):
        return ""
    normalized_userid = str(external_userid).strip()
    for customer in customer_list:
        if not isinstance(customer, Mapping):
            continue
        customer_id = str(customer.get("external_userid") or "").strip()
        if customer_id and customer_id != normalized_userid:
            continue
        nickname = (
            customer.get("nickname")
            or customer.get("name")
            or customer.get("display_name")
            or ""
        )
        if nickname:  # pragma: no branch
            return nickname
    if customer_list and isinstance(customer_list[0], Mapping):
        fallback = customer_list[0]
        return (
            fallback.get("nickname")
            or fallback.get("name")
            or fallback.get("display_name")
            or ""
        )
    return ""


async def _fetch_cs_customer_username(
    client: httpx.AsyncClient,
    access_token: str,
    external_userid: str,
) -> str:
    """Fetch external customer nickname for WeCom CS messages."""
    nickname = ""
    external_userid = str(external_userid).strip()
    if not external_userid:
        return nickname
    url = "https://qyapi.weixin.qq.com/cgi-bin/kf/customer/batchget"
    params = {"access_token": access_token}
    payload = {
        "external_userid_list": [external_userid],
        "need_enter_session_context": 0,
    }
    try:
        response = await client.post(url, params=params, json=payload)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "WeCom CS customer info fetch failed",
            extra={
                "event": "wecom_cs_customer_info",
                "status": "failed",
                "error": str(exc),
                "external_userid": external_userid,
            },
        )
    else:
        errcode = data.get("errcode", 0)
        errmsg = data.get("errmsg", "Unknown error")
        if errcode != 0:
            logger.warning(
                "WeCom CS customer info error: errcode=%s errmsg=%s",
                errcode,
                errmsg,
                extra={
                    "event": "wecom_cs_customer_info",
                    "status": "failed",
                    "errcode": errcode,
                    "errmsg": errmsg,
                    "external_userid": external_userid,
                },
            )
        else:
            customer_list = data.get("customer_list", [])
            nickname = _select_cs_customer_nickname(customer_list, external_userid)
    return nickname.strip() if isinstance(nickname, str) else ""


@registry.register(
    NodeMetadata(
        name="WeComCustomerServiceSyncNode",
        description="Sync messages from WeCom Customer Service (微信客服)",
        category="wecom",
    )
)
class WeComCustomerServiceSyncNode(TaskNode):
    """Sync messages from WeCom Customer Service (微信客服).

    This node fetches messages from external WeChat users who contact
    the enterprise through Customer Service. It should be used after
    WeComEventsParserNode detects a kf_msg_or_event callback.

    The sync_msg API returns all new messages since the last cursor.
    Messages are stored in Redis and returned in chronological order.

    Requires an access token from a preceding WeComAccessTokenNode
    (named 'get_access_token' or 'get_cs_access_token' in the workflow).
    """

    open_kf_id: str | None = Field(
        default=None,
        description="Customer Service account ID (from parser result)",
    )
    kf_token: str | None = Field(
        default=None,
        description="Sync token from callback (from parser result)",
    )
    cursor: str | None = Field(
        default=None,
        description="Optional cursor for pagination",
    )
    limit: int = Field(
        default=1000,
        description="Maximum number of messages to fetch (1-1000)",
    )
    timeout: float | None = Field(
        default=DEFAULT_TIMEOUT,
        description="Timeout in seconds for the sync request",
    )

    @staticmethod
    def _build_agent_messages(
        history_messages: list[dict[str, Any]] | None,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if not isinstance(history_messages, list):
            return messages
        for item in history_messages:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            role = item.get("role") or item.get("type")
            if role == "ai":
                role = "assistant"
            if role in {"user", "assistant"} and isinstance(
                content, str
            ):  # pragma: no branch
                content = content.strip()
                if content:
                    messages.append({"role": role, "content": content})
        return messages

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Sync messages from Customer Service."""
        results = state.get("results", {})
        parser_result = results.get("wecom_events_parser", {})

        # Get access token from state (requires preceding WeComAccessTokenNode)
        access_token = get_access_token_from_state(results)
        if not access_token:
            logger.warning(
                "WeCom CS sync failed: missing access token",
                extra={
                    "event": "wecom_cs_sync",
                    "status": "failed",
                    "reason": "missing_access_token",
                },
            )
            return {
                "is_error": True,
                "error": "No access token available",
                "agent_messages": [],
            }

        open_kf_id, kf_token = _resolve_cs_sync_inputs(
            self.open_kf_id, self.kf_token, parser_result
        )

        if not open_kf_id:
            logger.warning(
                "WeCom CS sync failed: missing open_kf_id",
                extra={
                    "event": "wecom_cs_sync",
                    "status": "failed",
                    "reason": "missing_open_kf_id",
                },
            )
            return {
                "is_error": True,
                "error": "No open_kf_id provided",
                "agent_messages": [],
            }

        url = "https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg"
        params = {"access_token": access_token}
        base_payload: dict[str, Any] = {
            "open_kfid": open_kf_id,
            "limit": min(self.limit, 1000),
        }
        if kf_token:
            base_payload["token"] = kf_token

        redis_client = _create_cs_redis_client()
        client = httpx.AsyncClient(timeout=self.timeout)
        try:
            sync_result = await _pull_cs_pages(
                client, redis_client, url, params, base_payload, self.cursor
            )
            if sync_result.get("is_error"):
                return {
                    "is_error": True,
                    "errcode": sync_result.get("errcode", 0),
                    "errmsg": sync_result.get("errmsg", "Unknown error"),
                    "agent_messages": [],
                }

            redis_client = sync_result.get("redis_client")
            new_user_messages = sync_result.get("new_user_messages", [])
            latest_message = sync_result.get("latest_message")
            external_userid, history_messages = await _resolve_cs_history(
                redis_client, latest_message, new_user_messages
            )
            should_process = any(
                message.get("external_userid") == external_userid
                for message in new_user_messages
            )

            external_username = await _fetch_cs_customer_username(
                client, access_token, external_userid
            )
            agent_messages = self._build_agent_messages(history_messages)
            return {
                "is_error": False,
                "open_kf_id": open_kf_id,
                "external_userid": external_userid,
                "external_username": external_username,
                "messages": history_messages,
                "agent_messages": agent_messages,
                "message_count": sync_result.get("message_count", 0),
                "next_cursor": sync_result.get("next_cursor", ""),
                "has_more": sync_result.get("has_more", False),
                "should_process": should_process,
            }
        finally:
            await client.aclose()
            await _close_cs_redis_client(redis_client)

    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Execute the node and merge agent messages into state."""
        runnable = self.resolved_for_run(state, config=config)
        result = await runnable.run(state, config)
        serialized = runnable._serialize_result(result)
        output: dict[str, Any] = {"results": {self.name: serialized}}
        if isinstance(serialized, dict):  # pragma: no branch
            agent_messages = serialized.pop("agent_messages", None)
            if isinstance(agent_messages, list):
                output["messages"] = agent_messages
        return output


@registry.register(
    NodeMetadata(
        name="WeComCustomerServiceSendNode",
        description="Send messages via WeCom Customer Service (微信客服)",
        category="wecom",
    )
)
class WeComCustomerServiceSendNode(TaskNode):
    """Send messages via WeCom Customer Service (微信客服).

    This node sends messages to external WeChat users through Customer
    Service. It should be used after WeComCustomerServiceSyncNode has
    fetched the external user ID.

    Requires an access token from a preceding WeComAccessTokenNode
    (named 'get_access_token' or 'get_cs_access_token' in the workflow).
    """

    open_kf_id: str | None = Field(
        default=None,
        description="Customer Service account ID",
    )
    external_userid: str | None = Field(
        default=None,
        description="External WeChat user ID",
    )
    message: str = Field(description="Message content to send")
    msg_type: str = Field(default="text", description="Message type (text only)")
    raise_on_error: bool = Field(
        default=False,
        description="Raise ValueError when send fails instead of returning is_error",
    )
    timeout: float | None = Field(
        default=DEFAULT_TIMEOUT,
        description="Timeout in seconds for the send request",
    )

    def _error_result(
        self,
        *,
        error: str | None = None,
        errcode: int | None = None,
        errmsg: str | None = None,
    ) -> dict[str, Any]:
        """Return a structured error result or raise when configured."""
        result: dict[str, Any] = {"is_error": True}
        if error is not None:
            result["error"] = error
        if errcode is not None:
            result["errcode"] = errcode
        if errmsg is not None:
            result["errmsg"] = errmsg

        if self.raise_on_error:
            detail = error or errmsg or "Unknown error"
            raise ValueError(f"WeCom customer service send failed: {detail}")

        return result

    async def send_message(
        self,
        access_token: str,
        external_userid: str,
        open_kf_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a customer service message."""
        import redis.asyncio as redis

        url = "https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg"
        params: dict[str, str] = {"access_token": access_token}

        client = httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(url, params=params, json=payload)
            response.raise_for_status()
            data = response.json()
        finally:
            await client.aclose()

        errcode = data.get("errcode", 0)
        errmsg = data.get("errmsg", "Unknown error")
        if errcode != 0:
            logger.warning(
                "WeCom CS send failed: errcode=%s errmsg=%s",
                errcode,
                errmsg,
                extra={
                    "event": "wecom_cs_send",
                    "status": "failed",
                    "errcode": errcode,
                    "errmsg": errmsg,
                    "external_userid": external_userid,
                },
            )
            return self._error_result(
                errcode=errcode,
                errmsg=data.get("errmsg", "Unknown error"),
            )

        logger.info(
            "WeCom CS message delivered",
            extra={
                "event": "wecom_cs_send",
                "status": "success",
                "errcode": 0,
                "external_userid": external_userid,
                "msgid": data.get("msgid"),
            },
        )
        msgtime = int(time.time())
        message_id = str(data.get("msgid") or "").strip()
        if not message_id:
            payload_id = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            message_id = hashlib.sha1(payload_id.encode("utf-8")).hexdigest()
        entry = {
            "id": message_id,
            "external_userid": external_userid,
            "open_kf_id": open_kf_id,
            "direction": "outbound",
            "role": "assistant",
            "msgtype": payload.get("msgtype", "text"),
            "content": payload.get("text", {}).get("content", ""),
            "msgtime": msgtime,
        }
        redis_client: redis.Redis | None = None
        try:
            redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            message_key = _cs_message_key(external_userid, message_id)
            index_key = _cs_message_index_key(external_userid)
            await _store_cs_message(
                redis_client,
                message_key,
                index_key,
                msgtime,
                entry,
            )
        except redis.RedisError as exc:
            logger.warning(
                "WeCom CS send: Redis write failed",
                extra={
                    "event": "wecom_cs_send",
                    "status": "degraded",
                    "reason": "redis_write_failed",
                    "error": str(exc),
                    "external_userid": external_userid,
                },
            )
        finally:
            if redis_client is not None:  # pragma: no branch
                await redis_client.aclose()
        return {
            "is_error": False,
            "errcode": 0,
            "errmsg": "ok",
            "msgid": data.get("msgid"),
            # Set immediate_response to ack WeCom after sending.
            # Set should_process=False to prevent queuing a duplicate async run.
            "immediate_response": {
                "content": "success",
                "content_type": "text/plain",
                "status_code": 200,
            },
            "should_process": False,
        }

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Send message via Customer Service."""
        results = state.get("results", {})
        sync_result = results.get("wecom_cs_sync", {})
        parser_result = results.get("wecom_events_parser", {})

        # Get access token from state (requires preceding WeComAccessTokenNode)
        access_token = get_access_token_from_state(results)
        if not access_token:
            logger.warning(
                "WeCom CS send failed: missing access token",
                extra={
                    "event": "wecom_cs_send",
                    "status": "failed",
                    "reason": "missing_access_token",
                },
            )
            return self._error_result(error="No access token available")

        open_kf_id = (
            self.open_kf_id
            or sync_result.get("open_kf_id")
            or parser_result.get("open_kf_id", "")
        )
        external_userid = self.external_userid or sync_result.get("external_userid", "")

        if not open_kf_id:
            logger.warning(
                "WeCom CS send failed: missing open_kf_id",
                extra={
                    "event": "wecom_cs_send",
                    "status": "failed",
                    "reason": "missing_open_kf_id",
                },
            )
            return self._error_result(error="No open_kf_id provided")

        if not external_userid:
            logger.warning(
                "WeCom CS send failed: missing external_userid",
                extra={
                    "event": "wecom_cs_send",
                    "status": "failed",
                    "reason": "missing_external_userid",
                },
            )
            return self._error_result(error="No external_userid provided")

        payload: dict[str, Any] = {
            "touser": external_userid,
            "open_kfid": open_kf_id,
            "msgtype": self.msg_type,
        }

        if self.msg_type == "text":
            payload["text"] = {"content": self.message}
        else:
            # Only text is currently supported
            payload["text"] = {"content": self.message}

        return await self.send_message(
            access_token, external_userid, open_kf_id, payload
        )


__all__ = [
    "WeComAIBotEventsParserNode",
    "WeComAIBotPassiveReplyNode",
    "WeComAIBotResponseNode",
    "WeComAccessTokenNode",
    "WeComCustomerServiceSendNode",
    "WeComCustomerServiceSyncNode",
    "WeComEventsParserNode",
    "WeComGroupPushNode",
    "WeComSendMessageNode",
    "CS_MESSAGE_TTL_SECONDS",
    "_normalize_optional_runtime_value",
    "decrypt_wecom_message",
    "encrypt_wecom_message",
    "verify_wecom_signature",
    "get_access_token_from_state",
    "_build_cs_inbound_entry",
    "_build_cs_message_id",
    "_build_cs_sync_payload",
    "_close_cs_redis_client",
    "_coerce_msgtime",
    "_compute_message_ttl",
    "_create_cs_redis_client",
    "_cs_message_index_key",
    "_cs_message_key",
    "_fetch_cs_customer_username",
    "_fetch_cs_message_history",
    "_fetch_cs_page",
    "_normalize_cs_sync_response",
    "_pick_latest_message",
    "_process_cs_page",
    "_pull_cs_pages",
    "_resolve_cs_history",
    "_resolve_cs_sync_inputs",
    "_select_cs_customer_nickname",
    "_store_cs_message",
]
