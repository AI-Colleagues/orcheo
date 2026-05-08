"""Per-workspace plugin install/enable state stores."""

from __future__ import annotations
import asyncio
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from orcheo_backend.app.history.sqlite_utils import (
    connect_sqlite,
    ensure_sqlite_schema,
)


_AsyncConnectionPool: Any | None
_DictRowFactory: Any | None

try:  # pragma: no cover - optional dependency
    _AsyncConnectionPool = importlib.import_module("psycopg_pool").AsyncConnectionPool
    _DictRowFactory = importlib.import_module("psycopg.rows").dict_row
except Exception:  # pragma: no cover
    _AsyncConnectionPool = None
    _DictRowFactory = None


POSTGRES_PLUGIN_INSTALLATION_MIGRATION = """
CREATE TABLE IF NOT EXISTS plugin_installations (
    plugin_name TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (plugin_name, workspace_id)
);
CREATE INDEX IF NOT EXISTS idx_plugin_installations_workspace_id
    ON plugin_installations (workspace_id);
"""


@dataclass(slots=True)
class WorkspacePluginState:
    """Per-workspace enable/disable override for an installed plugin."""

    plugin_name: str
    workspace_id: str
    enabled: bool


@runtime_checkable
class PluginInstallationStore(Protocol):
    """Protocol for per-workspace plugin installation state."""

    async def set_plugin_enabled(
        self, plugin_name: str, *, workspace_id: str, enabled: bool
    ) -> None:
        """Persist whether one plugin is enabled for one workspace."""
        ...  # pragma: no cover

    async def get_plugin_enabled(
        self, plugin_name: str, *, workspace_id: str
    ) -> bool | None:
        """Return the workspace-scoped enabled flag for one plugin."""
        ...  # pragma: no cover

    async def list_plugin_states(
        self, *, workspace_id: str | None = None
    ) -> list[WorkspacePluginState]:
        """Return workspace plugin states, optionally filtered by workspace."""
        ...  # pragma: no cover


class InMemoryPluginInstallationStore:
    """Thread-safe in-memory plugin installation store for testing."""

    def __init__(self) -> None:
        """Initialise an empty in-memory plugin state registry."""
        self._states: dict[tuple[str, str], bool] = {}
        self._lock = asyncio.Lock()

    async def set_plugin_enabled(
        self, plugin_name: str, *, workspace_id: str, enabled: bool
    ) -> None:
        """Store a workspace-scoped enabled flag for one plugin."""
        async with self._lock:
            self._states[(plugin_name, workspace_id)] = enabled

    async def get_plugin_enabled(
        self, plugin_name: str, *, workspace_id: str
    ) -> bool | None:
        """Return the workspace-scoped enabled flag for one plugin."""
        async with self._lock:
            return self._states.get((plugin_name, workspace_id))

    async def list_plugin_states(
        self, *, workspace_id: str | None = None
    ) -> list[WorkspacePluginState]:
        """List all workspace plugin states, optionally filtered."""
        async with self._lock:
            items = self._states.items()
            if workspace_id is not None:
                return [
                    WorkspacePluginState(plugin_name=k[0], workspace_id=k[1], enabled=v)
                    for k, v in items
                    if k[1] == workspace_id
                ]
            return [
                WorkspacePluginState(plugin_name=k[0], workspace_id=k[1], enabled=v)
                for k, v in items
            ]


class SqlitePluginInstallationStore:
    """SQLite-backed per-workspace plugin installation store."""

    def __init__(self, database_path: str | Path) -> None:
        """Initialise the SQLite-backed store for the configured database."""
        self._database_path = Path(database_path).expanduser()
        self._lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await ensure_sqlite_schema(self._database_path)
            self._initialized = True

    async def set_plugin_enabled(
        self, plugin_name: str, *, workspace_id: str, enabled: bool
    ) -> None:
        """Store a workspace-scoped enabled flag in SQLite."""
        await self._ensure_initialized()
        async with self._lock:
            async with connect_sqlite(self._database_path) as conn:
                await conn.execute(
                    "INSERT INTO plugin_installations "
                    "(plugin_name, workspace_id, enabled) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(plugin_name, workspace_id) "
                    "DO UPDATE SET enabled = excluded.enabled",
                    (plugin_name, workspace_id, 1 if enabled else 0),
                )
                await conn.commit()

    async def get_plugin_enabled(
        self, plugin_name: str, *, workspace_id: str
    ) -> bool | None:
        """Return the SQLite-stored enabled flag for one plugin."""
        await self._ensure_initialized()
        async with connect_sqlite(self._database_path) as conn:
            async with conn.execute(
                "SELECT enabled FROM plugin_installations "
                "WHERE plugin_name = ? AND workspace_id = ?",
                (plugin_name, workspace_id),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                return bool(row["enabled"])

    async def list_plugin_states(
        self, *, workspace_id: str | None = None
    ) -> list[WorkspacePluginState]:
        """List all SQLite workspace plugin states, optionally filtered."""
        await self._ensure_initialized()
        if workspace_id is not None:
            sql = (
                "SELECT plugin_name, workspace_id, enabled "
                "FROM plugin_installations WHERE workspace_id = ?"
            )
            params: tuple[str, ...] = (workspace_id,)
        else:
            sql = "SELECT plugin_name, workspace_id, enabled FROM plugin_installations"
            params = ()
        async with connect_sqlite(self._database_path) as conn:
            async with conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        return [
            WorkspacePluginState(
                plugin_name=str(row["plugin_name"]),
                workspace_id=str(row["workspace_id"]),
                enabled=bool(row["enabled"]),
            )
            for row in rows
        ]


class PostgresPluginInstallationStore:
    """PostgreSQL-backed per-workspace plugin installation store."""

    def __init__(
        self,
        dsn: str,
        *,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        pool_timeout: float = 30.0,
        pool_max_idle: float = 300.0,
    ) -> None:
        """Initialise the PostgreSQL-backed store for the configured DSN."""
        if _AsyncConnectionPool is None or _DictRowFactory is None:
            msg = "PostgreSQL backend requires psycopg[binary,pool] to be installed."
            raise RuntimeError(msg)
        self._dsn = dsn
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool_timeout = pool_timeout
        self._pool_max_idle = pool_max_idle
        self._pool: Any | None = None
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def _get_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        async with self._init_lock:
            if self._pool is not None:
                return self._pool
            pool_class = _AsyncConnectionPool
            assert pool_class is not None
            self._pool = pool_class(
                self._dsn,
                min_size=self._pool_min_size,
                max_size=self._pool_max_size,
                timeout=self._pool_timeout,
                max_idle=self._pool_max_idle,
                open=False,
                kwargs={
                    "autocommit": False,
                    "prepare_threshold": 0,
                    "row_factory": _DictRowFactory,
                },
            )
            await self._pool.open()
            return self._pool

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            pool = await self._get_pool()
            async with pool.connection() as conn:
                for raw_stmt in POSTGRES_PLUGIN_INSTALLATION_MIGRATION.strip().split(
                    ";"
                ):
                    stmt = raw_stmt.strip()
                    if stmt:
                        await conn.execute(stmt)
                await conn.commit()
            self._initialized = True

    async def set_plugin_enabled(
        self, plugin_name: str, *, workspace_id: str, enabled: bool
    ) -> None:
        """Store a workspace-scoped enabled flag in PostgreSQL."""
        await self._ensure_initialized()
        pool = await self._get_pool()
        async with pool.connection() as conn:
            await conn.execute(
                "INSERT INTO plugin_installations "
                "(plugin_name, workspace_id, enabled) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT(plugin_name, workspace_id) "
                "DO UPDATE SET enabled = EXCLUDED.enabled",
                (plugin_name, workspace_id, enabled),
            )
            await conn.commit()

    async def get_plugin_enabled(
        self, plugin_name: str, *, workspace_id: str
    ) -> bool | None:
        """Return the PostgreSQL-stored enabled flag for one plugin."""
        await self._ensure_initialized()
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT enabled FROM plugin_installations "
                    "WHERE plugin_name = %s AND workspace_id = %s",
                    (plugin_name, workspace_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                return bool(row["enabled"])

    async def list_plugin_states(
        self, *, workspace_id: str | None = None
    ) -> list[WorkspacePluginState]:
        """List all PostgreSQL workspace plugin states, optionally filtered."""
        await self._ensure_initialized()
        if workspace_id is not None:
            sql = (
                "SELECT plugin_name, workspace_id, enabled "
                "FROM plugin_installations WHERE workspace_id = %s"
            )
            params: tuple[Any, ...] = (workspace_id,)
        else:
            sql = "SELECT plugin_name, workspace_id, enabled FROM plugin_installations"
            params = ()
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
                rows = await cursor.fetchall()
        return [
            WorkspacePluginState(
                plugin_name=str(row["plugin_name"]),
                workspace_id=str(row["workspace_id"]),
                enabled=bool(row["enabled"]),
            )
            for row in rows
        ]


__all__ = [
    "InMemoryPluginInstallationStore",
    "POSTGRES_PLUGIN_INSTALLATION_MIGRATION",
    "PluginInstallationStore",
    "PostgresPluginInstallationStore",
    "SqlitePluginInstallationStore",
    "WorkspacePluginState",
]
