"""Persistence backends for Agentensor training checkpoints."""

from __future__ import annotations
import asyncio
import importlib
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from orcheo.agentensor.checkpoints import (
    AgentensorCheckpoint,
    AgentensorCheckpointNotFoundError,
    AgentensorCheckpointStore,
)


# Optional psycopg dependencies
_AsyncConnectionPool: Any | None
_DictRowFactory: Any | None

try:  # pragma: no cover - optional dependency
    _AsyncConnectionPool = importlib.import_module("psycopg_pool").AsyncConnectionPool
    _DictRowFactory = importlib.import_module("psycopg.rows").dict_row
except Exception:  # pragma: no cover - fallback when dependency missing
    _AsyncConnectionPool = None
    _DictRowFactory = None


POSTGRES_CHECKPOINT_MIGRATION = """
CREATE TABLE IF NOT EXISTS agentensor_checkpoints (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    workspace_id TEXT,
    config_version INTEGER NOT NULL,
    runnable_config JSONB NOT NULL,
    metrics JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    artifact_url TEXT NULL,
    is_best BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agentensor_checkpoints_workflow
    ON agentensor_checkpoints (workflow_id, config_version);
CREATE INDEX IF NOT EXISTS idx_agentensor_checkpoints_best
    ON agentensor_checkpoints (workflow_id, is_best);
CREATE INDEX IF NOT EXISTS idx_agentensor_checkpoints_workspace_id
    ON agentensor_checkpoints (workspace_id, workflow_id, config_version);
CREATE INDEX IF NOT EXISTS idx_agentensor_checkpoints_metrics
    ON agentensor_checkpoints USING GIN (metrics);
CREATE INDEX IF NOT EXISTS idx_agentensor_checkpoints_metadata
    ON agentensor_checkpoints USING GIN (metadata);
"""


class PostgresAgentensorCheckpointStore(AgentensorCheckpointStore):
    """PostgreSQL-backed checkpoint store for production deployments."""

    def __init__(
        self,
        dsn: str,
        *,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        pool_timeout: float = 30.0,
        pool_max_idle: float = 300.0,
    ) -> None:
        """Initialize the PostgreSQL checkpoint store."""
        if _AsyncConnectionPool is None or _DictRowFactory is None:
            msg = "PostgreSQL backend requires psycopg[binary,pool] to be installed."
            raise RuntimeError(msg)

        self._dsn = dsn
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool_timeout = pool_timeout
        self._pool_max_idle = pool_max_idle
        self._pool: Any | None = None
        self._lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def _get_pool(self) -> Any:
        """Return the connection pool, creating it if necessary."""
        if self._pool is not None:
            return self._pool

        async with self._init_lock:
            if self._pool is not None:
                return self._pool

            pool_class = _AsyncConnectionPool
            assert pool_class is not None  # mypy
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

        async with self._init_lock:
            if self._initialized:
                return

            async with self._connection() as conn:
                for raw_stmt in POSTGRES_CHECKPOINT_MIGRATION.strip().split(";"):
                    stmt = raw_stmt.strip()
                    if stmt:
                        await conn.execute(stmt)

            self._initialized = True

    async def record_checkpoint(
        self,
        *,
        workflow_id: str,
        runnable_config: Mapping[str, object],
        metrics: Mapping[str, object],
        metadata: Mapping[str, object] | None = None,
        artifact_url: str | None = None,
        is_best: bool = False,
        config_version: int | None = None,
        workspace_id: str | None = None,
    ) -> AgentensorCheckpoint:
        """Persist a checkpoint and return the stored record."""
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                version = await self._resolve_version(conn, workflow_id, config_version)
                checkpoint = AgentensorCheckpoint(
                    workflow_id=workflow_id,
                    workspace_id=workspace_id,
                    config_version=version,
                    runnable_config=dict(runnable_config),
                    metrics=dict(metrics),
                    metadata=dict(metadata or {}),
                    artifact_url=artifact_url,
                    is_best=is_best,
                )
                await conn.execute(
                    """
                    INSERT INTO agentensor_checkpoints (
                        id, workflow_id, workspace_id, config_version, runnable_config,
                        metrics, metadata, artifact_url, is_best, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        checkpoint.id,
                        checkpoint.workflow_id,
                        checkpoint.workspace_id,
                        checkpoint.config_version,
                        json.dumps(checkpoint.runnable_config),
                        json.dumps(checkpoint.metrics),
                        json.dumps(checkpoint.metadata),
                        checkpoint.artifact_url,
                        checkpoint.is_best,
                        checkpoint.created_at,
                    ),
                )
                if is_best:
                    await conn.execute(
                        """
                        UPDATE agentensor_checkpoints
                           SET is_best = FALSE
                         WHERE workflow_id = %s
                           AND id != %s
                        """,
                        (workflow_id, checkpoint.id),
                    )
                return checkpoint

    async def list_checkpoints(
        self,
        workflow_id: str,
        *,
        limit: int | None = None,
        workspace_id: str | None = None,
    ) -> list[AgentensorCheckpoint]:
        """Return checkpoints for the workflow ordered newest-first."""
        await self._ensure_initialized()
        query = """
            SELECT id, workflow_id, workspace_id, config_version, runnable_config,
                   metrics, metadata, artifact_url, is_best, created_at
              FROM agentensor_checkpoints
             WHERE workflow_id = %s
        """
        params: list[object] = [workflow_id]
        if workspace_id is not None:
            query += " AND workspace_id = %s"
            params.append(workspace_id)
        query += " ORDER BY config_version DESC"
        if limit is not None:
            query += " LIMIT %s"
            params.append(limit)
        async with self._connection() as conn:
            cursor = await conn.execute(query, tuple(params))
            rows = await cursor.fetchall()
        return [self._row_to_checkpoint(row) for row in rows]

    async def get_checkpoint(self, checkpoint_id: str) -> AgentensorCheckpoint:
        """Return the checkpoint by identifier or raise when missing."""
        await self._ensure_initialized()
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, workflow_id, workspace_id, config_version, runnable_config,
                       metrics, metadata, artifact_url, is_best, created_at
                  FROM agentensor_checkpoints
                 WHERE id = %s
                """,
                (checkpoint_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            msg = f"Checkpoint {checkpoint_id!r} not found."
            raise AgentensorCheckpointNotFoundError(msg)
        return self._row_to_checkpoint(row)

    async def latest_checkpoint(
        self,
        workflow_id: str,
    ) -> AgentensorCheckpoint | None:
        """Return the most recent checkpoint for the workflow if present."""
        checkpoints = await self.list_checkpoints(workflow_id, limit=1)
        return checkpoints[0] if checkpoints else None

    async def _resolve_version(
        self,
        conn: Any,
        workflow_id: str,
        provided_version: int | None,
    ) -> int:
        """Resolve the next config version for the workflow."""
        if provided_version is not None:
            return provided_version
        cursor = await conn.execute(
            """
            SELECT COALESCE(MAX(config_version), 0) AS max_version
              FROM agentensor_checkpoints
             WHERE workflow_id = %s
            """,
            (workflow_id,),
        )
        row = await cursor.fetchone()
        max_version = row["max_version"] if row else 0
        return int(max_version) + 1

    @staticmethod
    def _row_to_checkpoint(row: dict[str, Any]) -> AgentensorCheckpoint:
        """Convert a PostgreSQL row into an AgentensorCheckpoint instance."""
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        runnable_config = row["runnable_config"]
        if isinstance(runnable_config, str):
            runnable_config = json.loads(runnable_config)

        metrics = row["metrics"]
        if isinstance(metrics, str):
            metrics = json.loads(metrics)

        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        return AgentensorCheckpoint(
            id=row["id"],
            workflow_id=row["workflow_id"],
            workspace_id=row.get("workspace_id"),
            config_version=int(row["config_version"]),
            runnable_config=runnable_config,
            metrics=metrics,
            metadata=metadata,
            artifact_url=row["artifact_url"],
            is_best=bool(row["is_best"]),
            created_at=created_at,
        )

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


__all__ = [
    "AgentensorCheckpointNotFoundError",
    "POSTGRES_CHECKPOINT_MIGRATION",
    "PostgresAgentensorCheckpointStore",
]
