"""Tests for communication nodes."""

from __future__ import annotations
import json
from typing import Any
import httpx
import pytest
import respx
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.connectors.discord import (
    DiscordWebhookNode,
    MessageDiscordNode,
)
from orcheo.nodes.connectors.email import EmailNode
from orcheo.nodes.connectors.qq import MessageQQNode
from orcheo.nodes.communication import (
    _assistant_message_from_state,
    _non_empty_string,
)


class DummySMTP:
    """Simple SMTP stub for validating EmailNode behaviour."""

    def __init__(self, host: str, port: int, timeout: float | None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.messages: list[tuple[Any, Any]] = []

    def __enter__(self) -> DummySMTP:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)

    def send_message(self, message, to_addrs):  # type: ignore[no-untyped-def]
        self.messages.append((message, to_addrs))
        return {}


@pytest.mark.asyncio
async def test_email_node_sends_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """EmailNode should send a message via SMTP."""

    dummy = DummySMTP("localhost", 1025, timeout=30.0)

    def smtp_factory(host: str, port: int, timeout: float | None) -> DummySMTP:
        assert host == "smtp.test"
        assert port == 2525
        assert timeout == 10.0
        return dummy

    monkeypatch.setattr("orcheo.nodes.communication.smtplib.SMTP", smtp_factory)

    node = EmailNode(
        name="email",
        smtp_host="smtp.test",
        smtp_port=2525,
        timeout=10.0,
        from_address="sender@example.com",
        to_addresses=["recipient@example.com"],
        subject="Test",
        body="Hello",
        username="user",
        password="pass",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["email"]

    assert dummy.started_tls is True
    assert dummy.logged_in == ("user", "pass")
    assert dummy.messages
    assert payload["accepted"] == ["recipient@example.com"]


@pytest.mark.asyncio
async def test_email_node_supports_cc_and_bcc(monkeypatch: pytest.MonkeyPatch) -> None:
    """EmailNode should include CC/BCC recipients and respect TLS settings."""

    dummy = DummySMTP("localhost", 1025, timeout=30.0)

    def smtp_factory(host: str, port: int, timeout: float | None) -> DummySMTP:
        return dummy

    monkeypatch.setattr("orcheo.nodes.communication.smtplib.SMTP", smtp_factory)

    node = EmailNode(
        name="email",
        smtp_host="smtp.test",
        smtp_port=2525,
        from_address="sender@example.com",
        to_addresses=[],
        cc_addresses=["cc@example.com"],
        bcc_addresses=["bcc@example.com"],
        subject="Test",
        body="Hello",
        use_tls=False,
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["email"]

    assert dummy.started_tls is False
    assert dummy.logged_in is None
    assert dummy.messages
    message, recipients = dummy.messages[0]
    assert message["Cc"] == "cc@example.com"
    assert set(recipients) == {"cc@example.com", "bcc@example.com"}
    assert payload["accepted"] == ["cc@example.com", "bcc@example.com"]


@pytest.mark.asyncio
async def test_email_node_requires_recipients() -> None:
    """EmailNode should validate recipients are provided."""

    node = EmailNode(
        name="email",
        smtp_host="smtp.test",
        smtp_port=2525,
        from_address="sender@example.com",
    )

    state = State({"node_results": {}})
    with pytest.raises(ValueError):
        await node(state, RunnableConfig())


@pytest.mark.asyncio
async def test_discord_webhook_node_posts_payload() -> None:
    """DiscordWebhookNode should post to the configured webhook URL."""

    state = State({"node_results": {}})
    node = DiscordWebhookNode(
        name="discord",
        webhook_url="https://discordapp.com/api/webhooks/123",
        content="Hello",
        username="Orcheo",
    )

    with respx.mock(base_url="https://discordapp.com") as router:
        route = router.post("/api/webhooks/123").mock(return_value=httpx.Response(204))
        payload = (await node(state, RunnableConfig()))["node_results"]["discord"]

    assert route.called
    assert payload["status_code"] == 204


@pytest.mark.asyncio
async def test_discord_webhook_node_supports_optional_fields() -> None:
    """DiscordWebhookNode should include optional payload fields when provided."""

    state = State({"node_results": {}})
    node = DiscordWebhookNode(
        name="discord",
        webhook_url="https://discordapp.com/api/webhooks/456",
        content="Hello",
        username="Orcheo",
        avatar_url="https://example.com/avatar.png",
        embeds=[{"title": "Update"}],
        tts=True,
    )

    with respx.mock(base_url="https://discordapp.com") as router:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(204)

        route = router.post("/api/webhooks/456").mock(side_effect=handler)
        await node(state, RunnableConfig())

    assert route.called
    assert captured["username"] == "Orcheo"
    assert captured["avatar_url"].endswith("avatar.png")
    assert captured["embeds"] == [{"title": "Update"}]
    assert captured["tts"] is True


@pytest.mark.asyncio
async def test_discord_webhook_node_omits_optional_fields_when_none() -> None:
    """DiscordWebhookNode should omit optional fields when they are None."""

    state = State({"node_results": {}})
    node = DiscordWebhookNode(
        name="discord",
        webhook_url="https://discordapp.com/api/webhooks/789",
        content=None,
        username=None,
        avatar_url=None,
        embeds=None,
        tts=False,
    )

    with respx.mock(base_url="https://discordapp.com") as router:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(204)

        route = router.post("/api/webhooks/789").mock(side_effect=handler)
        await node(state, RunnableConfig())

    assert route.called
    assert "content" not in captured
    assert "username" not in captured
    assert "avatar_url" not in captured
    assert "embeds" not in captured
    assert captured["tts"] is False


@pytest.mark.asyncio
async def test_message_discord_posts_bot_message() -> None:
    """MessageDiscord should send a bot-authenticated channel message."""

    state = State({"node_results": {}})
    node = MessageDiscordNode(
        name="discord_message",
        token="discord_bot_token",
        channel_id="123",
        message="Hello from Orcheo",
        reply_to_message_id="456",
    )

    with respx.mock(base_url="https://discord.com") as router:
        captured_headers: dict[str, str] = {}
        captured_body: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_headers.update(request.headers)
            captured_body.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"id": "789", "channel_id": "123"},
            )

        route = router.post("/api/v10/channels/123/messages").mock(side_effect=handler)
        payload = (await node(state, RunnableConfig()))["node_results"][
            "discord_message"
        ]

    assert route.called
    assert captured_headers["authorization"] == "Bot discord_bot_token"
    assert captured_body["content"] == "Hello from Orcheo"
    assert captured_body["message_reference"] == {"message_id": "456"}
    assert payload["message_id"] == "789"
    assert payload["channel_id"] == "123"


@pytest.mark.asyncio
async def test_message_discord_uses_last_ai_message_when_message_missing() -> None:
    """MessageDiscord should default to the latest assistant message content."""

    state = State({"messages": [AIMessage(content="Synthesized reply")]})
    node = MessageDiscordNode(
        name="discord_message",
        token="discord_bot_token",
        channel_id="123",
    )

    with respx.mock(base_url="https://discord.com") as router:
        captured_body: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_body.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"id": "789", "channel_id": "123"},
            )

        router.post("/api/v10/channels/123/messages").mock(side_effect=handler)
        await node(state, RunnableConfig())

    assert captured_body["content"] == "Synthesized reply"


@pytest.mark.asyncio
async def test_message_qq_posts_c2c_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """MessageQQNode should send a C2C QQ message using bot auth."""

    async def fake_token(self, *, app_id: str, client_secret: str) -> str:  # noqa: ARG001
        assert app_id == "qq-app-id"
        assert client_secret == "qq-client-secret"
        return "qq-access-token"

    monkeypatch.setattr(
        "orcheo.nodes.communication.DefaultQQAccessTokenProvider.get_access_token",
        fake_token,
    )

    state = State({"node_results": {}})
    node = MessageQQNode(
        name="qq_message",
        app_id="qq-app-id",
        client_secret="qq-client-secret",
        openid="user-openid",
        message="Hello QQ",
        msg_id="source-message",
    )

    with respx.mock(base_url="https://api.sgroup.qq.com") as router:
        captured_headers: dict[str, str] = {}
        captured_body: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_headers.update(request.headers)
            captured_body.update(json.loads(request.content))
            return httpx.Response(200, json={"id": "reply-1"})

        route = router.post("/v2/users/user-openid/messages").mock(side_effect=handler)
        payload = (await node(state, RunnableConfig()))["node_results"]["qq_message"]

    assert route.called
    assert captured_headers["authorization"] == "QQBot qq-access-token"
    assert captured_body["content"] == "Hello QQ"
    assert captured_body["msg_id"] == "source-message"
    assert captured_body["msg_seq"] == 1
    assert payload["message_id"] == "reply-1"
    assert payload["scene_type"] == "c2c"


@pytest.mark.asyncio
async def test_message_qq_uses_last_ai_message_for_group_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MessageQQNode should reuse the latest assistant message when needed."""

    async def fake_token(self, *, app_id: str, client_secret: str) -> str:  # noqa: ARG001
        del app_id, client_secret
        return "qq-access-token"

    monkeypatch.setattr(
        "orcheo.nodes.communication.DefaultQQAccessTokenProvider.get_access_token",
        fake_token,
    )

    state = State({"messages": [AIMessage(content="Synthesized QQ reply")]})
    node = MessageQQNode(
        name="qq_message",
        app_id="qq-app-id",
        client_secret="qq-client-secret",
        group_openid="group-openid",
    )

    with respx.mock(base_url="https://api.sgroup.qq.com") as router:
        captured_body: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_body.update(json.loads(request.content))
            return httpx.Response(200, json={"id": "reply-2"})

        router.post("/v2/groups/group-openid/messages").mock(side_effect=handler)
        await node(state, RunnableConfig())

    assert captured_body["content"] == "Synthesized QQ reply"
    assert captured_body["msg_type"] == 0


def test_assistant_message_from_state_prefers_latest_assistant_string() -> None:
    state = State(
        {
            "messages": [
                {"role": "assistant", "content": "  Ready  "},
                {"role": "user", "content": "Ignore this"},
            ]
        }
    )
    assert _assistant_message_from_state(state) == "  Ready  "


def test_assistant_message_from_state_handles_ai_message_content_types() -> None:
    state = State({"messages": [AIMessage(content=[{"text": "block"}])]})
    assert _assistant_message_from_state(state) == "[{'text': 'block'}]"


def test_assistant_message_from_state_skips_blank_dict_and_falls_back_to_ai() -> None:
    state = State(
        {
            "messages": [
                AIMessage(content="fallback"),
                {"role": "assistant", "content": "   "},
            ]
        }
    )
    assert _assistant_message_from_state(state) == "fallback"


def test_assistant_message_from_state_returns_none_when_absent() -> None:
    state = State({"messages": [{"role": "user", "content": "hi"}]})
    assert _assistant_message_from_state(state) is None


def test_non_empty_string_trims_and_returns_none_for_blanks() -> None:
    assert _non_empty_string("  keep  ") == "keep"
    assert _non_empty_string("   ") is None


@pytest.mark.asyncio
async def test_message_discord_requires_channel_id() -> None:
    node = MessageDiscordNode(name="discord", token="token", message="hello")
    with pytest.raises(ValueError, match="Discord channel_id is required"):
        await node.run(State({"node_results": {}}), RunnableConfig())


@pytest.mark.asyncio
async def test_message_discord_requires_message_content() -> None:
    node = MessageDiscordNode(name="discord", token="token", channel_id="123")
    with pytest.raises(ValueError, match="Discord message content is required"):
        await node.run(State({"node_results": {}}), RunnableConfig())


@pytest.mark.asyncio
async def test_message_qq_requires_message_content() -> None:
    node = MessageQQNode(
        name="qq",
        app_id="qq-app-id",
        client_secret="qq-client-secret",
        openid="user",
    )
    with pytest.raises(ValueError, match="QQ message content is required"):
        await node.run(State({"node_results": {}}), RunnableConfig())


@pytest.mark.asyncio
async def test_message_qq_requires_target_id(monkeypatch: pytest.MonkeyPatch) -> None:
    node = MessageQQNode(
        name="qq",
        app_id="qq-app-id",
        client_secret="qq-client-secret",
        message="hi",
    )

    async def fake_token(self, *, app_id: str, client_secret: str) -> str:  # noqa: ARG001
        del app_id, client_secret
        return "token"

    monkeypatch.setattr(
        "orcheo.nodes.communication.DefaultQQAccessTokenProvider.get_access_token",
        fake_token,
    )
    with pytest.raises(
        ValueError, match="QQ openid, group_openid, or channel_id is required"
    ):
        await node.run(State({"node_results": {}}), RunnableConfig())


@pytest.mark.asyncio
async def test_message_qq_posts_channel_message_with_event_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_token(self, *, app_id: str, client_secret: str) -> str:  # noqa: ARG001
        del app_id, client_secret
        return "qq-access-token"

    monkeypatch.setattr(
        "orcheo.nodes.communication.DefaultQQAccessTokenProvider.get_access_token",
        fake_token,
    )

    node = MessageQQNode(
        name="qq_channel_message",
        app_id="qq-app-id",
        client_secret="qq-client-secret",
        channel_id="channel-123",
        event_id="event-1",
        message="hello channel",
    )

    with respx.mock(base_url="https://api.sgroup.qq.com") as router:
        captured_body: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_body.update(json.loads(request.content))
            return httpx.Response(200, json={"id": "reply-3"})

        route = router.post("/channels/channel-123/messages").mock(side_effect=handler)
        payload = (await node(State({"node_results": {}}), RunnableConfig()))[
            "node_results"
        ]["qq_channel_message"]

    assert route.called
    assert captured_body["content"] == "hello channel"
    assert captured_body["event_id"] == "event-1"
    assert payload["message_id"] == "reply-3"
    assert payload["scene_type"] == "channel"
    assert payload["target_id"] == "channel-123"
