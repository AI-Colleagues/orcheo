from datetime import UTC, datetime
import pytest
import redis
from orcheo_backend.app.external_agent_runtime_store import (
    ExternalAgentRuntimeStore,
    _utcnow,
    is_terminal_login_state,
    list_active_login_sessions,
)
from orcheo_backend.app.schemas.system import (
    ExternalAgentLoginSession,
    ExternalAgentLoginSessionState,
    ExternalAgentProviderName,
    ExternalAgentProviderState,
    ExternalAgentProviderStatus,
)


@pytest.fixture
def runtime_store() -> ExternalAgentRuntimeStore:
    store = ExternalAgentRuntimeStore()
    store._redis = None
    return store


def test_list_provider_statuses_returns_defaults(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    statuses = runtime_store.list_provider_statuses()
    assert [status.provider for status in statuses] == [
        ExternalAgentProviderName.CLAUDE_CODE,
        ExternalAgentProviderName.CODEX,
        ExternalAgentProviderName.GEMINI,
    ]
    assert statuses[0].state == ExternalAgentProviderState.UNKNOWN


def test_provider_status_round_trip(runtime_store: ExternalAgentRuntimeStore) -> None:
    status = ExternalAgentProviderStatus(
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude",
        state=ExternalAgentProviderState.READY,
        installed=True,
        authenticated=True,
    )
    runtime_store.save_provider_status(status)
    loaded = runtime_store.get_provider_status(ExternalAgentProviderName.CLAUDE_CODE)
    assert loaded.display_name == "Claude"
    assert loaded.provider == ExternalAgentProviderName.CLAUDE_CODE
    assert runtime_store.list_provider_statuses()[0].provider == status.provider


def test_provider_environment_trims_empty(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    runtime_store.save_provider_environment(
        ExternalAgentProviderName.CLAUDE_CODE,
        {"CLAUDE_TOKEN": "token", "EMPTY": "   "},
    )
    env = runtime_store.get_provider_environment(ExternalAgentProviderName.CLAUDE_CODE)
    assert env == {"CLAUDE_TOKEN": "token"}


def test_login_session_round_trip(runtime_store: ExternalAgentRuntimeStore) -> None:
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="session",
        provider=ExternalAgentProviderName.CODEX,
        display_name="Codex",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
        detail="pending",
    )
    runtime_store.save_login_session(session)
    loaded = runtime_store.get_login_session("session")
    assert loaded is not None
    assert loaded.session_id == session.session_id


def test_clear_provider_session(runtime_store: ExternalAgentRuntimeStore) -> None:
    status = ExternalAgentProviderStatus(
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude",
        state=ExternalAgentProviderState.AUTHENTICATING,
        installed=True,
        authenticated=False,
        active_session_id="session",
    )
    runtime_store.save_provider_status(status)
    cleared = runtime_store.clear_provider_session(
        ExternalAgentProviderName.CLAUDE_CODE
    )
    assert cleared.active_session_id is None


def test_login_input_management(runtime_store: ExternalAgentRuntimeStore) -> None:
    runtime_store.save_login_input("session", "1234")
    assert runtime_store.get_login_input("session") == "1234"
    runtime_store.clear_login_input("session")
    assert runtime_store.get_login_input("session") is None


def test_list_active_login_sessions_filters_terminal() -> None:
    now = datetime.now(UTC)
    active = ExternalAgentLoginSession(
        session_id="1",
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    done = ExternalAgentLoginSession(
        session_id="2",
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude",
        state=ExternalAgentLoginSessionState.AUTHENTICATED,
        created_at=now,
        updated_at=now,
    )
    active_sessions = list_active_login_sessions([active, done])
    assert active_sessions == [active]


def test_is_terminal_login_state() -> None:
    assert is_terminal_login_state(ExternalAgentLoginSessionState.AUTHENTICATED)
    assert is_terminal_login_state(ExternalAgentLoginSessionState.FAILED)
    assert is_terminal_login_state(ExternalAgentLoginSessionState.TIMED_OUT)
    assert not is_terminal_login_state(ExternalAgentLoginSessionState.PENDING)


class DummyRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, **kwargs: object) -> None:
        self.store[key] = value

    def delete(self, key: str) -> None:
        self.store.pop(key, None)


class ErrorRedis:
    def __init__(
        self,
        *,
        get_error: Exception | None = None,
        set_error: Exception | None = None,
        delete_error: Exception | None = None,
        payload: str | None = None,
    ) -> None:
        self.get_error = get_error
        self.set_error = set_error
        self.delete_error = delete_error
        self.payload = payload

    def get(self, key: str) -> str | None:
        if self.get_error is not None:
            raise self.get_error
        return self.payload

    def set(self, key: str, value: str, **kwargs: object) -> None:
        if self.set_error is not None:
            raise self.set_error

    def delete(self, key: str) -> None:
        if self.delete_error is not None:
            raise self.delete_error


def test_utcnow_returns_utc_timestamp() -> None:
    assert _utcnow().tzinfo == UTC


def test_provider_environment_uses_redis_payload(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    runtime_store._redis = DummyRedis()
    runtime_store.save_provider_environment(
        ExternalAgentProviderName.CLAUDE_CODE,
        {"CLAUDE_TOKEN": "abc"},
    )
    env = runtime_store.get_provider_environment(ExternalAgentProviderName.CLAUDE_CODE)
    assert env == {"CLAUDE_TOKEN": "abc"}


def test_get_provider_status_uses_redis_payload(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    runtime_store._redis = DummyRedis()
    status = ExternalAgentProviderStatus(
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude",
        state=ExternalAgentProviderState.READY,
        installed=True,
        authenticated=True,
    )
    runtime_store._redis.set(
        runtime_store._provider_key(ExternalAgentProviderName.CLAUDE_CODE),
        status.model_dump_json(),
    )

    loaded = runtime_store.get_provider_status(ExternalAgentProviderName.CLAUDE_CODE)

    assert loaded.state == ExternalAgentProviderState.READY


def test_get_provider_status_falls_back_when_redis_payload_missing(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    runtime_store._redis = DummyRedis()
    runtime_store._provider_statuses[
        runtime_store._provider_key(ExternalAgentProviderName.CLAUDE_CODE)
    ] = ExternalAgentProviderStatus(
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude",
        state=ExternalAgentProviderState.CHECKING,
        installed=False,
        authenticated=False,
    )

    loaded = runtime_store.get_provider_status(ExternalAgentProviderName.CLAUDE_CODE)

    assert loaded.state == ExternalAgentProviderState.CHECKING


def test_login_session_redis_fallback(runtime_store: ExternalAgentRuntimeStore) -> None:
    runtime_store._redis = DummyRedis()
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="redis-session",
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
        detail="pending",
    )
    runtime_store.save_login_session(session)
    loaded = runtime_store.get_login_session("redis-session")
    assert loaded is not None
    assert loaded.session_id == session.session_id
    runtime_store.save_login_input("redis-session", "code")
    assert runtime_store.get_login_input("redis-session") == "code"
    runtime_store.clear_login_input("redis-session")
    assert runtime_store.get_login_input("redis-session") is None


def test_store_initialization_falls_back_when_redis_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        redis,
        "from_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(redis.RedisError("boom")),
    )

    store = ExternalAgentRuntimeStore(redis_url="redis://broken")

    assert store._redis is None


def test_runtime_store_key_helpers(runtime_store: ExternalAgentRuntimeStore) -> None:
    assert (
        runtime_store._provider_key(ExternalAgentProviderName.CLAUDE_CODE)
        == "orcheo:external_agents:workspace:global:provider:claude_code"
    )
    assert (
        runtime_store._session_key("abc")
        == "orcheo:external_agents:workspace:global:session:abc"
    )
    assert (
        runtime_store._provider_environment_key(ExternalAgentProviderName.CODEX)
        == "orcheo:external_agents:workspace:global:provider-env:codex"
    )
    assert (
        runtime_store._session_input_key("abc")
        == "orcheo:external_agents:workspace:global:session-input:abc"
    )


def test_get_provider_status_falls_back_when_redis_read_fails(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    status = ExternalAgentProviderStatus(
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude",
        state=ExternalAgentProviderState.READY,
        installed=True,
        authenticated=True,
    )
    runtime_store._provider_statuses[runtime_store._provider_key(status.provider)] = (
        status
    )
    runtime_store._redis = ErrorRedis(get_error=redis.RedisError("boom"))

    loaded = runtime_store.get_provider_status(ExternalAgentProviderName.CLAUDE_CODE)

    assert loaded.state == ExternalAgentProviderState.READY


def test_save_provider_status_keeps_memory_copy_when_redis_write_fails(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    runtime_store._redis = ErrorRedis(set_error=redis.RedisError("boom"))
    status = ExternalAgentProviderStatus(
        provider=ExternalAgentProviderName.CODEX,
        display_name="Codex",
        state=ExternalAgentProviderState.CHECKING,
        installed=False,
        authenticated=False,
    )

    runtime_store.save_provider_status(status)

    assert (
        runtime_store._provider_statuses[
            runtime_store._provider_key(status.provider)
        ].state
        == ExternalAgentProviderState.CHECKING
    )


def test_get_provider_environment_falls_back_for_non_dict_redis_payload(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    runtime_store._provider_environments[
        runtime_store._provider_environment_key(ExternalAgentProviderName.CLAUDE_CODE)
    ] = {"CLAUDE_TOKEN": "memory"}
    runtime_store._redis = ErrorRedis(payload='["not-a-dict"]')

    env = runtime_store.get_provider_environment(ExternalAgentProviderName.CLAUDE_CODE)

    assert env == {"CLAUDE_TOKEN": "memory"}


def test_get_provider_environment_falls_back_for_invalid_redis_json(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    runtime_store._provider_environments[
        runtime_store._provider_environment_key(ExternalAgentProviderName.CLAUDE_CODE)
    ] = {"CLAUDE_TOKEN": "memory"}
    runtime_store._redis = ErrorRedis(payload="{invalid")

    env = runtime_store.get_provider_environment(ExternalAgentProviderName.CLAUDE_CODE)

    assert env == {"CLAUDE_TOKEN": "memory"}


def test_save_provider_environment_keeps_memory_copy_when_redis_write_fails(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    runtime_store._redis = ErrorRedis(set_error=redis.RedisError("boom"))

    runtime_store.save_provider_environment(
        ExternalAgentProviderName.CLAUDE_CODE,
        {"CLAUDE_TOKEN": "abc"},
    )

    assert runtime_store._provider_environments[
        runtime_store._provider_environment_key(ExternalAgentProviderName.CLAUDE_CODE)
    ] == {"CLAUDE_TOKEN": "abc"}


def test_get_login_session_falls_back_when_redis_payload_is_blank(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="blank-redis-session",
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    runtime_store._sessions[runtime_store._session_key(session.session_id)] = session
    runtime_store._redis = ErrorRedis(payload="")

    loaded = runtime_store.get_login_session(session.session_id)

    assert loaded is not None
    assert loaded.session_id == session.session_id


def test_get_login_session_falls_back_when_redis_read_fails(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="error-redis-session",
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    runtime_store._sessions[runtime_store._session_key(session.session_id)] = session
    runtime_store._redis = ErrorRedis(get_error=redis.RedisError("boom"))

    loaded = runtime_store.get_login_session(session.session_id)

    assert loaded is not None
    assert loaded.session_id == session.session_id


def test_save_login_session_keeps_memory_copy_when_redis_write_fails(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    runtime_store._redis = ErrorRedis(set_error=redis.RedisError("boom"))
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="save-error",
        provider=ExternalAgentProviderName.CODEX,
        display_name="Codex",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )

    runtime_store.save_login_session(session)

    assert (
        runtime_store._sessions[runtime_store._session_key("save-error")].provider
        == ExternalAgentProviderName.CODEX
    )


def test_login_input_methods_fall_back_when_redis_operations_fail(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    runtime_store._redis = ErrorRedis(
        set_error=redis.RedisError("set"),
        get_error=redis.RedisError("get"),
        delete_error=redis.RedisError("delete"),
    )

    runtime_store.save_login_input("session", "1234")
    assert runtime_store.get_login_input("session") == "1234"
    runtime_store.clear_login_input("session")
    assert runtime_store.get_login_input("session") is None
