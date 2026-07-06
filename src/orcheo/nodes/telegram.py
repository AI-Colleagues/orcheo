"""Telegram messaging and event parsing nodes for Orcheo."""

import asyncio
from collections.abc import Mapping
from typing import Any
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from telegram import Bot
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


def detect_telegram_update_type(payload: dict[str, Any]) -> str | None:
    """Return the Telegram update type present in the payload."""
    for candidate in (
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "callback_query",
        "inline_query",
        "my_chat_member",
        "chat_member",
        "chat_join_request",
    ):
        if candidate in payload:
            return candidate
    return None


def extract_telegram_update_details(
    payload: dict[str, Any],
    update_type: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    """Extract message, chat, sender, and text for the update."""
    msg = payload.get(update_type, {})
    if not isinstance(msg, dict):
        msg = {}

    if update_type == "callback_query":
        callback_msg = msg.get("message", {})
        chat = callback_msg.get("chat", {}) if isinstance(callback_msg, dict) else {}
        sender = msg.get("from", {})
        text = msg.get("data", "")
    else:
        chat = msg.get("chat", {})
        sender = msg.get("from", {})
        text = msg.get("text", "")

    if not isinstance(chat, dict):
        chat = {}
    if not isinstance(sender, dict):
        sender = {}
    if not isinstance(text, str):
        text = str(text)

    return msg, chat, sender, text


def escape_markdown(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special_chars = [
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ]
    escaped_text = ""
    for char in text:
        if char in special_chars:
            escaped_text += f"\\{char}"
        else:
            escaped_text += char
    return escaped_text


@registry.register(
    NodeMetadata(
        name="MessageTelegramNode",
        description="Send message to Telegram",
        category="messaging",
    )
)
class MessageTelegramNode(TaskNode):
    """Node for sending Telegram messages."""

    token: str = "[[telegram_token]]"
    chat_id: str | None = None
    message: str | None = None
    parse_mode: str | None = None

    @staticmethod
    def _message_from_state(state: State) -> str | None:
        """Return the last assistant message content from workflow state."""
        messages = state.get("messages", []) if isinstance(state, Mapping) else []
        for item in reversed(messages):
            if isinstance(item, dict) and item.get("role") == "assistant":
                content = item.get("content")
                if isinstance(content, str) and content.strip():  # pragma: no branch
                    return content
            if (
                isinstance(item, BaseMessage) and item.type == "ai" and item.content
            ):  # pragma: no branch
                if isinstance(item.content, str):
                    return item.content
                return str(item.content)
        return None

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Send message to Telegram and return status."""
        assert self.chat_id is not None
        message = self.message or self._message_from_state(state)
        if message is None:
            msg = "Telegram message content is required"
            raise ValueError(msg)
        return await self.tool_arun(self.chat_id, message, self.parse_mode)

    def tool_run(
        self, chat_id: str, message: str, parse_mode: str | None = None
    ) -> Any:
        """Send message to Telegram and return status.

        Args:
            chat_id: The ID of the chat to send the message to.
            message: The message to send.
            parse_mode: The parse mode to use for the message.
        """
        return asyncio.run(
            self.tool_arun(chat_id, message, parse_mode)
        )  # pragma: no cover

    async def tool_arun(
        self, chat_id: str, message: str, parse_mode: str | None = None
    ) -> dict:
        """Send message to Telegram and return status.

        Args:
            chat_id: The ID of the chat to send the message to.
            message: The message to send.
            parse_mode: The parse mode to use for the message.
        """
        bot = Bot(token=self.token)
        try:
            result = await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=parse_mode,
            )
            return {"message_id": result.message_id, "status": "sent"}
        except Exception as e:
            raise ValueError(f"Telegram API error: {str(e)}") from e


@registry.register(
    NodeMetadata(
        name="TelegramEventsParserNode",
        description="Validate and parse Telegram Bot webhook updates",
        category="telegram",
    )
)
class TelegramEventsParserNode(TaskNode):
    """Parse Telegram Bot API webhook updates into a structured event.

    Extracts message metadata (chat ID, username, text) from the
    ``Update`` JSON payload delivered by the Telegram webhook.
    Optionally validates the ``X-Telegram-Bot-Api-Secret-Token`` header
    and filters by allowed update / chat types.
    """

    secret_token: str | None = Field(
        default=None,
        description=(
            "Expected value of the X-Telegram-Bot-Api-Secret-Token header. "
            "When set, updates with a missing or mismatched token are rejected."
        ),
    )
    allowed_update_types: list[str] = Field(
        default_factory=lambda: ["message"],
        description="Telegram update types allowed to pass through",
    )
    allowed_chat_types: list[str] = Field(
        default_factory=lambda: ["private", "group", "supergroup"],
        description="Chat types allowed to pass through",
    )
    body_key: str = Field(
        default="body",
        description="Key in inputs that contains the webhook payload",
    )

    def _extract_inputs(self, state: State) -> dict[str, Any]:
        """Extract webhook inputs from state."""
        if isinstance(state, BaseModel):
            state_dict = state.model_dump()
        elif isinstance(state, Mapping):
            state_dict = dict(state)
        else:
            return {}
        raw_inputs = state_dict.get("inputs")
        if isinstance(raw_inputs, Mapping):
            return dict(raw_inputs)
        return state_dict

    def _verify_secret_token(self, headers: dict[str, str]) -> None:
        """Verify the secret token header if configured."""
        if not self.secret_token:
            return
        normalized = {k.lower(): v for k, v in headers.items()}
        token = normalized.get("x-telegram-bot-api-secret-token")
        if token != self.secret_token:
            raise ValueError("Telegram secret token verification failed")

    def _detect_update_type(self, payload: dict[str, Any]) -> str | None:
        """Return the update type present in the payload."""
        return detect_telegram_update_type(payload)

    def _parse_body(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Return the webhook body payload as a dict."""
        body = inputs.get(self.body_key, {})
        if isinstance(body, str):
            import json

            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                return {}
        if isinstance(body, dict):
            return body
        return {}

    def _extract_update_details(
        self,
        payload: dict[str, Any],
        update_type: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
        """Extract message, chat, sender, and text for the update."""
        return extract_telegram_update_details(payload, update_type)

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Parse a Telegram webhook update and return structured event data."""
        inputs = self._extract_inputs(state)
        headers = inputs.get("headers", {})
        if isinstance(headers, dict):
            self._verify_secret_token(headers)

        body = self._parse_body(inputs)

        update_type = self._detect_update_type(body)
        if update_type is None or update_type not in self.allowed_update_types:
            return {
                "update_type": update_type,
                "should_process": False,
                "chat_id": None,
                "username": None,
                "text": None,
            }

        msg, chat, sender, text = self._extract_update_details(body, update_type)

        chat_type = chat.get("type", "")
        if self.allowed_chat_types and chat_type not in self.allowed_chat_types:
            return {
                "update_type": update_type,
                "should_process": False,
                "chat_id": str(chat.get("id", "")),
                "username": None,
                "text": None,
            }

        chat_id = str(chat.get("id", ""))
        username = sender.get("first_name") or sender.get("username") or ""
        should_process = bool(chat_id and text)

        return {
            "update_type": update_type,
            "chat_id": chat_id,
            "chat_type": chat_type,
            "username": username,
            "user_id": str(sender.get("id", "")),
            "text": text or "",
            "message_id": msg.get("message_id"),
            "should_process": should_process,
        }

    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Execute the node and inject user text as a HumanMessage into state."""
        runnable = self.resolved_for_run(state, config=config)
        result = await runnable.run(state, config)
        serialized = runnable._serialize_result(result)
        output: dict[str, Any] = {"node_results": {self.name: serialized}}
        if isinstance(serialized, dict) and serialized.get("should_process"):
            text = serialized.get("text", "")
            if isinstance(text, str) and text.strip():
                output["messages"] = [HumanMessage(content=text)]
        return output


MessageTelegram = MessageTelegramNode


__all__ = [
    "MessageTelegram",
    "MessageTelegramNode",
    "TelegramEventsParserNode",
    "detect_telegram_update_type",
    "escape_markdown",
    "extract_telegram_update_details",
]
