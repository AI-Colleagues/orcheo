"""Communication nodes covering email and Discord webhooks."""

from __future__ import annotations
import asyncio
import smtplib
from collections.abc import Mapping
from email.message import EmailMessage
from typing import Any
import httpx
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.listeners.qq import DefaultQQAccessTokenProvider
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.security.ssrf import (
    restricted_egress_client_kwargs,
    validate_restricted_egress_host_async,
)


@registry.register(
    NodeMetadata(
        name="EmailNode",
        description="Send an email via SMTP with optional TLS and authentication.",
        category="communication",
    )
)
class EmailNode(TaskNode):
    """Node for dispatching email messages via SMTP."""

    smtp_host: str = Field(default="localhost", description="SMTP server host")
    smtp_port: int = Field(default=587, description="SMTP server port")
    use_tls: bool = Field(
        default=True,
        description="Upgrade the connection using STARTTLS",
    )
    username: str | None = Field(default=None, description="Optional SMTP username")
    password: str | None = Field(default=None, description="Optional SMTP password")
    from_address: str = Field(description="Sender email address")
    to_addresses: list[str] = Field(
        default_factory=list, description="List of recipient email addresses"
    )
    cc_addresses: list[str] | None = Field(
        default=None, description="Optional CC recipient email addresses"
    )
    bcc_addresses: list[str] | None = Field(
        default=None, description="Optional BCC recipient email addresses"
    )
    subject: str = Field(default="", description="Email subject line")
    body: str = Field(default="", description="Email body content")
    subtype: str = Field(
        default="plain", description="Email body subtype (plain or html)"
    )
    timeout: float | None = Field(
        default=30.0,
        description="Timeout in seconds for SMTP operations",
    )

    def _build_message(self) -> tuple[EmailMessage, list[str]]:
        message = EmailMessage()
        message["Subject"] = self.subject
        message["From"] = self.from_address
        message["To"] = ", ".join(self.to_addresses)
        if self.cc_addresses:
            message["Cc"] = ", ".join(self.cc_addresses)
        recipients = list(self.to_addresses)
        if self.cc_addresses:
            recipients.extend(self.cc_addresses)
        if self.bcc_addresses:
            recipients.extend(self.bcc_addresses)
        message.set_content(self.body, subtype=self.subtype)
        return message, recipients

    def _send_email(self) -> dict[str, Any]:
        message, recipients = self._build_message()
        timeout = self.timeout if self.timeout is not None else 30.0
        with smtplib.SMTP(
            self.smtp_host,
            self.smtp_port,
            timeout=timeout,
        ) as client:
            if self.use_tls:
                client.starttls()
            if self.username and self.password:
                client.login(self.username, self.password)
            refused = client.send_message(message, to_addrs=recipients)
        accepted = [
            address for address in recipients if not refused or address not in refused
        ]
        return {"accepted": accepted, "refused": refused}

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Dispatch the email message."""
        if not self.to_addresses and not self.cc_addresses and not self.bcc_addresses:
            msg = "At least one recipient must be specified"
            raise ValueError(msg)
        await validate_restricted_egress_host_async(self.smtp_host, self.smtp_port)
        return await asyncio.to_thread(self._send_email)


@registry.register(
    NodeMetadata(
        name="DiscordWebhookNode",
        description="Send messages to Discord via incoming webhooks.",
        category="communication",
    )
)
class DiscordWebhookNode(TaskNode):
    """Node that posts messages to a Discord webhook URL."""

    webhook_url: str = Field(description="Discord webhook URL")
    content: str | None = Field(default=None, description="Message content to send")
    username: str | None = Field(
        default=None, description="Override username displayed in Discord"
    )
    avatar_url: str | None = Field(
        default=None, description="Override avatar URL displayed in Discord"
    )
    embeds: list[dict[str, Any]] | None = Field(
        default=None, description="Optional embeds payload"
    )
    tts: bool = Field(default=False, description="Enable text-to-speech announcement")
    timeout: float | None = Field(
        default=10.0,
        description="Timeout in seconds for the webhook request",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Send the webhook payload to Discord."""
        payload: dict[str, Any] = {"tts": self.tts}
        if self.content is not None:
            payload["content"] = self.content
        if self.username is not None:
            payload["username"] = self.username
        if self.avatar_url is not None:
            payload["avatar_url"] = self.avatar_url
        if self.embeds is not None:
            payload["embeds"] = self.embeds

        async with httpx.AsyncClient(
            timeout=self.timeout,
            **restricted_egress_client_kwargs(),
        ) as client:
            response = await client.post(self.webhook_url, json=payload)
            response.raise_for_status()

        return {
            "status_code": response.status_code,
            "reason": response.reason_phrase,
        }


def _assistant_message_from_state(state: State) -> str | None:
    """Return the last assistant message content from workflow state."""
    messages = state.get("messages", []) if isinstance(state, Mapping) else []
    for item in reversed(messages):
        if isinstance(item, dict) and item.get("role") == "assistant":
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                return content
        if isinstance(item, BaseMessage) and item.type == "ai" and item.content:
            if isinstance(item.content, str):
                return item.content
            return str(item.content)
    return None


@registry.register(
    NodeMetadata(
        name="MessageDiscordNode",
        description="Send a message to a Discord channel using a bot token.",
        category="messaging",
    )
)
class MessageDiscordNode(TaskNode):
    """Node that posts a bot-authenticated message to a Discord channel."""

    token: str = "[[discord_bot_token]]"
    channel_id: str | None = None
    message: str | None = None
    reply_to_message_id: str | None = None
    timeout: float | None = Field(
        default=10.0,
        description="Timeout in seconds for the Discord API request",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Send a Discord channel message and return the API result."""
        del config
        if self.channel_id is None:
            msg = "Discord channel_id is required"
            raise ValueError(msg)
        message = self.message or _assistant_message_from_state(state)
        if message is None:
            msg = "Discord message content is required"
            raise ValueError(msg)

        payload: dict[str, Any] = {"content": message}
        if self.reply_to_message_id is not None:
            payload["message_reference"] = {"message_id": self.reply_to_message_id}

        async with httpx.AsyncClient(
            base_url="https://discord.com/api/v10",
            timeout=self.timeout,
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json",
            },
        ) as client:
            response = await client.post(
                f"/channels/{self.channel_id}/messages",
                json=payload,
            )
            response.raise_for_status()
            body = response.json()

        message_id = body.get("id") if isinstance(body, dict) else None
        channel_id = body.get("channel_id") if isinstance(body, dict) else None
        return {
            "status_code": response.status_code,
            "message_id": str(message_id) if message_id is not None else None,
            "channel_id": str(channel_id) if channel_id is not None else None,
        }


MessageDiscord = MessageDiscordNode


@registry.register(
    NodeMetadata(
        name="MessageQQNode",
        description="Send a message to QQ using AppID and client secret.",
        category="messaging",
    )
)
class MessageQQNode(TaskNode):
    """Node that posts a QQ reply through the matching QQ bot identity."""

    app_id: str = "[[qq_app_id]]"
    client_secret: str = "[[qq_client_secret]]"
    openid: str | None = None
    group_openid: str | None = None
    channel_id: str | None = None
    guild_id: str | None = None
    message: str | None = None
    msg_id: str | None = None
    msg_seq: int = 1
    event_id: str | None = None
    sandbox: bool = False
    timeout: float | None = Field(
        default=10.0,
        description="Timeout in seconds for the QQ API request",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Send a QQ message to a C2C, group, or channel target."""
        del config
        message = self.message or _assistant_message_from_state(state)
        if message is None:
            msg = "QQ message content is required"
            raise ValueError(msg)

        payload: dict[str, Any] = {
            "content": message,
            "msg_type": 0,
        }
        if self.msg_id is not None:
            payload["msg_id"] = self.msg_id
            payload["msg_seq"] = self.msg_seq
        if self.event_id is not None:
            payload["event_id"] = self.event_id

        token_provider = DefaultQQAccessTokenProvider()
        access_token = await token_provider.get_access_token(
            app_id=self.app_id,
            client_secret=self.client_secret,
        )
        base_url = (
            "https://sandbox.api.sgroup.qq.com"
            if self.sandbox
            else "https://api.sgroup.qq.com"
        )
        endpoint: str
        scene_type: str
        target_id: str
        openid = _non_empty_string(self.openid)
        group_openid = _non_empty_string(self.group_openid)
        channel_id = _non_empty_string(self.channel_id)

        if openid is not None:
            endpoint = f"/v2/users/{openid}/messages"
            scene_type = "c2c"
            target_id = openid
        elif group_openid is not None:
            endpoint = f"/v2/groups/{group_openid}/messages"
            scene_type = "group"
            target_id = group_openid
        elif channel_id is not None:
            endpoint = f"/channels/{channel_id}/messages"
            scene_type = "channel"
            target_id = channel_id
        else:
            msg = "QQ openid, group_openid, or channel_id is required"
            raise ValueError(msg)

        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=self.timeout,
            headers={
                "Authorization": f"QQBot {access_token}",
                "Content-Type": "application/json",
            },
        ) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            body = response.json()

        message_id = body.get("id") if isinstance(body, dict) else None
        return {
            "status_code": response.status_code,
            "message_id": str(message_id) if message_id is not None else None,
            "scene_type": scene_type,
            "target_id": target_id,
        }


MessageQQ = MessageQQNode


def _non_empty_string(value: str | None) -> str | None:
    """Return a trimmed string unless the value is empty."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


__all__ = [
    "DiscordWebhookNode",
    "EmailNode",
    "MessageDiscord",
    "MessageDiscordNode",
    "MessageQQ",
    "MessageQQNode",
]
