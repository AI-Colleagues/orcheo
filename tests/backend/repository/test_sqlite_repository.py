from __future__ import annotations
import asyncio
import json
import pathlib
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4
import aiosqlite
import pytest
from orcheo.models.workflow import Workflow, WorkflowDraftAccess
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.manual import ManualDispatchItem, ManualDispatchRequest
from orcheo.triggers.retry import RetryPolicyConfig
from orcheo.triggers.webhook import WebhookTriggerConfig
from orcheo_backend.app.repository import (
    SqliteWorkflowRepository,
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
    WorkflowPublishStateError,
)
from orcheo_backend.app.repository_sqlite._base import (
    SqliteRepositoryBase,
    _parse_optional_datetime,
)


@pytest.mark.asyncio()
async def test_sqlite_repository_hydrates_failed_run_retry_state(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Failed runs maintain retry state after the SQLite repo restarts."""

    db_path = tmp_path_factory.mktemp("repo") / "workflow.sqlite"
    repository = SqliteWorkflowRepository(db_path)
    restart_repository: SqliteWorkflowRepository | None = None

    try:
        workflow = await repository.create_workflow(
            name="Retryable",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )
        await repository.create_version(
            workflow.id,
            graph={},
            metadata={},
            notes=None,
            created_by="author",
        )
        await repository.configure_retry_policy(
            workflow.id,
            RetryPolicyConfig(
                max_attempts=2,
                initial_delay_seconds=1.0,
                jitter_factor=0.0,
            ),
        )
        await repository.configure_webhook_trigger(
            workflow.id,
            WebhookTriggerConfig(allowed_methods={"POST"}),
        )
        await repository.configure_cron_trigger(
            workflow.id,
            CronTriggerConfig(expression="0 9 * * *", timezone="UTC"),
        )

        (cron_run,) = await repository.dispatch_due_cron_runs(
            now=datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
        )

        (run,) = await repository.dispatch_manual_runs(
            ManualDispatchRequest(
                workflow_id=workflow.id,
                actor="tester",
                runs=[ManualDispatchItem()],
            )
        )
        await repository.mark_run_failed(run.id, actor="worker", error="boom")

        restart_repository = SqliteWorkflowRepository(db_path)
        decision = await restart_repository.schedule_retry_for_run(run.id)
        assert decision is not None
        assert decision.retry_number == 1
        webhook_config = await restart_repository.get_webhook_trigger_config(
            workflow.id
        )
        assert "POST" in webhook_config.allowed_methods
        cron_config = await restart_repository.get_cron_trigger_config(workflow.id)
        assert cron_config.expression == "0 9 * * *"
        assert cron_run.id in restart_repository._trigger_layer._cron_run_index  # noqa: SLF001
    finally:
        if restart_repository is not None:
            await restart_repository.reset()
        await repository.reset()


def test_parse_optional_datetime_adds_utc_timezone() -> None:
    """Naive timestamps returned from SQLite should be converted to UTC."""

    iso_naive = "2025-01-01T00:00:00"
    parsed = _parse_optional_datetime(iso_naive)
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.isoformat().endswith("+00:00")


@pytest.mark.asyncio()
async def test_sqlite_ensure_cron_schema_migrations_adds_missing_column(
    tmp_path: pathlib.Path,
) -> None:
    """The migration adds the `last_dispatched_at` column when absent."""

    db_path = tmp_path / "legacy.sqlite"
    repo_base = SqliteRepositoryBase(db_path)

    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """
            CREATE TABLE cron_triggers (
                workflow_id TEXT PRIMARY KEY,
                config TEXT NOT NULL
            );
            """
        )
        await conn.commit()
        await repo_base._ensure_cron_schema_migrations(conn)
        cursor = await conn.execute("PRAGMA table_info(cron_triggers)")
        rows = await cursor.fetchall()

    assert any(row["name"] == "last_dispatched_at" for row in rows)


@pytest.mark.asyncio()
async def test_sqlite_ensure_workflow_schema_migrations_backfills_columns(
    tmp_path: pathlib.Path,
) -> None:
    """The workflow migration adds mirrored columns and backfills them."""

    db_path = tmp_path / "legacy-workflows.sqlite"
    repo_base = SqliteRepositoryBase(db_path)
    workflow = Workflow(name="Legacy Flow", handle="legacy-flow")
    workflow.is_archived = True

    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """
            CREATE TABLE workflows (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        await conn.execute(
            """
            INSERT INTO workflows (id, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                str(workflow.id),
                workflow.model_dump_json(),
                workflow.created_at.isoformat(),
                workflow.updated_at.isoformat(),
            ),
        )
        await conn.commit()

        await repo_base._ensure_workflow_schema_migrations(conn)

        cursor = await conn.execute("PRAGMA table_info(workflows)")
        columns = {row["name"] for row in await cursor.fetchall()}
        cursor = await conn.execute(
            "SELECT handle, is_archived FROM workflows WHERE id = ?",
            (str(workflow.id),),
        )
        row = await cursor.fetchone()

    assert "handle" in columns
    assert "is_archived" in columns
    assert row is not None
    assert row["handle"] == "legacy-flow"
    assert row["is_archived"] == 1


@pytest.mark.asyncio()
async def test_sqlite_ensure_initialized_adds_versions_workspace_index(
    tmp_path: pathlib.Path,
) -> None:
    """Legacy SQLite databases should migrate workflow_versions on startup."""

    db_path = tmp_path / "legacy-versions.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE workflow_versions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(workflow_id, version)
            )
            """
        )
        conn.commit()

    repository = SqliteWorkflowRepository(db_path)
    await repository._ensure_initialized()  # noqa: SLF001

    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute("PRAGMA table_info(workflow_versions)")
        columns = {row["name"] for row in await cursor.fetchall()}
        cursor = await conn.execute("PRAGMA index_list(workflow_versions)")
        indexes = {row["name"] for row in await cursor.fetchall()}

    assert "workspace_id" in columns
    assert "idx_versions_workspace_id" in indexes


@pytest.mark.asyncio()
async def test_sqlite_workflow_schema_migration_accepts_legacy_publish_fields(
    tmp_path: pathlib.Path,
) -> None:
    """The workflow migration tolerates removed publish-token fields."""

    db_path = tmp_path / "legacy-publish-fields.sqlite"
    repo_base = SqliteRepositoryBase(db_path)
    workflow = Workflow(name="Legacy Published Flow", handle="legacy-published-flow")
    payload = workflow.model_dump(mode="json")
    payload["publish_token_hash"] = "old-hash"
    payload["publish_token_rotated_at"] = None

    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """
            CREATE TABLE workflows (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        await conn.execute(
            """
            INSERT INTO workflows (id, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                str(workflow.id),
                json.dumps(payload),
                workflow.created_at.isoformat(),
                workflow.updated_at.isoformat(),
            ),
        )
        await conn.commit()

        await repo_base._ensure_workflow_schema_migrations(conn)

        cursor = await conn.execute(
            "SELECT handle, is_archived FROM workflows WHERE id = ?",
            (str(workflow.id),),
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row["handle"] == "legacy-published-flow"
    assert row["is_archived"] == 0


@pytest.mark.asyncio()
async def test_sqlite_dispatch_due_cron_runs_persists_last_dispatched(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Cron dispatch stores the latest dispatch time for recovery."""

    db_path = tmp_path_factory.mktemp("cron") / "dispatch.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await repository.create_workflow(
            name="Persistence Flow",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="owner",
        )
        await repository.create_version(
            workflow.id,
            graph={},
            metadata={},
            notes=None,
            created_by="owner",
        )
        await repository.configure_cron_trigger(
            workflow.id,
            CronTriggerConfig(expression="0 9 * * *", timezone="UTC"),
        )

        now = datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
        runs = await repository.dispatch_due_cron_runs(now=now)
        assert runs

        async with repository._connection() as conn:
            cursor = await conn.execute(
                "SELECT last_dispatched_at FROM cron_triggers WHERE workflow_id = ?",
                (str(workflow.id),),
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row["last_dispatched_at"] == now.isoformat()
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_cron_dispatch_reflects_external_unschedule(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Cron dispatch refreshes configs when another process unschedules."""
    db_path = tmp_path_factory.mktemp("repo") / "workflow.sqlite"
    api_repository = SqliteWorkflowRepository(db_path)
    worker_repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await api_repository.create_workflow(
            name="Scheduled",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )
        await api_repository.create_version(
            workflow.id,
            graph={},
            metadata={},
            notes=None,
            created_by="author",
        )
        await api_repository.configure_cron_trigger(
            workflow.id,
            CronTriggerConfig(
                expression="* * * * *", timezone="UTC", allow_overlapping=True
            ),
        )

        first_runs = await worker_repository.dispatch_due_cron_runs(
            now=datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
        )
        assert len(first_runs) == 1

        await api_repository.delete_cron_trigger(workflow.id)

        follow_up = await worker_repository.dispatch_due_cron_runs(
            now=datetime(2025, 1, 1, 9, 1, tzinfo=UTC)
        )
        assert follow_up == []
    finally:
        await worker_repository.reset()
        await api_repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_refresh_cron_triggers_hydrates_missing_state(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Refresh loads cron configs that were added by other processes."""

    db_path = tmp_path_factory.mktemp("repo") / "refresh.sqlite"
    api_repository = SqliteWorkflowRepository(db_path)
    worker_repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await api_repository.create_workflow(
            name="Refresh Cron",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )
        await api_repository.configure_cron_trigger(
            workflow.id,
            CronTriggerConfig(expression="0 0 * * *", timezone="UTC"),
        )

        assert workflow.id not in worker_repository._trigger_layer._cron_states

        await worker_repository._refresh_cron_triggers()
        assert workflow.id in worker_repository._trigger_layer._cron_states
        assert (
            worker_repository._trigger_layer._cron_states[workflow.id].config.expression
            == "0 0 * * *"
        )
    finally:
        await worker_repository.reset()
        await api_repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_refresh_cron_triggers_updates_changed_configs(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Refreshing cron configs updates state when the persisted config changes."""

    db_path = tmp_path_factory.mktemp("repo") / "refresh-change.sqlite"
    primary_repository = SqliteWorkflowRepository(db_path)
    api_repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await primary_repository.create_workflow(
            name="Refresh Change",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )
        await primary_repository.create_version(
            workflow.id,
            graph={},
            metadata={},
            notes=None,
            created_by="author",
        )

        await primary_repository.configure_cron_trigger(
            workflow.id,
            CronTriggerConfig(expression="0 0 * * *", timezone="UTC"),
        )

        await api_repository.configure_cron_trigger(
            workflow.id,
            CronTriggerConfig(expression="0 0 * * *", timezone="America/Los_Angeles"),
        )

        await primary_repository._refresh_cron_triggers()
        updated_state = primary_repository._trigger_layer._cron_states[workflow.id]
        assert updated_state.config.timezone == "America/Los_Angeles"
    finally:
        await primary_repository.reset()
        await api_repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_refresh_cron_triggers_updates_last_dispatched_at(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Refreshing cron configs updates last_dispatched_at when it changes."""

    db_path = tmp_path_factory.mktemp("repo") / "refresh-last-dispatched.sqlite"
    worker_repository = SqliteWorkflowRepository(db_path)
    api_repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await api_repository.create_workflow(
            name="Refresh Dispatch Cursor",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )
        await api_repository.create_version(
            workflow.id,
            graph={},
            metadata={},
            notes=None,
            created_by="author",
        )
        await api_repository.configure_cron_trigger(
            workflow.id,
            CronTriggerConfig(expression="0 0 * * *", timezone="UTC"),
        )

        await worker_repository._refresh_cron_triggers()
        state = worker_repository._trigger_layer._cron_states[workflow.id]
        assert state.last_dispatched_at is None

        last_dispatched_at = datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
        async with api_repository._connection() as conn:
            await conn.execute(
                """
                UPDATE cron_triggers
                   SET last_dispatched_at = ?
                 WHERE workflow_id = ?
                """,
                (last_dispatched_at.isoformat(), str(workflow.id)),
            )

        await worker_repository._refresh_cron_triggers()
        updated_state = worker_repository._trigger_layer._cron_states[workflow.id]
        assert updated_state.last_dispatched_at == last_dispatched_at
    finally:
        await worker_repository.reset()
        await api_repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_persistence_get_workflow_locked_not_found(
    tmp_path: pathlib.Path,
) -> None:
    """Locked workflow fetch raises when the record does not exist."""

    repository = SqliteWorkflowRepository(tmp_path / "workflow-missing.sqlite")

    try:
        await repository._ensure_initialized()  # noqa: SLF001

        with pytest.raises(WorkflowNotFoundError):
            await repository._get_workflow_locked(uuid4())  # noqa: SLF001
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_persistence_workflow_exists_locked_returns_false(
    tmp_path: pathlib.Path,
) -> None:
    """Locked existence checks return False for unknown workflow ids."""

    repository = SqliteWorkflowRepository(tmp_path / "workflow-exists.sqlite")

    try:
        await repository._ensure_initialized()  # noqa: SLF001

        exists = await repository._workflow_exists_locked(uuid4())  # noqa: SLF001

        assert exists is False
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_persistence_ensure_handle_available_locked_rejects_conflict(
    tmp_path: pathlib.Path,
) -> None:
    """Locked handle validation raises for duplicate active handles."""

    repository = SqliteWorkflowRepository(tmp_path / "workflow-handle.sqlite")

    try:
        await repository.create_workflow(
            name="Existing",
            handle="shared-handle",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="tester",
        )

        with pytest.raises(WorkflowHandleConflictError):
            await repository._ensure_handle_available_locked(  # noqa: SLF001
                "shared-handle",
                workflow_id=None,
                is_archived=False,
            )
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_persistence_ensure_handle_available_locked_allows_active_reuse_after_archive(
    tmp_path: pathlib.Path,
) -> None:
    """Archived workflows should not block new active workflows from reusing a handle."""

    repository = SqliteWorkflowRepository(tmp_path / "workflow-handle-reuse.sqlite")

    try:
        archived = await repository.create_workflow(
            name="Archived",
            handle="shared-handle",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="tester",
        )
        await repository.archive_workflow(archived.id, actor="tester")

        await repository._ensure_handle_available_locked(  # noqa: SLF001
            "shared-handle",
            workflow_id=None,
            is_archived=False,
        )
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_persistence_resolve_workflow_ref_locked_rejects_blank_ref(
    tmp_path: pathlib.Path,
) -> None:
    """Locked workflow-ref resolution rejects blank refs."""

    repository = SqliteWorkflowRepository(tmp_path / "workflow-ref.sqlite")

    try:
        await repository._ensure_initialized()  # noqa: SLF001

        with pytest.raises(WorkflowNotFoundError, match="workflow ref is empty"):
            await repository._resolve_workflow_ref_locked("   ")  # noqa: SLF001
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_handle_webhook_trigger_success(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Webhook triggers enqueue runs with normalized payloads."""

    db_path = tmp_path_factory.mktemp("repo") / "webhook.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await repository.create_workflow(
            name="Webhook Flow",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )
        await repository.create_version(
            workflow.id,
            graph={},
            metadata={},
            notes=None,
            created_by="author",
        )
        await repository.configure_webhook_trigger(
            workflow.id, WebhookTriggerConfig(allowed_methods={"POST"})
        )

        run = await repository.handle_webhook_trigger(
            workflow.id,
            method="POST",
            headers={"X-Test": "value"},
            query_params={"ok": "1"},
            payload={"payload": True},
            source_ip="127.0.0.1",
        )

        assert run.triggered_by == "webhook"
        stored = await repository.get_run(run.id)
        assert stored.input_payload["body"] == {"payload": True}
        assert stored.input_payload["query_params"] == {"ok": "1"}
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_ensure_initialized_concurrent_calls(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Concurrent initialization requests exit early once setup completes."""

    db_path = tmp_path_factory.mktemp("repo") / "init.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        await asyncio.gather(
            repository._ensure_initialized(),  # noqa: SLF001
            repository._ensure_initialized(),  # noqa: SLF001
        )
        assert repository._initialized is True  # noqa: SLF001
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_publish_revoke_workflow_lifecycle(
    tmp_path: pathlib.Path,
) -> None:
    """publish/revoke roundtrip persists workflow state."""

    db_path = tmp_path / "publish.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await repository.create_workflow(
            name="Lifecycle",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )
        published = await repository.publish_workflow(
            workflow.id,
            require_login=True,
            actor="author",
        )
        assert published.is_public is True
        assert published.require_login is True
        stored = await repository.get_workflow(workflow.id)
        assert stored.is_public is True

        revoked = await repository.revoke_publish(workflow.id, actor="auditor")
        assert revoked.is_public is False
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_archive_workflow_revokes_publish(
    tmp_path: pathlib.Path,
) -> None:
    """Archiving a public workflow revokes publish state."""

    db_path = tmp_path / "archive-revoke.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await repository.create_workflow(
            name="Archive",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )
        await repository.publish_workflow(
            workflow.id,
            require_login=True,
            actor="author",
        )

        archived = await repository.archive_workflow(workflow.id, actor="author")
        assert archived.is_archived is True
        assert archived.is_public is False
        assert archived.require_login is False

        stored = await repository.get_workflow(workflow.id)
        assert stored.is_archived is True
        assert stored.is_public is False
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_publish_workflow_translates_value_error(
    tmp_path: pathlib.Path,
) -> None:
    """Publishing an already public workflow reports WorkflowPublishStateError."""

    db_path = tmp_path / "publish-conflict.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await repository.create_workflow(
            name="Conflict",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )
        await repository.publish_workflow(
            workflow.id,
            require_login=False,
            actor="author",
        )

        with pytest.raises(WorkflowPublishStateError):
            await repository.publish_workflow(
                workflow.id,
                require_login=False,
                actor="author",
            )
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_publish_archived_workflow_raises_not_found(
    tmp_path: pathlib.Path,
) -> None:
    """Publishing an archived workflow raises WorkflowNotFoundError."""

    db_path = tmp_path / "publish-archived.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await repository.create_workflow(
            name="Archived Publish",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )
        await repository.archive_workflow(workflow.id, actor="author")

        with pytest.raises(WorkflowNotFoundError):
            await repository.publish_workflow(
                workflow.id,
                require_login=False,
                actor="author",
            )
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_revoke_requires_published_state(
    tmp_path: pathlib.Path,
) -> None:
    """Revoke propagates WorkflowPublishStateError when unpublished."""

    db_path = tmp_path / "publish-invalid.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await repository.create_workflow(
            name="Rotate Revoke",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )

        with pytest.raises(WorkflowPublishStateError):
            await repository.revoke_publish(workflow.id, actor="author")
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_revoke_archived_workflow_raises_not_found(
    tmp_path: pathlib.Path,
) -> None:
    """Revoking publish on an archived workflow raises WorkflowNotFoundError."""

    db_path = tmp_path / "revoke-archived.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await repository.create_workflow(
            name="Archived Revoke",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )
        await repository.archive_workflow(workflow.id, actor="author")

        with pytest.raises(WorkflowNotFoundError):
            await repository.revoke_publish(workflow.id, actor="author")
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_hydrate_cron_overlap_logs_warning(
    tmp_path_factory: pytest.TempPathFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When two non-failed cron runs exist with allow_overlapping=False the second
    register_cron_run call raises and the warning is logged during hydration."""
    import json
    import logging
    from uuid import uuid4

    db_path = tmp_path_factory.mktemp("repo") / "overlap-hydrate.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await repository.create_workflow(
            name="Overlap Hydrate",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )
        version = await repository.create_version(
            workflow.id,
            graph={},
            metadata={},
            notes=None,
            created_by="author",
        )
        await repository.configure_cron_trigger(
            workflow.id,
            CronTriggerConfig(expression="0 9 * * *", timezone="UTC"),
        )

        now = datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
        now_str = now.isoformat()
        run_id_1 = uuid4()
        run_id_2 = uuid4()

        # Insert two pending cron-triggered runs directly to bypass dispatch logic
        async with repository._connection() as conn:
            for run_id in (run_id_1, run_id_2):
                await conn.execute(
                    """
                    INSERT INTO workflow_runs
                        (id, workflow_id, workflow_version_id, status,
                         triggered_by, payload, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(run_id),
                        str(workflow.id),
                        str(version.id),
                        "pending",
                        "cron",
                        json.dumps({}),
                        now_str,
                        now_str,
                    ),
                )

        # Create a fresh repository on the same DB so hydration runs from scratch
        fresh_repository = SqliteWorkflowRepository(db_path)
        try:
            with caplog.at_level(logging.WARNING):
                await fresh_repository._ensure_initialized()

            assert any(
                "Skipped cron overlap registration" in record.message
                for record in caplog.records
            )
        finally:
            await fresh_repository.reset()
    finally:
        await repository.reset()


def test_sqlite_sync_listener_subscriptions_locked_noop(
    tmp_path: pathlib.Path,
) -> None:
    """_sync_listener_subscriptions_locked on the base class is a no-op
    del statement."""
    from uuid import uuid4
    from orcheo_backend.app.repository_sqlite._base import SqliteRepositoryBase

    repo = SqliteRepositoryBase(tmp_path / "noop.sqlite")
    # Calling the base no-op should not raise and should execute the del statement.
    repo._sync_listener_subscriptions_locked(  # noqa: SLF001
        uuid4(), uuid4(), {}, actor="author"
    )


@pytest.mark.asyncio()
async def test_sqlite_disable_listener_subscriptions_locked_without_conn(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """_disable_listener_subscriptions_locked creates its own connection when
    conn=None."""
    db_path = tmp_path_factory.mktemp("repo") / "disable.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await repository.create_workflow(
            name="Disable No Conn",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )
        await repository.create_version(
            workflow.id,
            graph={
                "nodes": [],
                "edges": [],
                "index": {
                    "listeners": [
                        {
                            "node_name": "tg",
                            "platform": "telegram",
                            "token": "[[tok]]",
                        }
                    ]
                },
            },
            metadata={},
            notes=None,
            created_by="author",
        )
        subscriptions = await repository.list_listener_subscriptions(
            workflow_id=workflow.id
        )
        assert len(subscriptions) == 1
        assert subscriptions[0].status.value == "active"

        # Call _disable_listener_subscriptions_locked directly without conn
        await repository._disable_listener_subscriptions_locked(  # noqa: SLF001
            workflow.id, actor="admin"
        )

        refreshed = await repository.list_listener_subscriptions(
            workflow_id=workflow.id
        )
        assert all(s.status.value == "disabled" for s in refreshed)
    finally:
        await repository.reset()
