"""Per-workspace plugin install/enable state stores."""

from __future__ import annotations
import asyncio
import importlib
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


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
    "POSTGRES_PLUGIN_INSTALLATION_MIGRATION",
    "PluginInstallationStore",
    "PostgresPluginInstallationStore",
    "WorkspacePluginState",
]
