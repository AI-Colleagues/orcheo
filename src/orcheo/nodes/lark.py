"""Lark integration nodes for Orcheo."""

from __future__ import annotations
import json
import logging
from typing import Any
import httpx
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


logger = logging.getLogger(__name__)
DEFAULT_TIMEOUT = 10.0
LARK_TENANT_ACCESS_TOKEN_URL = (
    "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal"
)


def _normalize_optional_value(value: str | None) -> str | None:
    """Return a usable runtime value or ``None`` for blanks/templates."""
    if not value:
        return None
    stripped = value.strip()
    if not stripped or ("{{" in stripped and "}}" in stripped):
        return None
    return stripped


def _parse_tenant_access_token_response(data: dict[str, Any]) -> str:
    """Extract and validate the tenant access token from a Lark auth response."""
    if data.get("code", 0) != 0:
        msg = data.get("msg", "Unknown error")
        raise ValueError(f"Lark tenant token error: {msg}")

    token = data.get("tenant_access_token")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("Lark tenant token response missing tenant_access_token")
    return token


def _extract_tenant_access_token(result: Any) -> str | None:
    """Extract a tenant access token from a prior node result when present."""
    if not isinstance(result, dict):
        return None

    token = result.get("tenant_access_token")
    if isinstance(token, str) and token.strip():
        return token

    json_payload = result.get("json")
    if not isinstance(json_payload, dict):
        return None

    token = json_payload.get("tenant_access_token")
    if isinstance(token, str) and token.strip():
        return token

    return None


async def _request_tenant_access_token(
    app_id: str,
    app_secret: str,
    timeout: float | None,
) -> dict[str, Any]:
    """Request a tenant access token from the Lark auth API."""
    payload = {
        "app_id": app_id,
        "app_secret": app_secret,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(LARK_TENANT_ACCESS_TOKEN_URL, json=payload)
        response.raise_for_status()
        return response.json()


@registry.register(
    NodeMetadata(
        name="LarkTenantAccessTokenNode",
        description="Fetch a tenant access token from the Lark auth API",
        category="lark",
    )
)
class LarkTenantAccessTokenNode(TaskNode):
    """Fetch a Lark tenant access token."""

    app_id: str = Field(description="Lark app ID")
    app_secret: str = Field(description="Lark app secret")
    timeout: float | None = Field(
        default=DEFAULT_TIMEOUT,
        description="Timeout in seconds for the tenant access token request",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Fetch a tenant access token from the Lark auth API."""
        del state
        del config

        data = await _request_tenant_access_token(
            app_id=self.app_id,
            app_secret=self.app_secret,
            timeout=self.timeout,
        )
        token = _parse_tenant_access_token_response(data)

        return {
            "tenant_access_token": token,
            "expire": data.get("expire"),
            "code": data.get("code", 0),
            "msg": data.get("msg", "success"),
            "json": data,
        }


@registry.register(
    NodeMetadata(
        name="LarkSendMessageNode",
        description="Send messages to Lark chats or reply to inbound Lark messages",
        category="lark",
    )
)
class LarkSendMessageNode(TaskNode):
    """Send a text reply through the Lark Open Platform."""

    app_id: str = Field(description="Lark app ID")
    app_secret: str = Field(description="Lark app secret")
    receive_id: str | None = Field(
        default=None,
        description="Lark receive_id for new-message sends (typically chat_id)",
    )
    receive_id_type: str = Field(
        default="chat_id",
        description="Lark receive_id type used for new-message sends",
    )
    reply_to_message_id: str | None = Field(
        default=None,
        description="Optional originating message ID for reply sends",
    )
    thread_id: str | None = Field(
        default=None,
        description="Optional thread ID; when present, replies are sent in-thread",
    )
    message: str = Field(description="Text content to send")
    timeout: float | None = Field(
        default=DEFAULT_TIMEOUT,
        description="Timeout in seconds for Lark API requests",
    )

    async def _fetch_tenant_access_token(self) -> str:
        """Fetch a tenant access token directly from the Lark auth API."""
        data = await _request_tenant_access_token(
            app_id=self.app_id,
            app_secret=self.app_secret,
            timeout=self.timeout,
        )
        return _parse_tenant_access_token_response(data)

    async def _resolve_access_token(self, state: State) -> str:
        """Resolve the tenant access token from prior results or fetch it."""
        results = state.get("results", {})
        if isinstance(results, dict):
            token = _extract_tenant_access_token(results.get("get_lark_tenant_token"))
            if token is not None:
                return token
        return await self._fetch_tenant_access_token()

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Send a Lark message or reply to an existing inbound message."""
        del config
        access_token = await self._resolve_access_token(state)
        receive_id = _normalize_optional_value(self.receive_id)
        reply_to_message_id = _normalize_optional_value(self.reply_to_message_id)
        reply_in_thread = _normalize_optional_value(self.thread_id) is not None

        if reply_to_message_id is None and receive_id is None:
            logger.warning(
                "Lark message delivery failed: missing recipient",
                extra={
                    "event": "lark_message_delivery",
                    "status": "failed",
                    "reason": "missing_recipient",
                },
            )
            return {
                "is_error": True,
                "error": "No Lark receive_id or reply_to_message_id provided",
            }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        content = json.dumps({"text": self.message}, ensure_ascii=False)

        if reply_to_message_id is not None:
            url = (
                "https://open.larksuite.com/open-apis/im/v1/messages/"
                f"{reply_to_message_id}/reply"
            )
            params = None
            payload = {
                "content": content,
                "msg_type": "text",
                "reply_in_thread": reply_in_thread,
            }
        else:
            url = "https://open.larksuite.com/open-apis/im/v1/messages"
            params = {"receive_id_type": self.receive_id_type}
            payload = {
                "receive_id": receive_id,
                "content": content,
                "msg_type": "text",
            }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                url,
                params=params,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        code = data.get("code", 0)
        if code != 0:
            logger.warning(
                "Lark message delivery failed",
                extra={
                    "event": "lark_message_delivery",
                    "status": "failed",
                    "code": code,
                    "error_msg": data.get("msg", "Unknown error"),
                },
            )
            return {
                "is_error": True,
                "code": code,
                "msg": data.get("msg", "Unknown error"),
            }

        logger.info(
            "Lark message delivered",
            extra={
                "event": "lark_message_delivery",
                "status": "success",
                "code": 0,
            },
        )
        return {
            "is_error": False,
            "code": 0,
            "msg": data.get("msg", "success"),
            "data": data.get("data"),
        }


__all__ = ["LarkSendMessageNode", "LarkTenantAccessTokenNode"]
