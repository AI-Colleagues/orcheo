"""Service token management operations.

Pure business logic for service token operations, shared by CLI and MCP interfaces.
"""

from __future__ import annotations
from typing import Any
from orcheo_sdk.cli.http import ApiClient


def list_service_tokens_data(client: ApiClient) -> dict[str, Any]:
    """List all service tokens.

    Args:
        client: API client instance

    Returns:
        Dictionary with tokens list and total count
    """
    return client.get("/api/admin/service-tokens")


def show_service_token_data(
    client: ApiClient,
    token_id: str,
) -> dict[str, Any]:
    """Get details for a specific service token.

    Args:
        client: API client instance
        token_id: Token identifier

    Returns:
        Service token details
    """
    return client.get(f"/api/admin/service-tokens/{token_id}")


def create_service_token_data(
    client: ApiClient,
    name: str | None = None,
    scopes: list[str] | None = None,
    workspace_ids: list[str] | None = None,
    expires_in_seconds: int | None = None,
) -> dict[str, Any]:
    """Create a new service token.

    Args:
        client: API client instance
        name: Optional human-readable name for the token (need not be unique)
        scopes: Optional list of scopes to grant
        workspace_ids: Optional list of workspace IDs the token can access
        expires_in_seconds: Optional expiration time in seconds

    Returns:
        Created token with identifier and secret
    """
    payload: dict[str, str | list[str] | int] = {}
    if name:
        payload["name"] = name
    if scopes:
        payload["scopes"] = scopes
    if workspace_ids:
        payload["workspace_ids"] = workspace_ids
    if expires_in_seconds:
        payload["expires_in_seconds"] = expires_in_seconds

    return client.post("/api/admin/service-tokens", json_body=payload)


def revoke_service_token_data(
    client: ApiClient,
    token_id: str,
    reason: str,
) -> dict[str, str]:
    """Revoke a service token immediately.

    Args:
        client: API client instance
        token_id: Token identifier to revoke
        reason: Reason for revocation

    Returns:
        Success message
    """
    response = client.delete(
        f"/api/admin/service-tokens/{token_id}",
        json_body={"reason": reason},
    )
    if response and "message" in response:
        return {"status": "success", "message": response["message"]}
    return {"status": "success", "message": f"Service token '{token_id}' revoked"}
