"""Tests for Discord listener normalization and Gateway handling."""

from __future__ import annotations
import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any
from uuid import UUID, uuid4
import pytest
from orcheo.listeners import (
    DefaultDiscordGatewayConnector,
    DefaultDiscordGatewayHttpClient,
    DiscordGatewayAdapter,
    DiscordGatewayInfo,
    DiscordGatewaySessionStartLimit,
    ListenerCursor,
    ListenerDispatchPayload,
    ListenerPlatform,
    ListenerSubscription,
    discord_intents_bitmask,
    normalize_discord_event,
)
from orcheo.listeners.discord import (
    _decode_gateway_payload,
    _discord_message_passes_filters,
    _discord_message_type_name,
    _extract_heartbeat_interval,
    _has_message_content_intent,
    _is_self_authored_message,
    _mentions_bot,
    _string_or_none,
    _with_gateway_params,
)
from orcheo.models import WorkflowDraftAccess
from orcheo_backend.app.repository import InMemoryWorkflowRepository


class FakeDiscordGatewayClient:
    """Simple stub for Discord ``GET /gateway/bot`` responses."""

    def __init__(self, info: DiscordGatewayInfo) -> None:
        self.info = info
        self.calls: list[str] = []

    async def get_gateway_bot(self, *, token: str) -> DiscordGatewayInfo:
        self.calls.append(token)
        return self.info


class FakeDiscordWebSocket:
    """Predictable WebSocket stub for Discord gateway tests."""

    def __init__(self, incoming: list[dict[str, Any]]) -> None:
        self._incoming = [json.dumps(item) for item in incoming]
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        if self._incoming:
            return self._incoming.pop(0)
        await asyncio.sleep(0.01)
        return json.dumps({"op": 7, "d": None})

    async def close(self, code: int = 1000) -> None:
        del code
        self.closed = True


class FakeDiscordConnector:
    """Connector returning a prebuilt fake WebSocket."""

    def __init__(self, websocket: FakeDiscordWebSocket) -> None:
        self.websocket = websocket
        self.calls: list[str] = []

    def connect(self, url: str) -> AbstractAsyncContextManager[FakeDiscordWebSocket]:
        self.calls.append(url)

        @asynccontextmanager
        async def _manager() -> AsyncIterator[FakeDiscordWebSocket]:
            yield self.websocket

        return _manager()


def _listener_graph(*listeners: dict[str, object]) -> dict[str, object]:
    return {"nodes": [], "edges": [], "index": {"listeners": list(listeners)}}


class RecordingDiscordRepository:
    def __init__(self) -> None:
        self.saved: list[ListenerCursor] = []
        self.dispatched: list[ListenerDispatchPayload] = []
        self.cursor: ListenerCursor | None = None

    async def get_listener_cursor(
        self,
        subscription_id: UUID,
    ) -> ListenerCursor | None:
        del subscription_id
        return self.cursor

    async def save_listener_cursor(
        self,
        cursor: ListenerCursor,
    ) -> ListenerCursor:
        self.saved.append(cursor)
        self.cursor = cursor
        return cursor

    async def dispatch_listener_event(
        self,
        subscription_id: UUID,
        payload: ListenerDispatchPayload,
    ) -> ListenerDispatchPayload:
        self.dispatched.append(payload)
        return payload


def _build_subscription(config: dict[str, Any] | None = None) -> ListenerSubscription:
    return ListenerSubscription(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        node_name="discord_listener",
        platform=ListenerPlatform.DISCORD,
        bot_identity_key="bot-1",
        config=dict(config or {}),
    )


async def _create_discord_subscription(
    *,
    repository: InMemoryWorkflowRepository,
    token: str = "[[discord_bot_token]]",
    intents: list[str] | None = None,
    allowed_guild_ids: list[str] | None = None,
    allowed_channel_ids: list[str] | None = None,
    include_direct_messages: bool = True,
    allowed_message_types: list[str] | None = None,
    require_bot_mention: bool = False,
) -> ListenerSubscription:
    workflow = await repository.create_workflow(
        name="Discord Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {
                "node_name": "discord_listener",
                "platform": "discord",
                "token": token,
                "intents": intents
                or [
                    "guilds",
                    "guild_messages",
                    "direct_messages",
                    "message_content",
                ],
                "allowed_guild_ids": allowed_guild_ids or [],
                "allowed_channel_ids": allowed_channel_ids or [],
                "include_direct_messages": include_direct_messages,
                "allowed_message_types": allowed_message_types or ["DEFAULT"],
                "require_bot_mention": require_bot_mention,
            }
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    return (await repository.list_listener_subscriptions(workflow_id=workflow.id))[0]


@pytest.mark.asyncio
async def test_normalize_discord_event_filters_and_routes_message() -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_discord_subscription(
        repository=repository,
        allowed_guild_ids=["guild-1"],
        allowed_channel_ids=["channel-1"],
        require_bot_mention=True,
    )

    payload = normalize_discord_event(
        subscription,
        "MESSAGE_CREATE",
        {
            "id": "message-1",
            "channel_id": "channel-1",
            "guild_id": "guild-1",
            "type": 0,
            "content": "hello discord",
            "author": {"id": "user-1", "username": "Alice"},
            "mentions": [{"id": "bot-1", "bot": True}],
        },
        bot_user_id="bot-1",
    )

    assert isinstance(payload, ListenerDispatchPayload)
    assert payload.message.channel_id == "channel-1"
    assert payload.message.guild_id == "guild-1"
    assert payload.message.text == "hello discord"
    assert payload.reply_target == {
        "channel_id": "channel-1",
        "reply_to_message_id": "message-1",
    }


@pytest.mark.asyncio
async def test_normalize_discord_event_tracks_missing_message_content() -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_discord_subscription(
        repository=repository,
        intents=["guilds", "guild_messages"],
    )

    payload = normalize_discord_event(
        subscription,
        "MESSAGE_CREATE",
        {
            "id": "message-1",
            "channel_id": "channel-1",
            "guild_id": "guild-1",
            "type": 0,
            "content": "",
            "author": {"id": "user-1", "username": "Alice"},
            "mentions": [],
        },
    )

    assert isinstance(payload, ListenerDispatchPayload)
    assert payload.message.text is None
    assert payload.metadata["content_available"] is False


@pytest.mark.asyncio
async def test_normalize_discord_event_ignores_self_authored_messages() -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_discord_subscription(repository=repository)

    payload = normalize_discord_event(
        subscription,
        "MESSAGE_CREATE",
        {
            "id": "message-1",
            "channel_id": "channel-1",
            "type": 0,
            "content": "hello discord",
            "author": {"id": "bot-1", "username": "CD Bot", "bot": True},
            "mentions": [],
        },
        bot_user_id="bot-1",
    )

    assert payload is None


@pytest.mark.asyncio
async def test_normalize_discord_event_requires_matching_bot_mention() -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_discord_subscription(
        repository=repository,
        require_bot_mention=True,
    )

    payload = normalize_discord_event(
        subscription,
        "MESSAGE_CREATE",
        {
            "id": "message-1",
            "channel_id": "channel-1",
            "type": 0,
            "content": "hello discord",
            "author": {"id": "user-1", "username": "Alice"},
            "mentions": [{"id": "other-bot", "bot": True}],
        },
        bot_user_id="bot-1",
    )

    assert payload is None


@pytest.mark.asyncio
async def test_discord_gateway_adapter_dispatches_and_saves_cursor() -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_discord_subscription(repository=repository)
    client = FakeDiscordGatewayClient(
        DiscordGatewayInfo(
            url="wss://gateway.discord.gg",
            session_start_limit=DiscordGatewaySessionStartLimit(
                remaining=1,
                reset_after=1000,
            ),
        )
    )
    websocket = FakeDiscordWebSocket(
        [
            {"op": 10, "d": {"heartbeat_interval": 100}},
            {
                "op": 0,
                "t": "READY",
                "s": 1,
                "d": {
                    "session_id": "session-1",
                    "resume_gateway_url": "wss://resume.discord.gg",
                    "user": {"id": "bot-1"},
                },
            },
            {
                "op": 0,
                "t": "MESSAGE_CREATE",
                "s": 2,
                "d": {
                    "id": "message-1",
                    "channel_id": "channel-1",
                    "guild_id": "guild-1",
                    "type": 0,
                    "content": "hello discord",
                    "author": {"id": "user-1", "username": "Alice"},
                    "mentions": [{"id": "bot-1", "bot": True}],
                },
            },
            {"op": 7, "d": None},
        ]
    )
    connector = FakeDiscordConnector(websocket)
    adapter = DiscordGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
        gateway_client=client,
        gateway_connector=connector,
    )

    await adapter.run_session_once(
        token="[[discord_bot_token]]",
        stop_event=asyncio.Event(),
    )

    runs = await repository.list_runs_for_workflow(subscription.workflow_id)
    assert len(runs) == 1
    assert websocket.sent[0]["op"] == 2
    cursor = await repository.get_listener_cursor(subscription.id)
    assert cursor is not None
    assert cursor.discord_session_id == "session-1"
    assert cursor.discord_sequence == 2
    assert cursor.discord_resume_gateway_url == (
        "wss://resume.discord.gg?v=10&encoding=json"
    )
    health = adapter.health()
    assert health.platform is ListenerPlatform.DISCORD
    assert health.status == "healthy"


@pytest.mark.asyncio
async def test_discord_gateway_adapter_resumes_existing_session() -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_discord_subscription(repository=repository)
    await repository.save_listener_cursor(
        ListenerCursor(
            subscription_id=subscription.id,
            discord_session_id="session-1",
            discord_sequence=7,
            discord_resume_gateway_url="wss://resume.discord.gg?v=10&encoding=json",
        )
    )
    client = FakeDiscordGatewayClient(
        DiscordGatewayInfo(
            url="wss://gateway.discord.gg",
            session_start_limit=DiscordGatewaySessionStartLimit(
                remaining=1,
                reset_after=1000,
            ),
        )
    )
    websocket = FakeDiscordWebSocket(
        [
            {"op": 10, "d": {"heartbeat_interval": 100}},
            {"op": 0, "t": "RESUMED", "s": 8, "d": {}},
            {"op": 7, "d": None},
        ]
    )
    connector = FakeDiscordConnector(websocket)
    adapter = DiscordGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
        gateway_client=client,
        gateway_connector=connector,
    )

    await adapter.run_session_once(
        token="[[discord_bot_token]]",
        stop_event=asyncio.Event(),
    )

    assert websocket.sent[0] == {
        "op": 6,
        "d": {
            "token": "[[discord_bot_token]]",
            "session_id": "session-1",
            "seq": 7,
        },
    }
    cursor = await repository.get_listener_cursor(subscription.id)
    assert cursor is not None
    assert cursor.discord_sequence == 8


@pytest.mark.asyncio
async def test_discord_gateway_adapter_waits_when_session_limit_exhausted() -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_discord_subscription(repository=repository)
    client = FakeDiscordGatewayClient(
        DiscordGatewayInfo(
            url="wss://gateway.discord.gg",
            session_start_limit=DiscordGatewaySessionStartLimit(
                remaining=0,
                reset_after=10,
            ),
        )
    )
    websocket = FakeDiscordWebSocket([{"op": 10, "d": {"heartbeat_interval": 100}}])
    connector = FakeDiscordConnector(websocket)
    adapter = DiscordGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
        gateway_client=client,
        gateway_connector=connector,
    )

    await adapter.run_session_once(
        token="[[discord_bot_token]]",
        stop_event=asyncio.Event(),
    )

    assert connector.calls == []
    assert adapter.health().status == "backoff"


@pytest.mark.asyncio
async def test_discord_gateway_adapter_skips_receive_loop_when_stopped() -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_discord_subscription(repository=repository)
    client = FakeDiscordGatewayClient(
        DiscordGatewayInfo(
            url="wss://gateway.discord.gg",
            session_start_limit=DiscordGatewaySessionStartLimit(
                remaining=1,
                reset_after=1000,
            ),
        )
    )
    websocket = FakeDiscordWebSocket([{"op": 10, "d": {"heartbeat_interval": 100}}])
    connector = FakeDiscordConnector(websocket)
    adapter = DiscordGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
        gateway_client=client,
        gateway_connector=connector,
    )
    stop_event = asyncio.Event()
    stop_event.set()

    await adapter.run_session_once(
        token="[[discord_bot_token]]",
        stop_event=stop_event,
    )

    assert websocket.sent[0]["op"] == 2
    assert websocket.closed is True


@pytest.mark.asyncio
async def test_discord_gateway_adapter_heartbeat_uses_latest_sequence() -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_discord_subscription(repository=repository)
    websocket = FakeDiscordWebSocket([])
    adapter = DiscordGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
    )
    adapter._sequence = 42
    stop_event = asyncio.Event()

    task = asyncio.create_task(
        adapter._heartbeat_loop(
            websocket=websocket,
            stop_event=stop_event,
            interval_seconds=0.01,
        )
    )
    await asyncio.sleep(0.03)
    stop_event.set()
    await task

    assert websocket.sent
    assert websocket.sent[0] == {"op": 1, "d": 42}


def test_discord_intents_bitmask_includes_configured_flags() -> None:
    bitmask = discord_intents_bitmask(
        ["guilds", "guild_messages", "direct_messages", "message_content"]
    )
    assert bitmask == (1 | (1 << 9) | (1 << 12) | (1 << 15))


@pytest.mark.asyncio
async def test_handle_non_dispatch_opcodes_reply_and_reconnect() -> None:
    repository = RecordingDiscordRepository()
    subscription = _build_subscription({"token": "[[discord_bot_token]]"})
    adapter = DiscordGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
    )
    websocket = FakeDiscordWebSocket([])
    cursor = ListenerCursor(subscription_id=subscription.id)
    adapter._sequence = 123

    assert (
        await adapter._handle_non_dispatch_op(websocket=websocket, cursor=cursor, op=11)
    ) is False
    assert websocket.sent == []

    assert (
        await adapter._handle_non_dispatch_op(websocket=websocket, cursor=cursor, op=1)
    ) is False
    assert websocket.sent[-1] == {"op": 1, "d": 123}

    assert (
        await adapter._handle_non_dispatch_op(websocket=websocket, cursor=cursor, op=7)
    ) is True
    assert adapter.health().detail == "gateway_requested_reconnect"

    assert (
        await adapter._handle_non_dispatch_op(websocket=websocket, cursor=cursor, op=5)
    ) is False

    cursor.discord_session_id = "session-1"
    cursor.discord_sequence = 42
    cursor.discord_resume_gateway_url = "wss://resume"
    adapter._bot_user_id = "bot-1"

    assert (
        await adapter._handle_non_dispatch_op(websocket=websocket, cursor=cursor, op=9)
    ) is True
    assert cursor.discord_session_id is None
    assert cursor.discord_sequence is None
    assert cursor.discord_resume_gateway_url is None
    assert adapter._bot_user_id is None
    assert repository.saved[-1] is cursor


@pytest.mark.asyncio
async def test_handle_gateway_frame_ignores_invalid_payload() -> None:
    repository = RecordingDiscordRepository()
    subscription = _build_subscription()
    adapter = DiscordGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
    )
    cursor = ListenerCursor(subscription_id=subscription.id)
    websocket = FakeDiscordWebSocket([])

    frame = json.dumps({"op": 0, "s": 1, "t": 123, "d": {}})
    assert (
        await adapter._handle_gateway_frame(
            websocket=websocket, frame=frame, cursor=cursor
        )
    ) is False
    assert repository.dispatched == []


@pytest.mark.asyncio
async def test_handle_dispatch_event_ready_routes_to_ready_handler() -> None:
    repository = RecordingDiscordRepository()
    subscription = _build_subscription()
    adapter = DiscordGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
    )
    cursor = ListenerCursor(subscription_id=subscription.id)

    result = await adapter._handle_dispatch_event(
        event_type="READY",
        data={
            "session_id": "session-1",
            "resume_gateway_url": "wss://resume",
            "user": {"id": "bot-1"},
        },
        cursor=cursor,
    )

    assert result is False
    assert cursor.discord_session_id == "session-1"
    assert cursor.discord_resume_gateway_url.endswith("v=10&encoding=json")
    assert adapter._bot_user_id == "bot-1"
    assert repository.saved


@pytest.mark.asyncio
async def test_handle_ready_event_ignores_invalid_optional_fields() -> None:
    repository = RecordingDiscordRepository()
    subscription = _build_subscription()
    adapter = DiscordGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
    )
    cursor = ListenerCursor(subscription_id=subscription.id)
    cursor.discord_session_id = "existing-session"
    cursor.discord_resume_gateway_url = "wss://existing"
    adapter._bot_user_id = "existing-bot"

    await adapter._handle_ready_event(
        data={
            "session_id": 123,
            "resume_gateway_url": "",
            "user": "not-a-mapping",
        },
        cursor=cursor,
    )
    await adapter._handle_ready_event(
        data={
            "session_id": None,
            "resume_gateway_url": None,
            "user": {"id": None},
        },
        cursor=cursor,
    )

    assert cursor.discord_session_id == "existing-session"
    assert cursor.discord_resume_gateway_url == "wss://existing"
    assert adapter._bot_user_id == "existing-bot"
    assert len(repository.saved) == 2


@pytest.mark.asyncio
async def test_handle_dispatch_event_dispatches_and_tracks_message() -> None:
    repository = RecordingDiscordRepository()
    subscription = _build_subscription({"allowed_guild_ids": ["guild-1"]})
    adapter = DiscordGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
    )
    cursor = ListenerCursor(subscription_id=subscription.id)

    result = await adapter._handle_dispatch_event(
        event_type="MESSAGE_CREATE",
        data={
            "id": "message-1",
            "channel_id": "chat-1",
            "guild_id": "guild-1",
            "type": 0,
            "content": "hello",
            "author": {"id": "user-1"},
            "mentions": [],
        },
        cursor=cursor,
    )

    assert result is False
    assert repository.dispatched
    assert repository.saved
    assert adapter._last_event_at is not None


@pytest.mark.asyncio
async def test_handle_dispatch_event_skips_invalid_message() -> None:
    repository = RecordingDiscordRepository()
    subscription = _build_subscription()
    adapter = DiscordGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
    )
    cursor = ListenerCursor(subscription_id=subscription.id)

    result = await adapter._handle_dispatch_event(
        event_type="MESSAGE_CREATE",
        data={"id": "message-1", "type": 0},
        cursor=cursor,
    )

    assert result is False
    assert repository.dispatched == []
    assert repository.saved


def test_normalize_discord_event_rejects_invalid_inputs() -> None:
    subscription = _build_subscription()

    assert (
        normalize_discord_event(
            subscription,
            "OTHER_EVENT",
            {"id": "message-1", "channel_id": "channel-1"},
        )
        is None
    )

    assert (
        normalize_discord_event(
            subscription,
            "MESSAGE_CREATE",
            {"id": "message-1", "type": 0},
        )
        is None
    )


def test_normalize_discord_event_handles_non_mapping_author() -> None:
    subscription = _build_subscription()
    payload = normalize_discord_event(
        subscription,
        "MESSAGE_CREATE",
        {
            "id": "message-1",
            "channel_id": "channel-1",
            "guild_id": "guild-1",
            "type": 0,
            "content": "text",
            "author": "bot",
            "mentions": [],
        },
    )

    assert payload is not None
    assert payload.message.user_id is None


def test_normalize_discord_event_handles_non_string_content() -> None:
    subscription = _build_subscription({"intents": ["message_content"]})
    payload = normalize_discord_event(
        subscription,
        "MESSAGE_CREATE",
        {
            "id": "message-1",
            "channel_id": "channel-1",
            "guild_id": "guild-1",
            "type": 0,
            "content": {"text": "hello"},
            "author": {"id": "user-1", "username": "Alice"},
            "mentions": [],
        },
    )

    assert payload is not None
    assert payload.message.text is None
    assert payload.metadata["content_available"] is True


@pytest.mark.asyncio
async def test_discord_gateway_adapter_run_recovers_after_backoff_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = RecordingDiscordRepository()
    subscription = _build_subscription(
        {
            "token": "[[discord_bot_token]]",
            "backoff_min_seconds": 0.001,
            "backoff_max_seconds": 0.001,
        }
    )
    adapter = DiscordGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
    )
    stop_event = asyncio.Event()
    call_tokens: list[str] = []

    async def fake_run_session_once(
        self: DiscordGatewayAdapter,
        *,
        token: str,
        stop_event: asyncio.Event,
    ) -> None:
        del self
        call_tokens.append(token)
        if len(call_tokens) == 1:
            raise RuntimeError("boom")
        stop_event.set()

    monkeypatch.setattr(
        DiscordGatewayAdapter, "run_session_once", fake_run_session_once
    )
    await adapter.run(stop_event)

    assert call_tokens == ["[[discord_bot_token]]", "[[discord_bot_token]]"]
    assert adapter.health().consecutive_failures == 1
    assert adapter.health().detail is None
    assert adapter.health().status == "stopped"


@pytest.mark.asyncio
async def test_run_session_once_recovers_from_receive_timeout(monkeypatch) -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_discord_subscription(repository=repository)
    client = FakeDiscordGatewayClient(
        DiscordGatewayInfo(
            url="wss://gateway.discord.gg",
            session_start_limit=DiscordGatewaySessionStartLimit(
                remaining=1,
                reset_after=1000,
            ),
        )
    )
    websocket = FakeDiscordWebSocket(
        [
            {"op": 10, "d": {"heartbeat_interval": 100}},
            {
                "op": 0,
                "t": "MESSAGE_CREATE",
                "s": 2,
                "d": {
                    "id": "message-1",
                    "channel_id": "channel-1",
                    "guild_id": "guild-1",
                    "type": 0,
                    "content": "hello",
                    "author": {"id": "user-1"},
                    "mentions": [],
                },
            },
            {"op": 7, "d": None},
        ]
    )
    connector = FakeDiscordConnector(websocket)
    adapter = DiscordGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
        gateway_client=client,
        gateway_connector=connector,
    )

    fired = False
    real_wait_for = asyncio.wait_for

    async def fake_wait_for(awaitable, *, timeout, **kwargs):
        nonlocal fired
        if (
            not fired
            and hasattr(awaitable, "cr_code")
            and (awaitable.cr_code.co_name == "recv")
        ):
            fired = True
            awaitable.close()
            raise TimeoutError
        return await real_wait_for(awaitable, timeout=timeout, **kwargs)

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    await adapter.run_session_once(
        token="[[discord_bot_token]]",
        stop_event=asyncio.Event(),
    )

    assert fired


@pytest.mark.asyncio
async def test_default_gateway_http_client_returns_gateway_info(monkeypatch) -> None:
    payload = {
        "url": "wss://gateway.discord.gg",
        "session_start_limit": {"remaining": 2, "reset_after": 1000},
    }

    class FakeResponse:
        def __init__(self, body: object) -> None:
            self._body = body

        def raise_for_status(self) -> None:
            pass

        def json(self) -> object:
            return self._body

    class FakeAsyncClient:
        def __init__(
            self, *, base_url: str, headers: dict[str, str], timeout: float
        ) -> None:
            assert base_url == DefaultDiscordGatewayHttpClient.BASE_URL
            assert headers["Authorization"] == "Bot token"
            assert timeout == 10.0

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, path: str) -> FakeResponse:
            assert path == "/gateway/bot"
            return FakeResponse(payload)

    monkeypatch.setattr("orcheo.listeners.discord.httpx.AsyncClient", FakeAsyncClient)
    client = DefaultDiscordGatewayHttpClient()
    info = await client.get_gateway_bot(token="token")

    assert info.url.endswith("v=10&encoding=json")
    assert info.session_start_limit is not None
    assert info.session_start_limit.remaining == 2


@pytest.mark.asyncio
async def test_default_gateway_http_client_rejects_invalid_payload(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, body: object) -> None:
            self._body = body

        def raise_for_status(self) -> None:
            pass

        def json(self) -> object:
            return self._body

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, path: str) -> FakeResponse:
            return FakeResponse(["not", "a", "dict"])

    monkeypatch.setattr("orcheo.listeners.discord.httpx.AsyncClient", FakeAsyncClient)
    client = DefaultDiscordGatewayHttpClient()

    with pytest.raises(ValueError, match="invalid payload"):
        await client.get_gateway_bot(token="token")


@pytest.mark.asyncio
async def test_default_gateway_http_client_rejects_missing_url(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> object:
            return {"url": "", "session_start_limit": {}}

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, path: str) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr("orcheo.listeners.discord.httpx.AsyncClient", FakeAsyncClient)
    client = DefaultDiscordGatewayHttpClient()

    with pytest.raises(ValueError, match="did not include a URL"):
        await client.get_gateway_bot(token="token")


def test_default_gateway_connector_delegates_to_websockets(monkeypatch) -> None:
    sentinel = object()

    def fake_connect(url: str, open_timeout: float) -> object:
        assert url == "wss://gateway"
        assert open_timeout == 10.0
        return sentinel

    monkeypatch.setattr("orcheo.listeners.discord.websockets.connect", fake_connect)
    connector = DefaultDiscordGatewayConnector()
    assert connector.connect("wss://gateway") is sentinel


def test_decode_gateway_payload_handles_bytes_and_rejects_objects() -> None:
    payload = _decode_gateway_payload(json.dumps({"op": 1}).encode("utf-8"))
    assert payload == {"op": 1}

    with pytest.raises(ValueError, match="not a JSON object"):
        _decode_gateway_payload(json.dumps([1, 2, 3]))


def test_extract_heartbeat_interval_valid() -> None:
    assert (
        _extract_heartbeat_interval({"op": 10, "d": {"heartbeat_interval": 2000}})
        == 2.0
    )


def test_extract_heartbeat_interval_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="did not start with HELLO"):
        _extract_heartbeat_interval({"op": 1, "d": {"heartbeat_interval": 100}})

    with pytest.raises(ValueError, match="did not include a heartbeat interval"):
        _extract_heartbeat_interval({"op": 10, "d": None})

    with pytest.raises(ValueError, match="was missing or invalid"):
        _extract_heartbeat_interval({"op": 10, "d": {"heartbeat_interval": "bad"}})


def test_discord_message_type_name_variants() -> None:
    assert _discord_message_type_name(" default ") == "DEFAULT"
    assert _discord_message_type_name(19) == "REPLY"
    assert _discord_message_type_name(99) == "99"
    assert _discord_message_type_name(None) is None


def test_has_message_content_intent_is_case_insensitive() -> None:
    subscription = _build_subscription({"intents": ["Message_Content"]})
    assert _has_message_content_intent(subscription)


def test_mentions_bot_checks_non_lists_and_matches() -> None:
    assert not _mentions_bot([], bot_user_id=None)
    assert not _mentions_bot("invalid", bot_user_id="bot")
    assert not _mentions_bot([{"id": 1}], bot_user_id="bot")
    assert _mentions_bot(["invalid-mention", {"id": "bot"}], bot_user_id="bot")
    assert _mentions_bot([{"id": "bot"}], bot_user_id="bot")


def test_is_self_authored_message_checks_bot_id() -> None:
    author = {"id": "bot"}
    assert not _is_self_authored_message(author, bot_user_id=None)
    assert _is_self_authored_message(author, bot_user_id="bot")
    assert not _is_self_authored_message(author, bot_user_id="other")


def test_string_or_none_and_with_gateway_params() -> None:
    assert _string_or_none(None) is None
    assert _string_or_none(123) == "123"
    assert _with_gateway_params("wss://gateway") == "wss://gateway?v=10&encoding=json"
    assert (
        _with_gateway_params("wss://gateway?v=9") == "wss://gateway?v=9&encoding=json"
    )


def test_discord_message_passes_filters_handles_allowed_values() -> None:
    base_event = {
        "message_type_name": "DEFAULT",
        "channel_id": "channel-1",
        "guild_id": "guild-1",
        "mentions": [],
    }

    subscription = _build_subscription({"include_direct_messages": False})
    assert not _discord_message_passes_filters(
        subscription=subscription,
        guild_id=None,
        channel_id=base_event["channel_id"],
        message_type_name=base_event["message_type_name"],
        mentions=base_event["mentions"],
        bot_user_id=None,
    )

    subscription = _build_subscription({"allowed_guild_ids": ["guild-1"]})
    assert not _discord_message_passes_filters(
        subscription=subscription,
        guild_id="other",
        channel_id=base_event["channel_id"],
        message_type_name=base_event["message_type_name"],
        mentions=base_event["mentions"],
        bot_user_id=None,
    )

    subscription = _build_subscription({"allowed_channel_ids": ["channel-1"]})
    assert not _discord_message_passes_filters(
        subscription=subscription,
        guild_id=base_event["guild_id"],
        channel_id="other",
        message_type_name=base_event["message_type_name"],
        mentions=base_event["mentions"],
        bot_user_id=None,
    )

    subscription = _build_subscription({"allowed_message_types": ["REPLY"]})
    assert not _discord_message_passes_filters(
        subscription=subscription,
        guild_id=base_event["guild_id"],
        channel_id=base_event["channel_id"],
        message_type_name="DEFAULT",
        mentions=base_event["mentions"],
        bot_user_id=None,
    )

    subscription = _build_subscription({"require_bot_mention": True})
    assert not _discord_message_passes_filters(
        subscription=subscription,
        guild_id=base_event["guild_id"],
        channel_id=base_event["channel_id"],
        message_type_name=base_event["message_type_name"],
        mentions=base_event["mentions"],
        bot_user_id="bot",
    )
    assert _discord_message_passes_filters(
        subscription=subscription,
        guild_id=base_event["guild_id"],
        channel_id=base_event["channel_id"],
        message_type_name=base_event["message_type_name"],
        mentions=[{"id": "bot"}],
        bot_user_id="bot",
    )
