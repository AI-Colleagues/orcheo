"""Tests for the persistence helper utilities."""

from contextlib import asynccontextmanager
from typing import cast
from unittest.mock import AsyncMock, MagicMock
import pytest
from dynaconf import Dynaconf
from orcheo import config, persistence
from orcheo.persistence import create_checkpointer, create_graph_store


def test_reset_persistence_singletons_clears_cached_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reset helper clears every cached singleton."""
    monkeypatch.setattr(persistence._state, "checkpointer_pool", object())
    monkeypatch.setattr(persistence._state, "graph_store", object())
    monkeypatch.setattr(persistence._state, "graph_store_exit_stack", object())

    persistence._reset_persistence_singletons()

    assert persistence._state.checkpointer_pool is None
    assert persistence._state.graph_store is None
    assert persistence._state.graph_store_exit_stack is None


@pytest.mark.asyncio
async def test_create_checkpointer_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pool is opened once and reused; never closed between calls."""

    # Reset the singleton so the monkeypatched class is used.
    monkeypatch.setattr(persistence._state, "checkpointer_pool", None)

    monkeypatch.setenv("ORCHEO_CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://example")

    settings = config.get_settings(refresh=True)

    fake_pool = MagicMock()
    fake_pool.open = AsyncMock()
    fake_conn_cm = AsyncMock()
    fake_conn_cm.__aenter__.return_value = "pg_connection"
    fake_conn_cm.__aexit__.return_value = None
    fake_pool.connection.return_value = fake_conn_cm
    fake_pool.close = AsyncMock()

    monkeypatch.setattr(
        "orcheo.persistence.AsyncConnectionPool", MagicMock(return_value=fake_pool)
    )
    if persistence.DictRowFactory is None:
        monkeypatch.setattr("orcheo.persistence.DictRowFactory", MagicMock())

    fake_saver = MagicMock()
    fake_saver.setup = AsyncMock()
    saver_class = MagicMock(return_value=fake_saver)
    monkeypatch.setattr("orcheo.persistence.AsyncPostgresSaver", saver_class)

    async with create_checkpointer(settings) as checkpointer:
        assert checkpointer is fake_saver

    saver_class.assert_called_once_with("pg_connection")
    fake_saver.setup.assert_awaited_once()
    fake_pool.connection.assert_called_once()
    fake_conn_cm.__aenter__.assert_awaited_once()
    fake_pool.open.assert_awaited_once()

    # Pool must NOT be closed between calls — it is a process-lifetime singleton.
    fake_pool.close.assert_not_awaited()

    # Second call reuses the same pool; open is NOT called again.
    fake_saver2 = MagicMock()
    fake_saver2.setup = AsyncMock()
    saver_class.return_value = fake_saver2
    async with create_checkpointer(settings) as checkpointer2:
        assert checkpointer2 is fake_saver2
    fake_pool.open.assert_awaited_once()  # still only one open across both calls


@pytest.mark.asyncio
async def test_create_checkpointer_reuses_pool_seeded_inside_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If another task seeds the singleton before the inner check, reuse it."""
    persistence._reset_persistence_singletons()
    monkeypatch.setattr(persistence._state, "checkpointer_pool", None)

    monkeypatch.setenv("ORCHEO_CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://example")

    settings = config.get_settings(refresh=True)

    fake_pool = MagicMock()
    fake_pool.open = AsyncMock()
    fake_conn_cm = AsyncMock()
    fake_conn_cm.__aenter__.return_value = "pg_connection"
    fake_conn_cm.__aexit__.return_value = None
    fake_pool.connection.return_value = fake_conn_cm

    fake_saver = MagicMock()
    fake_saver.setup = AsyncMock()
    saver_class = MagicMock(return_value=fake_saver)
    pool_factory = MagicMock(return_value=fake_pool)
    monkeypatch.setattr("orcheo.persistence.AsyncConnectionPool", pool_factory)
    monkeypatch.setattr("orcheo.persistence.AsyncPostgresSaver", saver_class)
    if persistence.DictRowFactory is None:
        monkeypatch.setattr("orcheo.persistence.DictRowFactory", MagicMock())

    class _SeedPoolLock:
        async def __aenter__(self):
            persistence._state.checkpointer_pool = fake_pool

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

    monkeypatch.setattr(persistence, "_checkpointer_pool_lock", _SeedPoolLock())

    async with create_checkpointer(settings) as checkpointer:
        assert checkpointer is fake_saver

    pool_factory.assert_not_called()
    fake_pool.open.assert_not_awaited()
    fake_saver.setup.assert_awaited_once()
    fake_pool.connection.assert_called_once()


@pytest.mark.asyncio
async def test_create_checkpointer_invalid_backend() -> None:
    """An unsupported backend should raise an error."""

    bad_settings = Dynaconf(
        envvar_prefix="ORCHEO", environments=False, load_dotenv=False, settings_files=[]
    )
    bad_settings.set("CHECKPOINT_BACKEND", cast(str, "invalid"))
    bad_settings.set("POSTGRES_DSN", None)

    with pytest.raises(ValueError):
        async with create_checkpointer(bad_settings):
            raise AssertionError("context should not yield")


@pytest.mark.asyncio
async def test_create_graph_store_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """Store is created once and reused; from_conn_string not called again."""

    # Reset the singleton so the monkeypatched class is used.
    monkeypatch.setattr(persistence._state, "graph_store", None)
    monkeypatch.setattr(persistence._state, "graph_store_exit_stack", None)

    monkeypatch.setenv("ORCHEO_GRAPH_STORE_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://example")
    monkeypatch.setenv("ORCHEO_POSTGRES_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("ORCHEO_POSTGRES_POOL_MAX_SIZE", "9")
    monkeypatch.setenv("ORCHEO_POSTGRES_POOL_TIMEOUT", "12")
    monkeypatch.setenv("ORCHEO_POSTGRES_POOL_MAX_IDLE", "60")

    fake_store = MagicMock()
    fake_store.setup = AsyncMock()
    calls: dict[str, object] = {}
    from_conn_string_call_count = 0

    class StubPostgresStore:
        @classmethod
        @asynccontextmanager
        async def from_conn_string(
            cls,
            conn_string: str,
            *,
            pool_config: dict[str, object],
        ):
            nonlocal from_conn_string_call_count
            from_conn_string_call_count += 1
            calls["conn_string"] = conn_string
            calls["pool_config"] = pool_config
            yield fake_store

    monkeypatch.setattr("orcheo.persistence.AsyncPostgresStore", StubPostgresStore)

    settings = config.get_settings(refresh=True)

    async with create_graph_store(settings) as graph_store:
        assert graph_store is fake_store

    assert calls["conn_string"] == "postgresql://example"
    assert calls["pool_config"] == {
        "min_size": 2,
        "max_size": 9,
        "timeout": 12.0,
        "max_idle": 60.0,
    }
    fake_store.setup.assert_awaited_once()
    assert from_conn_string_call_count == 1

    # Second call yields the same store without calling from_conn_string again.
    async with create_graph_store(settings) as graph_store2:
        assert graph_store2 is fake_store
    assert from_conn_string_call_count == 1  # still only one initialisation


@pytest.mark.asyncio
async def test_create_graph_store_reuses_store_seeded_inside_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If another task seeds the singleton before the inner check, reuse it."""
    persistence._reset_persistence_singletons()
    monkeypatch.setattr(persistence._state, "graph_store", None)
    monkeypatch.setattr(persistence._state, "graph_store_exit_stack", None)

    monkeypatch.setenv("ORCHEO_GRAPH_STORE_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://example")
    monkeypatch.setenv("ORCHEO_POSTGRES_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("ORCHEO_POSTGRES_POOL_MAX_SIZE", "9")
    monkeypatch.setenv("ORCHEO_POSTGRES_POOL_TIMEOUT", "12")
    monkeypatch.setenv("ORCHEO_POSTGRES_POOL_MAX_IDLE", "60")

    settings = config.get_settings(refresh=True)

    fake_store = MagicMock()
    fake_store.setup = AsyncMock()
    store_factory = MagicMock()
    store_factory.from_conn_string = AsyncMock()
    monkeypatch.setattr("orcheo.persistence.AsyncPostgresStore", store_factory)

    class _SeedStoreLock:
        async def __aenter__(self):
            persistence._state.graph_store = fake_store

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

    monkeypatch.setattr(persistence, "_graph_store_lock", _SeedStoreLock())

    async with create_graph_store(settings) as graph_store:
        assert graph_store is fake_store

    store_factory.from_conn_string.assert_not_called()
    fake_store.setup.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_graph_store_invalid_backend() -> None:
    """Unsupported graph-store backends should raise an error."""

    bad_settings = Dynaconf(
        envvar_prefix="ORCHEO", environments=False, load_dotenv=False, settings_files=[]
    )
    bad_settings.set("GRAPH_STORE_BACKEND", cast(str, "invalid"))
    bad_settings.set("POSTGRES_DSN", None)

    with pytest.raises(ValueError):
        async with create_graph_store(bad_settings):
            raise AssertionError("context should not yield")
