"""Base helpers for the PostgreSQL ChatKit store."""

from __future__ import annotations
import asyncio
import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from chatkit.store import Store
from orcheo.postgres_pools import acquire_async_pool
from orcheo_backend.app.chatkit_store_postgres.schema import ensure_schema
from orcheo_backend.app.chatkit_store_postgres.types import ChatKitRequestContext
from orcheo_backend.app.chatkit_store_postgres.utils import now_utc


# Optional psycopg dependencies
AsyncConnectionPool: Any | None
DictRowFactory: Any | None

try:  # pragma: no cover - optional dependency
    AsyncConnectionPool = importlib.import_module("psycopg_pool").AsyncConnectionPool
    DictRowFactory = importlib.import_module("psycopg.rows").dict_row
except Exception:  # pragma: no cover - fallback when dependency missing
    AsyncConnectionPool = None
    DictRowFactory = None


class BasePostgresStore(Store[ChatKitRequestContext]):
    """Manage the PostgreSQL connection pool and schema."""

    def __init__(
        self,
        dsn: str,
        *,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        pool_timeout: float = 30.0,
        pool_max_idle: float = 300.0,
    ) -> None:
        """Initialize the store with a Postgres DSN and pool settings."""
        if AsyncConnectionPool is None or DictRowFactory is None:
            msg = "PostgreSQL backend requires psycopg[binary,pool] to be installed."
            raise RuntimeError(msg)

        self._dsn = dsn
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool_timeout = pool_timeout
        self._pool_max_idle = pool_max_idle
        self._pool: Any | None = None
        self._lock = asyncio.Lock()
        self._pool_lock = asyncio.Lock()
        self._schema_lock = asyncio.Lock()
        self._initialized = False

    async def _get_pool(self) -> Any:
        if self._pool is not None:
            return self._pool

        async with self._pool_lock:
            if self._pool is not None:
                return self._pool

            pool_class = AsyncConnectionPool
            assert pool_class is not None  # mypy
            self._pool = await acquire_async_pool(
                pool_class,
                self._dsn,
                min_size=self._pool_min_size,
                max_size=self._pool_max_size,
                timeout=self._pool_timeout,
                max_idle=self._pool_max_idle,
                row_factory=DictRowFactory,
            )
            return self._pool

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[Any]:
        pool = await self._get_pool()
        async with pool.connection() as conn:
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        async with self._schema_lock:
            if self._initialized:
                return

            async with self._connection() as conn:
                await ensure_schema(conn)

            self._initialized = True

    async def _next_item_ordinal(self, conn: Any, thread_id: str) -> int:
        cursor = await conn.execute(
            (
                "SELECT COALESCE(MAX(ordinal), -1) AS current "
                "FROM chat_messages WHERE thread_id = %s"
            ),
            (thread_id,),
        )
        row = await cursor.fetchone()
        current = row["current"] if row is not None else -1
        return int(current) + 1

    async def _touch_thread(self, conn: Any, thread_id: str) -> None:
        await conn.execute(
            "UPDATE chat_threads SET updated_at = %s WHERE id = %s",
            (now_utc(), thread_id),
        )


__all__ = ["BasePostgresStore"]
