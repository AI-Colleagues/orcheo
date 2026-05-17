"""Tests for QQ listener normalization, token caching, and Gateway handling."""

from __future__ import annotations
import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any
import pytest
from orcheo.listeners import (
    DefaultQQAccessTokenProvider,
    ListenerCursor,
    ListenerDispatchPayload,
    ListenerPlatform,
    ListenerSubscription,
    QQAccessTokenPayload,
    QQGatewayAdapter,
    QQGatewayInfo,
    QQGatewaySessionStartLimit,
    normalize_qq_event,
    qq_intents_bitmask,
)
from orcheo.models import WorkflowDraftAccess
from orcheo_backend.app.repository import InMemoryWorkflowRepository


class FakeQQAccessTokenHttpClient:
    """Simple stub for QQ access-token responses."""

    def __init__(self, payloads: list[QQAccessTokenPayload]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, str]] = []

    async def fetch_access_token(
        self,
        *,
        app_id: str,
        client_secret: str,
    ) -> QQAccessTokenPayload:
        self.calls.append((app_id, client_secret))
        return self.payloads.pop(0)


class FakeQQGatewayClient:
    """Simple stub for QQ ``GET /gateway/bot`` responses."""

    def __init__(self, info: QQGatewayInfo) -> None:
        self.info = info
        self.calls: list[tuple[str, str, bool]] = []

    async def get_gateway_bot(
        self,
        *,
        app_id: str,
        client_secret: str,
        sandbox: bool = False,
    ) -> QQGatewayInfo:
        self.calls.append((app_id, client_secret, sandbox))
        return self.info


class FakeQQWebSocket:
    """Predictable WebSocket stub for QQ gateway tests."""

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


class FakeQQConnector:
    """Connector returning a prebuilt fake WebSocket."""

    def __init__(self, websocket: FakeQQWebSocket) -> None:
        self.websocket = websocket
        self.calls: list[str] = []

    def connect(self, url: str) -> AbstractAsyncContextManager[FakeQQWebSocket]:
        self.calls.append(url)

        @asynccontextmanager
        async def _manager() -> AsyncIterator[FakeQQWebSocket]:
            yield self.websocket

        return _manager()


def _listener_graph(*listeners: dict[str, object]) -> dict[str, object]:
    return {"nodes": [], "edges": [], "index": {"listeners": list(listeners)}}


async def _create_qq_subscription(
    *,
    repository: InMemoryWorkflowRepository,
    app_id: str = "[[qq_app_id]]",
    client_secret: str = "[[qq_client_secret]]",
    sandbox: bool = False,
    allowed_events: list[str] | None = None,
    allowed_scene_types: list[str] | None = None,
) -> ListenerSubscription:
    workflow = await repository.create_workflow(
        name="QQ Flow",
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
                "node_name": "qq_listener",
                "platform": "qq",
                "app_id": app_id,
                "client_secret": client_secret,
                "sandbox": sandbox,
                "allowed_events": allowed_events
                or [
                    "C2C_MESSAGE_CREATE",
                    "GROUP_AT_MESSAGE_CREATE",
                    "AT_MESSAGE_CREATE",
                ],
                "allowed_scene_types": allowed_scene_types
                or ["c2c", "group", "channel"],
            }
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    return (await repository.list_listener_subscriptions(workflow_id=workflow.id))[0]


@pytest.mark.asyncio
async def test_qq_access_token_provider_caches_by_app_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QQ access tokens should be cached per AppID and refreshed near expiry."""

    DefaultQQAccessTokenProvider._cache.clear()
    DefaultQQAccessTokenProvider._locks.clear()
    client = FakeQQAccessTokenHttpClient(
        [
            QQAccessTokenPayload(access_token="token-a", expires_in=7200),
            QQAccessTokenPayload(access_token="token-b", expires_in=7200),
            QQAccessTokenPayload(access_token="token-c", expires_in=7200),
        ]
    )
    provider = DefaultQQAccessTokenProvider(http_client=client)
    current = datetime(2026, 3, 11, tzinfo=UTC)

    def fake_now() -> datetime:
        return current

    monkeypatch.setattr("orcheo.listeners.qq._utcnow", fake_now)

    first = await provider.get_access_token(app_id="app-1", client_secret="secret-1")
    second = await provider.get_access_token(app_id="app-1", client_secret="secret-1")
    other = await provider.get_access_token(app_id="app-2", client_secret="secret-2")

    assert first == "token-a"
    assert second == "token-a"
    assert other == "token-b"
    assert client.calls == [("app-1", "secret-1"), ("app-2", "secret-2")]

    current = datetime(2026, 3, 11, 1, 59, 30, tzinfo=UTC)
    refreshed = await provider.get_access_token(
        app_id="app-1",
        client_secret="secret-1",
    )

    assert refreshed == "token-c"
    assert client.calls[-1] == ("app-1", "secret-1")


@pytest.mark.asyncio
async def test_normalize_qq_event_filters_and_routes_message() -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_qq_subscription(
        repository=repository,
        allowed_scene_types=["c2c", "group"],
    )

    c2c_payload = normalize_qq_event(
        subscription,
        "C2C_MESSAGE_CREATE",
        {
            "id": "message-1",
            "author": {"user_openid": "user-openid"},
            "content": "hello qq",
        },
    )
    channel_payload = normalize_qq_event(
        subscription,
        "AT_MESSAGE_CREATE",
        {
            "id": "message-2",
            "channel_id": "channel-1",
            "guild_id": "guild-1",
            "author": {"id": "user-1", "username": "Alice"},
            "content": "hello channel",
        },
    )

    assert isinstance(c2c_payload, ListenerDispatchPayload)
    assert c2c_payload.message.chat_type == "c2c"
    assert c2c_payload.reply_target == {
        "openid": "user-openid",
        "msg_id": "message-1",
        "msg_seq": 1,
    }
    assert channel_payload is None


@pytest.mark.asyncio
async def test_qq_gateway_adapter_dispatches_and_saves_cursor() -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_qq_subscription(repository=repository)
    token_provider = DefaultQQAccessTokenProvider(
        http_client=FakeQQAccessTokenHttpClient(
            [QQAccessTokenPayload(access_token="qq-token", expires_in=7200)]
        )
    )
    client = FakeQQGatewayClient(
        QQGatewayInfo(
            url="wss://api.sgroup.qq.com/websocket",
            session_start_limit=QQGatewaySessionStartLimit(
                remaining=1,
                reset_after=1000,
            ),
        )
    )
    websocket = FakeQQWebSocket(
        [
            {"op": 10, "d": {"heartbeat_interval": 100}},
            {
                "op": 0,
                "t": "READY",
                "s": 1,
                "d": {
                    "session_id": "session-1",
                    "user": {"id": "bot-1", "username": "qq-bot"},
                },
            },
            {
                "op": 0,
                "t": "C2C_MESSAGE_CREATE",
                "s": 2,
                "d": {
                    "id": "message-1",
                    "author": {"user_openid": "user-openid"},
                    "content": "hello qq",
                },
            },
            {"op": 7, "d": None},
        ]
    )
    connector = FakeQQConnector(websocket)
    adapter = QQGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
        token_provider=token_provider,
        gateway_client=client,
        gateway_connector=connector,
    )

    await adapter.run_session_once(
        app_id="[[qq_app_id]]",
        client_secret="[[qq_client_secret]]",
        sandbox=False,
        stop_event=asyncio.Event(),
    )

    runs = await repository.list_runs_for_workflow(subscription.workflow_id)
    assert len(runs) == 1
    assert websocket.sent[0]["op"] == 2
    assert websocket.sent[0]["d"]["token"] == "QQBot qq-token"
    cursor = await repository.get_listener_cursor(subscription.id)
    assert cursor is not None
    assert cursor.qq_session_id == "session-1"
    assert cursor.qq_sequence == 2
    assert cursor.qq_resume_gateway_url == "wss://api.sgroup.qq.com/websocket"
    health = adapter.health()
    assert health.platform is ListenerPlatform.QQ
    assert health.status == "healthy"


@pytest.mark.asyncio
async def test_qq_gateway_adapter_resumes_existing_session() -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_qq_subscription(repository=repository)
    await repository.save_listener_cursor(
        ListenerCursor(
            subscription_id=subscription.id,
            qq_session_id="stored-session",
            qq_sequence=99,
            qq_resume_gateway_url="wss://resume.qq.example/websocket",
        )
    )
    token_provider = DefaultQQAccessTokenProvider(
        http_client=FakeQQAccessTokenHttpClient(
            [QQAccessTokenPayload(access_token="qq-token", expires_in=7200)]
        )
    )
    websocket = FakeQQWebSocket(
        [
            {"op": 10, "d": {"heartbeat_interval": 100}},
            {"op": 0, "t": "RESUMED", "s": 100, "d": ""},
            {"op": 7, "d": None},
        ]
    )
    adapter = QQGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
        token_provider=token_provider,
        gateway_client=FakeQQGatewayClient(
            QQGatewayInfo(
                url="wss://api.sgroup.qq.com/websocket",
                session_start_limit=QQGatewaySessionStartLimit(
                    remaining=1,
                    reset_after=1000,
                ),
            )
        ),
        gateway_connector=FakeQQConnector(websocket),
    )

    await adapter.run_session_once(
        app_id="[[qq_app_id]]",
        client_secret="[[qq_client_secret]]",
        sandbox=False,
        stop_event=asyncio.Event(),
    )

    assert websocket.sent[0] == {
        "op": 6,
        "d": {
            "token": "QQBot qq-token",
            "session_id": "stored-session",
            "seq": 99,
        },
    }
    cursor = await repository.get_listener_cursor(subscription.id)
    assert cursor is not None
    assert cursor.qq_sequence == 100


@pytest.mark.asyncio
async def test_qq_gateway_adapter_reports_whitelist_failure() -> None:
    repository = InMemoryWorkflowRepository()
    subscription = await _create_qq_subscription(repository=repository)

    class RejectingGatewayClient:
        async def get_gateway_bot(
            self,
            *,
            app_id: str,
            client_secret: str,
            sandbox: bool = False,
        ) -> QQGatewayInfo:
            del app_id, client_secret, sandbox
            raise ValueError("qq whitelist rejected the current egress IP")

    stop_event = asyncio.Event()
    adapter = QQGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
        gateway_client=RejectingGatewayClient(),
        token_provider=DefaultQQAccessTokenProvider(
            http_client=FakeQQAccessTokenHttpClient(
                [QQAccessTokenPayload(access_token="qq-token", expires_in=7200)]
            )
        ),
    )

    task = asyncio.create_task(adapter.run(stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await task

    health = adapter.health()
    assert health.status in {"backoff", "stopped"}
    assert health.consecutive_failures >= 1
    assert "whitelist" in (health.detail or "")


def test_qq_intents_bitmask_maps_message_events() -> None:
    assert qq_intents_bitmask(
        [
            "C2C_MESSAGE_CREATE",
            "GROUP_AT_MESSAGE_CREATE",
            "AT_MESSAGE_CREATE",
        ]
    ) == ((1 << 25) | (1 << 30))
