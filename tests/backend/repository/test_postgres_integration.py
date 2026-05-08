"""Integration tests for PostgreSQL workflow repository.

These tests verify complex multi-step scenarios and cross-subsystem behavior
using fake database connections to simulate PostgreSQL interactions.
"""

from __future__ import annotations
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4
import pytest
from orcheo.models.workflow import WorkflowDraftAccess, WorkflowRunStatus
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.manual import ManualDispatchItem, ManualDispatchRequest
from orcheo.triggers.retry import RetryPolicyConfig
from orcheo.triggers.webhook import WebhookTriggerConfig
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
        self, *, row: dict[str, Any] | None = None, rows: list[Any] | None = None
    ) -> None:
        self._row = FakeRow(row) if row else None
        self._rows = [FakeRow(r) if isinstance(r, dict) else r for r in (rows or [])]

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
            return FakeCursor(row=response.get("row"), rows=response.get("rows"))
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

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

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


def _run_payload(
    run_id: UUID,
    version_id: UUID,
    *,
    status: str = "pending",
    triggered_by: str = "manual",
    **overrides: Any,
) -> dict[str, Any]:
    """Generate a fake run payload dictionary."""
    now = datetime.now(tz=UTC).isoformat()
    base = {
        "id": str(run_id),
        "workflow_version_id": str(version_id),
        "status": status,
        "triggered_by": triggered_by,
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
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_postgres_workflow_full_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test complete lifecycle: create -> version -> trigger -> run -> complete."""
    workflow_id = uuid4()
    # version_id and run_id used in responses but not referenced directly
    _ = uuid4()
    _ = uuid4()

    responses: list[Any] = [
        # create_workflow: INSERT
        {},
        # configure_webhook_trigger: get workflow, insert trigger
        {"row": {"payload": _workflow_payload(workflow_id)}},
        {},
        # configure_cron_trigger: get workflow, insert trigger
        {"row": {"payload": _workflow_payload(workflow_id)}},
        {},
        # create_version: get workflow, select max, insert version
        {"row": {"payload": _workflow_payload(workflow_id)}},
        {"row": {"max_version": 0}},
        {},
    ]
    repo = make_repository(monkeypatch, responses)

    # Create workflow
    workflow = await repo.create_workflow(
        name="Full Lifecycle",
        slug="full-lifecycle",
        description="Testing complete lifecycle",
        tags=["test", "lifecycle"],
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    assert workflow.name == "Full Lifecycle"

    # Configure triggers
    webhook_config = await repo.configure_webhook_trigger(
        workflow.id, WebhookTriggerConfig(allowed_methods={"POST"})
    )
    assert "POST" in webhook_config.allowed_methods

    cron_config = await repo.configure_cron_trigger(
        workflow.id, CronTriggerConfig(expression="0 9 * * *", timezone="UTC")
    )
    assert cron_config.expression == "0 9 * * *"

    # Create version
    version = await repo.create_version(
        workflow.id,
        graph={"nodes": ["start", "end"]},
        metadata={"type": "test"},
        notes="Initial version",
        created_by="author",
    )
    assert version.version == 1


@pytest.mark.asyncio
async def test_postgres_retry_policy_with_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test retry policy configuration and scheduling for failed runs."""
    workflow_id = uuid4()
    version_id = uuid4()
    run_id = uuid4()

    responses: list[Any] = [
        # configure_retry_policy: get workflow, insert policy
        {"row": {"payload": _workflow_payload(workflow_id)}},
        {},
        # schedule_retry_for_run: get run
        {
            "row": {
                "payload": _run_payload(run_id, version_id, status="failed"),
                "workflow_id": str(workflow_id),
                "triggered_by": "manual",
                "status": "failed",
            }
        },
    ]
    repo = make_repository(monkeypatch, responses)

    # Configure retry policy
    config = await repo.configure_retry_policy(
        workflow_id,
        RetryPolicyConfig(
            max_attempts=3,
            initial_delay_seconds=1.0,
            backoff_factor=2.0,
            jitter_factor=0.0,
        ),
    )
    assert config.max_attempts == 3

    # Track run for retry lookup
    repo._trigger_layer.track_run(workflow_id, run_id)

    # Schedule retry
    decision = await repo.schedule_retry_for_run(run_id)
    assert decision is not None
    assert decision.retry_number == 1
    assert decision.delay_seconds >= 1.0


@pytest.mark.asyncio
async def test_postgres_cron_trigger_state_management(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test cron trigger state is properly tracked and can be deleted."""
    workflow_id = uuid4()

    responses: list[Any] = [
        # configure_cron_trigger: get workflow, insert trigger
        {"row": {"payload": _workflow_payload(workflow_id)}},
        {},
        # get_cron_trigger_config: get workflow
        {"row": {"payload": _workflow_payload(workflow_id)}},
        # delete_cron_trigger: get workflow, delete
        {"row": {"payload": _workflow_payload(workflow_id)}},
        {},
    ]
    repo = make_repository(monkeypatch, responses)

    # Configure cron trigger
    await repo.configure_cron_trigger(
        workflow_id, CronTriggerConfig(expression="*/5 * * * *", timezone="UTC")
    )

    # Verify state is tracked
    assert workflow_id in repo._trigger_layer._cron_states

    # Get config (verifies it's accessible)
    config = await repo.get_cron_trigger_config(workflow_id)
    assert config.expression == "*/5 * * * *"

    # Delete and verify removal
    await repo.delete_cron_trigger(workflow_id)
    assert workflow_id not in repo._trigger_layer._cron_states


@pytest.mark.asyncio
async def test_postgres_run_status_transition_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test run status transitions: pending -> running -> completed."""
    workflow_id = uuid4()
    version_id = uuid4()
    run_id = uuid4()

    # Create payloads with different statuses
    pending_run = _run_payload(run_id, version_id, status="pending")
    running_run = _run_payload(run_id, version_id, status="running")

    responses: list[Any] = [
        # mark_run_started: get run, update
        {
            "row": {
                "payload": pending_run,
                "workflow_id": str(workflow_id),
                "triggered_by": "manual",
                "status": "pending",
            }
        },
        {},
        # mark_run_succeeded: get run, update
        {
            "row": {
                "payload": running_run,
                "workflow_id": str(workflow_id),
                "triggered_by": "manual",
                "status": "running",
            }
        },
        {},
    ]
    repo = make_repository(monkeypatch, responses)

    # Start run
    run = await repo.mark_run_started(run_id, actor="worker")
    assert run.status == WorkflowRunStatus.RUNNING

    # Complete run
    run = await repo.mark_run_succeeded(
        run_id,
        actor="worker",
        output={"result": "success", "data": [1, 2, 3]},
    )
    assert run.status == WorkflowRunStatus.SUCCEEDED
    assert run.output_payload["result"] == "success"


@pytest.mark.asyncio
async def test_postgres_multiple_trigger_types_coexist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test workflow can have webhook, cron, and retry triggers together."""
    workflow_id = uuid4()

    responses: list[Any] = [
        # configure_webhook_trigger
        {"row": {"payload": _workflow_payload(workflow_id)}},
        {},
        # configure_cron_trigger
        {"row": {"payload": _workflow_payload(workflow_id)}},
        {},
        # configure_retry_policy
        {"row": {"payload": _workflow_payload(workflow_id)}},
        {},
    ]
    repo = make_repository(monkeypatch, responses)

    # Configure all trigger types
    webhook = await repo.configure_webhook_trigger(
        workflow_id, WebhookTriggerConfig(allowed_methods={"POST", "PUT"})
    )
    cron = await repo.configure_cron_trigger(
        workflow_id,
        CronTriggerConfig(expression="0 0 * * *", timezone="America/New_York"),
    )
    retry = await repo.configure_retry_policy(
        workflow_id,
        RetryPolicyConfig(max_attempts=5, initial_delay_seconds=30.0),
    )

    # All configurations should be stored
    assert "POST" in webhook.allowed_methods
    assert cron.timezone == "America/New_York"
    assert retry.max_attempts == 5

    # Verify all are in the trigger layer
    assert workflow_id in repo._trigger_layer._webhook_states
    assert workflow_id in repo._trigger_layer._cron_states
    assert workflow_id in repo._trigger_layer._retry_configs


@pytest.mark.asyncio
async def test_postgres_version_incrementing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test version numbers auto-increment correctly."""
    workflow_id = uuid4()
    # version IDs generated for internal tracking
    _ = uuid4()
    _ = uuid4()

    responses: list[Any] = [
        # First version: get workflow, get max (0), insert, listener subs (no rows)
        {"row": {"payload": _workflow_payload(workflow_id)}},
        {"row": {"max_version": 0}},
        {},
        {},
        # Second version: get workflow, get max (1), insert, listener subs (no rows)
        {"row": {"payload": _workflow_payload(workflow_id)}},
        {"row": {"max_version": 1}},
        {},
        {},
        # Third version: get workflow, get max (2), insert, listener subs (no rows)
        {"row": {"payload": _workflow_payload(workflow_id)}},
        {"row": {"max_version": 2}},
        {},
        {},
    ]
    repo = make_repository(monkeypatch, responses)

    v1 = await repo.create_version(
        workflow_id, graph={"v": 1}, metadata={}, notes="v1", created_by="dev"
    )
    assert v1.version == 1

    v2 = await repo.create_version(
        workflow_id, graph={"v": 2}, metadata={}, notes="v2", created_by="dev"
    )
    assert v2.version == 2

    v3 = await repo.create_version(
        workflow_id, graph={"v": 3}, metadata={}, notes="v3", created_by="dev"
    )
    assert v3.version == 3


@pytest.mark.asyncio
async def test_postgres_workflow_archive_and_list_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test archived workflows are filtered from default listing."""
    workflow_id_1 = uuid4()
    workflow_id_2 = uuid4()
    workflow_id_3 = uuid4()

    active_workflow = _workflow_payload(workflow_id_1, name="Active 1")
    archived_workflow = _workflow_payload(
        workflow_id_2, name="Archived", is_archived=True
    )
    active_workflow_2 = _workflow_payload(workflow_id_3, name="Active 2")

    responses: list[Any] = [
        # list_workflows: select all
        {
            "rows": [
                {"payload": active_workflow},
                {"payload": archived_workflow},
                {"payload": active_workflow_2},
            ]
        },
        # list_workflows with include_archived: select all
        {
            "rows": [
                {"payload": active_workflow},
                {"payload": archived_workflow},
                {"payload": active_workflow_2},
            ]
        },
    ]
    repo = make_repository(monkeypatch, responses)

    # Default listing excludes archived
    workflows = await repo.list_workflows()
    assert len(workflows) == 2
    assert all(not w.is_archived for w in workflows)

    # Include archived shows all
    all_workflows = await repo.list_workflows(include_archived=True)
    assert len(all_workflows) == 3


@pytest.mark.asyncio
async def test_postgres_workflow_update_tracks_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test workflow update records change metadata in audit log."""
    workflow_id = uuid4()

    responses: list[Any] = [
        # update_workflow: get workflow, update
        {
            "row": {
                "payload": _workflow_payload(
                    workflow_id,
                    name="Original",
                    description="Original description",
                    tags=["old"],
                )
            }
        },
        {},
    ]
    repo = make_repository(monkeypatch, responses)

    workflow = await repo.update_workflow(
        workflow_id,
        name="Updated Name",
        description="New description",
        tags=["new", "updated"],
        is_archived=None,
        actor="updater",
    )

    assert workflow.name == "Updated Name"
    assert workflow.description == "New description"
    assert workflow.tags == ["new", "updated"]

    # Check audit log contains the update event
    update_event = next(
        (e for e in workflow.audit_log if e.action == "workflow_updated"), None
    )
    assert update_event is not None
    assert update_event.actor == "updater"
    # Metadata should contain the changes
    assert "name" in update_event.metadata
    assert update_event.metadata["name"]["from"] == "Original"
    assert update_event.metadata["name"]["to"] == "Updated Name"


@pytest.mark.asyncio
async def test_postgres_run_failure_tracks_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test failed runs properly track error message."""
    workflow_id = uuid4()
    version_id = uuid4()
    run_id = uuid4()

    responses: list[Any] = [
        {
            "row": {
                "payload": _run_payload(run_id, version_id, status="running"),
                "workflow_id": str(workflow_id),
                "triggered_by": "manual",
                "status": "running",
            }
        },
        {},
    ]
    repo = make_repository(monkeypatch, responses)

    run = await repo.mark_run_failed(
        run_id,
        actor="worker",
        error="Connection timeout: database unavailable after 30s",
    )

    assert run.status == WorkflowRunStatus.FAILED
    assert run.error == "Connection timeout: database unavailable after 30s"
    assert run.completed_at is not None

    # Check audit log
    fail_event = next((e for e in run.audit_log if e.action == "run_failed"), None)
    assert fail_event is not None
    assert (
        fail_event.metadata["error"]
        == "Connection timeout: database unavailable after 30s"
    )


@pytest.mark.asyncio
async def test_postgres_manual_dispatch_request_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test manual dispatch creates runs with correct configuration."""
    from orcheo_backend.app.repository_postgres import _triggers

    workflow_id = uuid4()
    version_id = uuid4()

    responses: list[Any] = [
        # dispatch_manual_runs: get workflow
        {"row": {"payload": _workflow_payload(workflow_id)}},
        # get latest version
        {"row": {"payload": _version_payload(version_id, workflow_id, version=1)}},
        # get workflow workspace id
        {"row": {"workspace_id": None}},
        # validate version belongs to workflow
        {"row": {"payload": _version_payload(version_id, workflow_id, version=1)}},
        # get version for run creation
        {"row": {"payload": _version_payload(version_id, workflow_id, version=1)}},
        # insert run
        {},
    ]
    repo = make_repository(monkeypatch, responses)

    # Monkeypatch the execute_run.delay to avoid Celery import issues
    monkeypatch.setattr(
        _triggers,
        "_enqueue_run_for_execution",
        lambda run: None,
    )

    request = ManualDispatchRequest(
        workflow_id=workflow_id,
        actor="operator",
        runs=[
            ManualDispatchItem(
                input_payload={"key": "value"},
            ),
        ],
    )

    runs = await repo.dispatch_manual_runs(request)

    assert len(runs) == 1
    assert runs[0].triggered_by == "manual"
    assert runs[0].input_payload == {"key": "value"}
    assert runs[0].status == WorkflowRunStatus.PENDING


@pytest.mark.asyncio
async def test_postgres_base_sync_listener_subscriptions_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 193: _sync_listener_subscriptions_locked is a no-op on the base class."""
    repo = make_repository(monkeypatch, [])
    # Call the base-class stub directly; it should return None without error
    repo._sync_listener_subscriptions_locked(  # noqa: SLF001
        uuid4(), uuid4(), {}, actor="test"
    )
