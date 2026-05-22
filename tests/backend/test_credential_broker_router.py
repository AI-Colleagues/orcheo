"""Tests for the Credential Broker FastAPI router."""

from __future__ import annotations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from orcheo.sandbox.broker import CredentialBroker
from orcheo_backend.app.credential_broker import build_credential_broker_router


def _client(store: dict[tuple[str, str], str]) -> tuple[TestClient, CredentialBroker]:
    def resolver(*, workspace_id: str, credential_name: str) -> str:
        return store[(workspace_id, credential_name)]

    broker = CredentialBroker(secret="s", resolver=resolver)
    app = FastAPI()
    app.include_router(build_credential_broker_router(broker))
    return TestClient(app), broker


def test_resolve_returns_value_for_matching_token() -> None:
    """Bearer token + matching workspace header returns the credential value."""
    client, broker = _client({("ws", "openai"): "sk-1"})
    token = broker.issue(workspace_id="ws", run_id="r1")
    response = client.post(
        "/internal/credentials/resolve",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Orcheo-Workspace": "ws",
        },
        json={"run_id": "r1", "credential_name": "openai"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["value"] == "sk-1"
    assert body["workspace_id"] == "ws"


def test_resolve_rejects_cross_workspace_header() -> None:
    """A workspace header that conflicts with the token returns 403."""
    client, broker = _client({("ws", "k"): "v"})
    token = broker.issue(workspace_id="ws", run_id="r1")
    response = client.post(
        "/internal/credentials/resolve",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Orcheo-Workspace": "other",
        },
        json={"run_id": "r1", "credential_name": "k"},
    )
    assert response.status_code == 403


def test_resolve_rejects_missing_token() -> None:
    """Requests without Authorization return 401."""
    client, _ = _client({})
    response = client.post(
        "/internal/credentials/resolve",
        json={"run_id": "r1", "credential_name": "k"},
    )
    assert response.status_code == 401


def test_resolve_rejects_mismatched_run_id() -> None:
    """A run_id in the payload that doesn't match the token returns 403."""
    client, broker = _client({("ws", "k"): "v"})
    token = broker.issue(workspace_id="ws", run_id="r1")
    response = client.post(
        "/internal/credentials/resolve",
        headers={"Authorization": f"Bearer {token}"},
        json={"run_id": "r2", "credential_name": "k"},
    )
    assert response.status_code == 403


def test_resolve_rejects_unknown_credential_with_403() -> None:
    """An unknown credential is treated as a scope error (403)."""
    client, broker = _client({})
    token = broker.issue(workspace_id="ws", run_id="r1")
    response = client.post(
        "/internal/credentials/resolve",
        headers={"Authorization": f"Bearer {token}"},
        json={"run_id": "r1", "credential_name": "missing"},
    )
    assert response.status_code == 403


def test_resolve_rejects_invalid_token_with_401() -> None:
    """An expired or tampered token returns 401 via BrokerTokenInvalidError."""
    client, broker = _client({("ws", "k"): "v"})
    # Issue a token and then revoke it so the broker raises BrokerTokenInvalidError.
    token = broker.issue(workspace_id="ws", run_id="r-invalid")
    broker.revoke("r-invalid")
    response = client.post(
        "/internal/credentials/resolve",
        headers={"Authorization": f"Bearer {token}"},
        json={"run_id": "r-invalid", "credential_name": "k"},
    )
    assert response.status_code == 401
