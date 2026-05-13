"""Cross-workspace isolation tests for the workflow repository.

Verifies that workflows and runs created under one workspace are not visible to
another workspace, regardless of whether the handle or UUID is known.
"""

from __future__ import annotations
from uuid import UUID
import pytest
import pytest_asyncio
import redis
from orcheo.workspace import InMemoryWorkspaceRepository as WorkspaceRepository
from orcheo.workspace import Workspace
from orcheo_backend.app import workspace_governance
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
)
from orcheo_backend.app.repository.in_memory import InMemoryWorkflowRepository
from orcheo_backend.app.workspace import reset_workspace_state, set_workspace_repository


WORKSPACE_A = "11111111-1111-1111-1111-111111111111"
WORKSPACE_B = "22222222-2222-2222-2222-222222222222"


@pytest_asyncio.fixture()
async def repository(monkeypatch: pytest.MonkeyPatch) -> InMemoryWorkflowRepository:
    repo = InMemoryWorkflowRepository()
    monkeypatch.setattr(
        workspace_governance.redis,
        "from_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(redis.RedisError("boom")),
    )
    reset_workspace_state()
    workspace_repo = WorkspaceRepository()
    workspace_repo.create_workspace(
        Workspace(
            id=UUID(WORKSPACE_A),
            slug="workspace-a",
            name="Workspace A",
        )
    )
    workspace_repo.create_workspace(
        Workspace(
            id=UUID(WORKSPACE_B),
            slug="workspace-b",
            name="Workspace B",
        )
    )
    set_workspace_repository(workspace_repo)
    try:
        yield repo
    finally:
        await repo.reset()
        reset_workspace_state()


# ── Workflow isolation ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_workflows_workspace_scoped(
    repository: InMemoryWorkflowRepository,
) -> None:
    """list_workflows returns only workflows belonging to the requested workspace."""
    wf_a = await repository.create_workflow(
        name="Workflow A",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        workspace_id=WORKSPACE_A,
    )
    await repository.create_workflow(
        name="Workflow B",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        workspace_id=WORKSPACE_B,
    )

    result_a = await repository.list_workflows(workspace_id=WORKSPACE_A)
    result_b = await repository.list_workflows(workspace_id=WORKSPACE_B)

    assert len(result_a) == 1
    assert result_a[0].id == wf_a.id
    assert len(result_b) == 1
    assert result_b[0].name == "Workflow B"


@pytest.mark.asyncio
async def test_get_workflow_cross_workspace_raises(
    repository: InMemoryWorkflowRepository,
) -> None:
    """get_workflow raises WorkflowNotFoundError when workspace_id does not match."""
    wf = await repository.create_workflow(
        name="Private",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        workspace_id=WORKSPACE_A,
    )

    with pytest.raises(WorkflowNotFoundError):
        await repository.get_workflow(wf.id, workspace_id=WORKSPACE_B)


@pytest.mark.asyncio
async def test_resolve_workflow_ref_cross_workspace_raises(
    repository: InMemoryWorkflowRepository,
) -> None:
    """resolve_workflow_ref raises WorkflowNotFoundError for a cross-workspace handle."""
    wf = await repository.create_workflow(
        name="Owned by A",
        handle="owned-by-a",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        workspace_id=WORKSPACE_A,
    )

    # Lookup by handle from the wrong workspace must fail
    with pytest.raises(WorkflowNotFoundError):
        await repository.resolve_workflow_ref("owned-by-a", workspace_id=WORKSPACE_B)

    # Lookup by UUID from the wrong workspace must fail
    with pytest.raises(WorkflowNotFoundError):
        await repository.resolve_workflow_ref(str(wf.id), workspace_id=WORKSPACE_B)


@pytest.mark.asyncio
async def test_resolve_workflow_ref_correct_workspace_succeeds(
    repository: InMemoryWorkflowRepository,
) -> None:
    """resolve_workflow_ref resolves successfully when workspace matches."""
    wf = await repository.create_workflow(
        name="My Workflow",
        handle="my-workflow",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        workspace_id=WORKSPACE_A,
    )

    resolved = await repository.resolve_workflow_ref(
        "my-workflow", workspace_id=WORKSPACE_A
    )
    assert resolved == wf.id

    resolved_by_id = await repository.resolve_workflow_ref(
        str(wf.id), workspace_id=WORKSPACE_A
    )
    assert resolved_by_id == wf.id


# ── Run isolation ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_runs_for_workflow_workspace_scoped(
    repository: InMemoryWorkflowRepository,
) -> None:
    """list_runs_for_workflow filters runs to the requested workspace."""
    wf = await repository.create_workflow(
        name="Shared Workflow",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        workspace_id=WORKSPACE_A,
    )
    version = await repository.create_version(
        wf.id,
        graph={"nodes": [], "edges": []},
        metadata={},
        notes=None,
        created_by="sys",
        runnable_config=None,
    )
    run_a = await repository.create_run(
        wf.id,
        workflow_version_id=version.id,
        triggered_by="api",
        input_payload={},
        workspace_id=WORKSPACE_A,
    )
    run_b = await repository.create_run(
        wf.id,
        workflow_version_id=version.id,
        triggered_by="api",
        input_payload={},
        workspace_id=WORKSPACE_B,
    )

    runs_a = await repository.list_runs_for_workflow(wf.id, workspace_id=WORKSPACE_A)
    runs_b = await repository.list_runs_for_workflow(wf.id, workspace_id=WORKSPACE_B)

    assert len(runs_a) == 1
    assert runs_a[0].id == run_a.id
    assert len(runs_b) == 1
    assert runs_b[0].id == run_b.id


@pytest.mark.asyncio
async def test_get_run_cross_workspace_raises(
    repository: InMemoryWorkflowRepository,
) -> None:
    """get_run raises WorkflowRunNotFoundError when workspace_id does not match."""
    wf = await repository.create_workflow(
        name="Shared Workflow",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        workspace_id=WORKSPACE_A,
    )
    version = await repository.create_version(
        wf.id,
        graph={"nodes": [], "edges": []},
        metadata={},
        notes=None,
        created_by="sys",
        runnable_config=None,
    )
    run = await repository.create_run(
        wf.id,
        workflow_version_id=version.id,
        triggered_by="api",
        input_payload={},
        workspace_id=WORKSPACE_A,
    )

    with pytest.raises(WorkflowRunNotFoundError):
        await repository.get_run(run.id, workspace_id=WORKSPACE_B)


@pytest.mark.asyncio
async def test_get_run_correct_workspace_succeeds(
    repository: InMemoryWorkflowRepository,
) -> None:
    """get_run succeeds when workspace_id matches."""
    wf = await repository.create_workflow(
        name="Shared Workflow",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        workspace_id=WORKSPACE_A,
    )
    version = await repository.create_version(
        wf.id,
        graph={"nodes": [], "edges": []},
        metadata={},
        notes=None,
        created_by="sys",
        runnable_config=None,
    )
    run = await repository.create_run(
        wf.id,
        workflow_version_id=version.id,
        triggered_by="api",
        input_payload={},
        workspace_id=WORKSPACE_A,
    )

    fetched = await repository.get_run(run.id, workspace_id=WORKSPACE_A)
    assert fetched.id == run.id


# ── Helpers ────────────────────────────────────────────────────────────────


def _draft_access() -> object:
    from orcheo.models.workflow import WorkflowDraftAccess

    return WorkflowDraftAccess.AUTHENTICATED
