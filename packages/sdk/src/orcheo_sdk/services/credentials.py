"""Credential service operations.

Pure business logic for credential operations, shared by CLI and MCP interfaces.
"""

from __future__ import annotations
from typing import Any
from orcheo_sdk.cli.http import ApiClient


def list_credentials_data(
    client: ApiClient,
    workflow_id: str | None = None,
) -> list[dict[str, Any]]:
    """Get list of credentials.

    Args:
        client: API client instance
        workflow_id: Optional workflow ID filter

    Returns:
        List of credential objects
    """
    params = {"workflow_id": workflow_id} if workflow_id else None
    return client.get("/api/credentials", params=params)


def create_credential_data(
    client: ApiClient,
    name: str,
    provider: str,
    secret: str,
    actor: str = "api",
    access: str = "scoped",
    workflow_id: str | None = None,
    scopes: list[str] | None = None,
    kind: str = "secret",
) -> dict[str, Any]:
    """Create a credential.

    Args:
        client: API client instance
        name: Credential name
        provider: Provider identifier
        secret: Secret value
        actor: Actor creating the credential
        access: Access level (scoped/shared)
        workflow_id: Optional workflow association
        scopes: Optional scopes list
        kind: Credential kind

    Returns:
        Created credential object
    """
    payload: dict[str, Any] = {
        "name": name,
        "provider": provider,
        "secret": secret,
        "actor": actor,
        "access": access,
        "scopes": scopes or [],
        "kind": kind,
    }
    if workflow_id:
        payload["workflow_id"] = workflow_id

    return client.post("/api/credentials", json_body=payload)


def delete_credential_data(
    client: ApiClient,
    credential_id: str,
    workflow_id: str | None = None,
) -> dict[str, str]:
    """Delete a credential.

    Args:
        client: API client instance
        credential_id: Credential identifier
        workflow_id: Optional workflow association

    Returns:
        Success message
    """
    params = {"workflow_id": workflow_id} if workflow_id else None
    client.delete(f"/api/credentials/{credential_id}", params=params)
    return {"status": "success", "message": "Credential deleted"}
