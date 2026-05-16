import asyncio
import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4
import pytest
import orcheo.listeners.qq as qq_module
from orcheo.listeners import (
    ListenerCursor,
    ListenerDispatchPayload,
    ListenerHealthSnapshot,
    ListenerPlatform,
    ListenerSubscription,
)
from orcheo.listeners.qq import (
    DefaultQQAccessTokenHttpClient,
    DefaultQQAccessTokenProvider,
    DefaultQQGatewayConnector,
    DefaultQQGatewayHttpClient,
    QQGatewayAdapter,
    QQGatewayInfo,
    QQGatewaySessionStartLimit,
    _build_qq_dispatch_payload,
    _decode_gateway_payload,
    _extract_heartbeat_interval,
    _qq_scene_type,
    _string_or_none,
    normalize_qq_event,
    qq_intents_bitmask,
)


class DummyJsonResponse:
    def __init__(self, body: Any) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._body


class DummyAsyncClient:
    def __init__(self, *args: Any, response: Any = None, **kwargs: Any) -> None:
        self.response = response
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self.get_calls: list[str] = []
        self.kwargs = kwargs

    async def __aenter__(self) -> "DummyAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, json: dict[str, Any]) -> DummyJsonResponse:
        self.post_calls.append((url, json))
        return DummyJsonResponse(self.response)

    async def get(self, url: str) -> DummyJsonResponse:
        self.get_calls.append(url)
        return DummyJsonResponse(self.response)


class RecordingRepository:
    def __init__(self) -> None:
        self.saved: list[ListenerCursor] = []
        self.dispatched: list[ListenerDispatchPayload] = []
        self.cursor: ListenerCursor | None = None

    async def get_listener_cursor(self, subscription_id: UUID) -> ListenerCursor | None:
        return self.cursor

    async def save_listener_cursor(self, cursor: ListenerCursor) -> ListenerCursor:
        self.saved.append(cursor)
        self.cursor = cursor
        return cursor

    async def dispatch_listener_event(
        self,
        subscription_id: UUID,
        payload: ListenerDispatchPayload,
    ) -> object | None:
        self.dispatched.append(payload)
        return payload


class DummyWebSocket:
    def __init__(self, to_recv: list[str] | None = None) -> None:
        self.sent: list[str] = []
        self._recv_queue: asyncio.Queue[str] = asyncio.Queue()
        if to_recv:
            for frame in to_recv:
                self._recv_queue.put_nowait(frame)
        self.closed_with: int | None = None

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        return await self._recv_queue.get()

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


def subscription_factory(**config: Any) -> ListenerSubscription:
    return ListenerSubscription(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        node_name="qq_listener",
        platform=ListenerPlatform.QQ,
        bot_identity_key="bot-key",
        config=config,
    )


def make_adapter(
    repository: RecordingRepository,
    *,
    token_provider: Any = None,
    gateway_client: Any = None,
    gateway_connector: Any = None,
    **config: Any,
) -> QQGatewayAdapter:
    subscription = subscription_factory(**config)
    return QQGatewayAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime",
        token_provider=token_provider,
        gateway_client=gateway_client,
        gateway_connector=gateway_connector,
    )


def test_string_or_none_and_scene_type() -> None:
    assert _string_or_none(None) is None
    assert _string_or_none(123) == "123"
    assert _qq_scene_type("C2C_MESSAGE_CREATE") == "c2c"
    assert _qq_scene_type("GROUP_AT_MESSAGE_CREATE") == "group"
    assert _qq_scene_type("AT_MESSAGE_CREATE") == "channel"
    assert _qq_scene_type("unknown") is None


def test_qq_intents_bitmask() -> None:
    bitmask = qq_intents_bitmask(["message_create", " C2C_MESSAGE_CREATE ", "unknown"])
    assert bitmask & (1 << 9)
    assert bitmask & (1 << 25)


def test_decode_gateway_payload_accepts_bytes_and_rejects_non_dict() -> None:
    payload = {"op": 0}
    assert _decode_gateway_payload(json.dumps(payload)) == payload
    assert _decode_gateway_payload(json.dumps(payload).encode("utf-8")) == payload
    with pytest.raises(ValueError, match="JSON object"):
        _decode_gateway_payload("[]")


def test_extract_heartbeat_interval_variants() -> None:
    payload = {"op": 10, "d": {"heartbeat_interval": 1500}}
    assert _extract_heartbeat_interval(payload) == pytest.approx(1.5)
    with pytest.raises(ValueError, match="did not start with HELLO"):
        _extract_heartbeat_interval({})
    with pytest.raises(ValueError, match="heartbeat interval"):
        _extract_heartbeat_interval({"op": 10, "d": []})
    with pytest.raises(ValueError, match="invalid"):
        _extract_heartbeat_interval({"op": 10, "d": {"heartbeat_interval": "fast"}})


def test_build_qq_dispatch_payload_branches() -> None:
    subscription = subscription_factory()
    event = {"id": "msg", "author": {"user_openid": "user"}}
    payload = _build_qq_dispatch_payload(
        subscription=subscription,
        event_type="C2C_MESSAGE_CREATE",
        event=event,
        scene_type="c2c",
        message_id="msg",
        author={"user_openid": "user"},
        content="hello",
    )
    assert payload is not None
    assert payload.message.chat_type == "c2c"
    assert payload.reply_target["openid"] == "user"
    assert payload.metadata["scene_type"] == "c2c"

    assert (
        _build_qq_dispatch_payload(
            subscription=subscription,
            event_type="C2C_MESSAGE_CREATE",
            event=event,
            scene_type="c2c",
            message_id="msg",
            author={},
            content=None,
        )
        is None
    )

    group_event = {"id": "g", "group_openid": "group"}
    payload = _build_qq_dispatch_payload(
        subscription=subscription,
        event_type="GROUP_AT_MESSAGE_CREATE",
        event=group_event,
        scene_type="group",
        message_id="g",
        author={"member_openid": "member"},
        content="hi",
    )
    assert payload is not None
    assert payload.message.chat_type == "group"
    assert payload.reply_target["group_openid"] == "group"

    assert (
        _build_qq_dispatch_payload(
            subscription=subscription,
            event_type="GROUP_AT_MESSAGE_CREATE",
            event={"id": "g"},
            scene_type="group",
            message_id="g",
            author={},
            content=None,
        )
        is None
    )

    channel_event = {
        "id": "c",
        "channel_id": "ch",
        "guild_id": "guild",
    }
    payload = _build_qq_dispatch_payload(
        subscription=subscription,
        event_type="MESSAGE_CREATE",
        event=channel_event,
        scene_type="channel",
        message_id="c",
        author={"id": "u", "username": "bot"},
        content="text",
    )
    assert payload is not None
    assert payload.reply_target["channel_id"] == "ch"
    assert payload.message.username == "bot"

    assert (
        _build_qq_dispatch_payload(
            subscription=subscription,
            event_type="MESSAGE_CREATE",
            event={"id": "c"},
            scene_type="channel",
            message_id="c",
            author={},
            content=None,
        )
        is None
    )


def test_normalize_qq_event_filters() -> None:
    subscription = subscription_factory()
    event = {
        "id": "123",
        "channel_id": "chan",
        "guild_id": "guild",
        "author": {"id": 2, "username": "tester"},
        "content": "hello",
    }
    payload = normalize_qq_event(subscription, "message_create", event)
    assert isinstance(payload, ListenerDispatchPayload)
    assert payload.event_type == "MESSAGE_CREATE"
    assert payload.bot_identity == subscription.bot_identity_key
    assert payload.metadata["scene_type"] == "channel"
    assert payload.reply_target["channel_id"] == "chan"
    assert payload.reply_target["msg_id"] == "123"
    assert payload.reply_target["guild_id"] == "guild"
    assert payload.message.text == "hello"
    assert payload.message.chat_id == "chan"
    assert payload.message.chat_type == "channel"
    assert payload.message.username == "tester"
    assert payload.metadata["node_name"] == subscription.node_name
    assert payload.dedupe_key.startswith("qq:message:MESSAGE_CREATE:123")

    blocked = subscription_factory(allowed_events=["C2C_MESSAGE_CREATE"])
    assert normalize_qq_event(blocked, "message_create", event) is None
    assert (
        normalize_qq_event(
            blocked, "C2C_MESSAGE_CREATE", {"id": "1", "author": {"user_openid": "u"}}
        )
        is not None
    )

    scene_blocked = subscription_factory(allowed_scene_types=["c2c"])
    assert normalize_qq_event(scene_blocked, "message_create", event) is None
    assert normalize_qq_event(subscription, "unknown", event) is None

    event_missing_id = {"author": {"user_openid": "u"}}
    assert (
        normalize_qq_event(subscription, "c2c_message_create", event_missing_id) is None
    )
    payload_with_non_mapping_author = normalize_qq_event(
        subscription,
        "message_create",
        {
            "id": "234",
            "channel_id": "chan",
            "guild_id": "guild",
            "author": "tester",
            "content": "hello",
        },
    )
    assert payload_with_non_mapping_author is not None
    assert payload_with_non_mapping_author.message.user_id is None


@pytest.mark.asyncio
async def test_default_access_token_http_client_parses_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {"access_token": "token", "expires_in": "3600"}
    client_instances: list[DummyAsyncClient] = []

    def factory(*args: Any, **kwargs: Any) -> DummyAsyncClient:
        instance = DummyAsyncClient(response=response, **kwargs)
        client_instances.append(instance)
        return instance

    monkeypatch.setattr(qq_module.httpx, "AsyncClient", factory)
    client = DefaultQQAccessTokenHttpClient()
    result = await client.fetch_access_token(app_id="app", client_secret="secret")
    assert result.access_token == "token"
    assert result.expires_in == 3600
    assert client_instances[0].post_calls[0][1] == {
        "appId": "app",
        "clientSecret": "secret",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,match",
    [
        ([], "invalid payload"),
        ({"access_token": "token"}, "expires_in"),
        ({"expires_in": 12}, "access_token"),
    ],
)
async def test_default_access_token_http_client_rejects_bad_payloads(
    monkeypatch: pytest.MonkeyPatch,
    body: Any,
    match: str,
) -> None:
    def factory(*args: Any, **kwargs: Any) -> DummyAsyncClient:
        return DummyAsyncClient(response=body, **kwargs)

    monkeypatch.setattr(qq_module.httpx, "AsyncClient", factory)
    client = DefaultQQAccessTokenHttpClient()
    with pytest.raises(ValueError, match=match):
        await client.fetch_access_token(app_id="app", client_secret="secret")


@pytest.mark.asyncio
async def test_default_access_token_provider_caches_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_payload = qq_module.QQAccessTokenPayload(
        access_token="cached", expires_in=3600
    )

    class StubClient:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_access_token(
            self, **kwargs: Any
        ) -> qq_module.QQAccessTokenPayload:
            self.calls += 1
            return token_payload

    stub = StubClient()
    provider = DefaultQQAccessTokenProvider(http_client=stub, refresh_overlap_seconds=0)
    DefaultQQAccessTokenProvider._cache.clear()
    DefaultQQAccessTokenProvider._locks.clear()
    fixed_now = datetime(2025, 1, 1)
    monkeypatch.setattr(qq_module, "_utcnow", lambda: fixed_now)

    first = await provider.get_access_token(app_id="app", client_secret="secret")
    second = await provider.get_access_token(app_id="app", client_secret="secret")
    assert first == second == "cached"
    assert stub.calls == 1


@pytest.mark.asyncio
async def test_default_access_token_provider_reuses_token_after_lock_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowClient:
        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()

        async def fetch_access_token(
            self, **kwargs: Any
        ) -> qq_module.QQAccessTokenPayload:
            del kwargs
            self.calls += 1
            self.started.set()
            await asyncio.sleep(0.01)
            return qq_module.QQAccessTokenPayload(
                access_token="shared-token",
                expires_in=3600,
            )

    stub = SlowClient()
    provider = DefaultQQAccessTokenProvider(http_client=stub, refresh_overlap_seconds=0)
    DefaultQQAccessTokenProvider._cache.clear()
    DefaultQQAccessTokenProvider._locks.clear()
    fixed_now = datetime(2025, 1, 1)
    monkeypatch.setattr(qq_module, "_utcnow", lambda: fixed_now)

    first_task = asyncio.create_task(
        provider.get_access_token(app_id="app", client_secret="secret")
    )
    await stub.started.wait()
    second_task = asyncio.create_task(
        provider.get_access_token(app_id="app", client_secret="secret")
    )
    first, second = await asyncio.gather(first_task, second_task)

    assert first == second == "shared-token"
    assert stub.calls == 1


@pytest.mark.asyncio
async def test_default_gateway_http_client_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = {"remaining": 5, "reset_after": 1000}
    response = {
        "url": "wss://gateway",
        "session_start_limit": limit,
        "shards": 2,
    }

    class TokenProvider:
        async def get_access_token(self, **kwargs: Any) -> str:
            return "token"

    token_provider = TokenProvider()
    clients: list[DummyAsyncClient] = []

    def factory(*args: Any, **kwargs: Any) -> DummyAsyncClient:
        instance = DummyAsyncClient(response=response, **kwargs)
        clients.append(instance)
        return instance

    monkeypatch.setattr(qq_module.httpx, "AsyncClient", factory)
    client = DefaultQQGatewayHttpClient(token_provider=token_provider)
    info = await client.get_gateway_bot(
        app_id="app", client_secret="secret", sandbox=True
    )
    assert info.url == "wss://gateway"
    assert info.session_start_limit is not None
    assert info.session_start_limit.remaining == 5
    assert clients[0].kwargs["base_url"] == "https://sandbox.api.sgroup.qq.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,match",
    [
        ([], "invalid payload"),
        ({"url": ""}, "did not include a URL"),
    ],
)
async def test_default_gateway_http_client_handles_errors(
    monkeypatch: pytest.MonkeyPatch,
    body: Any,
    match: str,
) -> None:
    class TokenProvider:
        async def get_access_token(self, **kwargs: Any) -> str:
            return "token"

    token_provider = TokenProvider()

    def factory(*args: Any, **kwargs: Any) -> DummyAsyncClient:
        return DummyAsyncClient(response=body, **kwargs)

    monkeypatch.setattr(qq_module.httpx, "AsyncClient", factory)
    client = DefaultQQGatewayHttpClient(token_provider=token_provider)
    with pytest.raises(ValueError, match=match):
        await client.get_gateway_bot(app_id="app", client_secret="secret")


@pytest.mark.asyncio
async def test_default_gateway_connector_wraps_websockets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered: list[str] = []

    class FakeConnector:
        def __init__(self, url: str, **kwargs: Any) -> None:
            self.url = url
            self.kwargs = kwargs

        async def __aenter__(self) -> "FakeConnector":
            entered.append(self.url)
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr(
        qq_module.websockets,
        "connect",
        lambda url, **kwargs: FakeConnector(url, **kwargs),
    )
    connector = DefaultQQGatewayConnector()
    async with connector.connect("wss://example") as context:
        assert context.url == "wss://example"
    assert entered == ["wss://example"]


@pytest.mark.asyncio
async def test_send_start_session_switches_between_resume_and_identify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = RecordingRepository()
    adapter = make_adapter(repository)
    payloads: list[dict[str, Any]] = []

    async def recorder(
        self: QQGatewayAdapter, websocket: Any, payload: dict[str, Any]
    ) -> None:
        payloads.append(payload)

    monkeypatch.setattr(QQGatewayAdapter, "_send_gateway_payload", recorder)
    cursor = ListenerCursor(subscription_id=adapter.subscription.id)
    cursor.qq_session_id = "session"
    cursor.qq_sequence = 42
    await adapter._send_start_session(websocket=None, token="token", cursor=cursor)
    assert payloads[0]["op"] == 6

    payloads.clear()
    cursor.qq_session_id = None
    cursor.qq_sequence = None
    await adapter._send_start_session(websocket=None, token="token", cursor=cursor)
    assert payloads[0]["op"] == 2
    assert payloads[0]["d"]["properties"]["$device"] == "orcheo"
    assert payloads[0]["d"]["intents"] >= 0


@pytest.mark.asyncio
async def test_handle_non_dispatch_op_opcodes(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = RecordingRepository()
    adapter = make_adapter(repository)
    payloads: list[dict[str, Any]] = []

    async def recorder(
        self: QQGatewayAdapter, websocket: Any, payload: dict[str, Any]
    ) -> None:
        payloads.append(payload)

    monkeypatch.setattr(QQGatewayAdapter, "_send_gateway_payload", recorder)
    cursor = ListenerCursor(subscription_id=adapter.subscription.id)
    assert not await adapter._handle_non_dispatch_op(
        websocket=None, cursor=cursor, gateway_url="url", op=11
    )
    assert payloads == []

    assert not await adapter._handle_non_dispatch_op(
        websocket=None, cursor=cursor, gateway_url="url", op=1
    )
    assert payloads and payloads[-1]["op"] == 1

    payloads.clear()
    cursor.qq_sequence = 5
    assert await adapter._handle_non_dispatch_op(
        websocket=None, cursor=cursor, gateway_url="url", op=7
    )
    assert adapter._detail == "gateway_requested_reconnect"
    assert repository.saved[-1].qq_resume_gateway_url == "url"

    assert not await adapter._handle_non_dispatch_op(
        websocket=None, cursor=cursor, gateway_url="url", op=5
    )

    cursor.qq_session_id = "sess"
    cursor.qq_sequence = 10
    adapter._bot_user_id = "bot"
    assert await adapter._handle_non_dispatch_op(
        websocket=None, cursor=cursor, gateway_url="url", op=9
    )
    assert cursor.qq_sequence is None
    assert adapter._bot_user_id is None


@pytest.mark.asyncio
async def test_heartbeat_loop_sends_pings(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = RecordingRepository()
    adapter = make_adapter(repository)
    payloads: list[dict[str, Any]] = []
    stop_event = asyncio.Event()
    wait_for_calls = 0

    async def recorder(
        self: QQGatewayAdapter, websocket: Any, payload: dict[str, Any]
    ) -> None:
        payloads.append(payload)
        stop_event.set()

    async def fake_wait_for(
        awaitable: Any, timeout: float
    ) -> None:  # pragma: no cover - exercised via heartbeat loop
        nonlocal wait_for_calls
        wait_for_calls += 1
        if wait_for_calls == 1:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise TimeoutError
        await awaitable

    adapter._sequence = 123
    monkeypatch.setattr(QQGatewayAdapter, "_send_gateway_payload", recorder)
    monkeypatch.setattr(qq_module.asyncio, "wait_for", fake_wait_for)
    task = asyncio.create_task(
        adapter._heartbeat_loop(
            websocket=None, stop_event=stop_event, interval_seconds=0.001
        )
    )
    await task
    assert any(payload.get("op") == 1 for payload in payloads)


@pytest.mark.asyncio
async def test_receive_and_send_gateway_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = RecordingRepository()
    adapter = make_adapter(repository)
    websocket = DummyWebSocket([json.dumps({"op": 0})])
    received = await adapter._receive_gateway_payload(websocket)
    assert received["op"] == 0
    await adapter._send_gateway_payload(websocket, {"op": 99})
    assert websocket.sent


@pytest.mark.asyncio
async def test_handle_ready_and_dispatch_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = RecordingRepository()
    adapter = make_adapter(repository)
    cursor = ListenerCursor(subscription_id=adapter.subscription.id)
    await adapter._handle_ready_event(
        data={"session_id": "sess", "user": {"id": "bot"}},
        cursor=cursor,
        gateway_url="url",
    )
    assert cursor.qq_session_id == "sess"
    assert adapter._bot_user_id == "bot"
    assert repository.saved[-1].qq_resume_gateway_url == "url"

    adapter._bot_user_id = "bot"
    cursor.qq_session_id = "sess"
    await adapter._handle_ready_event(
        data={"session_id": 1, "user": "not-a-mapping"},
        cursor=cursor,
        gateway_url="url",
    )
    await adapter._handle_ready_event(
        data={"session_id": None, "user": {"id": None}},
        cursor=cursor,
        gateway_url="url",
    )
    assert cursor.qq_session_id == "sess"
    assert adapter._bot_user_id == "bot"

    repository.saved.clear()
    cursor.qq_session_id = "sess"
    await adapter._handle_dispatch_event(
        event_type="RESUMED",
        data={},
        cursor=cursor,
        gateway_url="url",
    )
    assert repository.saved

    repository.saved.clear()
    await adapter._handle_dispatch_event(
        event_type="MESSAGE_CREATE",
        data={
            "id": "1",
            "channel_id": "chan",
            "author": {"id": 1, "username": "name"},
        },
        cursor=cursor,
        gateway_url="url",
    )
    assert repository.dispatched
    assert repository.saved

    repository.saved.clear()
    before_dispatch_count = len(repository.dispatched)
    await adapter._handle_dispatch_event(
        event_type="UNKNOWN_EVENT",
        data={"id": "1", "author": {"id": 1}},
        cursor=cursor,
        gateway_url="url",
    )
    assert len(repository.dispatched) == before_dispatch_count
    assert repository.saved


@pytest.mark.asyncio
async def test_handle_gateway_frame_dispatch_and_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = RecordingRepository()
    adapter = make_adapter(repository)
    cursor = ListenerCursor(subscription_id=adapter.subscription.id)
    frame = json.dumps(
        {
            "op": 0,
            "t": "MESSAGE_CREATE",
            "s": 7,
            "d": {
                "id": "m",
                "channel_id": "chan",
                "author": {"id": 1, "username": "user"},
            },
        }
    )
    assert not await adapter._handle_gateway_frame(
        websocket=None,
        frame=frame,
        cursor=cursor,
        gateway_url="url",
    )
    assert adapter._sequence == 7
    assert cursor.qq_sequence == 7
    assert repository.dispatched

    assert await adapter._handle_gateway_frame(
        websocket=None,
        frame=json.dumps({"op": 7}),
        cursor=cursor,
        gateway_url="url",
    )
    assert repository.saved[-1].qq_resume_gateway_url == "url"

    assert await adapter._handle_gateway_frame(
        websocket=None,
        frame=json.dumps({"op": 9}),
        cursor=cursor,
        gateway_url="url",
    )
    assert cursor.qq_session_id is None


@pytest.mark.asyncio
async def test_handle_gateway_frame_filters_invalid_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = RecordingRepository()
    adapter = make_adapter(repository, allowed_scene_types=["c2c"])
    cursor = ListenerCursor(subscription_id=adapter.subscription.id)
    assert not await adapter._handle_gateway_frame(
        websocket=None,
        frame=json.dumps({"op": 0, "t": 1, "d": {}}),
        cursor=cursor,
        gateway_url="url",
    )
    assert not await adapter._handle_gateway_frame(
        websocket=None,
        frame=json.dumps({"op": 0, "t": "message_create", "d": "bad"}),
        cursor=cursor,
        gateway_url="url",
    )


@pytest.mark.asyncio
async def test_run_session_once_backoff_on_limit() -> None:
    repository = RecordingRepository()

    class StubGatewayClient:
        async def get_gateway_bot(self, **kwargs: Any) -> QQGatewayInfo:
            return QQGatewayInfo(
                url="wss",
                session_start_limit=QQGatewaySessionStartLimit(
                    remaining=0,
                    reset_after=10,
                ),
            )

    class GuardedTokenProvider:
        async def get_access_token(self, **kwargs: Any) -> str:
            raise AssertionError("Should not be called")

    adapter = make_adapter(
        repository,
        token_provider=GuardedTokenProvider(),
        gateway_client=StubGatewayClient(),
    )
    stop_event = asyncio.Event()
    await adapter.run_session_once(
        app_id="app",
        client_secret="secret",
        sandbox=False,
        stop_event=stop_event,
    )
    assert adapter._status == "backoff"


@pytest.mark.asyncio
async def test_run_session_once_handles_receive_timeout_and_closes() -> None:
    repository = RecordingRepository()

    class StubTokenProvider:
        async def get_access_token(self, **kwargs: Any) -> str:
            del kwargs
            return "token"

    class StubGatewayClient:
        async def get_gateway_bot(self, **kwargs: Any) -> QQGatewayInfo:
            del kwargs
            return QQGatewayInfo(
                url="wss://gateway.qq",
                session_start_limit=QQGatewaySessionStartLimit(
                    remaining=1,
                    reset_after=1000,
                ),
            )

    class StubConnector:
        def __init__(self, websocket: DummyWebSocket) -> None:
            self.websocket = websocket

        async def __aenter__(self) -> DummyWebSocket:
            return self.websocket

        async def __aexit__(self, *args: Any) -> None:
            return None

    class StubGatewayConnector:
        def __init__(self, websocket: DummyWebSocket) -> None:
            self.websocket = websocket

        def connect(self, url: str) -> StubConnector:
            del url
            return StubConnector(self.websocket)

    websocket = DummyWebSocket(
        [json.dumps({"op": 10, "d": {"heartbeat_interval": 100}})]
    )
    adapter = make_adapter(
        repository,
        token_provider=StubTokenProvider(),
        gateway_client=StubGatewayClient(),
        gateway_connector=StubGatewayConnector(websocket),
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        adapter.run_session_once(
            app_id="app",
            client_secret="secret",
            sandbox=False,
            stop_event=stop_event,
        )
    )
    await asyncio.sleep(0.02)
    stop_event.set()
    await task

    assert websocket.sent
    assert websocket.closed_with == 1000


@pytest.mark.asyncio
async def test_adapter_run_clears_detail_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = RecordingRepository()
    adapter = make_adapter(
        repository,
        app_id="app",
        client_secret="secret",
        backoff_min_seconds=0.001,
        backoff_max_seconds=0.001,
    )
    adapter._detail = "stale"
    stop_event = asyncio.Event()

    async def fake_run_session_once(
        self: QQGatewayAdapter,
        *,
        app_id: str,
        client_secret: str,
        sandbox: bool,
        stop_event: asyncio.Event,
    ) -> None:
        del self, app_id, client_secret, sandbox
        stop_event.set()

    monkeypatch.setattr(QQGatewayAdapter, "run_session_once", fake_run_session_once)
    await adapter.run(stop_event)

    assert adapter._detail is None
    assert adapter._status == "stopped"


@pytest.mark.asyncio
async def test_health_reports_status_changes() -> None:
    repository = RecordingRepository()
    adapter = make_adapter(repository)
    state = adapter.health()
    assert isinstance(state, ListenerHealthSnapshot)
