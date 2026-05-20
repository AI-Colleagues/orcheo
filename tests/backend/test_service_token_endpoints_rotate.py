"""Tests for the rotate_service_token endpoint."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import HTTPException, status
from orcheo_backend.app.authentication import (
    AuthenticationError,
    AuthorizationPolicy,
    RequestContext,
    ServiceTokenRecord,
)
from orcheo_backend.app.service_token_endpoints import (
    RotateServiceTokenRequest,
    rotate_service_token,
)


def _owned_record(token_id: str, workspace_id: str) -> ServiceTokenRecord:
    return ServiceTokenRecord(
        identifier=token_id,
        secret_hash="hash",
        scopes=frozenset(["read"]),
        workspace_ids=frozenset([workspace_id]),
        issued_at=datetime.now(tz=UTC),
        workspace_id=workspace_id,
    )


@pytest.mark.asyncio
async def test_rotate_service_token_success(admin_policy, mock_workspace):
    """Endpoint should return the new token details."""
    workspace_id = str(mock_workspace.workspace_id)
    request = RotateServiceTokenRequest(
        overlap_seconds=300,
        expires_in_seconds=7200,
    )

    mock_new_secret = "new-secret-value"
    mock_new_record = ServiceTokenRecord(
        identifier="new-token-id",
        secret_hash="new-hash",
        scopes=frozenset(["read"]),
        workspace_ids=frozenset([workspace_id]),
        issued_at=datetime.now(tz=UTC),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=2),
        workspace_id=workspace_id,
    )

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_manager._repository.find_by_id.return_value = _owned_record(
            "old-token-id", workspace_id
        )
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
    workspace_id = str(mock_workspace.workspace_id)
    request = RotateServiceTokenRequest()

    mock_new_secret = "new-secret"
    mock_new_record = ServiceTokenRecord(
        identifier="new-token",
        secret_hash="hash",
        scopes=frozenset(),
        workspace_ids=frozenset([workspace_id]),
        issued_at=datetime.now(tz=UTC),
        workspace_id=workspace_id,
    )

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_manager._repository.find_by_id.return_value = _owned_record(
            "old-token", workspace_id
        )
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
        mock_manager._repository.find_by_id.return_value = None
        mock_get_manager.return_value = mock_manager

        with pytest.raises(HTTPException) as exc_info:
            await rotate_service_token(
                "nonexistent-token", request, admin_policy, mock_workspace
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in str(exc_info.value.detail)
        mock_manager.rotate.assert_not_called()


@pytest.mark.asyncio
async def test_rotate_service_token_other_workspace(admin_policy, mock_workspace):
    """Tokens owned by another workspace cannot be rotated."""
    request = RotateServiceTokenRequest()

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_manager._repository.find_by_id.return_value = _owned_record(
            "foreign-token", "other-workspace"
        )
        mock_get_manager.return_value = mock_manager

        with pytest.raises(HTTPException) as exc_info:
            await rotate_service_token(
                "foreign-token", request, admin_policy, mock_workspace
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        mock_manager.rotate.assert_not_called()


@pytest.mark.asyncio
async def test_rotate_service_token_without_authentication(mock_workspace):
    """Anonymous users should be rejected."""
    anonymous_context = RequestContext.anonymous()
    policy = AuthorizationPolicy(anonymous_context)
    request = RotateServiceTokenRequest()

    with pytest.raises(AuthenticationError):
        await rotate_service_token("token-123", request, policy, mock_workspace)
