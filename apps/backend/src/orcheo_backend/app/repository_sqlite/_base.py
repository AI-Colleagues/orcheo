"""Core utilities shared by the SQLite workflow repository mixins."""

from __future__ import annotations
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
import aiosqlite
from orcheo.listeners import ListenerCursor, ListenerDedupeRecord, ListenerSubscription
from orcheo.models.workflow import (
    Workflow,
    WorkflowRun,
    WorkflowRunRemediation,
    WorkflowRunStatus,
    WorkflowVersion,
)
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.layer import TriggerLayer
from orcheo.triggers.retry import RetryPolicyConfig
from orcheo.triggers.webhook import WebhookTriggerConfig
from orcheo.vault.oauth import CredentialHealthError, OAuthCredentialService


logger = logging.getLogger(__name__)


def _parse_optional_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp if present."""
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _deserialize_legacy_workflow_payload(payload_json: str) -> Workflow:
    """Load workflow payloads while ignoring removed publish-token fields."""
    payload = json.loads(payload_json)
    payload.pop("publish_token_hash", None)
    payload.pop("publish_token_rotated_at", None)
    return Workflow.model_validate(payload)


class SqliteRepositoryBase:
    """Provide locking, connections, and trigger-layer hydration."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        credential_service: OAuthCredentialService | None = None,
    ) -> None:
        self._database_path = Path(database_path).expanduser()
        self._lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._initialized = False
        self._credential_service = credential_service
        self._trigger_layer = TriggerLayer(health_guard=credential_service)

    async def _ensure_workflow_health(
        self,
        workflow_id: UUID,
        *,
        actor: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        if self._credential_service is None:
            return
        report = await self._credential_service.ensure_workflow_health(
            workflow_id,
            actor=actor,
            workspace_id=workspace_id,
        )
        if not report.is_healthy:
            raise CredentialHealthError(report)

    def _release_cron_run(self, run_id: UUID) -> None:
        self._trigger_layer.release_cron_run(run_id)

    @staticmethod
    def _dump_model(
        model: Workflow | WorkflowVersion | WorkflowRun | WorkflowRunRemediation,
    ) -> str:
        return json.dumps(model.model_dump(mode="json"))

    @staticmethod
    def _dump_config(
        config: WebhookTriggerConfig | CronTriggerConfig | RetryPolicyConfig,
    ) -> str:
        return json.dumps(config.model_dump(mode="json"))

    def _sync_listener_subscriptions_locked(
        self,
        workflow_id: UUID,
        workflow_version_id: UUID,
        graph: dict[str, object],
        *,
        actor: str,
    ) -> None:
        """Synchronize listener subscriptions for the workflow version."""
        del workflow_id, workflow_version_id, graph, actor

    @staticmethod
    def _dump_listener_subscription(model: ListenerSubscription) -> str:
        return json.dumps(model.model_dump(mode="json"))

    @staticmethod
    def _dump_listener_cursor(model: ListenerCursor) -> str:
        return json.dumps(model.model_dump(mode="json"))

    @staticmethod
    def _dump_listener_dedupe(model: ListenerDedupeRecord) -> str:
        return json.dumps(model.model_dump(mode="json"))

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiosqlite.Connection]:
        conn = await aiosqlite.connect(str(self._database_path))
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
            await conn.commit()
        except Exception:  # pragma: no cover - defensive rollback
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            async with self._connection() as conn:
                await conn.executescript(
                    """
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS workflows (
                        id TEXT PRIMARY KEY,
                        handle TEXT,
                        is_archived INTEGER NOT NULL DEFAULT 0,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        workspace_id TEXT
                    );
                    CREATE TABLE IF NOT EXISTS workflow_versions (
                        id TEXT PRIMARY KEY,
                        workflow_id TEXT NOT NULL,
                        workspace_id TEXT,
                        version INTEGER NOT NULL,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(workflow_id, version)
                    );
                    CREATE INDEX IF NOT EXISTS idx_versions_workflow
                        ON workflow_versions(workflow_id);
                    CREATE TABLE IF NOT EXISTS workflow_runs (
                        id TEXT PRIMARY KEY,
                        workflow_id TEXT NOT NULL,
                        workflow_version_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        triggered_by TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        workspace_id TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_runs_workflow
                        ON workflow_runs(workflow_id);
                    CREATE INDEX IF NOT EXISTS idx_runs_version
                        ON workflow_runs(workflow_version_id);
                    CREATE TABLE IF NOT EXISTS workflow_run_remediations (
                        id TEXT PRIMARY KEY,
                        workflow_id TEXT NOT NULL,
                        workflow_version_id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        version_checksum TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_remediations_workflow
                        ON workflow_run_remediations(workflow_id);
                    CREATE INDEX IF NOT EXISTS idx_remediations_version
                        ON workflow_run_remediations(workflow_version_id);
                    CREATE INDEX IF NOT EXISTS idx_remediations_run
                        ON workflow_run_remediations(run_id);
                    CREATE INDEX IF NOT EXISTS idx_remediations_status
                        ON workflow_run_remediations(status);
                    CREATE INDEX IF NOT EXISTS idx_remediations_fingerprint
                        ON workflow_run_remediations(fingerprint);
                    CREATE TABLE IF NOT EXISTS webhook_triggers (
                        workflow_id TEXT PRIMARY KEY,
                        config TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS cron_triggers (
                        workflow_id TEXT PRIMARY KEY,
                        config TEXT NOT NULL,
                        last_dispatched_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS retry_policies (
                        workflow_id TEXT PRIMARY KEY,
                        config TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS listener_subscriptions (
                        id TEXT PRIMARY KEY,
                        workflow_id TEXT NOT NULL,
                        workflow_version_id TEXT NOT NULL,
                        node_name TEXT NOT NULL,
                        platform TEXT NOT NULL,
                        bot_identity_key TEXT NOT NULL,
                        status TEXT NOT NULL,
                        assigned_runtime TEXT,
                        lease_expires_at TEXT,
                        last_event_at TEXT,
                        last_error TEXT,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_listener_subscriptions_workflow
                        ON listener_subscriptions(workflow_id);
                    CREATE INDEX IF NOT EXISTS idx_listener_subscriptions_version
                        ON listener_subscriptions(workflow_version_id);
                    CREATE TABLE IF NOT EXISTS listener_cursors (
                        subscription_id TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS listener_dedupe (
                        subscription_id TEXT NOT NULL,
                        dedupe_key TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        PRIMARY KEY (subscription_id, dedupe_key)
                    );
                    """
                )
                await self._ensure_cron_schema_migrations(conn)
                await self._ensure_workflow_schema_migrations(conn)
                await self._ensure_workflow_versions_schema_migrations(conn)

            await self._hydrate_trigger_state()
            self._initialized = True

    async def _ensure_cron_schema_migrations(self, conn: aiosqlite.Connection) -> None:
        """Add missing columns to cron_triggers table for existing databases."""
        cursor = await conn.execute("PRAGMA table_info(cron_triggers)")
        rows = await cursor.fetchall()
        existing_columns = {row["name"] for row in rows}
        if "last_dispatched_at" not in existing_columns:
            await conn.execute(
                "ALTER TABLE cron_triggers ADD COLUMN last_dispatched_at TEXT"
            )

    async def _ensure_workflow_schema_migrations(
        self, conn: aiosqlite.Connection
    ) -> None:
        """Add mirrored workflow columns and backfill them from payloads."""
        cursor = await conn.execute("PRAGMA table_info(workflows)")
        rows = await cursor.fetchall()
        existing_columns = {row["name"] for row in rows}
        if "handle" not in existing_columns:
            await conn.execute("ALTER TABLE workflows ADD COLUMN handle TEXT")
        if "is_archived" not in existing_columns:
            await conn.execute(
                "ALTER TABLE workflows "
                "ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0"
            )
        if "workspace_id" not in existing_columns:
            await conn.execute("ALTER TABLE workflows ADD COLUMN workspace_id TEXT")

        cursor = await conn.execute("SELECT id, payload FROM workflows")
        workflow_rows = await cursor.fetchall()
        for row in workflow_rows:
            workflow = _deserialize_legacy_workflow_payload(row["payload"])
            await conn.execute(
                """
                UPDATE workflows
                   SET handle = ?, is_archived = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    workflow.handle,
                    int(workflow.is_archived),
                    workflow.updated_at.isoformat(),
                    row["id"],
                ),
            )

        # Add workspace_id to workflow_runs if table exists and column is missing
        cursor = await conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
        )
        runs_table_exists = await cursor.fetchone() is not None
        if runs_table_exists:
            cursor = await conn.execute("PRAGMA table_info(workflow_runs)")
            runs_rows = await cursor.fetchall()
            runs_columns = {row["name"] for row in runs_rows}
            if "workspace_id" not in runs_columns:
                await conn.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN workspace_id TEXT"
                )

        # Create indexes after columns are guaranteed to exist
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflows_handle ON workflows(handle)"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_workflows_active_handle"
            " ON workflows(handle)"
            " WHERE is_archived = 0 AND handle IS NOT NULL"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflows_workspace_id "
            "ON workflows(workspace_id)"
        )
        cursor = await conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
        )
        if await cursor.fetchone() is not None:
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_workspace_id "
                "ON workflow_runs(workspace_id)"
            )

    async def _ensure_workflow_versions_schema_migrations(
        self, conn: aiosqlite.Connection
    ) -> None:
        """Add workspace_id to workflow_versions when upgrading existing databases."""
        cursor = await conn.execute("PRAGMA table_info(workflow_versions)")
        rows = await cursor.fetchall()
        existing_columns = {row["name"] for row in rows}
        if "workspace_id" not in existing_columns:
            await conn.execute(
                "ALTER TABLE workflow_versions ADD COLUMN workspace_id TEXT"
            )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_versions_workspace_id "
            "ON workflow_versions(workspace_id)"
        )

    async def _hydrate_trigger_state(self) -> None:
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT workflow_id, config FROM retry_policies"
            )
            for row in await cursor.fetchall():
                workflow_id = UUID(row["workflow_id"])
                retry_config = RetryPolicyConfig.model_validate_json(row["config"])
                self._trigger_layer.configure_retry_policy(workflow_id, retry_config)

            cursor = await conn.execute(
                "SELECT workflow_id, config FROM webhook_triggers"
            )
            for row in await cursor.fetchall():
                workflow_id = UUID(row["workflow_id"])
                webhook_config = WebhookTriggerConfig.model_validate_json(row["config"])
                self._trigger_layer.configure_webhook(workflow_id, webhook_config)

            cursor = await conn.execute(
                "SELECT workflow_id, config, last_dispatched_at FROM cron_triggers"
            )
            for row in await cursor.fetchall():
                workflow_id = UUID(row["workflow_id"])
                cron_config = CronTriggerConfig.model_validate_json(row["config"])
                last_dispatched_at = _parse_optional_datetime(row["last_dispatched_at"])
                self._trigger_layer.configure_cron(
                    workflow_id, cron_config, last_dispatched_at=last_dispatched_at
                )

            cursor = await conn.execute(
                """
                SELECT id, workflow_id, triggered_by, status
                  FROM workflow_runs
                 WHERE status IN (?, ?, ?)
                """,
                (
                    WorkflowRunStatus.PENDING.value,
                    WorkflowRunStatus.RUNNING.value,
                    WorkflowRunStatus.FAILED.value,
                ),
            )
            for row in await cursor.fetchall():
                run_id = UUID(row["id"])
                workflow_id = UUID(row["workflow_id"])
                status = row["status"]
                self._trigger_layer.track_run(workflow_id, run_id)
                if (
                    row["triggered_by"] == "cron"
                    and status != WorkflowRunStatus.FAILED.value
                ):
                    try:
                        self._trigger_layer.register_cron_run(run_id)
                    except Exception:
                        logger.warning(
                            "Skipped cron overlap registration for run %s "
                            "(workflow %s): overlap conflict during hydration",
                            run_id,
                            workflow_id,
                        )

    async def _refresh_cron_triggers(self) -> None:
        """Refresh cron trigger configs to reflect the latest persisted state."""
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT workflow_id, config, last_dispatched_at FROM cron_triggers"
            )
            rows = await cursor.fetchall()

        desired: dict[UUID, tuple[CronTriggerConfig, datetime | None]] = {}
        for row in rows:
            workflow_id = UUID(row["workflow_id"])
            config = CronTriggerConfig.model_validate_json(row["config"])
            last_dispatched_at = _parse_optional_datetime(row["last_dispatched_at"])
            desired[workflow_id] = (config, last_dispatched_at)

        current_states = self._trigger_layer._cron_states  # noqa: SLF001
        current_ids = set(current_states)
        desired_ids = set(desired)

        for workflow_id in current_ids - desired_ids:
            self._trigger_layer.remove_cron_config(workflow_id)

        for workflow_id, (config, last_dispatched_at) in desired.items():
            state = current_states.get(workflow_id)
            if state is None:
                self._trigger_layer.configure_cron(
                    workflow_id, config, last_dispatched_at=last_dispatched_at
                )
                continue
            if (
                state.config.model_dump(mode="json") != config.model_dump(mode="json")
                or state.last_dispatched_at != last_dispatched_at
            ):
                self._trigger_layer.configure_cron(
                    workflow_id, config, last_dispatched_at=last_dispatched_at
                )


__all__ = ["SqliteRepositoryBase", "logger"]
