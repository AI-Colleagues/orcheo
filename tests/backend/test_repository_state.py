"""Tests for the in-memory repository shared state helpers."""

from __future__ import annotations
from collections.abc import Mapping
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from orcheo.models.workflow import Workflow, WorkflowVersion
from orcheo.vault.oauth.models import CredentialHealthError
from orcheo_backend.app.repository.errors import WorkflowRunNotFoundError
from orcheo_backend.app.repository.in_memory.state import InMemoryRepositoryState


def _build_state() -> tuple[InMemoryRepositoryState, UUID, UUID]:
    state = InMemoryRepositoryState()
    workflow = Workflow(name="test-workflow")
    state._workflows[workflow.id] = workflow
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version=1,
        graph={},
        metadata={},
        created_by="tester",
    )
    state._versions[version.id] = version
    return state, workflow.id, version.id


def test_sync_listener_subscriptions_locked_noop() -> None:
    """Calling the base listener sync helper should be a safe no-op."""
    state = InMemoryRepositoryState()
    state._sync_listener_subscriptions_locked(  # noqa: SLF001
        uuid4(),
        uuid4(),
        {},
        actor="tester",
    )


class _DummyTriggerLayer:
    """Simple stub used to observe cron release calls."""

    def __init__(self) -> None:
        self.released: list[UUID] = []

    def release_cron_run(self, run_id: UUID) -> None:
        self.released.append(run_id)


class DummyCredentialService:
    """Minimal credential service stub used by health guard tests."""

    def __init__(self, report: SimpleNamespace) -> None:
        self.report = report
        self.calls: list[tuple[UUID, str | None]] = []

    async def ensure_workflow_health(
        self,
        workflow_id: UUID,
        *,
        actor: str | None = None,
        workspace_id: str | None = None,
    ) -> SimpleNamespace:
        self.calls.append((workflow_id, actor))
        return self.report

    def is_workflow_healthy(self, workflow_id: UUID) -> bool:
        return getattr(self.report, "is_healthy", True)

    def get_report(self, workflow_id: UUID) -> SimpleNamespace | None:
        return self.report


class _ConfigModel:
    """Minimal config object that exposes `model_dump`."""

    def model_dump(self, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {
            "tags": ["tag"],
            "callbacks": ["callback"],
            "metadata": {"ctx": "value"},
            "run_name": "model-run",
        }


class _MappingConfig(Mapping[str, object]):
    """Mapping wrapper that exposes workflow config data."""

    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def test_create_run_locked_with_mapping_config() -> None:
    state, workflow_id, version_id = _build_state()
    run = state._create_run_locked(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        triggered_by="user",
        input_payload={"input": 1},
        runnable_config={
            "tags": ["tag"],
            "callbacks": ["cb"],
            "metadata": {"a": "b"},
            "run_name": "mapped",
        },
    )

    assert run.tags == ["tag"]
    assert run.callbacks == ["cb"]
    assert run.metadata == {"a": "b"}
    assert run.run_name == "mapped"


def test_create_run_locked_with_model_dump_config() -> None:
    state, workflow_id, version_id = _build_state()
    config = _ConfigModel()
    run = state._create_run_locked(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        triggered_by="user",
        input_payload={},
        runnable_config=config,
    )

    assert run.tags == ["tag"]
    assert run.callbacks == ["callback"]
    assert run.metadata == {"ctx": "value"}
    assert run.run_name == "model-run"


def test_create_run_locked_accepts_mapping_subclass() -> None:
    state, workflow_id, version_id = _build_state()
    mapping_config = _MappingConfig(
        {
            "tags": ["mapped"],
            "callbacks": ["cb"],
            "metadata": {"env": "prod"},
            "run_name": "mapped-run",
        }
    )
    run = state._create_run_locked(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        triggered_by="user",
        input_payload={},
        runnable_config=mapping_config,
    )

    assert run.tags == ["mapped"]
    assert run.callbacks == ["cb"]
    assert run.metadata == {"env": "prod"}
    assert run.run_name == "mapped-run"


@pytest.mark.asyncio
async def test_update_run_applies_updater_and_returns_copy() -> None:
    state, workflow_id, version_id = _build_state()
    run = state._create_run_locked(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        triggered_by="user",
        input_payload={},
        runnable_config={"metadata": {"initial": "value"}},
    )

    def updater(target: object) -> None:
        target.metadata["updated"] = "yes"

    updated = await state._update_run(run.id, updater)

    assert updated.metadata["updated"] == "yes"
    assert updated is not state._runs[run.id]
    assert state._runs[run.id].metadata["updated"] == "yes"


@pytest.mark.asyncio
async def test_update_run_missing_run_raises() -> None:
    state = InMemoryRepositoryState()

    with pytest.raises(WorkflowRunNotFoundError):
        await state._update_run(uuid4(), lambda _: None)


def test_release_cron_run_delegates_to_trigger_layer() -> None:
    state = InMemoryRepositoryState()
    run_id = uuid4()
    stub = _DummyTriggerLayer()
    state._trigger_layer = stub

    state._release_cron_run(run_id)

    assert stub.released == [run_id]


@pytest.mark.asyncio()
async def test_create_run_locked_releases_workspace_slot_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace reservations are released if run creation fails mid-flight."""

    state, workflow_id, version_id = _build_state()
    workspace_id = str(uuid4())
    reserve_calls: list[tuple[str, int]] = []
    release_calls: list[str] = []

    class _WorkspaceRepo:
        def get_workspace(self, workspace_uuid: UUID) -> SimpleNamespace:
            assert str(workspace_uuid) == workspace_id
            return SimpleNamespace(
                quotas=SimpleNamespace(max_concurrent_runs=3),
            )

    class _Governance:
        def reserve_run_slot(self, workspace: str, *, limit: int) -> None:
            reserve_calls.append((workspace, limit))

        def release_run_slot(self, workspace: str) -> None:
            release_calls.append(workspace)

    from importlib import import_module

    workspace_module = import_module("orcheo_backend.app.workspace")
    workspace_dependencies = import_module("orcheo_backend.app.workspace.dependencies")
    governance_module = import_module("orcheo_backend.app.workspace_governance")
    monkeypatch.setattr(
        workspace_module,
        "get_workspace_repository",
        lambda: _WorkspaceRepo(),
    )
    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_repository",
        lambda: _WorkspaceRepo(),
    )
    monkeypatch.setattr(
        governance_module,
        "get_workspace_governance",
        lambda: _Governance(),
    )

    def _boom(*_: object, **__: object) -> None:
        raise RuntimeError("track failed")

    state._trigger_layer.track_run = _boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="track failed"):
        state._create_run_locked(
            workflow_id=workflow_id,
            workflow_version_id=version_id,
            triggered_by="manual",
            input_payload={},
            actor="tester",
            workspace_id=workspace_id,
        )

    assert reserve_calls == [(workspace_id, 3)]
    assert release_calls == [workspace_id]


@pytest.mark.asyncio()
async def test_resolve_workflow_ref_prefers_archived_match_then_uuid() -> None:
    """Archived matches and UUID references are resolved when workspace-scoped."""

    state = InMemoryRepositoryState()
    workflow_archived_a = Workflow(name="A", handle="shared-handle")
    workflow_archived_a.is_archived = True
    workflow_archived_a.updated_at = workflow_archived_a.updated_at.replace(year=2024)
    workflow_archived_b = Workflow(name="B", handle="shared-handle")
    workflow_archived_b.is_archived = True
    workflow_archived_b.updated_at = workflow_archived_b.updated_at.replace(year=2025)
    workflow_uuid = Workflow(name="Uuid Match")

    state._workflows[workflow_archived_a.id] = workflow_archived_a
    state._workflows[workflow_archived_b.id] = workflow_archived_b
    state._workflows[workflow_uuid.id] = workflow_uuid
    state._workflow_workspaces[workflow_archived_a.id] = "workspace-a"
    state._workflow_workspaces[workflow_archived_b.id] = "workspace-b"
    state._workflow_workspaces[workflow_uuid.id] = "workspace-b"
    state._rebuild_handle_indexes_locked()

    resolved_archived = await state.resolve_workflow_ref(
        "shared-handle",
        workspace_id="workspace-b",
    )
    resolved_uuid = await state.resolve_workflow_ref(str(workflow_uuid.id))

    assert resolved_archived == workflow_archived_b.id
    assert resolved_uuid == workflow_uuid.id


@pytest.mark.asyncio
async def test_ensure_workflow_health_no_service_returns() -> None:
    state = InMemoryRepositoryState()
    await state._ensure_workflow_health(uuid4())


@pytest.mark.asyncio
async def test_ensure_workflow_health_invokes_service() -> None:
    workflow_id = uuid4()
    report = SimpleNamespace(is_healthy=True, workflow_id=workflow_id, failures=[])
    service = DummyCredentialService(report)
    state = InMemoryRepositoryState(credential_service=service)

    await state._ensure_workflow_health(workflow_id, actor="tester")

    assert service.calls == [(workflow_id, "tester")]


@pytest.mark.asyncio
async def test_ensure_workflow_health_raises_for_unhealthy_report() -> None:
    workflow_id = uuid4()
    report = SimpleNamespace(
        is_healthy=False,
        workflow_id=workflow_id,
        failures=["unhealthy"],
    )
    service = DummyCredentialService(report)
    state = InMemoryRepositoryState(credential_service=service)

    with pytest.raises(CredentialHealthError):
        await state._ensure_workflow_health(workflow_id)

    assert service.calls == [(workflow_id, None)]
