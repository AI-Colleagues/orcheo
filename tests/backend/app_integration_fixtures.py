"""Shared fixtures for backend integration tests that hit the FastAPI app."""

from __future__ import annotations
from collections.abc import Generator
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from orcheo.workspace import InMemoryWorkspaceRepository
from orcheo.workspace.models import Role, Workspace, WorkspaceContext
from orcheo_backend.app import create_app
from orcheo_backend.app.authentication import (
    AuthorizationPolicy,
    RequestContext,
    authenticate_request,
    get_authorization_policy,
)
from orcheo_backend.app.history import InMemoryRunHistoryStore
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.workspace.dependencies import (
    reset_workspace_state,
    resolve_workspace_context,
    set_workspace_repository,
)


_TEST_WORKSPACE = WorkspaceContext(
    workspace_id=uuid4(),
    workspace_slug="test",
    user_id="test-user",
    role=Role.OWNER,
)

_ANON_CONTEXT = RequestContext.anonymous()
_AUTH_CONTEXT = RequestContext(
    subject="test-user", identity_type="user", scopes=frozenset()
)


@pytest.fixture
def repository() -> InMemoryWorkflowRepository:
    """Provide an in-memory workflow repository."""
    return InMemoryWorkflowRepository()


@pytest.fixture
def history_store() -> InMemoryRunHistoryStore:
    """Provide an in-memory run history store."""
    return InMemoryRunHistoryStore()


@pytest.fixture
def client(
    repository: InMemoryWorkflowRepository,
    history_store: InMemoryRunHistoryStore,
) -> Generator[TestClient, None, None]:
    """Return a TestClient wired up with in-memory dependencies."""
    ws_repo = InMemoryWorkspaceRepository()
    ws_repo.create_workspace(
        Workspace(id=_TEST_WORKSPACE.workspace_id, slug="test", name="Test Workspace")
    )
    set_workspace_repository(ws_repo)
    app = create_app(repository=repository, history_store=history_store)
    app.dependency_overrides[authenticate_request] = lambda: _ANON_CONTEXT
    app.dependency_overrides[get_authorization_policy] = lambda: AuthorizationPolicy(
        _AUTH_CONTEXT
    )
    app.dependency_overrides[resolve_workspace_context] = lambda: _TEST_WORKSPACE
    yield TestClient(app)
    reset_workspace_state()
