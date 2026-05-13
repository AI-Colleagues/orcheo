"""Tests for the rotate_service_token endpoint."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import HTTPException, status
from orcheo_backend.app.authentication import (
    AuthenticationError,
    AuthorizationError,
    AuthorizationPolicy,
    RequestContext,
    ServiceTokenRecord,
)
from orcheo_backend.app.service_token_endpoints import (
    RotateServiceTokenRequest,
    rotate_service_token,
)


@pytest.mark.asyncio
async def test_rotate_service_token_success(admin_policy, mock_workspace):
    """Endpoint should return the new token details."""
    request = RotateServiceTokenRequest(
        overlap_seconds=300,
        expires_in_seconds=7200,
    )

    mock_new_secret = "new-secret-value"
    mock_new_record = ServiceTokenRecord(
        identifier="new-token-id",
        secret_hash="new-hash",
        scopes=frozenset(["read"]),
        workspace_ids=frozenset(["ws-1"]),
        issued_at=datetime.now(tz=UTC),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=2),
    )

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_manager.rotate.return_value = (mock_new_secret, mock_new_record)
        mock_get_manager.return_value = mock_manager

        response = await rotate_service_token(
            "old-token-id", request, admin_policy, mock_workspace
        )

        assert response.identifier == "new-token-id"
        assert response.secret == mock_new_secret
        assert "Old token 'old-token-id' valid for 300s" in response.message
        mock_manager.rotate.assert_called_once_with(
            "old-token-id",
            overlap_seconds=300,
            expires_in=7200,
        )


@pytest.mark.asyncio
async def test_rotate_service_token_with_default_overlap(admin_policy, mock_workspace):
    """Default overlap of 300 seconds should be applied."""
    request = RotateServiceTokenRequest()

    mock_new_secret = "new-secret"
    mock_new_record = ServiceTokenRecord(
        identifier="new-token",
        secret_hash="hash",
        scopes=frozenset(),
        workspace_ids=frozenset(),
        issued_at=datetime.now(tz=UTC),
    )

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_manager.rotate.return_value = (mock_new_secret, mock_new_record)
        mock_get_manager.return_value = mock_manager

        response = await rotate_service_token(
            "old-token", request, admin_policy, mock_workspace
        )

        mock_manager.rotate.assert_called_once_with(
            "old-token",
            overlap_seconds=300,
            expires_in=None,
        )
        assert "300s" in response.message


@pytest.mark.asyncio
async def test_rotate_service_token_not_found(admin_policy, mock_workspace):
    """Missing tokens should raise HTTP 404."""
    request = RotateServiceTokenRequest(overlap_seconds=300)

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_manager.rotate.side_effect = KeyError("Token not found")
        mock_get_manager.return_value = mock_manager

        with pytest.raises(HTTPException) as exc_info:
            await rotate_service_token(
                "nonexistent-token", request, admin_policy, mock_workspace
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_rotate_service_token_without_authentication(mock_workspace):
    """Anonymous users should be rejected."""
    anonymous_context = RequestContext.anonymous()
    policy = AuthorizationPolicy(anonymous_context)
    request = RotateServiceTokenRequest()

    with pytest.raises(AuthenticationError):
        await rotate_service_token("token-123", request, policy, mock_workspace)


@pytest.mark.asyncio
async def test_rotate_service_token_without_required_scope(mock_workspace):
    """Missing admin:tokens:write scope should raise AuthorizationError."""
    context = RequestContext(
        subject="user",
        identity_type="user",
        scopes=frozenset(["read"]),
    )
    policy = AuthorizationPolicy(context)
    request = RotateServiceTokenRequest()

    with pytest.raises(AuthorizationError):
        await rotate_service_token("token-123", request, policy, mock_workspace)
