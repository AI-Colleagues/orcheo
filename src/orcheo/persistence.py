"""Persistence helpers that create LangGraph checkpoint savers and stores."""

from __future__ import annotations
import asyncio
import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from dynaconf import Dynaconf
from orcheo.config import CheckpointBackend, GraphStoreBackend
from orcheo.postgres_pools import connection_kwargs


AsyncPostgresSaver: Any | None
AsyncConnectionPool: Any | None
DictRowFactory: Any | None
AsyncPostgresStore: Any | None

try:  # pragma: no cover - optional dependency
    AsyncPostgresSaver = importlib.import_module(
        "langgraph.checkpoint.postgres.aio"
    ).AsyncPostgresSaver
    AsyncPostgresStore = importlib.import_module(
        "langgraph.store.postgres.aio"
    ).AsyncPostgresStore
    AsyncConnectionPool = importlib.import_module("psycopg_pool").AsyncConnectionPool
    DictRowFactory = importlib.import_module("psycopg.rows").dict_row
except Exception:  # pragma: no cover - fallback when dependency missing
    AsyncPostgresSaver = None
    AsyncPostgresStore = None
    AsyncConnectionPool = None
    DictRowFactory = None


class _State:
    """Mutable singletons shared for the lifetime of the worker process.

    Opened on first use via double-checked locking; never explicitly closed so
    that pool.close() cannot block in getaddrinfo() threads during DNS failures.
    """

    checkpointer_pool: Any = None
    graph_store: Any = None


_state = _State()
_checkpointer_pool_lock: asyncio.Lock = asyncio.Lock()
_graph_store_lock: asyncio.Lock = asyncio.Lock()


def _reset_persistence_singletons() -> None:
    """Reset module-level singletons. Intended for test isolation only."""
    _state.checkpointer_pool = None
    _state.graph_store = None


def _require_postgres_dsn(settings: Dynaconf) -> str:
    dsn = settings.postgres_dsn
    if dsn is None:  # pragma: no cover - defensive, validated earlier
        msg = "Postgres backend requires ORCHEO_POSTGRES_DSN to be set."
        raise RuntimeError(msg)
    return str(dsn)


async def _open_langgraph_pool(settings: Dynaconf) -> Any:
    """Open a pool configured for LangGraph's Postgres checkpointer or store.

    LangGraph expects autocommit connections that return dict rows. Prepared
    statements stay off so the pool also works behind a transaction pooler.
    The checkpointer and store get separate pools because a checkpointer holds
    its connection for a whole run, which would starve store lookups.
    """
    assert AsyncConnectionPool is not None  # mypy
    pool = AsyncConnectionPool(
        _require_postgres_dsn(settings),
        open=False,
        min_size=int(settings.postgres_pool_min_size),
        max_size=int(settings.postgres_pool_max_size),
        timeout=float(settings.postgres_pool_timeout),
        max_idle=float(settings.postgres_pool_max_idle),
        kwargs=connection_kwargs(autocommit=True, row_factory=DictRowFactory),
    )
    await pool.open()
    return pool


@asynccontextmanager
async def create_checkpointer(settings: Dynaconf) -> AsyncIterator[Any]:
    """Create a LangGraph checkpointer based on the configured backend.

    The underlying connection pool is a process-lifetime singleton.  It is
    opened on the first call and reused on every subsequent call, preventing
    per-execution pool creation and the associated background-thread
    accumulation that can cause deadlocks when DNS is unresponsive.
    """
    backend = cast(CheckpointBackend, settings.checkpoint_backend)
    if backend != "postgres":
        msg = "Checkpoint backend must be 'postgres'."
        raise ValueError(msg)

    if (
        AsyncPostgresSaver is None
        or AsyncConnectionPool is None
        or DictRowFactory is None
    ):  # pragma: no cover
        msg = "Postgres backend requires psycopg_pool and langgraph postgres extras."
        raise RuntimeError(msg)

    if _state.checkpointer_pool is None:
        async with _checkpointer_pool_lock:
            if _state.checkpointer_pool is None:
                _state.checkpointer_pool = await _open_langgraph_pool(settings)

    async with _state.checkpointer_pool.connection() as conn:
        checkpointer = AsyncPostgresSaver(cast(Any, conn))
        await checkpointer.setup()
        yield checkpointer


@asynccontextmanager
async def create_graph_store(settings: Dynaconf) -> AsyncIterator[Any]:
    """Create a LangGraph store based on the configured backend.

    The store and its connection pool are a process-lifetime singleton, opened
    on the first call and reused on every subsequent call.
    """
    backend = cast(GraphStoreBackend, settings.graph_store_backend)
    if backend != "postgres":
        msg = "Graph store backend must be 'postgres'."
        raise ValueError(msg)

    if (
        AsyncPostgresStore is None
        or AsyncConnectionPool is None
        or DictRowFactory is None
    ):  # pragma: no cover
        msg = "Postgres graph store requires langgraph postgres extras."
        raise RuntimeError(msg)

    if _state.graph_store is None:
        async with _graph_store_lock:
            if _state.graph_store is None:
                pool = await _open_langgraph_pool(settings)
                store = AsyncPostgresStore(conn=pool)
                await store.setup()
                _state.graph_store = store

    yield _state.graph_store
