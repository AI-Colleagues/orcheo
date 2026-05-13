"""ChatKit app startup tests."""

from __future__ import annotations
import importlib
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from orcheo.models import AesGcmCredentialCipher
from orcheo.vault import InMemoryCredentialVault
from orcheo.vault.oauth import OAuthCredentialService
from orcheo_backend.app import create_app
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from tests.backend.api.shared import backend_app


factory_module = importlib.import_module("orcheo_backend.app.factory")


def test_create_app_startup_exception_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup handler swallows HTTPException from get_chatkit_server."""

    def mock_get_chatkit_server() -> None:
        raise HTTPException(status_code=503, detail="ChatKit not configured")

    monkeypatch.setattr(backend_app, "get_chatkit_server", mock_get_chatkit_server)
    monkeypatch.setattr(
        factory_module,
        "get_chatkit_server",
        mock_get_chatkit_server,
    )

    cipher = AesGcmCredentialCipher(key="test-key")
    vault = InMemoryCredentialVault(cipher=cipher)
    service = OAuthCredentialService(vault, token_ttl_seconds=600, providers={})
    repository = InMemoryWorkflowRepository(credential_service=service)

    app = create_app(repository, credential_service=service)

    with TestClient(app) as client:
        assert client is not None


def test_create_app_seeds_managed_vibe_workflow(
    api_client: TestClient,
) -> None:
    """The managed Vibe workflow should exist immediately after startup."""

    response = api_client.get("/api/workflows/orcheo-vibe-agent/canvas")
    assert response.status_code == 200

    payload = response.json()
    assert payload["workflow"]["handle"] == "orcheo-vibe-agent"
    assert payload["workflow"]["is_archived"] is False
    assert payload["versions"][0]["version"] == 1
