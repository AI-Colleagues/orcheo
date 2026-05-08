"""Integration tests for listener runtime startup in the backend app."""

from __future__ import annotations
import asyncio
import time
from importlib import import_module
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from orcheo.models import AesGcmCredentialCipher, CredentialScope
from orcheo.models.workflow import WorkflowDraftAccess
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
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.workspace import reset_workspace_state, set_workspace_repository
from orcheo_backend.app.workspace.dependencies import resolve_workspace_context


def test_listener_runtime_starts_and_resolves_telegram_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Listener polling should start on app boot with resolved vault secrets."""
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    monkeypatch.delenv("ORCHEO_AUTH_SERVICE_TOKENS", raising=False)

    factory_module = import_module("orcheo_backend.app.factory")
    monkeypatch.setattr(factory_module, "get_chatkit_server", lambda: object())
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

    runtime_module = import_module("orcheo_backend.app.listener_runtime_service")
    monkeypatch.setattr(
        runtime_module,
        "DEFAULT_LISTENER_RECONCILE_INTERVAL_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        runtime_module,
        "DEFAULT_LISTENER_HEALTH_PUBLISH_INTERVAL_SECONDS",
        0.01,
    )

    calls: list[str] = []

    async def fake_get_updates(
        self,
        *,
        token: str,
        offset: int | None,
        timeout: int,
        allowed_updates: list[str],
        limit: int,
    ) -> list[dict[str, object]]:
        del self, offset, timeout, allowed_updates, limit
        calls.append(token)
        await asyncio.sleep(0.01)
        return []

    telegram_module = import_module("orcheo.listeners.telegram")
    monkeypatch.setattr(
        telegram_module.DefaultTelegramPollingClient,
        "get_updates",
        fake_get_updates,
    )

    cipher = AesGcmCredentialCipher(key="listener-runtime-test-key")
    vault = InMemoryCredentialVault(cipher=cipher)
    service = OAuthCredentialService(vault, token_ttl_seconds=600, providers={})
    repository = InMemoryWorkflowRepository(credential_service=service)

    workflow = asyncio.run(
        repository.create_workflow(
            name="Telegram Listener Flow",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="tester",
        )
    )
    asyncio.run(
        repository.create_version(
            workflow.id,
            graph={
                "nodes": [],
                "edges": [],
                "index": {
                    "listeners": [
                        {
                            "node_name": "telegram_listener",
                            "platform": "telegram",
                            "token": "[[telegram_token]]",
                            "allowed_updates": ["message"],
                            "allowed_chat_types": ["private"],
                            "poll_timeout_seconds": 1,
                        }
                    ]
                },
            },
            metadata={},
            notes=None,
            created_by="tester",
        )
    )
    vault.create_credential(
        name="telegram_token",
        provider="telegram",
        scopes=[],
        secret="resolved-telegram-token",
        actor="tester",
        scope=CredentialScope.for_workflows(workflow.id),
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
    reset_workspace_state()
    set_workspace_repository(workspace_repo)
    workspace_context = WorkspaceContext(
        workspace_id=workspace_id,
        workspace_slug="default",
        user_id="anonymous",
        role=Role.OWNER,
    )

    app = create_app(repository, credential_service=service)
    app.dependency_overrides[resolve_workspace_context] = lambda: workspace_context
    try:
        with TestClient(app) as client:
            deadline = time.time() + 1.0
            response_payload: list[dict[str, object]] = []
            while time.time() < deadline:
                if calls:
                    response = client.get(f"/api/workflows/{workflow.id}/listeners")
                    if response.status_code == 200:
                        response_payload = response.json()
                        if response_payload and response_payload[0][
                            "runtime_status"
                        ] in {
                            "starting",
                            "healthy",
                        }:
                            break
                time.sleep(0.02)

            assert calls
            assert "resolved-telegram-token" in calls
            assert response_payload
            assert response_payload[0]["runtime_status"] in {"starting", "healthy"}
            assert response_payload[0]["assigned_runtime"] is not None
    finally:
        reset_workspace_state()


def test_listener_runtime_blocks_missing_telegram_credentials_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing listener credentials should block the listener without retries."""
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    monkeypatch.delenv("ORCHEO_AUTH_SERVICE_TOKENS", raising=False)

    factory_module = import_module("orcheo_backend.app.factory")
    monkeypatch.setattr(factory_module, "get_chatkit_server", lambda: object())
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

    runtime_module = import_module("orcheo_backend.app.listener_runtime_service")
    monkeypatch.setattr(
        runtime_module,
        "DEFAULT_LISTENER_RECONCILE_INTERVAL_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        runtime_module,
        "DEFAULT_LISTENER_HEALTH_PUBLISH_INTERVAL_SECONDS",
        0.01,
    )

    cipher = AesGcmCredentialCipher(key="listener-runtime-test-key")
    vault = InMemoryCredentialVault(cipher=cipher)
    service = OAuthCredentialService(vault, token_ttl_seconds=600, providers={})
    repository = InMemoryWorkflowRepository(credential_service=service)

    workflow = asyncio.run(
        repository.create_workflow(
            name="Telegram Listener Flow",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="tester",
        )
    )
    asyncio.run(
        repository.create_version(
            workflow.id,
            graph={
                "nodes": [],
                "edges": [],
                "index": {
                    "listeners": [
                        {
                            "node_name": "telegram_listener",
                            "platform": "telegram",
                            "token": "[[telegram_token]]",
                            "allowed_updates": ["message"],
                            "allowed_chat_types": ["private"],
                            "poll_timeout_seconds": 1,
                        }
                    ]
                },
            },
            metadata={},
            notes=None,
            created_by="tester",
        )
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
    reset_workspace_state()
    set_workspace_repository(workspace_repo)
    workspace_context = WorkspaceContext(
        workspace_id=workspace_id,
        workspace_slug="default",
        user_id="anonymous",
        role=Role.OWNER,
    )

    app = create_app(repository, credential_service=service)
    app.dependency_overrides[resolve_workspace_context] = lambda: workspace_context
    try:
        with TestClient(app) as client:
            deadline = time.time() + 1.0
            response_payload: list[dict[str, object]] = []
            while time.time() < deadline:
                response = client.get(f"/api/workflows/{workflow.id}/listeners")
                if response.status_code == 200:
                    response_payload = response.json()
                    if response_payload and response_payload[0]["status"] == "blocked":
                        break
                time.sleep(0.02)

            assert response_payload
            assert response_payload[0]["status"] == "blocked"
            assert response_payload[0]["runtime_status"] == "unknown"
            assert "telegram_token" in str(response_payload[0]["last_error"])
            assert response_payload[0]["assigned_runtime"] is None
    finally:
        reset_workspace_state()
