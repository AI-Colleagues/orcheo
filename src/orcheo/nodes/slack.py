"""Slack node."""

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any, Literal
import httpx
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, field_validator
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


_SLACK_API_BASE_URL = "https://slack.com/api"


@registry.register(
    NodeMetadata(
        name="SlackNode",
        description="Slack node",
        category="slack",
    )
)
class SlackNode(TaskNode):
    """Slack node.

    To use this node, you need to set the following environment variables:
    - SLACK_BOT_TOKEN: Required. The Bot User OAuth Token starting with xoxb-.
    - SLACK_TEAM_ID: Required. Your Slack workspace ID starting with T.
    - SLACK_CHANNEL_IDS: Optional. Comma-separated list of channel IDs to limit
    channel access (e.g., "C01234567, C76543210"). If not set, all public
    channels will be listed.
    """

    tool_name: Literal[
        "slack_list_channels",
        "slack_post_message",
        "slack_reply_to_thread",
        "slack_add_reaction",
        "slack_get_channel_history",
        "slack_get_thread_replies",
        "slack_get_users",
        "slack_get_user_profile",
    ]
    """The name of the supported Slack action."""
    kwargs: dict = {}
    """The keyword arguments to pass to the tool."""
    bot_token: str = "[[slack_bot_token]]"
    """Bot user OAuth token."""
    team_id: str = "[[slack_team_id]]"
    """Slack workspace ID."""
    channel_ids: str | None = None
    """Optional comma separated list of channel IDs."""

    def _serialize_response(self, response_payload: dict[str, Any]) -> dict[str, Any]:
        """Return a structured node result from a Slack API response."""
        is_error = not bool(response_payload.get("ok"))
        return {
            **response_payload,
            "is_error": is_error,
            "error": response_payload.get("error") if is_error else None,
        }

    def _error_result(self, message: str) -> dict[str, Any]:
        """Return a normalized error payload."""
        return {
            "is_error": True,
            "error": message,
        }

    def _headers(self) -> dict[str, str]:
        """Return Slack Web API request headers."""
        return {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _coerce_limit(self, raw_value: Any, default: int) -> int:
        """Return a bounded integer limit for Slack list endpoints."""
        if raw_value is None:
            return default
        try:
            return min(int(raw_value), 200)
        except (TypeError, ValueError):
            return default

    def _normalize_channel_payload(self) -> dict[str, Any]:
        """Map generic kwargs into Slack Web API field names."""
        payload = dict(self.kwargs)
        channel = payload.pop("channel_id", None) or payload.get("channel")
        if isinstance(channel, str) and channel:
            payload["channel"] = channel
        return payload

    async def _get_json(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Issue a GET request to Slack Web API and return parsed JSON."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{_SLACK_API_BASE_URL}/{endpoint}",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
        return response.json()

    async def _post_json(
        self, endpoint: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Issue a POST request to Slack Web API and return parsed JSON."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{_SLACK_API_BASE_URL}/{endpoint}",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
        return response.json()

    async def _post_message(self) -> dict[str, Any]:
        """Send a message or thread reply via Slack Web API."""
        payload = self._normalize_channel_payload()
        channel = payload.get("channel")
        text = payload.get("text")
        if not isinstance(channel, str) or not channel:
            return self._error_result("Missing required argument: channel_id")
        if not isinstance(text, str) or not text:
            return self._error_result("Missing required argument: text")
        if self.tool_name == "slack_reply_to_thread":
            thread_ts = payload.get("thread_ts")
            if not isinstance(thread_ts, str) or not thread_ts:
                return self._error_result("Missing required argument: thread_ts")
        try:
            response_payload = await self._post_json("chat.postMessage", payload)
        except httpx.HTTPError as exc:
            return self._error_result(str(exc))
        return self._serialize_response(response_payload)

    async def _list_channels(self) -> dict[str, Any]:
        """List accessible Slack channels."""
        try:
            if self.channel_ids:
                channels = []
                for channel_id in self.channel_ids.split(","):
                    normalized_channel_id = channel_id.strip()
                    if not normalized_channel_id:
                        continue
                    response_payload = await self._get_json(
                        "conversations.info",
                        {"channel": normalized_channel_id},
                    )
                    if not bool(response_payload.get("ok")):
                        return self._serialize_response(response_payload)
                    channel = response_payload.get("channel")
                    if isinstance(channel, dict) and not channel.get("is_archived"):
                        channels.append(channel)
                return self._serialize_response(
                    {
                        "ok": True,
                        "channels": channels,
                        "response_metadata": {"next_cursor": ""},
                    }
                )

            params = {
                "types": "public_channel",
                "exclude_archived": "true",
                "limit": str(self._coerce_limit(self.kwargs.get("limit"), 100)),
                "team_id": self.team_id,
            }
            cursor = self.kwargs.get("cursor")
            if isinstance(cursor, str) and cursor:
                params["cursor"] = cursor
            response_payload = await self._get_json("conversations.list", params)
        except httpx.HTTPError as exc:
            return self._error_result(str(exc))
        return self._serialize_response(response_payload)

    async def _add_reaction(self) -> dict[str, Any]:
        """Add a reaction to a Slack message."""
        payload = self._normalize_channel_payload()
        timestamp = payload.get("timestamp")
        reaction = payload.pop("reaction", None)
        if not isinstance(payload.get("channel"), str) or not payload["channel"]:
            return self._error_result("Missing required argument: channel_id")
        if not isinstance(timestamp, str) or not timestamp:
            return self._error_result("Missing required argument: timestamp")
        if not isinstance(reaction, str) or not reaction:
            return self._error_result("Missing required argument: reaction")
        payload["timestamp"] = timestamp
        payload["name"] = reaction
        try:
            response_payload = await self._post_json("reactions.add", payload)
        except httpx.HTTPError as exc:
            return self._error_result(str(exc))
        return self._serialize_response(response_payload)

    async def _get_channel_history(self) -> dict[str, Any]:
        """Fetch recent messages from a Slack channel."""
        payload = self._normalize_channel_payload()
        channel = payload.get("channel")
        if not isinstance(channel, str) or not channel:
            return self._error_result("Missing required argument: channel_id")
        params = {
            "channel": channel,
            "limit": str(self._coerce_limit(self.kwargs.get("limit"), 10)),
        }
        try:
            response_payload = await self._get_json("conversations.history", params)
        except httpx.HTTPError as exc:
            return self._error_result(str(exc))
        return self._serialize_response(response_payload)

    async def _get_thread_replies(self) -> dict[str, Any]:
        """Fetch all replies for a Slack thread."""
        payload = self._normalize_channel_payload()
        channel = payload.get("channel")
        thread_ts = payload.get("thread_ts")
        if not isinstance(channel, str) or not channel:
            return self._error_result("Missing required argument: channel_id")
        if not isinstance(thread_ts, str) or not thread_ts:
            return self._error_result("Missing required argument: thread_ts")
        try:
            response_payload = await self._get_json(
                "conversations.replies",
                {"channel": channel, "ts": thread_ts},
            )
        except httpx.HTTPError as exc:
            return self._error_result(str(exc))
        return self._serialize_response(response_payload)

    async def _get_users(self) -> dict[str, Any]:
        """List Slack workspace users."""
        params = {
            "limit": str(self._coerce_limit(self.kwargs.get("limit"), 100)),
            "team_id": self.team_id,
        }
        cursor = self.kwargs.get("cursor")
        if isinstance(cursor, str) and cursor:
            params["cursor"] = cursor
        try:
            response_payload = await self._get_json("users.list", params)
        except httpx.HTTPError as exc:
            return self._error_result(str(exc))
        return self._serialize_response(response_payload)

    async def _get_user_profile(self) -> dict[str, Any]:
        """Fetch one Slack user profile."""
        user_id = self.kwargs.get("user_id")
        if not isinstance(user_id, str) or not user_id:
            return self._error_result("Missing required argument: user_id")
        try:
            response_payload = await self._get_json(
                "users.profile.get",
                {"user": user_id, "include_labels": "true"},
            )
        except httpx.HTTPError as exc:
            return self._error_result(str(exc))
        return self._serialize_response(response_payload)

    async def run(self, state: State, config: RunnableConfig) -> dict:
        """Run the Slack node."""
        tool_handlers = {
            "slack_list_channels": self._list_channels,
            "slack_post_message": self._post_message,
            "slack_reply_to_thread": self._post_message,
            "slack_add_reaction": self._add_reaction,
            "slack_get_channel_history": self._get_channel_history,
            "slack_get_thread_replies": self._get_thread_replies,
            "slack_get_users": self._get_users,
            "slack_get_user_profile": self._get_user_profile,
        }
        return await tool_handlers[self.tool_name]()


@registry.register(
    NodeMetadata(
        name="SlackEventsParserNode",
        description="Validate and parse Slack Events API payloads",
        category="slack",
    )
)
class SlackEventsParserNode(TaskNode):
    """Validate Slack signatures and parse Events API payloads."""

    signing_secret: str = "[[slack_signing_secret]]"
    """Slack signing secret."""
    allowed_event_types: list[str] = Field(
        default_factory=lambda: ["app_mention"],
        description="Slack event types allowed to pass through",
    )
    channel_id: str | None = Field(
        default=None,
        description="Optional channel ID to filter events",
    )
    timestamp_tolerance_seconds: int | str = Field(
        default=300,
        description="Maximum age for Slack signature timestamps",
    )
    body_key: str = Field(
        default="body",
        description="Key in inputs that contains the webhook payload",
    )

    @field_validator("timestamp_tolerance_seconds", mode="before")
    @classmethod
    def _validate_timestamp_tolerance(cls, value: Any) -> Any:
        if isinstance(value, str):
            if cls.contains_template(value):
                return value
            try:
                value = int(value)
            except ValueError as exc:
                msg = "timestamp_tolerance_seconds must be an integer"
                raise ValueError(msg) from exc
        if isinstance(value, int) and value < 0:
            msg = "timestamp_tolerance_seconds must be >= 0"
            raise ValueError(msg)
        return value  # pragma: no cover - defensive code

    def _normalize_headers(self, headers: dict[str, str]) -> dict[str, str]:
        return {key.lower(): value for key, value in headers.items()}

    def _extract_inputs(self, state: State) -> dict[str, Any]:
        if isinstance(state, BaseModel):
            state_dict = state.model_dump()
            raw_inputs = state_dict.get("inputs")
            if isinstance(raw_inputs, Mapping):
                return dict(raw_inputs)
            return dict(state_dict)
        if isinstance(state, Mapping):
            state_dict = dict(state)
            raw_inputs = state_dict.get("inputs")
            if isinstance(raw_inputs, Mapping):
                merged = dict(raw_inputs)
                for key in ("body", "headers", "query_params", "source_ip"):
                    if key in state_dict and key not in merged:
                        merged[key] = state_dict[key]
                return merged
            return state_dict
        return {}

    def _extract_raw_body(self, body: Any) -> tuple[str, dict[str, Any]]:
        if isinstance(body, Mapping) and "raw" in body:
            raw_body = body.get("raw")
            if isinstance(raw_body, str):  # pragma: no branch
                return raw_body, self._parse_json(raw_body)
        if isinstance(body, bytes):
            raw_text = body.decode("utf-8", errors="replace")
            return raw_text, self._parse_json(raw_text)
        if isinstance(body, str):
            return body, self._parse_json(body)
        if isinstance(body, Mapping):
            raw_text = json.dumps(body, separators=(",", ":"), ensure_ascii=True)
            return raw_text, dict(body)
        msg = "Slack event payload must be a dict, string, or bytes"
        raise ValueError(msg)

    def _parse_json(self, raw_body: str) -> dict[str, Any]:
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
        return {}

    def _verify_signature(self, raw_body: str, headers: dict[str, str]) -> None:
        signature = headers.get("x-slack-signature")
        timestamp_value = headers.get("x-slack-request-timestamp")
        if not signature or not timestamp_value:
            raise ValueError("Missing Slack signature headers")

        try:
            timestamp = int(timestamp_value)
        except ValueError as exc:
            raise ValueError("Invalid Slack timestamp header") from exc

        tolerance = self.timestamp_tolerance_seconds
        if isinstance(tolerance, str):
            try:
                tolerance_value = int(tolerance)
            except ValueError as exc:
                msg = "timestamp_tolerance_seconds must resolve to an integer"
                raise ValueError(msg) from exc
        else:
            tolerance_value = tolerance

        if tolerance_value:  # pragma: no branch
            now = int(time.time())
            if abs(now - timestamp) > tolerance_value:
                raise ValueError("Slack request timestamp outside tolerance window")

        signature_base = f"v0:{timestamp}:{raw_body}".encode()
        digest = hmac.new(
            self.signing_secret.encode(),
            signature_base,
            hashlib.sha256,
        ).hexdigest()
        expected = f"v0={digest}"
        if not hmac.compare_digest(expected, signature):
            raise ValueError("Slack signature verification failed")

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Parse the Slack Events API payload and validate signatures."""
        inputs = self._extract_inputs(state)
        headers = inputs.get("headers", {})
        if not isinstance(headers, dict):
            msg = "Slack event headers must be a dictionary"
            raise ValueError(msg)

        normalized_headers = self._normalize_headers(headers)
        body = inputs.get(self.body_key)
        raw_body, payload = self._extract_raw_body(body)

        if self.signing_secret:
            if not raw_body:
                raise ValueError("Slack signature verification requires raw payload")
            self._verify_signature(raw_body, normalized_headers)

        # Reject Slack retries to prevent duplicate processing.  Slack sets
        # the x-slack-retry-num header on every retry attempt.
        if normalized_headers.get("x-slack-retry-num") is not None:
            return {
                "is_verification": False,
                "event_type": None,
                "event": None,
                "should_process": False,
            }

        payload_type = payload.get("type")
        if payload_type == "url_verification":
            return {
                "is_verification": True,
                "challenge": payload.get("challenge"),
                "should_process": False,
            }

        if payload_type != "event_callback":
            return {
                "is_verification": False,
                "event_type": payload_type,
                "event": payload.get("event"),
                "should_process": False,
            }

        event = payload.get("event") or {}
        event_type = event.get("type")
        channel = event.get("channel")

        if self.allowed_event_types and event_type not in self.allowed_event_types:
            return {
                "is_verification": False,
                "event_type": event_type,
                "event": event,
                "should_process": False,
            }

        if self.channel_id and channel != self.channel_id:
            return {
                "is_verification": False,
                "event_type": event_type,
                "event": event,
                "should_process": False,
            }

        return {
            "is_verification": False,
            "event_type": event_type,
            "event": event,
            "channel": channel,
            "user": event.get("user"),
            "text": event.get("text"),
            "should_process": True,
        }


__all__ = ["SlackEventsParserNode", "SlackNode"]
