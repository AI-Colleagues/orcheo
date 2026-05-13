"""Tests for the revoke_service_token endpoint."""

from __future__ import annotations
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import HTTPException, status
from orcheo_backend.app.authentication import (
    AuthenticationError,
    AuthorizationError,
    AuthorizationPolicy,
    RequestContext,
)
from orcheo_backend.app.service_token_endpoints import (
    RevokeServiceTokenRequest,
    revoke_service_token,
)


@pytest.mark.asyncio
async def test_revoke_service_token_success(admin_policy, mock_workspace):
    """Endpoint should revoke tokens and return 204."""
    request = RevokeServiceTokenRequest(reason="Security breach detected")

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_manager.revoke.return_value = None
        mock_get_manager.return_value = mock_manager

        response = await revoke_service_token(
            "token-to-revoke", request, admin_policy, mock_workspace
        )

        assert response is None
        mock_manager.revoke.assert_called_once_with(
            "token-to-revoke",
            reason="Security breach detected",
        )


@pytest.mark.asyncio
async def test_revoke_service_token_not_found(admin_policy, mock_workspace):
    """Missing tokens should raise HTTP 404."""
    request = RevokeServiceTokenRequest(reason="Test")

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_manager.revoke.side_effect = KeyError("Token not found")
        mock_get_manager.return_value = mock_manager

        with pytest.raises(HTTPException) as exc_info:
            await revoke_service_token(
                "nonexistent-token", request, admin_policy, mock_workspace
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_revoke_service_token_without_authentication(mock_workspace):
    """Anonymous users should be rejected."""
    anonymous_context = RequestContext.anonymous()
    policy = AuthorizationPolicy(anonymous_context)
    request = RevokeServiceTokenRequest(reason="Test")

    with pytest.raises(AuthenticationError):
        await revoke_service_token("token-123", request, policy, mock_workspace)


@pytest.mark.asyncio
async def test_revoke_service_token_without_required_scope(mock_workspace):
    """Missing admin:tokens:write scope should raise AuthorizationError."""
    context = RequestContext(
        subject="user",
        identity_type="user",
        scopes=frozenset(["read"]),
    )
    policy = AuthorizationPolicy(context)
    request = RevokeServiceTokenRequest(reason="Test")

    with pytest.raises(AuthorizationError):
        await revoke_service_token("token-123", request, policy, mock_workspace)
