"""Tests for workflow actor and workspace tag resolution in router handlers."""

from __future__ import annotations
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo.models.workflow import Workflow, WorkflowDraftAccess
from orcheo_backend.app.authentication import AuthorizationPolicy, RequestContext
from orcheo_backend.app.repository.errors import WorkflowNotFoundError
from orcheo_backend.app.routers import workflows
from orcheo_backend.app.schemas.workflows import (
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
)


_MOCK_TENANT = SimpleNamespace(tenant_id=uuid4())


class _Repository:
    def __init__(self) -> None:
        self.last_actor: str | None = None
        self.last_tags: list[str] | None = None
        self.last_draft_access: WorkflowDraftAccess | None = None

    async def create_workflow(
        self,
        *,
        name: str,
        slug: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        draft_access: WorkflowDraftAccess = WorkflowDraftAccess.PERSONAL,
        actor: str,
        tenant_id: str | None = None,
        handle: str | None = None,
    ) -> Workflow:
        self.last_actor = actor
        self.last_tags = list(tags or [])
        self.last_draft_access = draft_access
        return Workflow(
            name=name,
            slug=slug or "",
            description=description,
            tags=tags or [],
            draft_access=draft_access,
        )

    async def update_workflow(
        self,
        workflow_id,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        draft_access: WorkflowDraftAccess | None = None,
        is_archived: bool | None = None,
        actor: str,
        **kwargs,
    ) -> Workflow:
        self.last_actor = actor
        self.last_tags = list(tags) if tags is not None else None
        self.last_draft_access = draft_access
        return Workflow(
            id=workflow_id,
            name=name or "Workflow",
            description=description,
            tags=tags or [],
            draft_access=draft_access or WorkflowDraftAccess.PERSONAL,
            is_archived=bool(is_archived),
        )

    async def resolve_workflow_ref(
        self, workflow_ref, *, include_archived=True, tenant_id=None
    ):
        del include_archived
        return UUID(str(workflow_ref))


class _RepositoryWithExistingWorkflow(_Repository):
    def __init__(self, workflow: Workflow) -> None:
        super().__init__()
        self._existing_workflow = workflow
        self.last_get_workflow_id: UUID | None = None

    async def get_workflow(self, workflow_id: UUID, *, tenant_id=None) -> Workflow:
        self.last_get_workflow_id = workflow_id
        return self._existing_workflow


class _RepositoryMissingWorkflow(_Repository):
    async def get_workflow(self, workflow_id: UUID, *, tenant_id=None) -> Workflow:
        raise WorkflowNotFoundError(str(workflow_id))


@pytest.mark.asyncio()
async def test_create_workflow_uses_authenticated_subject_and_workspace_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflows,
        "load_auth_settings",
        lambda: SimpleNamespace(enforce=True),
    )
    repository = _Repository()
    request = WorkflowCreateRequest(
        name="CLI uploaded workflow",
        tags=["langgraph", "cli-upload"],
        actor="cli",
    )
    policy = AuthorizationPolicy(
        RequestContext(
            subject="auth0|user-123",
            identity_type="user",
            scopes=frozenset({"workflows:write"}),
            workspace_ids=frozenset({"team-a"}),
        )
    )

    await workflows.create_workflow(request, repository, _MOCK_TENANT, policy=policy)

    assert repository.last_actor == "auth0|user-123"
    assert repository.last_tags is not None
    assert "langgraph" in repository.last_tags
    assert "cli-upload" in repository.last_tags
    assert "workspace:team-a" in repository.last_tags
    assert repository.last_draft_access is WorkflowDraftAccess.AUTHENTICATED


@pytest.mark.asyncio()
async def test_create_workflow_keeps_request_actor_when_context_unavailable() -> None:
    repository = _Repository()
    request = WorkflowCreateRequest(
        name="Legacy workflow",
        tags=["legacy"],
        actor="cli",
    )

    await workflows.create_workflow(request, repository, _MOCK_TENANT)

    assert repository.last_actor == "cli"
    assert repository.last_tags == ["legacy"]
    assert repository.last_draft_access is WorkflowDraftAccess.PERSONAL


@pytest.mark.asyncio()
async def test_create_workflow_adds_workspace_tags_when_tags_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflows,
        "load_auth_settings",
        lambda: SimpleNamespace(enforce=True),
    )
    repository = _Repository()
    request = WorkflowCreateRequest(
        name="Tagless workflow",
        actor="cli",
    )
    policy = AuthorizationPolicy(
        RequestContext(
            subject="service-token-2",
            identity_type="service",
            scopes=frozenset({"workflows:write"}),
            workspace_ids=frozenset({"team-x"}),
        )
    )

    await workflows.create_workflow(request, repository, _MOCK_TENANT, policy=policy)

    assert repository.last_actor == "service-token-2"
    assert repository.last_tags == ["workspace:team-x"]
    assert repository.last_draft_access is WorkflowDraftAccess.WORKSPACE


@pytest.mark.asyncio()
async def test_create_workflow_defaults_authenticated_users_to_authenticated_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflows,
        "load_auth_settings",
        lambda: SimpleNamespace(enforce=True),
    )
    repository = _Repository()
    request = WorkflowCreateRequest(
        name="Shared draft workflow",
        tags=["shared"],
        actor="cli",
    )
    policy = AuthorizationPolicy(
        RequestContext(
            subject="auth0|user-456",
            identity_type="user",
            scopes=frozenset({"workflows:write"}),
            workspace_ids=frozenset(),
        )
    )

    await workflows.create_workflow(request, repository, _MOCK_TENANT, policy=policy)

    assert repository.last_actor == "auth0|user-456"
    assert repository.last_tags == ["shared"]
    assert repository.last_draft_access is WorkflowDraftAccess.AUTHENTICATED


@pytest.mark.asyncio()
async def test_create_workflow_normalizes_workspace_tag_casing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflows,
        "load_auth_settings",
        lambda: SimpleNamespace(enforce=True),
    )
    repository = _Repository()
    request = WorkflowCreateRequest(
        name="Case sensitive workspace",
        actor="cli",
    )
    policy = AuthorizationPolicy(
        RequestContext(
            subject="service-token-4",
            identity_type="service",
            scopes=frozenset({"workflows:write"}),
            workspace_ids=frozenset({"Team-X"}),
        )
    )

    await workflows.create_workflow(request, repository, _MOCK_TENANT, policy=policy)

    assert repository.last_actor == "service-token-4"
    assert repository.last_tags == ["workspace:team-x"]
    assert repository.last_draft_access is WorkflowDraftAccess.WORKSPACE


@pytest.mark.asyncio()
async def test_update_workflow_appends_workspace_tags_when_auth_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflows,
        "load_auth_settings",
        lambda: SimpleNamespace(enforce=True),
    )
    repository = _Repository()
    workflow = Workflow(name="Workflow")
    request = WorkflowUpdateRequest(tags=["cli-upload"], actor="cli")
    policy = AuthorizationPolicy(
        RequestContext(
            subject="service-token-1",
            identity_type="service",
            scopes=frozenset({"workflows:write"}),
            workspace_ids=frozenset({"ws-1", "ws-2"}),
        )
    )

    await workflows.update_workflow(
        str(workflow.id), request, repository, _MOCK_TENANT, policy=policy
    )

    assert repository.last_actor == "service-token-1"
    assert repository.last_tags is not None
    assert "cli-upload" in repository.last_tags
    assert "workspace:ws-1" in repository.last_tags
    assert "workspace:ws-2" in repository.last_tags
    assert repository.last_draft_access is None


def test_append_workspace_tags_returns_list_when_tags_none() -> None:
    """_append_workspace_tags returns workspace tag list when tags is None.

    Covers line 507.
    """
    context = RequestContext(
        subject="user-1",
        identity_type="user",
        scopes=frozenset({"workflows:write"}),
        workspace_ids=frozenset({"ws-a"}),
    )
    result = workflows._append_workspace_tags(None, context)
    assert result == ["workspace:ws-a"]


def test_append_workspace_tags_skips_duplicate_workspace_tag() -> None:
    """Workspace tag already present is not added again (branch 516->514)."""
    context = RequestContext(
        subject="user-1",
        identity_type="user",
        scopes=frozenset({"workflows:write"}),
        workspace_ids=frozenset({"ws-a"}),
    )
    result = workflows._append_workspace_tags(["workspace:ws-a"], context)
    assert result == ["workspace:ws-a"]


@pytest.mark.asyncio()
async def test_update_workflow_preserves_none_tags_when_request_omits_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflows,
        "load_auth_settings",
        lambda: SimpleNamespace(enforce=True),
    )
    repository = _Repository()
    workflow = Workflow(name="Workflow")
    request = WorkflowUpdateRequest(actor="cli")
    policy = AuthorizationPolicy(
        RequestContext(
            subject="service-token-3",
            identity_type="service",
            scopes=frozenset({"workflows:write"}),
            workspace_ids=frozenset({"ws-1"}),
        )
    )

    await workflows.update_workflow(
        str(workflow.id), request, repository, _MOCK_TENANT, policy=policy
    )

    assert repository.last_actor == "service-token-3"
    assert repository.last_tags is None
    assert repository.last_draft_access is None


@pytest.mark.asyncio()
async def test_create_workflow_rejects_personal_scope_with_workspace_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflows,
        "load_auth_settings",
        lambda: SimpleNamespace(enforce=True),
    )
    repository = _Repository()
    request = WorkflowCreateRequest(
        name="Conflicting workflow",
        tags=["workspace:team-a"],
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="cli",
    )
    policy = AuthorizationPolicy(
        RequestContext(
            subject="auth0|user-123",
            identity_type="user",
            scopes=frozenset({"workflows:write"}),
            workspace_ids=frozenset({"team-a"}),
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await workflows.create_workflow(
            request, repository, _MOCK_TENANT, policy=policy
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "workflow.draft_access.conflict"


@pytest.mark.asyncio()
async def test_create_workflow_rejects_workspace_scope_without_workspace_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflows,
        "load_auth_settings",
        lambda: SimpleNamespace(enforce=True),
    )
    repository = _Repository()
    request = WorkflowCreateRequest(
        name="Conflicting workflow",
        tags=["shared"],
        draft_access=WorkflowDraftAccess.WORKSPACE,
        actor="cli",
    )
    policy = AuthorizationPolicy(
        RequestContext(
            subject="auth0|user-123",
            identity_type="user",
            scopes=frozenset({"workflows:write"}),
            workspace_ids=frozenset(),
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await workflows.create_workflow(
            request, repository, _MOCK_TENANT, policy=policy
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "workflow.draft_access.workspace_required"


@pytest.mark.asyncio()
async def test_update_workflow_does_not_recompute_draft_access_from_tag_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflows,
        "load_auth_settings",
        lambda: SimpleNamespace(enforce=True),
    )
    repository = _Repository()
    workflow = Workflow(
        name="Workflow",
        draft_access=WorkflowDraftAccess.PERSONAL,
    )
    request = WorkflowUpdateRequest(tags=["shared"], actor="cli")
    policy = AuthorizationPolicy(
        RequestContext(
            subject="auth0|user-789",
            identity_type="user",
            scopes=frozenset({"workflows:write"}),
            workspace_ids=frozenset(),
        )
    )

    await workflows.update_workflow(
        str(workflow.id), request, repository, _MOCK_TENANT, policy=policy
    )

    assert repository.last_tags == ["shared"]
    assert repository.last_draft_access is None


@pytest.mark.asyncio()
async def test_update_workflow_reuses_existing_tags_for_draft_access_when_tags_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflows,
        "load_auth_settings",
        lambda: SimpleNamespace(enforce=True),
    )
    existing = Workflow(
        name="Existing",
        tags=["workspace:team-x"],
        draft_access=WorkflowDraftAccess.WORKSPACE,
    )
    repository = _RepositoryWithExistingWorkflow(existing)
    request = WorkflowUpdateRequest(
        draft_access=WorkflowDraftAccess.WORKSPACE,
        actor="cli",
    )
    policy = AuthorizationPolicy(
        RequestContext(
            subject="service-token-7",
            identity_type="service",
            scopes=frozenset({"workflows:write"}),
            workspace_ids=frozenset(),
        )
    )

    await workflows.update_workflow(
        str(existing.id),
        request,
        repository,
        _MOCK_TENANT,
        policy=policy,
    )

    assert repository.last_draft_access is WorkflowDraftAccess.WORKSPACE
    assert repository.last_get_workflow_id == existing.id


@pytest.mark.asyncio()
async def test_update_workflow_raises_not_found_when_existing_workflow_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflows,
        "load_auth_settings",
        lambda: SimpleNamespace(enforce=True),
    )
    repository = _RepositoryMissingWorkflow()
    request = WorkflowUpdateRequest(
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="cli",
    )
    policy = AuthorizationPolicy(
        RequestContext(
            subject="service-token-8",
            identity_type="service",
            scopes=frozenset({"workflows:write"}),
            workspace_ids=frozenset(),
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await workflows.update_workflow(
            str(uuid4()),
            request,
            repository,
            _MOCK_TENANT,
            policy=policy,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Workflow not found"
