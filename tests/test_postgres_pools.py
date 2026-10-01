"""Tests for the process-wide PostgreSQL pool registry."""

from __future__ import annotations
import asyncio
from typing import Any
import pytest
from orcheo import postgres_pools
from orcheo.identity import PostgresIdentityRepository
from orcheo.identity import postgres_store as identity_store
from orcheo.workspace import PostgresWorkspaceRepository
from orcheo.workspace import postgres_store as workspace_store


ROW_FACTORY = object()


class FakeAsyncPool:
    """Record construction arguments and open/close calls."""

    instances: list[FakeAsyncPool] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.opened = 0
        self.closed = 0
        FakeAsyncPool.instances.append(self)

    async def open(self) -> None:
        self.opened += 1

    async def close(self) -> None:
        self.closed += 1


@pytest.fixture(autouse=True)
def _clear_fake_pools() -> None:
    FakeAsyncPool.instances = []


async def _acquire(dsn: str = "postgresql://db") -> Any:
    return await postgres_pools.acquire_async_pool(
        FakeAsyncPool,
        dsn,
        min_size=1,
        max_size=4,
        timeout=5.0,
        max_idle=60.0,
        row_factory=ROW_FACTORY,
    )


def test_connection_kwargs_disable_prepared_statements() -> None:
    assert postgres_pools.connection_kwargs(
        autocommit=True, row_factory=ROW_FACTORY
    ) == {
        "autocommit": True,
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
        "tcp_user_timeout": 30_000,
        "prepare_threshold": None,
        "row_factory": ROW_FACTORY,
    }


@pytest.mark.asyncio
async def test_acquire_async_pool_shares_one_open_pool() -> None:
    first = await _acquire()
    second = await _acquire()

    assert first is second
    assert len(FakeAsyncPool.instances) == 1
    assert first.opened == 1
    assert first.args == ("postgresql://db",)
    assert first.kwargs == {
        "min_size": 1,
        "max_size": 4,
        "timeout": 5.0,
        "max_idle": 60.0,
        "check": postgres_pools.AsyncConnectionPool.check_connection,
        "open": False,
        "kwargs": {
            "autocommit": False,
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
            "tcp_user_timeout": 30_000,
            "prepare_threshold": None,
            "row_factory": ROW_FACTORY,
        },
    }


@pytest.mark.asyncio
async def test_acquire_async_pool_separates_dsns() -> None:
    first = await _acquire("postgresql://one")
    second = await _acquire("postgresql://two")

    assert first is not second


def test_acquire_async_pool_separates_event_loops() -> None:
    first = asyncio.run(_acquire())
    second = asyncio.run(_acquire())

    assert first is not second


@pytest.mark.asyncio
async def test_release_async_pool_closes_after_last_reference() -> None:
    other = await _acquire("postgresql://other")
    pool = await _acquire()
    await _acquire()

    await postgres_pools.release_async_pool(pool)
    assert pool.closed == 0

    await postgres_pools.release_async_pool(pool)
    assert pool.closed == 1

    replacement = await _acquire()
    assert replacement is not pool
    assert other.closed == 0


@pytest.mark.asyncio
async def test_release_async_pool_closes_unregistered_pool() -> None:
    pool = FakeAsyncPool()

    await postgres_pools.release_async_pool(pool)

    assert pool.closed == 1


def test_workspace_and_identity_repositories_share_sync_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[FakeSyncPool] = []

    class FakeSyncPool:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs
            created.append(self)

    monkeypatch.setattr(workspace_store, "ConnectionPool", FakeSyncPool)
    monkeypatch.setattr(identity_store, "ConnectionPool", FakeSyncPool)
    monkeypatch.setattr(PostgresWorkspaceRepository, "_ensure_schema", lambda _: None)
    monkeypatch.setattr(PostgresIdentityRepository, "_ensure_schema", lambda _: None)

    workspaces = PostgresWorkspaceRepository(
        "postgresql://db", pool_min_size=2, pool_max_size=3
    )
    identities = PostgresIdentityRepository("postgresql://db")

    assert workspaces._pool is identities._pool
    [pool] = created
    assert pool.args == ("postgresql://db",)
    assert pool.kwargs == {
        "min_size": 2,
        "max_size": 3,
        "open": True,
        **postgres_pools.pool_kwargs(),
        "kwargs": {
            "autocommit": False,
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
            "tcp_user_timeout": 30_000,
            "prepare_threshold": None,
            "row_factory": workspace_store.dict_row,
        },
    }


def test_reset_shared_pools_forgets_sync_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSyncPool:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

    def _get() -> Any:
        return postgres_pools.get_shared_sync_pool(
            FakeSyncPool,
            "postgresql://db",
            min_size=1,
            max_size=2,
            row_factory=ROW_FACTORY,
        )

    first = _get()
    assert _get() is first

    postgres_pools.reset_shared_pools()

    assert _get() is not first
