"""Core coverage tests for plugin installation stores."""

from __future__ import annotations
from pathlib import Path
from typing import Any
import pytest
from orcheo_backend.app import plugin_installation_store as plugin_store
from orcheo_backend.app.plugin_installation_store import (
    PostgresPluginInstallationStore,
    SqlitePluginInstallationStore,
)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self._response: Any = {}

    async def fetchone(self) -> dict[str, Any] | None:
        response = self._response
        if isinstance(response, dict):
            return response.get("row") if "row" in response else response
        return response

    async def fetchall(self) -> list[Any]:
        response = self._response
        if isinstance(response, dict):
            if "rows" in response:
                return list(response.get("rows") or [])
            if "row" in response and response.get("row") is not None:
                return [response["row"]]
            return []
        if isinstance(response, list):
            return list(response)
        return []

    async def execute(self, query: str, params: Any | None = None) -> FakeCursor:
        self._connection.queries.append((query.strip(), params))
        self._response = self._connection._pop_response()
        return self

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeConnection:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.queries: list[tuple[str, Any | None]] = []
        self.commits = 0

    def _pop_response(self) -> Any:
        return self._responses.pop(0) if self._responses else {}

    async def execute(self, query: str, params: Any | None = None) -> FakeCursor:
        self.queries.append((query.strip(), params))
        cursor = FakeCursor(self)
        cursor._response = self._pop_response()
        return cursor

    async def commit(self) -> None:
        self.commits += 1

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.opened = False

    async def open(self) -> None:
        self.opened = True

    def connection(self) -> FakeConnection:
        return self._connection


@pytest.mark.asyncio
async def test_sqlite_store_ensure_initialized_runs_schema_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """SQLite initialization should call the schema helper only once."""

    calls: list[Path] = []

    async def fake_ensure_schema(database_path: Path) -> None:
        calls.append(database_path)

    monkeypatch.setattr(plugin_store, "ensure_sqlite_schema", fake_ensure_schema)

    store = SqlitePluginInstallationStore(tmp_path / "plugins.sqlite")
    await store._ensure_initialized()
    await store._ensure_initialized()

    assert calls == [tmp_path / "plugins.sqlite"]
    assert store._initialized is True


@pytest.mark.asyncio
async def test_sqlite_store_ensure_initialized_race_returns_early(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The SQLite initializer should return early if another task wins the race."""

    calls: list[Path] = []

    async def fake_ensure_schema(database_path: Path) -> None:
        calls.append(database_path)

    monkeypatch.setattr(plugin_store, "ensure_sqlite_schema", fake_ensure_schema)

    store = SqlitePluginInstallationStore(tmp_path / "plugins.sqlite")

    class SideEffectLock:
        async def __aenter__(self) -> None:
            store._initialized = True

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    store._init_lock = SideEffectLock()  # type: ignore[assignment]

    await store._ensure_initialized()

    assert calls == []
    assert store._initialized is True


@pytest.mark.asyncio
async def test_postgres_store_requires_psycopg_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PostgreSQL store should fail fast when psycopg is missing."""

    monkeypatch.setattr(plugin_store, "_AsyncConnectionPool", None)
    monkeypatch.setattr(plugin_store, "_DictRowFactory", None)

    with pytest.raises(RuntimeError, match="requires psycopg"):
        PostgresPluginInstallationStore("postgresql://example")


@pytest.mark.asyncio
async def test_postgres_store_get_pool_creates_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_get_pool should create and open a pool on first use."""

    created: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class FakeAsyncConnectionPool:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            created.append((args, kwargs))
            self.opened = False

        async def open(self) -> None:
            self.opened = True

    monkeypatch.setattr(plugin_store, "_AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(plugin_store, "_DictRowFactory", object())

    store = PostgresPluginInstallationStore(
        "postgresql://example",
        pool_min_size=2,
        pool_max_size=20,
        pool_timeout=15.0,
        pool_max_idle=120.0,
    )

    pool = await store._get_pool()

    assert pool.opened is True
    assert created[0][0] == ("postgresql://example",)
    assert created[0][1]["min_size"] == 2
    assert created[0][1]["max_size"] == 20
    assert created[0][1]["timeout"] == 15.0
    assert created[0][1]["max_idle"] == 120.0


@pytest.mark.asyncio
async def test_postgres_store_get_pool_race_returns_existing_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_get_pool should return the pool set by another task while waiting."""

    class FakeAsyncConnectionPool:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.opened = False

        async def open(self) -> None:
            self.opened = True

    monkeypatch.setattr(plugin_store, "_AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(plugin_store, "_DictRowFactory", object())

    store = PostgresPluginInstallationStore("postgresql://example")

    class SideEffectLock:
        async def __aenter__(self) -> None:
            store._pool = "existing_pool"  # type: ignore[assignment]

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    store._init_lock = SideEffectLock()  # type: ignore[assignment]

    pool = await store._get_pool()

    assert pool == "existing_pool"


@pytest.mark.asyncio
async def test_postgres_store_ensure_initialized_runs_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_initialized should run the migration statements once."""

    monkeypatch.setattr(plugin_store, "_AsyncConnectionPool", object())
    monkeypatch.setattr(plugin_store, "_DictRowFactory", object())

    connection = FakeConnection([])
    store = PostgresPluginInstallationStore("postgresql://example")
    store._pool = FakePool(connection)
    store._initialized = False

    await store._ensure_initialized()

    assert store._initialized is True
    assert connection.queries


@pytest.mark.asyncio
async def test_postgres_store_ensure_initialized_race_returns_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_initialized should return if another task already finished."""

    monkeypatch.setattr(plugin_store, "_AsyncConnectionPool", object())
    monkeypatch.setattr(plugin_store, "_DictRowFactory", object())

    connection = FakeConnection([])
    store = PostgresPluginInstallationStore("postgresql://example")
    store._pool = FakePool(connection)
    store._initialized = False

    class SideEffectLock:
        async def __aenter__(self) -> None:
            store._initialized = True

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    store._init_lock = SideEffectLock()  # type: ignore[assignment]

    await store._ensure_initialized()

    assert store._initialized is True
    assert connection.queries == []


@pytest.mark.asyncio
async def test_postgres_store_set_get_and_list_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PostgreSQL store should persist, fetch, and list workspace state."""

    monkeypatch.setattr(plugin_store, "_AsyncConnectionPool", object())
    monkeypatch.setattr(plugin_store, "_DictRowFactory", object())

    responses = [
        {},  # set_plugin_enabled
        {"row": {"enabled": 1}},  # get_plugin_enabled
        {
            "rows": [
                {
                    "plugin_name": "plugin-a",
                    "workspace_id": "workspace-1",
                    "enabled": 1,
                }
            ]
        },
        {
            "rows": [
                {
                    "plugin_name": "plugin-b",
                    "workspace_id": "workspace-2",
                    "enabled": 0,
                }
            ]
        },
    ]
    connection = FakeConnection(responses)
    store = PostgresPluginInstallationStore("postgresql://example")
    store._pool = FakePool(connection)
    store._initialized = True

    await store.set_plugin_enabled("plugin-a", workspace_id="workspace-1", enabled=True)
    enabled = await store.get_plugin_enabled("plugin-a", workspace_id="workspace-1")
    filtered = await store.list_plugin_states(workspace_id="workspace-1")
    all_states = await store.list_plugin_states()

    assert enabled is True
    assert len(filtered) == 1
    assert filtered[0].plugin_name == "plugin-a"
    assert len(all_states) == 1
    assert all_states[0].plugin_name == "plugin-b"
    assert "workspace_id = %s" in connection.queries[1][0]
    assert "workspace_id = %s" in connection.queries[2][0]
    assert "FROM plugin_installations" in connection.queries[3][0]


@pytest.mark.asyncio
async def test_postgres_store_get_returns_none_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_plugin_enabled should return None when a row is missing."""

    monkeypatch.setattr(plugin_store, "_AsyncConnectionPool", object())
    monkeypatch.setattr(plugin_store, "_DictRowFactory", object())

    connection = FakeConnection([{"row": None}])
    store = PostgresPluginInstallationStore("postgresql://example")
    store._pool = FakePool(connection)
    store._initialized = True

    enabled = await store.get_plugin_enabled("plugin-a", workspace_id="workspace-1")

    assert enabled is None
