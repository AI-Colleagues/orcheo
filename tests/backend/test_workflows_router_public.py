"""Coverage for the public workflow router endpoint."""

from __future__ import annotations
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo.models.workflow import Workflow
from orcheo_backend.app.repository import WorkflowNotFoundError
from orcheo_backend.app.routers import workflows
from orcheo_backend.app.routers.workflows import _resolve_chatkit_public_base_url


class _WorkflowRepo:
    def __init__(self, workflow: Workflow) -> None:
        self.workflow = workflow

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        tenant_id: str | None = None,
    ) -> UUID:
        del include_archived
        if UUID(str(workflow_ref)) != self.workflow.id:
            raise WorkflowNotFoundError(str(workflow_ref))
        return self.workflow.id

    async def get_workflow(self, workflow_id: UUID) -> Workflow:
        if workflow_id != self.workflow.id:
            raise WorkflowNotFoundError(str(workflow_id))
        return self.workflow


class _MissingWorkflowRepo:
    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        tenant_id: str | None = None,
    ) -> UUID:
        del include_archived
        raise WorkflowNotFoundError(str(workflow_ref))

    async def get_workflow(self, workflow_id: UUID) -> Workflow:
        raise WorkflowNotFoundError(str(workflow_id))


@pytest.mark.asyncio()
async def test_get_public_workflow_not_found_after_resolution() -> None:
    class _ResolveThenMissRepo:
        async def resolve_workflow_ref(
            self,
            workflow_ref: str,
            *,
            include_archived: bool = True,
            tenant_id: str | None = None,
        ) -> UUID:
            del include_archived
            return UUID(str(workflow_ref))

        async def get_workflow(self, workflow_id: UUID) -> Workflow:
            raise WorkflowNotFoundError(str(workflow_id))

    with pytest.raises(HTTPException) as excinfo:
        await workflows.get_public_workflow(
            str(uuid4()),
            _ResolveThenMissRepo(),
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_public_workflow_not_found() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await workflows.get_public_workflow(
            str(uuid4()),
            _MissingWorkflowRepo(),
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_public_workflow_denies_private_workflows() -> None:
    workflow = Workflow(name="Hidden workflow", is_public=False)
    repo = _WorkflowRepo(workflow)

    with pytest.raises(HTTPException) as excinfo:
        await workflows.get_public_workflow(str(workflow.id), repo)

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["code"] == "workflow.not_public"


@pytest.mark.asyncio()
async def test_get_public_workflow_includes_share_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = Workflow(name="Published workflow", is_public=True)
    repo = _WorkflowRepo(workflow)
    monkeypatch.setattr(
        workflows,
        "_resolve_chatkit_public_base_url",
        lambda: "https://canvas.example",
    )

    response = await workflows.get_public_workflow(str(workflow.id), repo)

    assert response.share_url == f"https://canvas.example/chat/{workflow.id}"


def test_resolve_chatkit_public_base_url_returns_none_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify _resolve_chatkit_public_base_url returns None when setting is empty."""
    monkeypatch.setattr(
        workflows,
        "get_settings",
        lambda: {"CHATKIT_PUBLIC_BASE_URL": ""},
    )

    result = _resolve_chatkit_public_base_url()
    assert result is None
