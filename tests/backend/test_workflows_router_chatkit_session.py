"""Coverage for the workflow-scoped ChatKit session endpoint."""

from __future__ import annotations
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4
import jwt
import pytest
from fastapi import HTTPException
from orcheo.models.workflow import Workflow, WorkflowDraftAccess
from orcheo_backend.app.authentication import (
    AuthenticationError,
    AuthorizationError,
    AuthorizationPolicy,
    RequestContext,
)
from orcheo_backend.app.chatkit_tokens import (
    ChatKitSessionTokenIssuer,
    ChatKitTokenSettings,
)
from orcheo_backend.app.repository import WorkflowNotFoundError
from orcheo_backend.app.routers import chatkit, workflows
from tests.backend.chatkit_router_helpers_support import make_chatkit_request


class _WorkflowRepo:
    def __init__(self, workflow: Workflow) -> None:
        self._workflow = workflow

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        del include_archived
        if UUID(str(workflow_ref)) != self._workflow.id:
            raise WorkflowNotFoundError(str(workflow_ref))
        return self._workflow.id

    async def get_workflow(self, workflow_id: UUID, *, workspace_id=None) -> Workflow:
        if workflow_id != self._workflow.id:
            raise WorkflowNotFoundError(str(workflow_id))
        return self._workflow

    async def get_workflow_workspace_id(self, workflow_id: UUID) -> str | None:
        if workflow_id != self._workflow.id:
            raise WorkflowNotFoundError(str(workflow_id))
        workspace_id = self._workflow.workspace_id
        return str(workspace_id) if workspace_id is not None else None


class _MissingWorkflowRepo:
    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        del include_archived
        raise WorkflowNotFoundError(str(workflow_ref))

    async def get_workflow(self, workflow_id: UUID, *, workspace_id=None) -> Workflow:
        raise WorkflowNotFoundError(str(workflow_id))


class _ResolveThenMissingWorkflowRepo:
    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        del include_archived
        return UUID(str(workflow_ref))

    async def get_workflow(self, workflow_id: UUID, *, workspace_id=None) -> Workflow:
        raise WorkflowNotFoundError(str(workflow_id))


_MOCK_WORKSPACE = SimpleNamespace(workspace_id=uuid4())


def _issuer() -> ChatKitSessionTokenIssuer:
    return ChatKitSessionTokenIssuer(
        ChatKitTokenSettings(
            signing_key="canvas-chatkit-key",
            issuer="canvas-backend",
            audience="chatkit-client",
            ttl_seconds=300,
        )
    )


def _policy(scopes: set[str]) -> AuthorizationPolicy:
    context = RequestContext(
        subject="canvas-user",
        identity_type="user",
        scopes=frozenset(scopes),
        workspace_ids=frozenset({"ws-1"}),
    )
    return AuthorizationPolicy(context)


@pytest.fixture(autouse=True)
def _enforce_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workflows,
        "load_auth_settings",
        lambda: SimpleNamespace(enforce=True),
    )


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_requires_authentication() -> None:
    workflow = Workflow(name="Canvas Workflow", tags=["workspace:ws-1"])
    repo = _WorkflowRepo(workflow)
    policy = AuthorizationPolicy(RequestContext.anonymous())

    with pytest.raises(AuthenticationError):
        await workflows.create_workflow_chatkit_session(
            str(workflow.id),
            repo,
            _MOCK_WORKSPACE,
            policy=policy,
            issuer=_issuer(),
        )


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_requires_permissions() -> None:
    workflow = Workflow(name="Canvas Workflow", tags=["workspace:ws-1"])
    repo = _WorkflowRepo(workflow)
    policy = _policy({"workflows:read"})  # missing execute scope

    with pytest.raises(AuthorizationError):
        await workflows.create_workflow_chatkit_session(
            str(workflow.id),
            repo,
            _MOCK_WORKSPACE,
            policy=policy,
            issuer=_issuer(),
        )


class _ArchivedWorkflowRepo:
    def __init__(self, workflow: Workflow) -> None:
        self._workflow = workflow

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        del include_archived
        if UUID(str(workflow_ref)) != self._workflow.id:
            raise WorkflowNotFoundError(str(workflow_ref))
        return self._workflow.id

    async def get_workflow(self, workflow_id: UUID, *, workspace_id=None) -> Workflow:
        if workflow_id != self._workflow.id:
            raise WorkflowNotFoundError(str(workflow_id))
        return self._workflow


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_validates_workflow_exists() -> None:
    repo = _MissingWorkflowRepo()
    policy = _policy({"workflows:read", "workflows:execute"})

    with pytest.raises(HTTPException) as excinfo:
        await workflows.create_workflow_chatkit_session(
            str(uuid4()),
            repo,
            _MOCK_WORKSPACE,
            policy=policy,
            issuer=_issuer(),
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_missing_after_resolution() -> None:
    policy = _policy({"workflows:read", "workflows:execute"})

    with pytest.raises(HTTPException) as excinfo:
        await workflows.create_workflow_chatkit_session(
            str(uuid4()),
            _ResolveThenMissingWorkflowRepo(),
            _MOCK_WORKSPACE,
            policy=policy,
            issuer=_issuer(),
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_rejects_archived_workflow() -> None:
    """Test that archived workflows return 404 as if they don't exist."""
    workflow = Workflow(
        name="Archived Workflow", tags=["workspace:ws-1"], is_archived=True
    )
    repo = _ArchivedWorkflowRepo(workflow)
    policy = _policy({"workflows:read", "workflows:execute"})

    with pytest.raises(HTTPException) as excinfo:
        await workflows.create_workflow_chatkit_session(
            str(workflow.id),
            repo,
            _MOCK_WORKSPACE,
            policy=policy,
            issuer=_issuer(),
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_mints_scoped_token() -> None:
    active_workspace_id = str(_MOCK_WORKSPACE.workspace_id)
    workflow = Workflow(
        name="Canvas Workflow", tags=[f"workspace:{active_workspace_id}"]
    )
    repo = _WorkflowRepo(workflow)
    policy = AuthorizationPolicy(
        RequestContext(
            subject="canvas-user",
            identity_type="user",
            scopes=frozenset({"workflows:read", "workflows:execute"}),
            workspace_ids=frozenset({active_workspace_id}),
        )
    )
    issuer = _issuer()

    response = await workflows.create_workflow_chatkit_session(
        str(workflow.id),
        repo,
        _MOCK_WORKSPACE,
        policy=policy,
        issuer=issuer,
    )

    decoded = jwt.decode(
        response.client_secret,
        "canvas-chatkit-key",
        algorithms=["HS256"],
        audience="chatkit-client",
        issuer="canvas-backend",
    )

    assert decoded["sub"] == "canvas-user"
    assert decoded["chatkit"]["workflow_id"] == str(workflow.id)
    assert decoded["chatkit"]["workspace_id"] == active_workspace_id
    assert decoded["chatkit"]["workspace_ids"] == [active_workspace_id]
    assert decoded["chatkit"]["metadata"]["workflow_name"] == "Canvas Workflow"
    assert decoded["chatkit"]["metadata"]["source"] == "canvas"
    assert decoded["chatkit"]["interface"] == "canvas_modal"


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_uses_active_workspace_when_ambiguous() -> (
    None
):
    active_workspace_id = str(_MOCK_WORKSPACE.workspace_id)
    workflow = Workflow(
        name="Canvas Workflow", tags=[f"workspace:{active_workspace_id}"]
    )
    repo = _WorkflowRepo(workflow)
    policy = AuthorizationPolicy(
        RequestContext(
            subject="canvas-user",
            identity_type="user",
            scopes=frozenset({"workflows:read", "workflows:execute"}),
            workspace_ids=frozenset({active_workspace_id, "ws-other"}),
        )
    )

    response = await workflows.create_workflow_chatkit_session(
        str(workflow.id),
        repo,
        _MOCK_WORKSPACE,
        policy=policy,
        issuer=_issuer(),
    )

    decoded = jwt.decode(
        response.client_secret,
        "canvas-chatkit-key",
        algorithms=["HS256"],
        audience="chatkit-client",
        issuer="canvas-backend",
    )

    assert decoded["chatkit"]["workspace_id"] == active_workspace_id
    assert decoded["chatkit"]["workspace_ids"] == sorted(
        {active_workspace_id, "ws-other"}
    )


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_requires_workspace_match() -> None:
    workflow = Workflow(name="Canvas Workflow", tags=["workspace:ws-allowed"])
    repo = _WorkflowRepo(workflow)
    policy = AuthorizationPolicy(
        RequestContext(
            subject="canvas-user",
            identity_type="user",
            scopes=frozenset({"workflows:read", "workflows:execute"}),
            workspace_ids=frozenset({"ws-denied"}),
        )
    )

    with pytest.raises(AuthorizationError):
        await workflows.create_workflow_chatkit_session(
            str(workflow.id),
            repo,
            _MOCK_WORKSPACE,
            policy=policy,
            issuer=_issuer(),
        )


@pytest.mark.asyncio()
async def test_chatkit_session_matches_workspace_case_insensitively() -> None:
    workflow = Workflow(name="Canvas Workflow", tags=["Workspace:Team-A"])
    repo = _WorkflowRepo(workflow)
    policy = AuthorizationPolicy(
        RequestContext(
            subject="canvas-user",
            identity_type="user",
            scopes=frozenset({"workflows:read", "workflows:execute"}),
            workspace_ids=frozenset({"TEAM-A"}),
        )
    )

    response = await workflows.create_workflow_chatkit_session(
        str(workflow.id),
        repo,
        _MOCK_WORKSPACE,
        policy=policy,
        issuer=_issuer(),
    )

    decoded = jwt.decode(
        response.client_secret,
        "canvas-chatkit-key",
        algorithms=["HS256"],
        audience="chatkit-client",
        issuer="canvas-backend",
    )

    assert decoded["chatkit"]["workspace_id"] == str(_MOCK_WORKSPACE.workspace_id)
    assert decoded["chatkit"]["workspace_ids"] == sorted(
        {str(_MOCK_WORKSPACE.workspace_id), "team-a"}
    )


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_round_trips_through_jwt_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_workspace_id = str(_MOCK_WORKSPACE.workspace_id)
    issuer = _issuer()
    monkeypatch.setattr(
        chatkit,
        "load_chatkit_token_settings",
        lambda refresh=False: issuer.settings,
    )
    workflow = Workflow(name="Canvas Workflow")
    repo = _WorkflowRepo(workflow)
    policy = AuthorizationPolicy(
        RequestContext(
            subject="canvas-user",
            identity_type="user",
            scopes=frozenset({"workflows:read", "workflows:execute"}),
            workspace_ids=frozenset(),
        )
    )

    response = await workflows.create_workflow_chatkit_session(
        str(workflow.id),
        repo,
        _MOCK_WORKSPACE,
        policy=policy,
        issuer=issuer,
    )

    request = make_chatkit_request(
        headers={"Authorization": f"Bearer {response.client_secret}"}
    )
    result = await chatkit._authenticate_jwt_request(
        request=request,
        workflow_id=workflow.id,
        now=datetime.now(tz=UTC),
        repository=repo,
    )

    assert result is not None
    assert result.workspace_id == active_workspace_id


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_falls_back_to_owner() -> None:
    workflow = Workflow(name="Canvas Workflow")
    workflow.record_event(actor="canvas-user", action="workflow_created")
    repo = _WorkflowRepo(workflow)
    policy = AuthorizationPolicy(
        RequestContext(
            subject="canvas-user",
            identity_type="user",
            scopes=frozenset({"workflows:read", "workflows:execute"}),
            workspace_ids=frozenset(),
        )
    )

    response = await workflows.create_workflow_chatkit_session(
        str(workflow.id),
        repo,
        _MOCK_WORKSPACE,
        policy=policy,
        issuer=_issuer(),
    )

    decoded = jwt.decode(
        response.client_secret,
        "canvas-chatkit-key",
        algorithms=["HS256"],
        audience="chatkit-client",
        issuer="canvas-backend",
    )

    assert decoded["chatkit"]["workflow_id"] == str(workflow.id)


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_requires_workspace_access_for_tagged_workflows() -> (  # noqa: E501
    None
):
    workflow = Workflow(
        name="Canvas Workflow",
        tags=["workspace:ws-1"],
        draft_access=WorkflowDraftAccess.WORKSPACE,
    )
    repo = _WorkflowRepo(workflow)
    policy = AuthorizationPolicy(
        RequestContext(
            subject="canvas-user",
            identity_type="user",
            scopes=frozenset({"workflows:read", "workflows:execute"}),
            workspace_ids=frozenset(),
        )
    )

    with pytest.raises(AuthorizationError) as excinfo:
        await workflows.create_workflow_chatkit_session(
            str(workflow.id),
            repo,
            _MOCK_WORKSPACE,
            policy=policy,
            issuer=_issuer(),
        )

    assert excinfo.value.code == "auth.workspace_forbidden"


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_allows_authenticated_scope_without_tags() -> (  # noqa: E501
    None
):
    workflow = Workflow(
        name="Canvas Workflow",
        draft_access=WorkflowDraftAccess.AUTHENTICATED,
    )
    workflow.record_event(actor="canvas-owner", action="workflow_created")
    repo = _WorkflowRepo(workflow)
    policy = AuthorizationPolicy(
        RequestContext(
            subject="another-user",
            identity_type="user",
            scopes=frozenset({"workflows:read", "workflows:execute"}),
            workspace_ids=frozenset(),
        )
    )

    response = await workflows.create_workflow_chatkit_session(
        str(workflow.id),
        repo,
        _MOCK_WORKSPACE,
        policy=policy,
        issuer=_issuer(),
    )

    decoded = jwt.decode(
        response.client_secret,
        "canvas-chatkit-key",
        algorithms=["HS256"],
        audience="chatkit-client",
        issuer="canvas-backend",
    )

    assert decoded["chatkit"]["workflow_id"] == str(workflow.id)


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_rejects_workspace_scope_without_tags() -> (  # noqa: E501
    None
):
    workflow = Workflow(
        name="Canvas Workflow",
        draft_access=WorkflowDraftAccess.WORKSPACE,
    )
    workflow.record_event(actor="canvas-owner", action="workflow_created")
    repo = _WorkflowRepo(workflow)
    policy = AuthorizationPolicy(
        RequestContext(
            subject="another-user",
            identity_type="user",
            scopes=frozenset({"workflows:read", "workflows:execute"}),
            workspace_ids=frozenset(),
        )
    )

    with pytest.raises(AuthorizationError) as excinfo:
        await workflows.create_workflow_chatkit_session(
            str(workflow.id),
            repo,
            _MOCK_WORKSPACE,
            policy=policy,
            issuer=_issuer(),
        )

    assert excinfo.value.code == "auth.workspace_forbidden"


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_denies_when_owner_mismatch() -> None:
    workflow = Workflow(
        name="Canvas Workflow",
        draft_access=WorkflowDraftAccess.PERSONAL,
    )
    workflow.record_event(actor="canvas-owner", action="workflow_created")
    repo = _WorkflowRepo(workflow)
    policy = AuthorizationPolicy(
        RequestContext(
            subject="another-user",
            identity_type="user",
            scopes=frozenset({"workflows:read", "workflows:execute"}),
            workspace_ids=frozenset(),
        )
    )

    with pytest.raises(AuthorizationError) as excinfo:
        await workflows.create_workflow_chatkit_session(
            str(workflow.id),
            repo,
            _MOCK_WORKSPACE,
            policy=policy,
            issuer=_issuer(),
        )

    assert excinfo.value.code == "auth.forbidden"


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_allows_developer_owner_mismatch() -> (
    None
):
    workflow = Workflow(
        name="Canvas Workflow",
        draft_access=WorkflowDraftAccess.PERSONAL,
    )
    workflow.record_event(actor="cli", action="workflow_created")
    repo = _WorkflowRepo(workflow)
    policy = AuthorizationPolicy(
        RequestContext(
            subject="dev:local-user",
            identity_type="developer",
            scopes=frozenset({"workflows:read", "workflows:execute"}),
            workspace_ids=frozenset(),
        )
    )

    response = await workflows.create_workflow_chatkit_session(
        str(workflow.id),
        repo,
        _MOCK_WORKSPACE,
        policy=policy,
        issuer=_issuer(),
    )

    decoded = jwt.decode(
        response.client_secret,
        "canvas-chatkit-key",
        algorithms=["HS256"],
        audience="chatkit-client",
        issuer="canvas-backend",
    )

    assert decoded["chatkit"]["workflow_id"] == str(workflow.id)


@pytest.mark.asyncio()
async def test_create_workflow_chatkit_session_allows_ownerless_workflow() -> None:
    workflow = Workflow(
        name="Canvas Workflow",
        draft_access=WorkflowDraftAccess.PERSONAL,
    )
    repo = _WorkflowRepo(workflow)
    policy = AuthorizationPolicy(
        RequestContext(
            subject="canvas-user",
            identity_type="user",
            scopes=frozenset({"workflows:read", "workflows:execute"}),
            workspace_ids=frozenset(),
        )
    )

    response = await workflows.create_workflow_chatkit_session(
        str(workflow.id),
        repo,
        _MOCK_WORKSPACE,
        policy=policy,
        issuer=_issuer(),
    )

    decoded = jwt.decode(
        response.client_secret,
        "canvas-chatkit-key",
        algorithms=["HS256"],
        audience="chatkit-client",
        issuer="canvas-backend",
    )

    assert decoded["chatkit"]["workflow_id"] == str(workflow.id)
