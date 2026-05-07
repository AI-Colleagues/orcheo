"""Tests for workspace-slug-prefixed webhook routing."""

from __future__ import annotations
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from orcheo_backend.app.routers.triggers import _invoke_workspace_webhook


class _MockRequest:
    """Minimal stand-in for a FastAPI Request in webhook tests."""

    method = "POST"
    headers = {}
    query_params = {}

    async def body(self) -> bytes:
        return b""

    client = None


class _MockRepository:
    """Stub repository that resolves trigger_id only when workspace_id matches."""

    def __init__(self, *, workspace_id: str, workflow_ref: str) -> None:
        self._workspace_id = workspace_id
        self._workflow_ref = workflow_ref

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> object:
        from orcheo_backend.app.repository import WorkflowNotFoundError

        if workspace_id != self._workspace_id or workflow_ref != self._workflow_ref:
            raise WorkflowNotFoundError(workflow_ref)
        return uuid4()


class _MockWorkspaceService:
    """Stub workspace service that knows one workspace by slug."""

    def __init__(self, *, slug: str, workspace_id: str) -> None:
        self._slug = slug
        self._workspace_id = workspace_id
        self.repository = self

    def get_workspace_by_slug(self, slug: str) -> object:
        from orcheo.workspace import WorkspaceNotFoundError

        if slug != self._slug:
            raise WorkspaceNotFoundError(slug)
        return SimpleNamespace(id=self._workspace_id)


class _MockVault:
    pass


@pytest.mark.asyncio
async def test_unknown_workspace_slug_returns_404() -> None:
    workspace_service = _MockWorkspaceService(slug="acme", workspace_id=str(uuid4()))
    with pytest.raises(HTTPException) as exc_info:
        await _invoke_workspace_webhook(
            "unknown-slug",
            "some-trigger",
            _MockRequest(),  # type: ignore[arg-type]
            _MockRepository(workspace_id="irrelevant", workflow_ref="x"),  # type: ignore[arg-type]
            _MockVault(),  # type: ignore[arg-type]
            workspace_service,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 404
    assert "unknown-slug" in exc_info.value.detail


@pytest.mark.asyncio
async def test_unknown_trigger_id_returns_404() -> None:
    workspace_id = str(uuid4())
    workspace_service = _MockWorkspaceService(slug="acme", workspace_id=workspace_id)
    repository = _MockRepository(
        workspace_id=workspace_id, workflow_ref="real-workflow"
    )

    with pytest.raises(HTTPException) as exc_info:
        await _invoke_workspace_webhook(
            "acme",
            "wrong-trigger",
            _MockRequest(),  # type: ignore[arg-type]
            repository,  # type: ignore[arg-type]
            _MockVault(),  # type: ignore[arg-type]
            workspace_service,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 404
    assert "wrong-trigger" in exc_info.value.detail


@pytest.mark.asyncio
async def test_wrong_workspace_cannot_access_other_workspaces_trigger() -> None:
    workspace_a_id = str(uuid4())
    workspace_b_id = str(uuid4())
    workspace_service_b = _MockWorkspaceService(
        slug="workspace-b", workspace_id=workspace_b_id
    )
    repository = _MockRepository(
        workspace_id=workspace_a_id, workflow_ref="workspace-a-wf"
    )

    with pytest.raises(HTTPException) as exc_info:
        await _invoke_workspace_webhook(
            "workspace-b",
            "workspace-a-wf",
            _MockRequest(),  # type: ignore[arg-type]
            repository,  # type: ignore[arg-type]
            _MockVault(),  # type: ignore[arg-type]
            workspace_service_b,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 404
