"""Additional tests for PostgreSQL repository mixins to improve coverage.

These tests target specific uncovered lines and edge cases in the repository
implementation.
"""

from __future__ import annotations
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4
import pytest
from orcheo.runtime.runnable_config import RunnableConfigModel
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.webhook import WebhookTriggerConfig
from orcheo.vault.oauth import CredentialHealthError, CredentialHealthReport
from orcheo_backend.app.repository.errors import (
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.repository_postgres import PostgresWorkflowRepository
from orcheo_backend.app.repository_postgres import _base as pg_base


class FakeRow(dict[str, Any]):
    """Fake row that supports both index and key access like psycopg rows."""

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class FakeCursor:
    """Fake database cursor for testing."""

    def __init__(
        self,
        *,
        row: dict[str, Any] | None = None,
        rows: list[Any] | None = None,
        rowcount: int = 1,
    ) -> None:
        self._row = FakeRow(row) if row else None
        self._rows = [FakeRow(r) if isinstance(r, dict) else r for r in (rows or [])]
        self.rowcount = rowcount

    async def fetchone(self) -> FakeRow | None:
        return self._row

    async def fetchall(self) -> list[Any]:
        return list(self._rows)


class FakeConnection:
    """Fake database connection recording queries and returning configured responses."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.queries: list[tuple[str, Any | None]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, query: str, params: Any | None = None) -> FakeCursor:
        self.queries.append((query.strip(), params))
        response = self._responses.pop(0) if self._responses else {}
        if isinstance(response, FakeCursor):
            return response
        if isinstance(response, dict):
            return FakeCursor(
                row=response.get("row"),
                rows=response.get("rows"),
                rowcount=response.get("rowcount", 1),
            )
        if isinstance(response, list):
            return FakeCursor(rows=response)
        return FakeCursor()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        return None


class FakePool:
    """Fake connection pool that returns a pre-configured connection."""

    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self._opened = False

    async def open(self) -> None:
        self._opened = True

    async def close(self) -> None:
        self._opened = False

    def connection(self) -> FakeConnection:
        return self._connection


def make_repository(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[Any],
    *,
    initialized: bool = True,
) -> PostgresWorkflowRepository:
    """Create a PostgresWorkflowRepository with fake connection pool."""
    monkeypatch.setattr(pg_base, "AsyncConnectionPool", object())
    monkeypatch.setattr(pg_base, "DictRowFactory", object())
    repo = PostgresWorkflowRepository("postgresql://test")
    repo._pool = FakePool(FakeConnection(responses))
    repo._initialized = initialized
    return repo


def _workflow_payload(workflow_id: UUID, **overrides: Any) -> dict[str, Any]:
    """Generate a fake workflow payload dictionary."""
    now = datetime.now(tz=UTC).isoformat()
    base = {
        "id": str(workflow_id),
        "name": "Test Workflow",
        "slug": "test-workflow",
        "description": None,
        "tags": [],
        "is_archived": False,
        "is_public": False,
        "published_at": None,
        "published_by": None,
        "require_login": False,
        "audit_log": [],
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_persistence_deserialize_workflow_uses_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow payloads should use `workspace_id`."""
    workflow_id = uuid4()
    payload = _workflow_payload(workflow_id, workspace_id="workspace-a")

    repo = make_repository(monkeypatch, [])
    workflow = repo._deserialize_workflow(payload)

    assert workflow.id == workflow_id
    assert workflow.workspace_id == "workspace-a"


def _version_payload(
    version_id: UUID, workflow_id: UUID, version: int = 1, **overrides: Any
) -> dict[str, Any]:
    """Generate a fake version payload dictionary."""
    now = datetime.now(tz=UTC).isoformat()
    base = {
        "id": str(version_id),
        "workflow_id": str(workflow_id),
        "version": version,
        "graph": {},
        "metadata": {},
        "runnable_config": None,
        "notes": None,
        "created_by": "author",
        "audit_log": [],
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_list_workflows_includes_legacy_unscoped_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace-scoped listing should keep legacy NULL-scoped workflows visible."""
    workflow_id = uuid4()
    workflow_payload = _workflow_payload(workflow_id)
    responses: list[Any] = [
        {"rows": [{"payload": workflow_payload}]},
    ]
    repo = make_repository(monkeypatch, responses)

    result = await repo.list_workflows(workspace_id="workspace-a")

    assert len(result) == 1
    query, params = repo._pool._connection.queries[0]  # noqa: SLF001
    assert "workspace_id = %s OR workspace_id IS NULL" in query
    assert params == ("workspace-a",)


@pytest.mark.asyncio
async def test_base_repository_ensure_workflow_health_no_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _ensure_workflow_health does nothing when
    credential_service is None."""
    repo = make_repository(monkeypatch, [])

    # Should not raise any errors
    await repo._ensure_workflow_health(uuid4(), actor="test")


@pytest.mark.asyncio
async def test_persistence_deserialize_workflow_with_deprecated_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _deserialize_workflow strips deprecated fields."""
    workflow_id = uuid4()
    payload = _workflow_payload(
        workflow_id,
        publish_token_hash="old_hash",
        publish_token_rotated_at="2024-01-01T00:00:00Z",
    )

    repo = make_repository(monkeypatch, [])
    workflow = repo._deserialize_workflow(payload)

    # Deprecated fields should not be present
    assert not hasattr(workflow, "publish_token_hash")
    assert not hasattr(workflow, "publish_token_rotated_at")


@pytest.mark.asyncio
async def test_persistence_deserialize_workflow_from_json_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _deserialize_workflow handles JSON string input."""
    workflow_id = uuid4()
    payload = _workflow_payload(workflow_id)
    json_str = json.dumps(payload)

    repo = make_repository(monkeypatch, [])
    workflow = repo._deserialize_workflow(json_str)

    assert workflow.id == workflow_id


@pytest.mark.asyncio
async def test_persistence_get_version_locked_with_string_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _get_version_locked handles string JSON payload."""
    workflow_id = uuid4()
    version_id = uuid4()
    payload = _version_payload(version_id, workflow_id)
    json_str = json.dumps(payload)

    responses: list[Any] = [
        {"row": {"payload": json_str}},  # String JSON
    ]
    repo = make_repository(monkeypatch, responses)

    version = await repo._get_version_locked(version_id)

    assert version.id == version_id


@pytest.mark.asyncio
async def test_persistence_get_latest_version_locked_with_string_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _get_latest_version_locked handles string JSON payload."""
    workflow_id = uuid4()
    version_id = uuid4()
    payload = _version_payload(version_id, workflow_id)
    json_str = json.dumps(payload)

    responses: list[Any] = [
        {"row": {"payload": json_str}},  # String JSON
    ]
    repo = make_repository(monkeypatch, responses)

    version = await repo._get_latest_version_locked(workflow_id)

    assert version.id == version_id


@pytest.mark.asyncio
async def test_persistence_get_latest_version_locked_not_found_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _get_latest_version_locked raises when no version exists."""
    workflow_id = uuid4()
    responses: list[Any] = [
        {"row": None},  # No version found
    ]
    repo = make_repository(monkeypatch, responses)

    with pytest.raises(WorkflowVersionNotFoundError, match="latest"):
        await repo._get_latest_version_locked(workflow_id)


@pytest.mark.asyncio
async def test_persistence_resolve_workflow_ref_locked_uses_single_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow ref resolution should not fan out into multiple sequential queries."""

    workflow_id = uuid4()
    responses: list[Any] = [
        {"row": {"id": str(workflow_id)}},
    ]
    repo = make_repository(monkeypatch, responses)

    resolved = await repo._resolve_workflow_ref_locked("handle-flow")

    assert resolved == workflow_id
    connection = repo._pool._connection  # type: ignore[union-attr]  # noqa: SLF001
    assert len(connection.queries) == 1


@pytest.mark.asyncio
async def test_persistence_ensure_handle_available_locked_rejects_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle validation raises when another active workflow owns the handle."""

    responses: list[Any] = [
        {"rows": [{"id": str(uuid4()), "is_archived": False}]},
    ]
    repo = make_repository(monkeypatch, responses)

    with pytest.raises(WorkflowHandleConflictError, match="already in use"):
        await repo._ensure_handle_available_locked(
            "shared-handle",
            workflow_id=None,
            is_archived=False,
        )


@pytest.mark.asyncio
async def test_persistence_ensure_handle_available_locked_create_path_avoids_null_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create-time handle validation should not bind an untyped NULL UUID."""

    responses: list[Any] = [{"rows": []}]
    repo = make_repository(monkeypatch, responses)

    await repo._ensure_handle_available_locked(
        "shared-handle",
        workflow_id=None,
        is_archived=False,
    )

    query, params = repo._pool._connection.queries[0]
    assert "id !=" not in query
    assert params == ("shared-handle",)


@pytest.mark.asyncio
async def test_persistence_ensure_handle_available_locked_allows_archived_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Archived workflows may reuse a handle owned only by archived workflows."""

    responses: list[Any] = [
        {"rows": [{"id": str(uuid4()), "is_archived": True}]},
    ]
    repo = make_repository(monkeypatch, responses)

    await repo._ensure_handle_available_locked(
        "shared-handle",
        workflow_id=None,
        is_archived=True,
    )


@pytest.mark.asyncio
async def test_persistence_ensure_handle_available_locked_allows_active_reuse_after_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Archived workflows should not block a new active workflow from reusing a handle."""

    responses: list[Any] = [
        {"rows": [{"id": str(uuid4()), "is_archived": True}]},
    ]
    repo = make_repository(monkeypatch, responses)

    await repo._ensure_handle_available_locked(
        "shared-handle",
        workflow_id=None,
        is_archived=False,
    )


@pytest.mark.asyncio
async def test_persistence_resolve_workflow_ref_locked_blank_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank workflow refs raise before any query is issued."""

    repo = make_repository(monkeypatch, [])

    with pytest.raises(WorkflowNotFoundError, match="workflow ref is empty"):
        await repo._resolve_workflow_ref_locked("   ")


@pytest.mark.asyncio
async def test_persistence_resolve_workflow_ref_locked_not_found_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow ref resolution raises when no matching row is found."""

    repo = make_repository(monkeypatch, [{"row": None}])

    with pytest.raises(WorkflowNotFoundError, match="missing-handle"):
        await repo._resolve_workflow_ref_locked("missing-handle")


@pytest.mark.asyncio
async def test_postgres_repository_resolve_workflow_ref_delegates_to_locked_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public resolve_workflow_ref delegates through the initialized wrapper."""

    workflow_id = uuid4()
    repo = make_repository(monkeypatch, [{"row": {"id": str(workflow_id)}}])

    resolved = await repo.resolve_workflow_ref("handle-flow")

    assert resolved == workflow_id


@pytest.mark.asyncio
async def test_base_ensure_workflow_schema_migrations_backfills_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow migrations should add mirrored columns for existing Postgres rows."""

    workflow_id = uuid4()
    legacy_payload = _workflow_payload(
        workflow_id,
        handle="legacy-flow",
        is_archived=True,
    )
    responses: list[Any] = [
        {"rows": []},  # SELECT columns from workflows (none exist)
        {},  # ALTER TABLE workflows ADD COLUMN handle
        {},  # ALTER TABLE workflows ADD COLUMN is_archived
        {},  # ALTER TABLE workflows ADD COLUMN workspace_id
        {
            "rows": [{"id": str(workflow_id), "payload": legacy_payload}]
        },  # SELECT workflows
        {},  # UPDATE workflows
        {"rows": []},  # SELECT columns from workflow_runs (none exist)
        {},  # ALTER TABLE workflow_runs ADD COLUMN workspace_id
        {},  # CREATE INDEX idx_workflows_handle
        {},  # CREATE UNIQUE INDEX idx_workflows_active_handle
        {},  # CREATE INDEX idx_workflows_workspace_id
        {},  # CREATE INDEX idx_runs_workspace_id
    ]
    repo = make_repository(monkeypatch, responses)
    connection = repo._pool._connection  # type: ignore[union-attr]  # noqa: SLF001

    await repo._ensure_workflow_schema_migrations(connection)

    queries = [query for query, _ in connection.queries]
    assert any(
        "ALTER TABLE workflows ADD COLUMN handle TEXT" in query for query in queries
    )
    assert any(
        "ALTER TABLE workflows ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE"
        in query
        for query in queries
    )
    # Find the UPDATE query (indexes are created after it)
    update_entries = [(q, p) for q, p in connection.queries if "UPDATE workflows" in q]
    assert len(update_entries) == 1
    update_query, update_params = update_entries[0]
    assert update_params is not None
    assert update_params[0] == "legacy-flow"
    assert update_params[1] is True
    assert isinstance(update_params[2], datetime)
    assert update_params[3] == str(workflow_id)
    # Indexes should be created after migration
    assert any("idx_workflows_handle" in q for q in queries)
    assert any("idx_workflows_active_handle" in q for q in queries)


@pytest.mark.asyncio
async def test_postgres_ensure_initialized_adds_versions_workspace_index_after_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy Postgres databases should migrate the column before the index."""

    repo = make_repository(monkeypatch, [], initialized=False)
    connection = repo._pool._connection  # type: ignore[union-attr]  # noqa: SLF001

    await repo._ensure_initialized()

    queries = [query for query, _ in connection.queries]
    workspace_index_queries = [
        query for query in queries if "idx_versions_workspace_id" in query
    ]
    assert len(workspace_index_queries) == 1

    alter_position = next(
        index
        for index, query in enumerate(queries)
        if "ALTER TABLE workflow_versions ADD COLUMN workspace_id TEXT" in query
    )
    index_position = next(
        index
        for index, query in enumerate(queries)
        if "CREATE INDEX IF NOT EXISTS idx_versions_workspace_id" in query
    )

    assert alter_position < index_position


@pytest.mark.asyncio
async def test_base_ensure_workflow_schema_migrations_respects_existing_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing mirrored columns should not be re-added during migration."""

    responses: list[Any] = [
        {"rows": [{"column_name": "handle"}]},  # SELECT columns from workflows
        {},  # ALTER TABLE workflows ADD COLUMN is_archived
        {},  # ALTER TABLE workflows ADD COLUMN workspace_id
        {"rows": []},  # SELECT workflows (no rows to backfill)
        {"rows": []},  # SELECT columns from workflow_runs (none exist)
        {},  # ALTER TABLE workflow_runs ADD COLUMN workspace_id
        {},  # CREATE INDEX idx_workflows_handle
        {},  # CREATE UNIQUE INDEX idx_workflows_active_handle
        {},  # CREATE INDEX idx_workflows_workspace_id
        {},  # CREATE INDEX idx_runs_workspace_id
    ]
    repo = make_repository(monkeypatch, responses)
    connection = repo._pool._connection  # type: ignore[union-attr]  # noqa: SLF001

    await repo._ensure_workflow_schema_migrations(connection)

    queries = [query for query, _ in connection.queries]
    assert not any(
        "ALTER TABLE workflows ADD COLUMN handle TEXT" in query for query in queries
    )
    assert any(
        "ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE" in query
        for query in queries
    )


@pytest.mark.asyncio
async def test_base_ensure_workflow_schema_migrations_skips_existing_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing mirrored columns should bypass both ALTER statements."""

    responses: list[Any] = [
        {
            "rows": [
                {"column_name": "handle"},
                {"column_name": "is_archived"},
                {"column_name": "workspace_id"},
            ]
        },  # SELECT columns from workflows (all exist)
        {"rows": []},  # SELECT workflows (no rows to backfill)
        {
            "rows": [{"column_name": "workspace_id"}]
        },  # SELECT columns from workflow_runs (workspace_id exists)
        {},  # CREATE INDEX idx_workflows_handle
        {},  # CREATE UNIQUE INDEX idx_workflows_active_handle
        {},  # CREATE INDEX idx_workflows_workspace_id
        {},  # CREATE INDEX idx_runs_workspace_id
    ]
    repo = make_repository(monkeypatch, responses)
    connection = repo._pool._connection  # type: ignore[union-attr]  # noqa: SLF001

    await repo._ensure_workflow_schema_migrations(connection)

    queries = [query for query, _ in connection.queries]
    assert not any(
        "ALTER TABLE workflows ADD COLUMN handle TEXT" in query for query in queries
    )
    assert not any(
        "ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE" in query
        for query in queries
    )
    assert not any("UPDATE workflows" in query for query in queries)


@pytest.mark.asyncio
async def test_persistence_get_run_locked_with_string_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _get_run_locked handles string JSON payload."""
    run_id = uuid4()
    version_id = uuid4()
    workflow_id = uuid4()
    now = datetime.now(tz=UTC).isoformat()

    payload = {
        "id": str(run_id),
        "workflow_version_id": str(version_id),
        "status": "pending",
        "triggered_by": "manual",
        "input_payload": {},
        "runnable_config": {},
        "tags": [],
        "callbacks": [],
        "metadata": {},
        "run_name": None,
        "output_payload": None,
        "error": None,
        "audit_log": [],
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
    }
    json_str = json.dumps(payload)

    responses: list[Any] = [
        {
            "row": {
                "payload": json_str,  # String JSON
                "workflow_id": str(workflow_id),
                "triggered_by": "manual",
                "status": "pending",
            }
        },
    ]
    repo = make_repository(monkeypatch, responses)

    run = await repo._get_run_locked(run_id)

    assert run.id == run_id


@pytest.mark.asyncio
async def test_persistence_create_run_locked_version_mismatch_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _create_run_locked raises when version doesn't match workflow."""
    workflow_id = uuid4()
    wrong_workflow_id = uuid4()
    version_id = uuid4()

    version_payload = _version_payload(version_id, wrong_workflow_id)
    responses: list[Any] = [
        {"row": {"payload": version_payload}},  # _get_version_locked
    ]
    repo = make_repository(monkeypatch, responses)

    with pytest.raises(WorkflowVersionNotFoundError):
        await repo._create_run_locked(
            workflow_id=workflow_id,
            workflow_version_id=version_id,
            triggered_by="manual",
            input_payload={},
            actor="test",
        )


@pytest.mark.asyncio
async def test_persistence_create_run_locked_with_pydantic_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _create_run_locked handles Pydantic models in runnable_config."""
    workflow_id = uuid4()
    version_id = uuid4()

    version_payload = _version_payload(version_id, workflow_id)
    responses: list[Any] = [
        {"row": {"payload": version_payload}},  # _get_version_locked
        {},  # INSERT run
    ]
    repo = make_repository(monkeypatch, responses)
    # Mock track_run to avoid trigger layer issues
    from unittest.mock import MagicMock

    repo._trigger_layer.track_run = MagicMock()

    runnable_config = RunnableConfigModel(tags=["pydantic-tag"])

    run = await repo._create_run_locked(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        triggered_by="manual",
        input_payload={},
        actor="test",
        runnable_config=runnable_config,
    )

    assert "pydantic-tag" in run.tags


@pytest.mark.asyncio
async def test_runs_get_run_not_found_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that get_run raises when run doesn't exist."""
    run_id = uuid4()

    responses: list[Any] = [
        {"row": None},  # run not found
    ]
    repo = make_repository(monkeypatch, responses)

    with pytest.raises(WorkflowRunNotFoundError):
        await repo.get_run(run_id)


@pytest.mark.asyncio
async def test_base_repository_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that close shuts down the connection pool."""
    repo = make_repository(monkeypatch, [])

    await repo.close()
    assert repo._pool is None


@pytest.mark.asyncio
async def test_triggers_handle_webhook_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test handling a webhook trigger creates and enqueues a run."""
    workflow_id = uuid4()
    version_id = uuid4()

    workflow_payload = _workflow_payload(workflow_id)
    version_payload = _version_payload(version_id, workflow_id)

    # responses for triggers

    responses = [
        {"row": {"payload": workflow_payload}},  # _get_workflow_locked
        {"row": {"workspace_id": None}},  # _get_workflow_workspace_id_locked
        {"row": {"payload": version_payload}},  # _get_latest_version_locked
        {"row": {"payload": version_payload}},  # _get_version_locked (for validation)
        {},  # INSERT run
    ]

    repo = make_repository(monkeypatch, responses)

    # Configure webhook trigger first
    config = WebhookTriggerConfig(allowed_methods={"POST"})
    repo._trigger_layer.configure_webhook(workflow_id, config)

    # Mock _enqueue_run_for_execution to avoid Celery dependency
    from unittest.mock import patch

    with patch(
        "orcheo_backend.app.repository_postgres._triggers._enqueue_run_for_execution"
    ):
        result = await repo.handle_webhook_trigger(
            workflow_id,
            method="POST",
            headers={"content-type": "application/json"},
            query_params={},
            payload={"test": "data"},
            source_ip="127.0.0.1",
        )

    assert result.workflow_version_id == version_id
    assert result.triggered_by == "webhook"


@pytest.mark.asyncio
async def test_triggers_dispatch_due_cron_runs_skip_missing_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that cron dispatch skips workflows with no versions."""
    workflow_id = uuid4()
    config = CronTriggerConfig(expression="0 9 * * *", timezone="UTC")

    # Provide response for _refresh_cron_triggers
    responses = [
        {
            "rows": [
                {
                    "workflow_id": str(workflow_id),
                    "config": config.model_dump(mode="json"),
                    "last_dispatched_at": None,
                }
            ]
        },
    ]
    repo = make_repository(monkeypatch, responses)

    # Mock _get_latest_version_locked to raise
    mock_get_version = AsyncMock(
        side_effect=WorkflowVersionNotFoundError(str(workflow_id))
    )
    monkeypatch.setattr(repo, "_get_latest_version_locked", mock_get_version)

    # Force a cron dispatch time
    now = datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
    runs = await repo.dispatch_due_cron_runs(now=now)

    # Should skip the workflow and return empty list
    assert mock_get_version.called
    assert len(runs) == 0


@pytest.mark.asyncio
async def test_triggers_dispatch_due_cron_runs_skip_unhealthy_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that cron dispatch skips workflows with credential health errors."""
    workflow_id = uuid4()
    version_id = uuid4()
    config = CronTriggerConfig(expression="0 9 * * *", timezone="UTC")

    version_payload = _version_payload(version_id, workflow_id)

    # Provide response for _refresh_cron_triggers
    responses = [
        {
            "rows": [
                {
                    "workflow_id": str(workflow_id),
                    "config": config.model_dump(mode="json"),
                    "last_dispatched_at": None,
                }
            ]
        },
    ]
    repo = make_repository(monkeypatch, responses)

    # Mock _get_latest_version_locked to return a version
    from unittest.mock import AsyncMock
    from orcheo.models.workflow import WorkflowVersion

    version = WorkflowVersion.model_validate(version_payload)
    monkeypatch.setattr(
        repo, "_get_latest_version_locked", AsyncMock(return_value=version)
    )

    # Mock _ensure_workflow_health to raise CredentialHealthError
    mock_report = CredentialHealthReport(
        workflow_id=workflow_id, results=[], checked_at=datetime.now(tz=UTC)
    )
    mock_health = AsyncMock(side_effect=CredentialHealthError(mock_report))
    monkeypatch.setattr(repo, "_ensure_workflow_health", mock_health)

    now = datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
    runs = await repo.dispatch_due_cron_runs(now=now)

    # Should skip the workflow and return empty list
    assert mock_health.called
    assert len(runs) == 0


@pytest.mark.asyncio
async def test_triggers_dispatch_manual_runs_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that manual dispatch raises when version belongs to different workflow."""
    workflow_id = uuid4()
    other_workflow_id = uuid4()
    version_id = uuid4()
    latest_version_id = uuid4()

    workflow_payload = _workflow_payload(workflow_id)
    latest_version_payload = _version_payload(latest_version_id, workflow_id)
    # Version that belongs to a different workflow
    wrong_version_payload = _version_payload(version_id, other_workflow_id)

    responses = [
        {"row": {"payload": workflow_payload}},  # _get_workflow_locked
        {"row": {"payload": latest_version_payload}},  # _get_latest_version_locked
        {"row": {"payload": wrong_version_payload}},  # _get_version_locked
    ]

    repo = make_repository(monkeypatch, responses)

    from orcheo.triggers.manual import ManualDispatchRequest

    request = ManualDispatchRequest(
        workflow_id=workflow_id,
        runs=[{"workflow_version_id": version_id, "input_payload": {}}],
    )

    with pytest.raises(WorkflowVersionNotFoundError):
        await repo.dispatch_manual_runs(request)


@pytest.mark.asyncio
async def test_triggers_dispatch_manual_runs_latest_version_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that manual dispatch raises when workflow has no versions."""
    workflow_id = uuid4()
    workflow_payload = _workflow_payload(workflow_id)

    responses = [
        {"row": {"payload": workflow_payload}},  # _get_workflow_locked
        {"row": None},  # _get_latest_version_locked returns None
    ]

    repo = make_repository(monkeypatch, responses)

    from orcheo.triggers.manual import ManualDispatchRequest

    request = ManualDispatchRequest(
        workflow_id=workflow_id,
        runs=[{"input_payload": {}}],
    )

    with pytest.raises(WorkflowVersionNotFoundError) as exc_info:
        await repo.dispatch_manual_runs(request)

    assert str(workflow_id) in str(exc_info.value)


@pytest.mark.asyncio
async def test_triggers_get_webhook_trigger_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test retrieving webhook trigger configuration."""
    workflow_id = uuid4()
    workflow_payload = _workflow_payload(workflow_id)

    responses = [
        {"row": {"payload": workflow_payload}},  # _get_workflow_locked
    ]

    repo = make_repository(monkeypatch, responses)

    # Configure webhook trigger first
    config = WebhookTriggerConfig(allowed_methods={"POST", "GET"})
    repo._trigger_layer.configure_webhook(workflow_id, config)

    result = await repo.get_webhook_trigger_config(workflow_id)
    assert "POST" in result.allowed_methods
    assert "GET" in result.allowed_methods


@pytest.mark.asyncio
async def test_triggers_get_cron_trigger_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test retrieving cron trigger configuration."""
    workflow_id = uuid4()
    workflow_payload = _workflow_payload(workflow_id)

    responses = [
        {"row": {"payload": workflow_payload}},  # _get_workflow_locked
    ]

    repo = make_repository(monkeypatch, responses)

    # Configure cron trigger first
    config = CronTriggerConfig(expression="0 0 * * *", timezone="UTC")
    repo._trigger_layer.configure_cron(workflow_id, config)

    result = await repo.get_cron_trigger_config(workflow_id)
    assert result.expression == "0 0 * * *"
    assert result.timezone == "UTC"


@pytest.mark.asyncio
async def test_triggers_refresh_cron_triggers_removes_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that _refresh_cron_triggers removes triggers no longer in the database."""
    workflow_id = uuid4()

    # Initially empty database responses
    repo = make_repository(monkeypatch, [{"rows": []}])

    # Manually add a trigger to the layer (simulating a stale trigger)
    config = CronTriggerConfig(expression="0 0 * * *", timezone="UTC")
    repo._trigger_layer.configure_cron(workflow_id, config)

    # Verify it exists in layer
    assert workflow_id in repo._trigger_layer._cron_states  # noqa: SLF001

    # Refresh - should remove it because responses is empty
    await repo._refresh_cron_triggers()

    # Verify it is removed
    assert workflow_id not in repo._trigger_layer._cron_states  # noqa: SLF001


@pytest.mark.asyncio
async def test_versions_get_version_by_number_string_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_version_by_number handles string JSON payload."""
    workflow_id = uuid4()
    version_id = uuid4()
    payload = _version_payload(version_id, workflow_id, version=2)
    json_str = json.dumps(payload)

    responses = [
        {"row": {"payload": _workflow_payload(workflow_id)}},  # _get_workflow_locked
        {"row": {"payload": json_str}},  # String JSON
    ]

    repo = make_repository(monkeypatch, responses)

    version = await repo.get_version_by_number(workflow_id, 2)
    assert version.id == version_id
    assert version.version == 2


@pytest.mark.asyncio
async def test_versions_get_version_by_number_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_version_by_number raises when version doesn't exist."""
    workflow_id = uuid4()

    responses = [
        {"row": {"payload": _workflow_payload(workflow_id)}},  # _get_workflow_locked
        {"row": None},  # No version found
    ]

    repo = make_repository(monkeypatch, responses)

    with pytest.raises(WorkflowVersionNotFoundError, match="v5"):
        await repo.get_version_by_number(workflow_id, 5)


@pytest.mark.asyncio
async def test_versions_update_version_runnable_config_persists_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """update_version_runnable_config updates payload and returns the version."""
    workflow_id = uuid4()
    version_id = uuid4()
    payload = _version_payload(version_id, workflow_id, version=2, runnable_config=None)
    json_payload = json.dumps(payload)

    responses = [
        {"row": {"payload": _workflow_payload(workflow_id)}},  # _get_workflow_locked
        {"row": {"id": str(version_id), "payload": json_payload}},  # SELECT version row
        {},  # UPDATE workflow_versions
    ]
    repo = make_repository(monkeypatch, responses)

    updated = await repo.update_version_runnable_config(
        workflow_id,
        version_number=2,
        runnable_config={"configurable": {"thread_id": "thr-2"}},
        actor="editor",
    )

    assert updated.id == version_id
    assert updated.runnable_config == {"configurable": {"thread_id": "thr-2"}}


@pytest.mark.asyncio
async def test_versions_update_version_runnable_config_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """update_version_runnable_config raises when the version does not exist."""
    workflow_id = uuid4()

    responses = [
        {"row": {"payload": _workflow_payload(workflow_id)}},  # _get_workflow_locked
        {"row": None},  # Missing version row
    ]
    repo = make_repository(monkeypatch, responses)

    with pytest.raises(WorkflowVersionNotFoundError, match="v2"):
        await repo.update_version_runnable_config(
            workflow_id,
            version_number=2,
            runnable_config=None,
            actor="editor",
        )


@pytest.mark.asyncio
async def test_versions_get_latest_version_string_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_latest_version handles string JSON payload."""
    workflow_id = uuid4()
    version_id = uuid4()
    payload = _version_payload(version_id, workflow_id, version=3)
    json_str = json.dumps(payload)

    responses = [
        {"row": {"payload": _workflow_payload(workflow_id)}},  # _get_workflow_locked
        {"row": {"payload": json_str}},  # String JSON
    ]

    repo = make_repository(monkeypatch, responses)

    version = await repo.get_latest_version(workflow_id)
    assert version.id == version_id
    assert version.version == 3


@pytest.mark.asyncio
async def test_versions_get_latest_version_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_latest_version raises when no version exists."""
    workflow_id = uuid4()

    responses = [
        {"row": {"payload": _workflow_payload(workflow_id)}},  # _get_workflow_locked
        {"row": None},  # No version found
    ]

    repo = make_repository(monkeypatch, responses)

    with pytest.raises(WorkflowVersionNotFoundError, match="latest"):
        await repo.get_latest_version(workflow_id)


@pytest.mark.asyncio
async def test_versions_get_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_version retrieves a specific version by ID."""
    workflow_id = uuid4()
    version_id = uuid4()
    payload = _version_payload(version_id, workflow_id)

    responses = [
        {"row": {"payload": payload}},  # _get_version_locked
    ]

    repo = make_repository(monkeypatch, responses)

    version = await repo.get_version(version_id)
    assert version.id == version_id


@pytest.mark.asyncio
async def test_versions_list_versions_dict_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test list_versions handles dictionary payloads."""
    workflow_id = uuid4()
    version_id = uuid4()
    payload = _version_payload(version_id, workflow_id, version=1)

    responses = [
        {"row": {"payload": _workflow_payload(workflow_id)}},  # _get_workflow_locked
        {"rows": [{"payload": payload}]},  # Dictionary payload
    ]

    repo = make_repository(monkeypatch, responses)

    versions = await repo.list_versions(workflow_id)
    assert len(versions) == 1
    assert versions[0].id == version_id


@pytest.mark.asyncio
async def test_versions_get_version_by_number_dict_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_version_by_number handles dictionary payload."""
    workflow_id = uuid4()
    version_id = uuid4()
    payload = _version_payload(version_id, workflow_id, version=2)

    responses = [
        {"row": {"payload": _workflow_payload(workflow_id)}},  # _get_workflow_locked
        {"row": {"payload": payload}},  # Dictionary payload
    ]

    repo = make_repository(monkeypatch, responses)

    version = await repo.get_version_by_number(workflow_id, 2)
    assert version.id == version_id


@pytest.mark.asyncio
async def test_versions_get_latest_version_dict_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_latest_version handles dictionary payload."""
    workflow_id = uuid4()
    version_id = uuid4()
    payload = _version_payload(version_id, workflow_id, version=3)

    responses = [
        {"row": {"payload": _workflow_payload(workflow_id)}},  # _get_workflow_locked
        {"row": {"payload": payload}},  # Dictionary payload
    ]

    repo = make_repository(monkeypatch, responses)

    version = await repo.get_latest_version(workflow_id)
    assert version.id == version_id


@pytest.mark.asyncio
async def test_versions_get_latest_version_uses_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow version payloads should use `workspace_id`."""
    workflow_id = uuid4()
    version_id = uuid4()
    payload = _version_payload(
        version_id, workflow_id, version=3, workspace_id="workspace-a"
    )

    responses = [
        {"row": {"payload": _workflow_payload(workflow_id)}},  # _get_workflow_locked
        {"row": {"payload": payload}},  # Legacy dictionary payload
    ]

    repo = make_repository(monkeypatch, responses)

    version = await repo.get_latest_version(workflow_id)
    assert version.id == version_id
    assert version.workspace_id == "workspace-a"


@pytest.mark.asyncio
async def test_persistence_get_workflow_locked_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _get_workflow_locked raises when workflow doesn't exist."""
    workflow_id = uuid4()

    responses = [
        {"row": None},  # Workflow not found
    ]

    repo = make_repository(monkeypatch, responses)

    with pytest.raises(WorkflowNotFoundError):
        await repo._get_workflow_locked(workflow_id)


@pytest.mark.asyncio
async def test_persistence_get_version_locked_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _get_version_locked raises when version doesn't exist."""
    version_id = uuid4()

    responses = [
        {"row": None},  # Version not found
    ]

    repo = make_repository(monkeypatch, responses)

    with pytest.raises(WorkflowVersionNotFoundError):
        await repo._get_version_locked(version_id)


@pytest.mark.asyncio
async def test_persistence_get_run_locked_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _get_run_locked raises when run doesn't exist."""
    run_id = uuid4()

    responses = [
        {"row": None},  # Run not found
    ]

    repo = make_repository(monkeypatch, responses)

    with pytest.raises(WorkflowRunNotFoundError):
        await repo._get_run_locked(run_id)


@pytest.mark.asyncio
async def test_base_get_pool_race_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that _get_pool handles race condition where pool is set while
    acquiring lock."""

    class FakeAsyncConnectionPool:
        def __init__(self, *args: Any, **kwargs: Any):
            self.opened = False

        async def open(self) -> None:
            self.opened = True

    monkeypatch.setattr(pg_base, "AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(pg_base, "DictRowFactory", lambda x: x)
    repo = PostgresWorkflowRepository("postgresql://test")

    # Simulate race condition: pool becomes non-None while acquiring lock
    class SideEffectLock:
        async def __aenter__(self) -> None:
            # Set pool before creating new one
            repo._pool = "existing_pool"  # type: ignore[assignment]

        async def __aexit__(self, *args: Any) -> None:
            pass

    repo._pool_lock = SideEffectLock()  # type: ignore[assignment]
    repo._pool = None

    pool = await repo._get_pool()
    assert pool == "existing_pool"


@pytest.mark.asyncio
async def test_persistence_create_run_locked_triggers_cron_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that _create_run_locked registers cron runs properly."""
    workflow_id = uuid4()
    version_id = uuid4()

    version_payload = _version_payload(version_id, workflow_id)
    responses = [
        {"row": {"payload": version_payload}},  # _get_version_locked
        {},  # INSERT workflow_runs
    ]

    repo = make_repository(monkeypatch, responses)

    # Track cron registrations
    registered_runs: list[UUID] = []

    original_register = repo._trigger_layer.register_cron_run

    def track_register(run_id: UUID) -> None:
        registered_runs.append(run_id)
        return original_register(run_id)

    repo._trigger_layer.register_cron_run = track_register  # type: ignore[method-assign]

    run = await repo._create_run_locked(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        triggered_by="cron",
        input_payload={},
        actor=None,
    )

    # Cron run should have been registered
    assert run.id in registered_runs


@pytest.mark.asyncio
async def test_base_repository_ensure_workflow_health_raises_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _ensure_workflow_health raises CredentialHealthError when unhealthy."""
    repo = make_repository(monkeypatch, [])

    # Set up a mock credential service that returns unhealthy report
    from unittest.mock import AsyncMock, MagicMock

    mock_service = MagicMock()
    mock_report = MagicMock()
    mock_report.is_healthy = False
    mock_service.ensure_workflow_health = AsyncMock(return_value=mock_report)
    repo._credential_service = mock_service

    with pytest.raises(CredentialHealthError):
        await repo._ensure_workflow_health(uuid4(), actor="test")


@pytest.mark.asyncio
async def test_base_repository_get_pool_creates_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _get_pool creates a new pool when one doesn't exist."""
    pool_created = False

    class FakeAsyncConnectionPool:
        def __init__(self, dsn: str, **kwargs: Any) -> None:
            nonlocal pool_created
            pool_created = True
            self.dsn = dsn
            self.kwargs = kwargs

        async def open(self) -> None:
            pass

    monkeypatch.setattr(pg_base, "AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(pg_base, "DictRowFactory", lambda: None)
    repo = PostgresWorkflowRepository("postgresql://test")
    repo._pool = None  # Ensure no pool exists

    pool = await repo._get_pool()

    assert pool_created
    assert pool is not None
    assert repo._pool is pool


@pytest.mark.asyncio
async def test_base_repository_close_when_pool_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test close handles case when pool is already None."""
    repo = make_repository(monkeypatch, [])
    repo._pool = None

    # Should not raise
    await repo.close()
    assert repo._pool is None


@pytest.mark.asyncio
async def test_hydrate_trigger_state_with_cron_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _hydrate_trigger_state registers runs triggered by cron."""
    w_id = uuid4()
    run_id = uuid4()
    cron_conf = CronTriggerConfig(
        expression="* * * * *", timezone="UTC", allow_overlapping=True
    ).model_dump(mode="json")

    class SmartFakeConnection:
        def __init__(self) -> None:
            self.queries: list[tuple[str, Any]] = []
            self.commits = 0
            self.rollbacks = 0

        async def execute(self, query: str, params: Any = None) -> FakeCursor:
            self.queries.append((query, params))
            if "information_schema.columns" in query:
                return FakeCursor(rows=[])
            # Return cron config to set up state first
            if "FROM cron_triggers" in query:
                return FakeCursor(
                    rows=[
                        {
                            "workflow_id": str(w_id),
                            "config": cron_conf,
                            "last_dispatched_at": None,
                        }
                    ]
                )
            # Return runs for workflow_runs query
            if "workflow_runs" in query and "status" in query:
                return FakeCursor(
                    rows=[
                        {
                            "id": str(run_id),
                            "workflow_id": str(w_id),
                            "triggered_by": "cron",
                            "status": "pending",
                        }
                    ]
                )
            return FakeCursor(rows=[])

        async def commit(self) -> None:
            self.commits += 1

        async def rollback(self) -> None:
            self.rollbacks += 1

        async def __aenter__(self) -> SmartFakeConnection:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

    repo = make_repository(monkeypatch, [], initialized=False)
    repo._pool = FakePool(SmartFakeConnection())  # type: ignore[arg-type]
    repo._initialized = False

    await repo._ensure_initialized()

    # Check that the cron run was registered in the cron_run_index
    assert run_id in repo._trigger_layer._cron_run_index


@pytest.mark.asyncio
async def test_runs_create_run_full_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test create_run method performs workflow lookup and health check."""
    workflow_id = uuid4()
    version_id = uuid4()

    workflow_payload = _workflow_payload(workflow_id)
    version_payload = _version_payload(version_id, workflow_id)

    responses = [
        {"row": {"payload": workflow_payload}},  # _get_workflow_locked
        {"row": {"payload": version_payload}},  # _get_version_locked
        {},  # INSERT workflow_runs
    ]

    repo = make_repository(monkeypatch, responses)

    run = await repo.create_run(
        workflow_id,
        workflow_version_id=version_id,
        triggered_by="manual",
        input_payload={"test": "data"},
        actor="tester",
    )

    assert run.workflow_version_id == version_id
    assert run.triggered_by == "manual"
    assert run.input_payload == {"test": "data"}


@pytest.mark.asyncio
async def test_runs_create_run_archived_workflow_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Archived workflows cannot create runs."""
    workflow_id = uuid4()
    responses = [
        {"row": {"payload": _workflow_payload(workflow_id, is_archived=True)}},
    ]

    repo = make_repository(monkeypatch, responses)

    with pytest.raises(WorkflowNotFoundError):
        await repo.create_run(
            workflow_id,
            workflow_version_id=uuid4(),
            triggered_by="manual",
            input_payload={},
        )


@pytest.mark.asyncio
async def test_triggers_enqueue_run_for_execution_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _enqueue_run_for_execution successfully enqueues a run."""
    import sys
    from orcheo.models.workflow import WorkflowRun
    from orcheo_backend.app.repository_postgres import _triggers

    enqueued_calls: list[tuple[str, Any | None]] = []

    class MockTask:
        @staticmethod
        def delay(run_id: str, headers: dict[str, Any] | None = None) -> None:
            enqueued_calls.append((run_id, headers))

    # Create a mock run
    run_id = uuid4()
    now = datetime.now(tz=UTC)
    run = WorkflowRun(
        id=run_id,
        workflow_version_id=uuid4(),
        triggered_by="manual",
        input_payload={},
        created_at=now,
        updated_at=now,
    )

    # Patch the import inside the function by creating a mock module
    mock_module = type(sys)("orcheo_backend.worker.tasks")
    mock_module.execute_run = MockTask()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "orcheo_backend.worker.tasks", mock_module)

    _triggers._enqueue_run_for_execution(run)

    assert (str(run_id), None) in enqueued_calls


@pytest.mark.asyncio
async def test_triggers_enqueue_run_for_execution_fallback_preserves_workspace_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback enqueue path passes workspace headers when apply_async is unavailable."""
    import sys
    from orcheo.models.workflow import WorkflowRun
    from orcheo_backend.app.repository_postgres import _triggers

    enqueued_calls: list[tuple[str, Any | None]] = []

    class MockTask:
        @staticmethod
        def delay(run_id: str, headers: dict[str, Any] | None = None) -> None:
            enqueued_calls.append((run_id, headers))

    run_id = uuid4()
    now = datetime.now(tz=UTC)
    run = WorkflowRun(
        id=run_id,
        workflow_version_id=uuid4(),
        triggered_by="manual",
        input_payload={},
        created_at=now,
        updated_at=now,
        workspace_id="workspace-a",
    )

    mock_module = type(sys)("orcheo_backend.worker.tasks")
    mock_module.execute_run = MockTask()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "orcheo_backend.worker.tasks", mock_module)

    _triggers._enqueue_run_for_execution(run)

    assert (str(run_id), {"workspace_id": "workspace-a"}) in enqueued_calls


@pytest.mark.asyncio
async def test_triggers_enqueue_run_for_execution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _enqueue_run_for_execution handles import/enqueue failure gracefully."""
    from orcheo.models.workflow import WorkflowRun
    from orcheo_backend.app.repository_postgres import _triggers

    # Create a mock run
    run_id = uuid4()
    now = datetime.now(tz=UTC)
    run = WorkflowRun(
        id=run_id,
        workflow_version_id=uuid4(),
        triggered_by="manual",
        input_payload={},
        created_at=now,
        updated_at=now,
    )

    # Make the import fail
    import sys

    if "orcheo_backend.worker.tasks" in sys.modules:
        monkeypatch.delitem(sys.modules, "orcheo_backend.worker.tasks")

    # Mock import to raise
    original_import = __builtins__["__import__"]  # type: ignore[index]

    def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if "orcheo_backend.worker.tasks" in name:
            raise ImportError("Celery not available")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)

    # Should not raise, just log warning
    _triggers._enqueue_run_for_execution(run)


@pytest.mark.asyncio
async def test_triggers_dispatch_due_cron_runs_with_naive_datetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test dispatch_due_cron_runs handles naive datetime by adding UTC."""
    from unittest.mock import patch

    workflow_id = uuid4()
    version_id = uuid4()
    version_payload = _version_payload(version_id, workflow_id)

    repo = make_repository(monkeypatch, [])
    config = CronTriggerConfig(
        expression="* * * * *", timezone="UTC", allow_overlapping=True
    )
    repo._trigger_layer.configure_cron(workflow_id, config)

    # Mock to return version
    from unittest.mock import AsyncMock
    from orcheo.models.workflow import WorkflowVersion

    version = WorkflowVersion.model_validate(version_payload)
    monkeypatch.setattr(
        repo, "_get_latest_version_locked", AsyncMock(return_value=version)
    )

    # Mock _create_run_locked
    run_id = uuid4()
    now = datetime.now(tz=UTC)
    from orcheo.models.workflow import WorkflowRun

    mock_run = WorkflowRun(
        id=run_id,
        workflow_version_id=version_id,
        triggered_by="cron",
        input_payload={},
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(repo, "_create_run_locked", AsyncMock(return_value=mock_run))

    # Mock _refresh_cron_triggers
    monkeypatch.setattr(repo, "_refresh_cron_triggers", AsyncMock())

    # Pass a naive datetime (no tzinfo)
    from datetime import timedelta

    naive_now = datetime.now() + timedelta(days=1)
    naive_now = naive_now.replace(tzinfo=None)

    with patch(
        "orcheo_backend.app.repository_postgres._triggers._enqueue_run_for_execution"
    ):
        await repo.dispatch_due_cron_runs(now=naive_now)

    # Should have processed (the naive datetime is converted to UTC)
    # The result depends on whether the cron expression matches


@pytest.mark.asyncio
async def test_triggers_dispatch_due_cron_runs_enqueues_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test dispatch_due_cron_runs enqueues runs after lock is released."""
    from unittest.mock import AsyncMock, patch
    from orcheo.models.workflow import WorkflowRun, WorkflowVersion

    workflow_id = uuid4()
    version_id = uuid4()
    version_payload = _version_payload(version_id, workflow_id)

    repo = make_repository(monkeypatch, [])
    config = CronTriggerConfig(
        expression="* * * * *", timezone="UTC", allow_overlapping=True
    )
    repo._trigger_layer.configure_cron(workflow_id, config)

    version = WorkflowVersion.model_validate(version_payload)
    monkeypatch.setattr(
        repo, "_get_latest_version_locked", AsyncMock(return_value=version)
    )

    run_id = uuid4()
    now = datetime.now(tz=UTC)
    mock_run = WorkflowRun(
        id=run_id,
        workflow_version_id=version_id,
        triggered_by="cron",
        input_payload={},
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(repo, "_create_run_locked", AsyncMock(return_value=mock_run))
    monkeypatch.setattr(repo, "_refresh_cron_triggers", AsyncMock())

    enqueued_runs: list[WorkflowRun] = []

    def track_enqueue(run: WorkflowRun) -> None:
        enqueued_runs.append(run)

    from datetime import timedelta

    future = datetime.now(tz=UTC) + timedelta(days=1)

    with patch(
        "orcheo_backend.app.repository_postgres._triggers._enqueue_run_for_execution",
        side_effect=track_enqueue,
    ):
        runs = await repo.dispatch_due_cron_runs(now=future)

    # If runs were created, they should have been enqueued
    assert len(enqueued_runs) == len(runs)


@pytest.mark.asyncio
async def test_triggers_dispatch_due_cron_runs_updates_last_dispatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cron dispatch should persist the latest dispatch time when available."""

    workflow_id = uuid4()
    version_id = uuid4()
    version_payload = _version_payload(version_id, workflow_id)

    repo = make_repository(monkeypatch, [])
    config = CronTriggerConfig(expression="0 9 * * *", timezone="UTC")
    repo._trigger_layer.configure_cron(workflow_id, config)

    from orcheo.models.workflow import WorkflowRun, WorkflowVersion

    version = WorkflowVersion.model_validate(version_payload)
    monkeypatch.setattr(
        repo, "_get_latest_version_locked", AsyncMock(return_value=version)
    )

    run_id = uuid4()
    now = datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
    mock_run = WorkflowRun(
        id=run_id,
        workflow_version_id=version_id,
        triggered_by="cron",
        input_payload={},
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(repo, "_create_run_locked", AsyncMock(return_value=mock_run))
    monkeypatch.setattr(repo, "_refresh_cron_triggers", AsyncMock())

    with patch(
        "orcheo_backend.app.repository_postgres._triggers._enqueue_run_for_execution"
    ):
        runs = await repo.dispatch_due_cron_runs(now=now)

    assert runs
    assert runs[0].id == run_id

    update_queries = [
        (query, params)
        for query, params in repo._pool._connection.queries
        if "UPDATE cron_triggers" in query
    ]
    assert len(update_queries) == 1
    _, params = update_queries[0]
    assert params[1] == str(workflow_id)
    assert params[0] == now


@pytest.mark.asyncio
async def test_triggers_configure_webhook_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test configuring a webhook trigger."""
    workflow_id = uuid4()
    workflow_payload = _workflow_payload(workflow_id)
    config = WebhookTriggerConfig(allowed_methods={"POST"})

    responses = [
        {"row": {"payload": workflow_payload}},  # _get_workflow_locked
        {},  # INSERT INTO webhook_triggers
    ]
    repo = make_repository(monkeypatch, responses)

    result = await repo.configure_webhook_trigger(workflow_id, config)
    assert "POST" in result.allowed_methods


@pytest.mark.asyncio
async def test_triggers_configure_cron_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test configuring a cron trigger."""
    workflow_id = uuid4()
    workflow_payload = _workflow_payload(workflow_id)
    config = CronTriggerConfig(expression="0 0 * * *", timezone="UTC")

    responses = [
        {"row": {"payload": workflow_payload}},  # _get_workflow_locked
        {},  # INSERT INTO cron_triggers
    ]
    repo = make_repository(monkeypatch, responses)

    result = await repo.configure_cron_trigger(workflow_id, config)
    assert result.expression == "0 0 * * *"


@pytest.mark.asyncio
async def test_triggers_delete_cron_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test deleting a cron trigger."""
    workflow_id = uuid4()
    workflow_payload = _workflow_payload(workflow_id)
    config = CronTriggerConfig(expression="0 0 * * *", timezone="UTC")

    responses = [
        {"row": {"payload": workflow_payload}},  # _get_workflow_locked
        {},  # DELETE FROM cron_triggers
    ]
    repo = make_repository(monkeypatch, responses)

    # Pre-configure in layer
    repo._trigger_layer.configure_cron(workflow_id, config)
    assert workflow_id in repo._trigger_layer._cron_states

    await repo.delete_cron_trigger(workflow_id)
    assert workflow_id not in repo._trigger_layer._cron_states


@pytest.mark.asyncio
async def test_triggers_dispatch_manual_runs_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test successful manual dispatch of runs."""
    workflow_id = uuid4()
    version_id = uuid4()
    run_id = uuid4()
    now_dt = datetime.now(tz=UTC)

    workflow_payload = _workflow_payload(workflow_id)
    version_payload = _version_payload(version_id, workflow_id)

    from orcheo.models.workflow import WorkflowRun, WorkflowRunStatus, WorkflowVersion

    mock_version = WorkflowVersion.model_validate(version_payload)
    mock_run = WorkflowRun(
        id=run_id,
        workflow_version_id=version_id,
        status=WorkflowRunStatus.PENDING,
        triggered_by="manual",
        input_payload={},
        created_at=now_dt,
        updated_at=now_dt,
    )

    responses = [
        {"row": {"payload": workflow_payload}},  # _get_workflow_locked
        {"row": {"payload": version_payload}},  # _get_latest_version_locked
    ]
    repo = make_repository(monkeypatch, responses)

    # Mock _get_version_locked and _create_run_locked
    monkeypatch.setattr(
        repo, "_get_version_locked", AsyncMock(return_value=mock_version)
    )
    monkeypatch.setattr(repo, "_create_run_locked", AsyncMock(return_value=mock_run))

    from orcheo.triggers.manual import ManualDispatchRequest

    request = ManualDispatchRequest(
        workflow_id=workflow_id,
        runs=[{"workflow_version_id": version_id, "input_payload": {"test": 1}}],
    )

    with patch(
        "orcheo_backend.app.repository_postgres._triggers._enqueue_run_for_execution"
    ) as mock_enqueue:
        runs = await repo.dispatch_manual_runs(request)

    assert len(runs) == 1
    assert runs[0].id == run_id
    assert mock_enqueue.called


@pytest.mark.asyncio
async def test_hydrate_trigger_state_non_cron_run_skips_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-cron triggered runs are tracked but not registered in the cron overlap set.

    This exercises the False branch of the 'triggered_by == cron' condition inside
    _hydrate_trigger_state (line 320->315 in coverage terms).
    """
    w_id = uuid4()
    run_id = uuid4()
    cron_conf = CronTriggerConfig(expression="0 9 * * *", timezone="UTC").model_dump(
        mode="json"
    )

    class SmartFakeConnection:
        def __init__(self) -> None:
            self.queries: list[tuple[str, Any]] = []
            self.commits = 0
            self.rollbacks = 0

        async def execute(self, query: str, params: Any = None) -> FakeCursor:
            self.queries.append((query, params))
            if "information_schema.columns" in query:
                return FakeCursor(rows=[])
            if "FROM cron_triggers" in query:
                return FakeCursor(
                    rows=[
                        {
                            "workflow_id": str(w_id),
                            "config": cron_conf,
                            "last_dispatched_at": None,
                        }
                    ]
                )
            if "workflow_runs" in query and "status" in query:
                # Return a webhook-triggered (non-cron) PENDING run
                return FakeCursor(
                    rows=[
                        {
                            "id": str(run_id),
                            "workflow_id": str(w_id),
                            "triggered_by": "webhook",
                            "status": "pending",
                        }
                    ]
                )
            return FakeCursor(rows=[])

        async def commit(self) -> None:
            self.commits += 1

        async def rollback(self) -> None:
            self.rollbacks += 1

        async def __aenter__(self) -> SmartFakeConnection:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

    repo = make_repository(monkeypatch, [], initialized=False)
    repo._pool = FakePool(SmartFakeConnection())  # type: ignore[arg-type]
    repo._initialized = False

    await repo._ensure_initialized()

    # The run should be tracked but NOT in the cron overlap registry
    assert run_id in repo._trigger_layer._run_workflows  # noqa: SLF001
    assert run_id not in repo._trigger_layer._cron_run_index  # noqa: SLF001


@pytest.mark.asyncio
async def test_hydrate_trigger_state_cron_overlap_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two non-failed cron runs with allow_overlapping=False logs a warning.

    When the second register_cron_run raises CronOverlapError during hydration
    the exception is caught and a warning is emitted (lines 326-327 in coverage).
    """
    import logging

    w_id = uuid4()
    run_id_1 = uuid4()
    run_id_2 = uuid4()
    cron_conf = CronTriggerConfig(
        expression="0 9 * * *", timezone="UTC", allow_overlapping=False
    ).model_dump(mode="json")

    class SmartFakeConnection:
        def __init__(self) -> None:
            self.queries: list[tuple[str, Any]] = []
            self.commits = 0
            self.rollbacks = 0

        async def execute(self, query: str, params: Any = None) -> FakeCursor:
            self.queries.append((query, params))
            if "information_schema.columns" in query:
                return FakeCursor(rows=[])
            if "FROM cron_triggers" in query:
                return FakeCursor(
                    rows=[
                        {
                            "workflow_id": str(w_id),
                            "config": cron_conf,
                            "last_dispatched_at": None,
                        }
                    ]
                )
            if "workflow_runs" in query and "status" in query:
                # Two PENDING cron runs for the same workflow
                return FakeCursor(
                    rows=[
                        {
                            "id": str(run_id_1),
                            "workflow_id": str(w_id),
                            "triggered_by": "cron",
                            "status": "pending",
                        },
                        {
                            "id": str(run_id_2),
                            "workflow_id": str(w_id),
                            "triggered_by": "cron",
                            "status": "pending",
                        },
                    ]
                )
            return FakeCursor(rows=[])

        async def commit(self) -> None:
            self.commits += 1

        async def rollback(self) -> None:
            self.rollbacks += 1

        async def __aenter__(self) -> SmartFakeConnection:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

    repo = make_repository(monkeypatch, [], initialized=False)
    repo._pool = FakePool(SmartFakeConnection())  # type: ignore[arg-type]
    repo._initialized = False

    with caplog.at_level(logging.WARNING):
        await repo._ensure_initialized()

    assert any(
        "Skipped cron overlap registration" in record.message
        for record in caplog.records
    )
    # First run should be registered; second skipped due to overlap
    assert run_id_1 in repo._trigger_layer._cron_run_index  # noqa: SLF001
    assert run_id_2 not in repo._trigger_layer._cron_run_index  # noqa: SLF001


@pytest.mark.asyncio
async def test_versions_create_version_without_listener_mixin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_version skips listener sync when not combined with
    ListenerRepositoryMixin."""
    from orcheo_backend.app.repository_postgres._versions import (
        WorkflowVersionMixin as PgWorkflowVersionMixin,
    )

    class _VersionOnlyRepo(PgWorkflowVersionMixin):
        pass

    monkeypatch.setattr(pg_base, "AsyncConnectionPool", object())
    monkeypatch.setattr(pg_base, "DictRowFactory", object())

    workflow_id = uuid4()
    responses: list[Any] = [
        {"row": {"payload": _workflow_payload(workflow_id)}},  # _get_workflow_locked
        {"row": {"max_version": 0}},  # SELECT COALESCE(MAX(version), 0)
        {},  # INSERT workflow_versions
    ]
    repo = _VersionOnlyRepo("postgresql://test")
    repo._pool = FakePool(FakeConnection(responses))
    repo._initialized = True

    version = await repo.create_version(
        workflow_id,
        graph={"nodes": []},
        metadata={},
        notes=None,
        created_by="author",
    )
    assert version.version == 1


@pytest.mark.asyncio
async def test_workflows_maybe_disable_listeners_method_not_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_maybe_disable_listener_subscriptions returns early when the disable
    method is absent."""
    from orcheo_backend.app.repository_postgres._workflows import (
        WorkflowRepositoryMixin as PgWorkflowRepositoryMixin,
    )

    class _WorkflowRepoNoDisable(PgWorkflowRepositoryMixin):
        pass

    monkeypatch.setattr(pg_base, "AsyncConnectionPool", object())
    monkeypatch.setattr(pg_base, "DictRowFactory", object())

    repo = _WorkflowRepoNoDisable("postgresql://test")
    repo._initialized = True

    # Should complete without error; returns early because method is absent.
    await repo._maybe_disable_listener_subscriptions(  # noqa: SLF001
        uuid4(), should_disable=True, actor="admin", conn=None
    )


@pytest.mark.asyncio
async def test_workflows_maybe_disable_listeners_sync_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_maybe_disable_listener_subscriptions handles a synchronous disable method."""
    from orcheo_backend.app.repository_postgres._workflows import (
        WorkflowRepositoryMixin as PgWorkflowRepositoryMixin,
    )

    calls: list[str] = []

    class _WorkflowRepoSyncDisable(PgWorkflowRepositoryMixin):
        def _disable_listener_subscriptions_locked(
            self, workflow_id: UUID, *, actor: str, conn: Any
        ) -> None:
            del workflow_id, actor, conn
            calls.append("called")

    monkeypatch.setattr(pg_base, "AsyncConnectionPool", object())
    monkeypatch.setattr(pg_base, "DictRowFactory", object())

    repo = _WorkflowRepoSyncDisable("postgresql://test")
    repo._initialized = True

    await repo._maybe_disable_listener_subscriptions(  # noqa: SLF001
        uuid4(), should_disable=True, actor="admin", conn=None
    )
    assert calls == ["called"]
