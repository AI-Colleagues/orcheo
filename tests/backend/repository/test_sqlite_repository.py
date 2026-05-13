from __future__ import annotations
import asyncio
import json
import pathlib
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4
import aiosqlite
import pytest
from orcheo.models.workflow import Workflow, WorkflowDraftAccess, WorkflowVersion
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.manual import ManualDispatchItem, ManualDispatchRequest
from orcheo.triggers.retry import RetryPolicyConfig
from orcheo.triggers.webhook import WebhookTriggerConfig
from orcheo_backend.app.errors import WorkspaceQuotaExceededError
from orcheo_backend.app.repository import (
    SqliteWorkflowRepository,
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
    WorkflowPublishStateError,
    WorkflowRunNotFoundError,
)
from orcheo_backend.app.repository_sqlite import _persistence as sqlite_persistence
from orcheo_backend.app.repository_sqlite import _triggers as sqlite_triggers
from orcheo_backend.app.repository_sqlite._base import (
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


@pytest.mark.asyncio()
async def test_sqlite_dispatch_manual_runs_skips_quota_exceeded(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Manual dispatch skips runs when the workspace quota is exceeded."""

    db_path = tmp_path_factory.mktemp("manual-quota") / "manual.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await repository.create_workflow(
            name="Quota Manual",
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

        async def _raise_quota(*_: object, **__: object):
            raise WorkspaceQuotaExceededError("quota", code="workspace.quota.runs")

        repository._create_run_locked = _raise_quota  # type: ignore[method-assign]

        runs = await repository.dispatch_manual_runs(
            ManualDispatchRequest(
                workflow_id=workflow.id,
                runs=[ManualDispatchItem()],
            )
        )

        assert runs == []
    finally:
        await repository.reset()


def test_parse_optional_datetime_adds_utc_timezone() -> None:
    """Naive timestamps returned from SQLite should be converted to UTC."""

    iso_naive = "2025-01-01T00:00:00"
    parsed = _parse_optional_datetime(iso_naive)
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.isoformat().endswith("+00:00")


def test_sqlite_enqueue_run_logs_when_enqueue_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Enqueue failures are logged and swallowed."""
    import importlib

    importlib.reload(sqlite_triggers)

    class _Run:
        id = uuid4()

        @property
        def workspace_id(self) -> str | None:
            raise RuntimeError("workspace lookup failed")

    run = _Run()

    with caplog.at_level(
        "WARNING", logger="orcheo_backend.app.repository_sqlite._base"
    ):
        assert sqlite_triggers._enqueue_run_for_execution(run) is None


@pytest.mark.asyncio()
async def test_sqlite_ensure_cron_schema_migrations_adds_missing_column(
    tmp_path: pathlib.Path,
) -> None:
    """The migration adds the `last_dispatched_at` column when absent."""

    db_path = tmp_path / "legacy.sqlite"
    repo_base = SqliteWorkflowRepository(db_path)

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
    repo_base = SqliteWorkflowRepository(db_path)
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


@pytest.mark.asyncio()
async def test_sqlite_ensure_workflow_schema_migrations_adds_run_workspace_column(
    tmp_path: pathlib.Path,
) -> None:
    """The migration adds workspace_id to workflow_runs when it is missing."""

    db_path = tmp_path / "legacy-run-workspace.sqlite"
    repo_base = SqliteWorkflowRepository(db_path)

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
            CREATE TABLE workflow_runs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                workflow_version_id TEXT NOT NULL,
                status TEXT NOT NULL,
                triggered_by TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        await conn.commit()
        await repo_base._ensure_workflow_schema_migrations(conn)
        cursor = await conn.execute("PRAGMA table_info(workflow_runs)")
        rows = await cursor.fetchall()

    assert any(row["name"] == "workspace_id" for row in rows)


@pytest.mark.asyncio()
async def test_sqlite_ensure_workflow_schema_migrations_keeps_existing_run_workspace_id(
    tmp_path: pathlib.Path,
) -> None:
    """Existing workflow_runs.workspace_id columns should not be re-added."""

    db_path = tmp_path / "legacy-workflow-runs.sqlite"
    repo_base = SqliteWorkflowRepository(db_path)

    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """
            CREATE TABLE workflows (
                id TEXT PRIMARY KEY,
                handle TEXT,
                is_archived INTEGER NOT NULL DEFAULT 0,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                workspace_id TEXT
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE workflow_runs (
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
            """
        )
        await conn.commit()
        await repo_base._ensure_workflow_schema_migrations(conn)
        cursor = await conn.execute("PRAGMA table_info(workflow_runs)")
        columns = {row["name"] for row in await cursor.fetchall()}

    assert "workspace_id" in columns


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
async def test_sqlite_ensure_workflow_versions_schema_migrations_keeps_existing_workspace_id(
    tmp_path: pathlib.Path,
) -> None:
    """Existing workflow_versions.workspace_id columns should not be re-added."""

    db_path = tmp_path / "legacy-version-workspace.sqlite"
    repo_base = SqliteWorkflowRepository(db_path)

    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """
            CREATE TABLE workflow_versions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                workspace_id TEXT,
                version INTEGER NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(workflow_id, version)
            );
            """
        )
        await conn.commit()
        await repo_base._ensure_workflow_versions_schema_migrations(conn)
        cursor = await conn.execute("PRAGMA table_info(workflow_versions)")
        columns = {row["name"] for row in await cursor.fetchall()}

    assert "workspace_id" in columns


@pytest.mark.asyncio()
async def test_sqlite_workflow_schema_migration_accepts_legacy_publish_fields(
    tmp_path: pathlib.Path,
) -> None:
    """The workflow migration tolerates removed publish-token fields."""

    db_path = tmp_path / "legacy-publish-fields.sqlite"
    repo_base = SqliteWorkflowRepository(db_path)
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
async def test_sqlite_deserialize_workflow_uses_workspace_id(
    tmp_path: pathlib.Path,
) -> None:
    """Workflow payloads should use `workspace_id`."""
    db_path = tmp_path / "workspace-id.sqlite"
    workflow = Workflow(name="Legacy Workspace Flow", handle="legacy-workspace-flow")
    payload = workflow.model_dump(mode="json")
    payload["workspace_id"] = "workspace-a"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE workflows (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
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
        conn.commit()

    repository = SqliteWorkflowRepository(db_path)
    try:
        workflows = await repository.list_workflows()
    finally:
        await repository.reset()

    assert len(workflows) == 1
    assert workflows[0].id == workflow.id
    assert workflows[0].workspace_id == "workspace-a"


@pytest.mark.asyncio()
async def test_sqlite_deserialize_workflow_explicit_workspace_id(
    tmp_path: pathlib.Path,
) -> None:
    """The helper should inject an explicit workspace_id into workflow payloads."""

    db_path = tmp_path / "workspace-id-explicit.sqlite"
    repo_base = SqliteWorkflowRepository(db_path)
    workflow = Workflow(
        name="Explicit Workspace Flow", handle="explicit-workspace-flow"
    )
    payload = workflow.model_dump(mode="json")

    result = repo_base._deserialize_workflow(json.dumps(payload), workspace_id="ws-a")

    assert result.workspace_id == "ws-a"


@pytest.mark.asyncio()
async def test_sqlite_deserialize_workflow_version_explicit_workspace_id(
    tmp_path: pathlib.Path,
) -> None:
    """The helper should inject an explicit workspace_id into version payloads."""

    db_path = tmp_path / "workspace-version-explicit.sqlite"
    repo_base = SqliteWorkflowRepository(db_path)
    workflow = Workflow(name="Explicit Version Flow", handle="explicit-version-flow")
    version = WorkflowVersion(workflow_id=workflow.id, version=1, created_by="author")
    payload = version.model_dump(mode="json")

    result = repo_base._deserialize_workflow_version(
        json.dumps(payload), workspace_id="ws-b"
    )

    assert result.workspace_id == "ws-b"


@pytest.mark.asyncio()
async def test_sqlite_workflow_workspace_scoping_and_update_branches(
    tmp_path: pathlib.Path,
) -> None:
    """Workspace filters and workflow updates should exercise all branch variants."""

    db_path = tmp_path / "workflow-scope-update.sqlite"
    repository = SqliteWorkflowRepository(db_path)
    workspace_id = str(uuid4())

    try:
        scoped = await repository.create_workflow(
            name="Scoped",
            slug=None,
            description="Scoped workflow",
            tags=["a"],
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
            workspace_id=workspace_id,
        )
        unscoped = await repository.create_workflow(
            name="Unscoped",
            slug=None,
            description="Legacy workflow",
            tags=["b"],
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
        )

        workflows = await repository.list_workflows(workspace_id=workspace_id)
        assert [workflow.id for workflow in workflows] == [scoped.id]
        assert await repository.get_workflow_workspace_id(scoped.id) == workspace_id
        assert await repository.get_workflow_workspace_id(unscoped.id) is None
        assert (
            await repository.get_workflow(unscoped.id, workspace_id=workspace_id)
        ).id == unscoped.id
        assert (
            await repository.resolve_workflow_ref(
                str(unscoped.id), workspace_id=workspace_id
            )
            == unscoped.id
        )

        await repository.publish_workflow(
            scoped.id,
            require_login=True,
            actor="author",
        )
        updated = await repository.update_workflow(
            scoped.id,
            name="Scoped Renamed",
            handle="scoped-renamed",
            description="Updated description",
            tags=["x", "y"],
            chatkit_start_screen_prompts=None,
            chatkit_supported_models=None,
            clear_chatkit_start_screen_prompts=False,
            clear_chatkit_supported_models=False,
            draft_access=WorkflowDraftAccess.AUTHENTICATED,
            is_archived=True,
            actor="editor",
        )

        assert updated.name == "Scoped Renamed"
        assert updated.handle == "scoped-renamed"
        assert updated.description == "Updated description"
        assert updated.tags == ["x", "y"]
        assert updated.draft_access == WorkflowDraftAccess.AUTHENTICATED
        assert updated.is_archived is True
        assert updated.is_public is False
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_create_run_locked_releases_workspace_slot_on_error(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run slot reservations are released if SQLite persistence fails."""

    db_path = tmp_path / "sqlite-run-slot.sqlite"
    repository = SqliteWorkflowRepository(db_path)
    workflow = await repository.create_workflow(
        name="Slot Flow",
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
    workspace_id = str(uuid4())
    reserve_calls: list[tuple[str, int]] = []
    release_calls: list[str] = []

    class _WorkspaceRepo:
        def get_workspace(self, workspace_uuid: UUID) -> SimpleNamespace:
            assert str(workspace_uuid) == workspace_id
            return SimpleNamespace(
                quotas=SimpleNamespace(max_concurrent_runs=2),
            )

    class _Governance:
        def reserve_run_slot(self, workspace: str, *, limit: int) -> None:
            reserve_calls.append((workspace, limit))

        def release_run_slot(self, workspace: str) -> None:
            release_calls.append(workspace)

    monkeypatch.setattr(
        sqlite_persistence,
        "get_workspace_repository",
        lambda: _WorkspaceRepo(),
    )
    monkeypatch.setattr(
        sqlite_persistence,
        "get_workspace_governance",
        lambda: _Governance(),
    )

    def _boom(*_: object, **__: object) -> None:
        raise RuntimeError("track failed")

    repository._trigger_layer.track_run = _boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="track failed"):
        await repository._create_run_locked(
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            triggered_by="manual",
            input_payload={},
            actor="author",
            workspace_id=workspace_id,
        )

    assert reserve_calls == [(workspace_id, 2)]
    assert release_calls == [workspace_id]


@pytest.mark.asyncio()
async def test_sqlite_create_run_locked_without_workspace_slot_does_not_release(
    tmp_path: pathlib.Path,
) -> None:
    """Failures without a workspace_id should not try to release a run slot."""

    db_path = tmp_path / "sqlite-run-slot-none.sqlite"
    repository = SqliteWorkflowRepository(db_path)
    workflow = await repository.create_workflow(
        name="Slot Flow",
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

    def _boom(*_: object, **__: object) -> None:
        raise RuntimeError("track failed")

    repository._trigger_layer.track_run = _boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="track failed"):
        await repository._create_run_locked(
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            triggered_by="manual",
            input_payload={},
            actor="author",
        )
    await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_get_workflow_locked_handles_missing_workspace_column(
    tmp_path: pathlib.Path,
) -> None:
    """Missing workspace_id columns are treated as unscoped workflows."""

    db_path = tmp_path / "workflow-missing-workspace-column.sqlite"
    workflow = Workflow(name="Missing Workspace", handle="missing-workspace")
    payload = workflow.model_dump_json()

    class _Cursor:
        async def fetchone(self) -> dict[str, str]:
            return {"payload": payload}

    class _Connection:
        async def execute(
            self, query: str, params: tuple[str, ...] | None = None
        ) -> _Cursor:
            del query, params
            return _Cursor()

    repo = SqliteWorkflowRepository(db_path)

    @asynccontextmanager
    async def _connection() -> Any:  # type: ignore[valid-type]
        yield _Connection()

    repo._connection = _connection  # type: ignore[method-assign]

    result = await repo._get_workflow_locked(workflow.id)  # noqa: SLF001

    assert result.workspace_id is None


@pytest.mark.asyncio()
async def test_sqlite_get_workflow_workspace_id_locked_handles_missing_row(
    tmp_path: pathlib.Path,
) -> None:
    """Missing workflow rows return no workspace_id."""

    db_path = tmp_path / "workflow-helper-none.sqlite"
    repo = SqliteWorkflowRepository(db_path)

    await repo._ensure_initialized()
    assert await repo._get_workflow_workspace_id_locked(uuid4()) is None  # noqa: SLF001


@pytest.mark.asyncio()
async def test_sqlite_get_workflow_workspace_id_locked_handles_missing_key(
    tmp_path: pathlib.Path,
) -> None:
    """Rows without a workspace_id column are treated as unscoped."""

    db_path = tmp_path / "workflow-helper-missing-key.sqlite"
    workflow = Workflow(name="Missing Key", handle="missing-key")

    class _Cursor:
        async def fetchone(self) -> dict[str, str]:
            return {"unexpected": "value"}

    class _Connection:
        async def execute(
            self, query: str, params: tuple[str, ...] | None = None
        ) -> _Cursor:
            del query, params
            return _Cursor()

    repo = SqliteWorkflowRepository(db_path)

    @asynccontextmanager
    async def _connection() -> Any:  # type: ignore[valid-type]
        yield _Connection()

    repo._connection = _connection  # type: ignore[method-assign]

    assert (
        await repo._get_workflow_workspace_id_locked(workflow.id)  # noqa: SLF001
    ) is None


@pytest.mark.asyncio()
async def test_sqlite_get_run_workspace_id_locked_handles_missing_column(
    tmp_path: pathlib.Path,
) -> None:
    """Missing workspace_id columns are treated as unscoped runs."""

    db_path = tmp_path / "run-missing-workspace-column.sqlite"
    run_id = uuid4()

    class _Cursor:
        async def fetchone(self) -> dict[str, str]:
            return {"unexpected": "value"}

    class _Connection:
        async def execute(
            self, query: str, params: tuple[str, ...] | None = None
        ) -> _Cursor:
            del query, params
            return _Cursor()

    repo = SqliteWorkflowRepository(db_path)

    @asynccontextmanager
    async def _connection() -> Any:  # type: ignore[valid-type]
        yield _Connection()

    repo._connection = _connection  # type: ignore[method-assign]

    result = await repo._get_run_workspace_id_locked(run_id)  # noqa: SLF001

    assert result is None


@pytest.mark.asyncio()
async def test_sqlite_get_latest_version_uses_workspace_id(
    tmp_path: pathlib.Path,
) -> None:
    """Workflow version payloads should use `workspace_id`."""

    db_path = tmp_path / "workspace-version-id.sqlite"
    workflow = Workflow(name="Legacy Version Flow", handle="legacy-version-flow")
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version=1,
        created_by="author",
    )
    payload = version.model_dump(mode="json")
    payload["workspace_id"] = "workspace-a"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE workflows (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE workflow_versions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                workspace_id TEXT,
                version INTEGER NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(workflow_id, version)
            );
            """
        )
        conn.execute(
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
        conn.execute(
            """
            INSERT INTO workflow_versions (
                id,
                workflow_id,
                workspace_id,
                version,
                payload,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(version.id),
                str(workflow.id),
                None,
                version.version,
                json.dumps(payload),
                version.created_at.isoformat(),
                version.updated_at.isoformat(),
            ),
        )
        conn.commit()

    repository = SqliteWorkflowRepository(db_path)
    try:
        latest = await repository.get_latest_version(workflow.id)
    finally:
        await repository.reset()

    assert latest.id == version.id
    assert latest.workspace_id == "workspace-a"


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
async def test_sqlite_dispatch_due_cron_runs_skips_quota_exceeded(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Cron dispatch skips runs when the workspace quota is exceeded."""

    db_path = tmp_path_factory.mktemp("cron-quota") / "dispatch.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workflow = await repository.create_workflow(
            name="Quota Cron",
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

        async def _raise_quota(*_: object, **__: object):
            raise WorkspaceQuotaExceededError("quota", code="workspace.quota.runs")

        repository._create_run_locked = _raise_quota  # type: ignore[method-assign]

        runs = await repository.dispatch_due_cron_runs(
            now=datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
        )

        assert runs == []
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
async def test_sqlite_list_runs_for_workflow_with_workspace_filter(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace-scoped run listing returns only matching rows."""

    db_path = tmp_path_factory.mktemp("repo") / "runs.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workspace_id = str(uuid4())
        workflow = await repository.create_workflow(
            name="Runs",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
            workspace_id=workspace_id,
        )
        version = await repository.create_version(
            workflow.id,
            graph={},
            metadata={},
            notes=None,
            created_by="author",
        )

        class _WorkspaceRepo:
            def get_workspace(self, workspace_uuid: UUID) -> SimpleNamespace:
                assert str(workspace_uuid) == workspace_id
                return SimpleNamespace(
                    quotas=SimpleNamespace(max_concurrent_runs=2),
                )

        class _Governance:
            def reserve_run_slot(self, workspace: str, *, limit: int) -> None:
                return None

            def release_run_slot(self, workspace: str) -> None:
                return None

        monkeypatch.setattr(
            sqlite_persistence,
            "get_workspace_repository",
            lambda: _WorkspaceRepo(),
        )
        monkeypatch.setattr(
            sqlite_persistence,
            "get_workspace_governance",
            lambda: _Governance(),
        )
        await repository.create_run(
            workflow.id,
            workflow_version_id=version.id,
            triggered_by="manual",
            input_payload={},
            workspace_id=workspace_id,
        )

        runs = await repository.list_runs_for_workflow(
            workflow.id, workspace_id=workspace_id
        )

        assert len(runs) == 1
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_get_run_workspace_mismatch_raises(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace-scoped get_run rejects rows from another workspace."""

    db_path = tmp_path_factory.mktemp("repo") / "run-mismatch.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workspace_id = str(uuid4())
        workflow = await repository.create_workflow(
            name="Runs",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
            workspace_id=workspace_id,
        )
        version = await repository.create_version(
            workflow.id,
            graph={},
            metadata={},
            notes=None,
            created_by="author",
        )

        class _WorkspaceRepo:
            def get_workspace(self, workspace_uuid: UUID) -> SimpleNamespace:
                assert str(workspace_uuid) == workspace_id
                return SimpleNamespace(
                    quotas=SimpleNamespace(max_concurrent_runs=2),
                )

        class _Governance:
            def reserve_run_slot(self, workspace: str, *, limit: int) -> None:
                return None

            def release_run_slot(self, workspace: str) -> None:
                return None

        monkeypatch.setattr(
            sqlite_persistence,
            "get_workspace_repository",
            lambda: _WorkspaceRepo(),
        )
        monkeypatch.setattr(
            sqlite_persistence,
            "get_workspace_governance",
            lambda: _Governance(),
        )
        run = await repository.create_run(
            workflow.id,
            workflow_version_id=version.id,
            triggered_by="manual",
            input_payload={},
            workspace_id=workspace_id,
        )

        with pytest.raises(WorkflowRunNotFoundError):
            await repository.get_run(run.id, workspace_id="workspace-b")
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_get_run_workspace_scoping_accepts_matching_rows(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace-scoped get_run accepts rows from the same workspace."""

    db_path = tmp_path_factory.mktemp("repo") / "run-match.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workspace_id = str(uuid4())
        workflow = await repository.create_workflow(
            name="Runs",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
            workspace_id=workspace_id,
        )
        version = await repository.create_version(
            workflow.id,
            graph={},
            metadata={},
            notes=None,
            created_by="author",
        )

        class _WorkspaceRepo:
            def get_workspace(self, workspace_uuid: UUID) -> SimpleNamespace:
                assert str(workspace_uuid) == workspace_id
                return SimpleNamespace(
                    quotas=SimpleNamespace(max_concurrent_runs=2),
                )

        class _Governance:
            def reserve_run_slot(self, workspace: str, *, limit: int) -> None:
                return None

            def release_run_slot(self, workspace: str) -> None:
                return None

        monkeypatch.setattr(
            sqlite_persistence,
            "get_workspace_repository",
            lambda: _WorkspaceRepo(),
        )
        monkeypatch.setattr(
            sqlite_persistence,
            "get_workspace_governance",
            lambda: _Governance(),
        )
        run = await repository.create_run(
            workflow.id,
            workflow_version_id=version.id,
            triggered_by="manual",
            input_payload={},
            workspace_id=workspace_id,
        )

        assert await repository.get_run(run.id, workspace_id=workspace_id) == run
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_get_run_workspace_id_helper_returns_none(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The run workspace_id helper returns None when the row is absent."""

    db_path = tmp_path_factory.mktemp("repo") / "run-helper-none.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        await repository._ensure_initialized()  # noqa: SLF001
        assert (
            await repository._get_run_workspace_id_locked(uuid4())  # noqa: SLF001
        ) is None
    finally:
        await repository.reset()


@pytest.mark.asyncio()
async def test_sqlite_get_workflow_workspace_scoping_rejects_mismatched_rows(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace-scoped workflow lookups reject rows from another workspace."""

    db_path = tmp_path_factory.mktemp("repo") / "workflow-mismatch.sqlite"
    repository = SqliteWorkflowRepository(db_path)

    try:
        workspace_id = str(uuid4())
        workflow = await repository.create_workflow(
            name="Scoped",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="author",
            workspace_id=workspace_id,
        )
        other_workspace_id = str(uuid4())

        with pytest.raises(WorkflowNotFoundError):
            await repository.get_workflow(workflow.id, workspace_id=other_workspace_id)

        with pytest.raises(WorkflowNotFoundError):
            await repository.resolve_workflow_ref(
                str(workflow.id), workspace_id=other_workspace_id
            )
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
