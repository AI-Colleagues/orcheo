"""Coverage for internal workflow router helpers and slug-scoped public access."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from orcheo.models import Workflow
from orcheo_backend.app.repository import WorkflowNotFoundError
from orcheo_backend.app.routers import workflows
from orcheo_backend.app.routers.workflows import (
    _apply_share_url,
    _resolve_team_id_from_slug,
    _resolve_team_slug,
    _resolve_workspace_id_from_slug,
)


class _WorkspaceService:
    def __init__(self, workspace_id: UUID | None) -> None:
        self.repository = self
        self._workspace_id = workspace_id

    def get_workspace_by_slug(self, slug: str) -> object | None:
        del slug
        if self._workspace_id is None:
            return None
        return SimpleNamespace(id=self._workspace_id)


class _WorkspaceLookupFailure:
    def get_workspace(self, workspace_id: UUID) -> object:
        del workspace_id
        raise RuntimeError("workspace lookup failed")


class _WorkspaceLookupError:
    def get_workspace_by_slug(self, slug: str) -> object:
        del slug
        raise RuntimeError("boom")


class _PublicWorkflowRepository:
    def __init__(
        self,
        workflow: Workflow,
        *,
        team_lookup_id: UUID | None = None,
        team_slug: str = "sales",
        team_lookup_error: Exception | None = None,
        team_by_slug_id: UUID | None = None,
        team_by_slug_error: Exception | None = None,
    ) -> None:
        self.workflow = workflow
        self.team_lookup_id = team_lookup_id
        self.team_slug = team_slug
        self.team_lookup_error = team_lookup_error
        self.team_by_slug_id = team_by_slug_id
        self.team_by_slug_error = team_by_slug_error

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
        team_id: str | None = None,
    ) -> UUID:
        del include_archived, workspace_id, team_id
        if UUID(str(workflow_ref)) != self.workflow.id:
            raise WorkflowNotFoundError(str(workflow_ref))
        return self.workflow.id

    async def get_workflow(self, workflow_id: UUID) -> Workflow:
        if workflow_id != self.workflow.id:
            raise WorkflowNotFoundError(str(workflow_id))
        return self.workflow

    async def get_team_by_slug(self, slug: str, *, workspace_id: str) -> object | None:
        del workspace_id
        if self.team_by_slug_error is not None:
            raise self.team_by_slug_error
        if slug != self.team_slug or self.team_by_slug_id is None:
            return None
        return SimpleNamespace(id=self.team_by_slug_id)

    async def get_team(
        self, team_id: UUID, *, workspace_id: str | None = None
    ) -> object:
        del workspace_id
        if self.team_lookup_error is not None:
            raise self.team_lookup_error
        if self.team_lookup_id is not None and team_id != self.team_lookup_id:
            raise WorkflowNotFoundError(str(team_id))
        return SimpleNamespace(id=team_id, slug=self.team_slug)


def test_apply_share_url_covers_all_variants() -> None:
    base_url = "https://studio.example"

    both = _apply_share_url(
        Workflow(name="Published", is_public=True, handle="published"),
        base_url,
        workspace_slug="acme",
        team_slug="sales",
    )
    assert both.share_url == "https://studio.example/chat/acme/team/sales/published"

    workspace_only = _apply_share_url(
        Workflow(name="Published", is_public=True, handle="published-2"),
        base_url,
        workspace_slug="acme",
    )
    assert workspace_only.share_url == ("https://studio.example/chat/acme/published-2")

    team_only = _apply_share_url(
        Workflow(name="Published", is_public=True, handle="published-3"),
        base_url,
        team_slug="sales",
    )
    assert team_only.share_url == "https://studio.example/chat/team/sales/published-3"

    private = _apply_share_url(
        Workflow(name="Private", is_public=False),
        base_url,
        workspace_slug="acme",
        team_slug="sales",
    )
    assert private.share_url is None


@pytest.mark.asyncio()
async def test_resolve_workspace_id_from_slug_handles_success_and_failure() -> None:
    workspace_id = uuid4()
    assert _resolve_workspace_id_from_slug(
        _WorkspaceService(workspace_id), "acme"
    ) == str(workspace_id)
    assert _resolve_workspace_id_from_slug(_WorkspaceService(None), "acme") is None
    assert (
        _resolve_workspace_id_from_slug(
            SimpleNamespace(repository=_WorkspaceLookupError()), "acme"
        )
        is None
    )


@pytest.mark.asyncio()
async def test_resolve_team_id_from_slug_handles_success_and_failure() -> None:
    team_id = uuid4()

    class _Repo:
        async def get_team_by_slug(self, slug: str, *, workspace_id: str) -> object:
            del slug, workspace_id
            return SimpleNamespace(id=team_id)

    class _RepoFailure:
        async def get_team_by_slug(self, slug: str, *, workspace_id: str) -> object:
            del slug, workspace_id
            raise RuntimeError("boom")

    assert await _resolve_team_id_from_slug(_Repo(), "sales", "ws-1") == str(team_id)
    assert await _resolve_team_id_from_slug(_RepoFailure(), "sales", "ws-1") is None


@pytest.mark.asyncio()
async def test_resolve_team_slug_handles_success_and_failure() -> None:
    team_id = uuid4()
    workflow = Workflow(name="Published", is_public=True, team_id=str(team_id))

    class _Repo:
        async def get_team(self, team_id: UUID, *, workspace_id: str | None = None):
            del workspace_id
            assert str(team_id) == workflow.team_id
            return SimpleNamespace(slug="sales")

    class _RepoFailure:
        async def get_team(self, team_id: UUID, *, workspace_id: str | None = None):
            del team_id, workspace_id
            raise RuntimeError("boom")

    assert await _resolve_team_slug(_Repo(), workflow) == "sales"
    assert await _resolve_team_slug(_RepoFailure(), workflow) is None


@pytest.mark.asyncio()
async def test_get_public_workflow_with_workspace_and_team_slugs_returns_share_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    team_id = uuid4()
    workflow = Workflow(
        name="Published workflow",
        handle="published",
        workspace_id=str(workspace_id),
        team_id=str(team_id),
        is_public=True,
    )
    repository = _PublicWorkflowRepository(
        workflow,
        team_lookup_id=team_id,
        team_slug="sales",
        team_by_slug_id=team_id,
    )
    monkeypatch.setattr(
        workflows,
        "get_workspace_repository",
        lambda: SimpleNamespace(get_workspace=lambda wid: SimpleNamespace(slug="acme")),
    )
    monkeypatch.setattr(
        workflows,
        "_resolve_studio_url",
        lambda: "https://studio.example",
    )

    response = await workflows.get_public_workflow(
        str(workflow.id),
        repository,
        _WorkspaceService(workspace_id),
        workspace_slug="acme",
        team_slug="sales",
    )

    assert response.share_url == "https://studio.example/chat/acme/team/sales/published"


@pytest.mark.asyncio()
async def test_get_public_workflow_with_workspace_slug_only_returns_share_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    workflow = Workflow(
        name="Published workflow",
        handle="published",
        workspace_id=str(workspace_id),
        is_public=True,
    )
    repository = _PublicWorkflowRepository(workflow)
    monkeypatch.setattr(
        workflows,
        "get_workspace_repository",
        lambda: SimpleNamespace(get_workspace=lambda wid: SimpleNamespace(slug="acme")),
    )
    monkeypatch.setattr(
        workflows,
        "_resolve_studio_url",
        lambda: "https://studio.example",
    )

    response = await workflows.get_public_workflow(
        str(workflow.id),
        repository,
        _WorkspaceService(workspace_id),
        workspace_slug="acme",
        team_slug=None,
    )

    assert response.share_url == "https://studio.example/chat/acme/published"


@pytest.mark.asyncio()
async def test_get_public_workflow_rejects_missing_workspace_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = Workflow(name="Published workflow", is_public=True)
    repository = _PublicWorkflowRepository(workflow)
    monkeypatch.setattr(
        workflows,
        "_resolve_studio_url",
        lambda: "https://studio.example",
    )

    with pytest.raises(HTTPException) as excinfo:
        await workflows.get_public_workflow(
            str(workflow.id),
            repository,
            _WorkspaceService(None),
            workspace_slug="acme",
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_public_workflow_rejects_missing_team_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    workflow = Workflow(
        name="Published workflow",
        workspace_id=str(workspace_id),
        is_public=True,
    )
    repository = _PublicWorkflowRepository(workflow)
    monkeypatch.setattr(
        workflows,
        "_resolve_studio_url",
        lambda: "https://studio.example",
    )

    with pytest.raises(HTTPException) as excinfo:
        await workflows.get_public_workflow(
            str(workflow.id),
            repository,
            _WorkspaceService(workspace_id),
            workspace_slug="acme",
            team_slug="missing",
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_public_workflow_ignores_workspace_lookup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    team_id = uuid4()
    workflow = Workflow(
        name="Published workflow",
        handle="published",
        workspace_id=str(workspace_id),
        team_id=str(team_id),
        is_public=True,
    )
    repository = _PublicWorkflowRepository(
        workflow,
        team_lookup_id=team_id,
        team_slug="sales",
        team_by_slug_id=team_id,
    )
    monkeypatch.setattr(
        workflows,
        "get_workspace_repository",
        lambda: _WorkspaceLookupFailure(),
    )
    monkeypatch.setattr(
        workflows,
        "_resolve_studio_url",
        lambda: "https://studio.example",
    )

    response = await workflows.get_public_workflow(
        str(workflow.id),
        repository,
        _WorkspaceService(workspace_id),
        workspace_slug=None,
        team_slug=None,
    )

    assert response.share_url == "https://studio.example/chat/team/sales/published"
