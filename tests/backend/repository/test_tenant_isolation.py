"""Cross-tenant isolation tests for the workflow repository.

Verifies that workflows and runs created under one tenant are not visible to
another tenant, regardless of whether the handle or UUID is known.
"""

from __future__ import annotations
import pytest
import pytest_asyncio
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
)
from orcheo_backend.app.repository.in_memory import InMemoryWorkflowRepository


TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


@pytest_asyncio.fixture()
async def repository() -> InMemoryWorkflowRepository:
    repo = InMemoryWorkflowRepository()
    try:
        yield repo
    finally:
        await repo.reset()


# ── Workflow isolation ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_workflows_tenant_scoped(
    repository: InMemoryWorkflowRepository,
) -> None:
    """list_workflows returns only workflows belonging to the requested tenant."""
    wf_a = await repository.create_workflow(
        name="Workflow A",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        tenant_id=TENANT_A,
    )
    await repository.create_workflow(
        name="Workflow B",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        tenant_id=TENANT_B,
    )

    result_a = await repository.list_workflows(tenant_id=TENANT_A)
    result_b = await repository.list_workflows(tenant_id=TENANT_B)

    assert len(result_a) == 1
    assert result_a[0].id == wf_a.id
    assert len(result_b) == 1
    assert result_b[0].name == "Workflow B"


@pytest.mark.asyncio
async def test_get_workflow_cross_tenant_raises(
    repository: InMemoryWorkflowRepository,
) -> None:
    """get_workflow raises WorkflowNotFoundError when tenant_id does not match."""
    wf = await repository.create_workflow(
        name="Private",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        tenant_id=TENANT_A,
    )

    with pytest.raises(WorkflowNotFoundError):
        await repository.get_workflow(wf.id, tenant_id=TENANT_B)


@pytest.mark.asyncio
async def test_resolve_workflow_ref_cross_tenant_raises(
    repository: InMemoryWorkflowRepository,
) -> None:
    """resolve_workflow_ref raises WorkflowNotFoundError for a cross-tenant handle."""
    wf = await repository.create_workflow(
        name="Owned by A",
        handle="owned-by-a",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        tenant_id=TENANT_A,
    )

    # Lookup by handle from the wrong tenant must fail
    with pytest.raises(WorkflowNotFoundError):
        await repository.resolve_workflow_ref("owned-by-a", tenant_id=TENANT_B)

    # Lookup by UUID from the wrong tenant must fail
    with pytest.raises(WorkflowNotFoundError):
        await repository.resolve_workflow_ref(str(wf.id), tenant_id=TENANT_B)


@pytest.mark.asyncio
async def test_resolve_workflow_ref_correct_tenant_succeeds(
    repository: InMemoryWorkflowRepository,
) -> None:
    """resolve_workflow_ref resolves successfully when tenant matches."""
    wf = await repository.create_workflow(
        name="My Workflow",
        handle="my-workflow",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        tenant_id=TENANT_A,
    )

    resolved = await repository.resolve_workflow_ref("my-workflow", tenant_id=TENANT_A)
    assert resolved == wf.id

    resolved_by_id = await repository.resolve_workflow_ref(
        str(wf.id), tenant_id=TENANT_A
    )
    assert resolved_by_id == wf.id


# ── Run isolation ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_runs_for_workflow_tenant_scoped(
    repository: InMemoryWorkflowRepository,
) -> None:
    """list_runs_for_workflow filters runs to the requested tenant."""
    wf = await repository.create_workflow(
        name="Shared Workflow",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        tenant_id=TENANT_A,
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
        tenant_id=TENANT_A,
    )
    run_b = await repository.create_run(
        wf.id,
        workflow_version_id=version.id,
        triggered_by="api",
        input_payload={},
        tenant_id=TENANT_B,
    )

    runs_a = await repository.list_runs_for_workflow(wf.id, tenant_id=TENANT_A)
    runs_b = await repository.list_runs_for_workflow(wf.id, tenant_id=TENANT_B)

    assert len(runs_a) == 1
    assert runs_a[0].id == run_a.id
    assert len(runs_b) == 1
    assert runs_b[0].id == run_b.id


@pytest.mark.asyncio
async def test_get_run_cross_tenant_raises(
    repository: InMemoryWorkflowRepository,
) -> None:
    """get_run raises WorkflowRunNotFoundError when tenant_id does not match."""
    wf = await repository.create_workflow(
        name="Shared Workflow",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        tenant_id=TENANT_A,
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
        tenant_id=TENANT_A,
    )

    with pytest.raises(WorkflowRunNotFoundError):
        await repository.get_run(run.id, tenant_id=TENANT_B)


@pytest.mark.asyncio
async def test_get_run_correct_tenant_succeeds(
    repository: InMemoryWorkflowRepository,
) -> None:
    """get_run succeeds when tenant_id matches."""
    wf = await repository.create_workflow(
        name="Shared Workflow",
        slug="",
        description=None,
        tags=[],
        draft_access=_draft_access(),
        actor="sys",
        tenant_id=TENANT_A,
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
        tenant_id=TENANT_A,
    )

    fetched = await repository.get_run(run.id, tenant_id=TENANT_A)
    assert fetched.id == run.id


# ── Helpers ────────────────────────────────────────────────────────────────


def _draft_access() -> object:
    from orcheo.models.workflow import WorkflowDraftAccess

    return WorkflowDraftAccess.AUTHENTICATED
