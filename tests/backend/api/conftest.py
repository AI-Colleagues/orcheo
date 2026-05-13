"""Fixtures for backend API end-to-end tests."""

from __future__ import annotations
from collections.abc import Iterator
from importlib import import_module
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from orcheo.models import AesGcmCredentialCipher
from orcheo.vault import InMemoryCredentialVault
from orcheo.vault.oauth import OAuthCredentialService
from orcheo.workspace import (
    InMemoryWorkspaceRepository,
    Role,
    Workspace,
    WorkspaceMembership,
)
from orcheo.workspace.models import WorkspaceContext
from orcheo_backend.app import create_app
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.chatkit_tokens import reset_chatkit_token_state
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.workspace import reset_workspace_state, set_workspace_repository
from orcheo_backend.app.workspace.dependencies import resolve_workspace_context


@pytest.fixture()
def api_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Yield a configured API client backed by a fresh repository."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    monkeypatch.delenv("ORCHEO_AUTH_SERVICE_TOKENS", raising=False)
    monkeypatch.delenv("CHATKIT_TOKEN_SIGNING_KEY", raising=False)
    monkeypatch.delenv("ORCHEO_CHATKIT_TOKEN_SIGNING_KEY", raising=False)
    reset_authentication_state()
    reset_chatkit_token_state()
    reset_workspace_state()

    factory_module = import_module("orcheo_backend.app.factory")
    monkeypatch.setattr(
        factory_module,
        "get_chatkit_server",
        lambda: object(),
    )
    monkeypatch.setattr(
        factory_module,
        "ensure_chatkit_cleanup_task",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        factory_module,
        "cancel_chatkit_cleanup_task",
        AsyncMock(return_value=None),
    )

    workspace_id = uuid4()
    workspace_repo = InMemoryWorkspaceRepository()
    workspace_repo.create_workspace(
        Workspace(id=workspace_id, slug="default", name="Default Workspace")
    )
    workspace_repo.add_membership(
        WorkspaceMembership(
            workspace_id=workspace_id, user_id="anonymous", role=Role.OWNER
        )
    )
    set_workspace_repository(workspace_repo)

    workspace_context = WorkspaceContext(
        workspace_id=workspace_id,
        workspace_slug="default",
        user_id="anonymous",
        role=Role.OWNER,
    )

    cipher = AesGcmCredentialCipher(key="api-client-key")
    vault = InMemoryCredentialVault(cipher=cipher)
    service = OAuthCredentialService(vault, token_ttl_seconds=600, providers={})
    repository = InMemoryWorkflowRepository(credential_service=service)
    app = create_app(repository, credential_service=service)
    app.state.vault = vault
    app.state.credential_service = service
    app.dependency_overrides[resolve_workspace_context] = lambda: workspace_context

    try:
        with TestClient(app) as client:
            yield client
    finally:
        reset_workspace_state()
