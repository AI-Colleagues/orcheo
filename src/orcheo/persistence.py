"""Persistence helpers that create LangGraph checkpoint savers and stores."""

from __future__ import annotations
import asyncio
import contextlib
import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from dynaconf import Dynaconf
from orcheo.config import CheckpointBackend, GraphStoreBackend


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
    graph_store_exit_stack: contextlib.AsyncExitStack | None = None


_state = _State()
_checkpointer_pool_lock: asyncio.Lock = asyncio.Lock()
_graph_store_lock: asyncio.Lock = asyncio.Lock()


def _reset_persistence_singletons() -> None:
    """Reset module-level singletons. Intended for test isolation only."""
    _state.checkpointer_pool = None
    _state.graph_store = None
    _state.graph_store_exit_stack = None


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

    dsn = settings.postgres_dsn
    if dsn is None:  # pragma: no cover - defensive, validated earlier
        msg = "Postgres backend requires ORCHEO_POSTGRES_DSN to be set."
        raise RuntimeError(msg)

    if _state.checkpointer_pool is None:
        async with _checkpointer_pool_lock:
            if _state.checkpointer_pool is None:
                pool = AsyncConnectionPool(
                    dsn,
                    open=False,
                    min_size=int(settings.postgres_pool_min_size),
                    max_size=int(settings.postgres_pool_max_size),
                    timeout=float(settings.postgres_pool_timeout),
                    max_idle=float(settings.postgres_pool_max_idle),
                    kwargs={
                        "autocommit": True,
                        "prepare_threshold": 0,
                        "row_factory": DictRowFactory,
                    },
                )
                await pool.open()
                _state.checkpointer_pool = pool

    async with _state.checkpointer_pool.connection() as conn:  # type: ignore[attr-defined]
        checkpointer = AsyncPostgresSaver(cast(Any, conn))
        await checkpointer.setup()
        yield checkpointer


@asynccontextmanager
async def create_graph_store(settings: Dynaconf) -> AsyncIterator[Any]:
    """Create a LangGraph store based on the configured backend.

    The underlying store (and its internal connection pool) is a
    process-lifetime singleton.  It is opened on the first call and reused on
    every subsequent call via an AsyncExitStack that keeps the context manager
    alive for the lifetime of the process.
    """
    backend = cast(GraphStoreBackend, settings.graph_store_backend)
    if backend != "postgres":
        msg = "Graph store backend must be 'postgres'."
        raise ValueError(msg)

    if AsyncPostgresStore is None:  # pragma: no cover
        msg = "Postgres graph store requires langgraph postgres extras."
        raise RuntimeError(msg)

    dsn = settings.postgres_dsn
    if dsn is None:  # pragma: no cover - defensive, validated earlier
        msg = "Postgres backend requires ORCHEO_POSTGRES_DSN to be set."
        raise RuntimeError(msg)

    if _state.graph_store is None:
        async with _graph_store_lock:
            if _state.graph_store is None:
                pool_config = {
                    "min_size": int(settings.postgres_pool_min_size),
                    "max_size": int(settings.postgres_pool_max_size),
                    "timeout": float(settings.postgres_pool_timeout),
                    "max_idle": float(settings.postgres_pool_max_idle),
                }
                exit_stack = contextlib.AsyncExitStack()
                store = await exit_stack.enter_async_context(
                    AsyncPostgresStore.from_conn_string(
                        dsn,
                        pool_config=pool_config,
                    )
                )
                await store.setup()
                _state.graph_store_exit_stack = exit_stack
                _state.graph_store = store

    yield _state.graph_store
