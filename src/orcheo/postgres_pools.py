"""Process-wide PostgreSQL connection pools shared by Orcheo's stores.

Each store used to open its own pool, so one process held a connection per
store even when idle. Poolers such as Supabase's session-mode Supavisor cap
client connections (15 on small projects), which a backend plus a few Celery
workers exceeded. Stores that use the same connection settings now share one
pool per process (and per event loop for async pools).

Connections never use server-side prepared statements: transaction-mode
poolers (PgBouncer, Supavisor on port 6543) hand each transaction to any server
connection, where a statement prepared on another one does not exist.
"""

from __future__ import annotations
import asyncio
import threading
from dataclasses import dataclass
from typing import Any
from psycopg_pool import AsyncConnectionPool, ConnectionPool
from orcheo.config import get_settings


__all__ = [
    "acquire_async_pool",
    "connection_kwargs",
    "pool_kwargs",
    "get_shared_sync_pool",
    "release_async_pool",
    "reset_shared_pools",
]


def connection_kwargs(
    *, autocommit: bool, row_factory: Any, settings: Any = None
) -> dict[str, Any]:
    """Return psycopg connection arguments safe behind a transaction pooler."""
    settings = settings if settings is not None else get_settings()
    return {
        "connect_timeout": int(settings.get("POSTGRES_CONNECT_TIMEOUT", 10)),
        "keepalives": 1,
        "keepalives_idle": int(settings.get("POSTGRES_KEEPALIVES_IDLE", 30)),
        "keepalives_interval": int(settings.get("POSTGRES_KEEPALIVES_INTERVAL", 10)),
        "keepalives_count": int(settings.get("POSTGRES_KEEPALIVES_COUNT", 3)),
        "tcp_user_timeout": int(settings.get("POSTGRES_TCP_USER_TIMEOUT_MS", 30_000)),
        "autocommit": autocommit,
        # None disables prepared statements; 0 would prepare every query.
        "prepare_threshold": None,
        "row_factory": row_factory,
    }


def pool_kwargs(*, async_pool: bool = False) -> dict[str, Any]:
    """Return checkout checks and bounded waits for every PostgreSQL pool.

    A checkout check itself performs network I/O, so connection_kwargs must
    also be used. max_idle only retires connections above the pool minimum.
    """
    settings = get_settings()
    return {
        "check": (
            AsyncConnectionPool.check_connection
            if async_pool
            else ConnectionPool.check_connection
        ),
        "timeout": float(settings.get("POSTGRES_POOL_TIMEOUT", 5.0)),
        "max_idle": float(settings.get("POSTGRES_POOL_MAX_IDLE", 240.0)),
    }


@dataclass
class _AsyncPoolEntry:
    pool: Any
    references: int


_async_pools: dict[tuple[Any, ...], _AsyncPoolEntry] = {}
_async_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}
_sync_pools: dict[tuple[Any, ...], Any] = {}
_sync_lock = threading.Lock()


async def acquire_async_pool(
    pool_class: Any,
    dsn: str,
    *,
    min_size: int,
    max_size: int,
    timeout: float,
    max_idle: float,
    row_factory: Any,
) -> Any:
    """Return the open shared async pool for `dsn`, creating it on first use.

    Pools are bound to the running event loop, so each loop gets its own. The
    first caller's sizing wins. Pair every call with `release_async_pool`.
    """
    loop = asyncio.get_running_loop()
    key = (pool_class, dsn, row_factory, loop)
    lock = _async_locks.setdefault(loop, asyncio.Lock())
    async with lock:
        entry = _async_pools.get(key)
        if entry is None:
            pool = pool_class(
                dsn,
                min_size=min_size,
                max_size=max_size,
                check=AsyncConnectionPool.check_connection,
                timeout=timeout,
                max_idle=max_idle,
                open=False,
                kwargs=connection_kwargs(autocommit=False, row_factory=row_factory),
            )
            await pool.open()
            entry = _AsyncPoolEntry(pool=pool, references=0)
            _async_pools[key] = entry
        entry.references += 1
        return entry.pool


async def release_async_pool(pool: Any) -> None:
    """Drop one reference to `pool`, closing it once no store uses it.

    Pools that did not come from `acquire_async_pool` are closed directly.
    """
    for key, entry in list(_async_pools.items()):
        if entry.pool is not pool:
            continue
        entry.references -= 1
        if entry.references > 0:
            return
        del _async_pools[key]
        break
    await pool.close()


def get_shared_sync_pool(
    pool_class: Any,
    dsn: str,
    *,
    min_size: int,
    max_size: int,
    row_factory: Any,
) -> Any:
    """Return the open shared sync pool for `dsn`, creating it on first use.

    Connections are transactional: the pool commits when a `connection()`
    block exits cleanly and rolls back when it raises.
    """
    key = (pool_class, dsn, row_factory)
    with _sync_lock:
        pool = _sync_pools.get(key)
        if pool is None:
            pool = pool_class(
                dsn,
                min_size=min_size,
                max_size=max_size,
                open=True,
                **pool_kwargs(),
                kwargs=connection_kwargs(autocommit=False, row_factory=row_factory),
            )
            _sync_pools[key] = pool
        return pool


def reset_shared_pools() -> None:
    """Forget every shared pool without closing it. Intended for tests only."""
    _async_pools.clear()
    _async_locks.clear()
    with _sync_lock:
        _sync_pools.clear()
