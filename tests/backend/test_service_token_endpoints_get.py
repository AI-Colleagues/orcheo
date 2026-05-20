"""Tests for the get_service_token endpoint."""

from __future__ import annotations
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import HTTPException, status
from orcheo_backend.app.authentication import (
    AuthenticationError,
    AuthorizationPolicy,
    RequestContext,
    ServiceTokenRecord,
)
from orcheo_backend.app.service_token_endpoints import get_service_token


@pytest.mark.asyncio
async def test_get_service_token_success(admin_policy, mock_workspace):
    """Endpoint should return token metadata without secret."""
    workspace_id = str(mock_workspace.workspace_id)
    mock_record = ServiceTokenRecord(
        identifier="token-123",
        secret_hash="hash123",
        scopes=frozenset(["read", "write"]),
        workspace_ids=frozenset([workspace_id]),
        issued_at=datetime.now(tz=UTC),
        workspace_id=workspace_id,
    )

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_manager._repository.find_by_id.return_value = mock_record
        mock_get_manager.return_value = mock_manager

        response = await get_service_token("token-123", admin_policy, mock_workspace)

        assert response.identifier == "token-123"
        assert response.scopes == ["read", "write"]
        assert response.workspace_ids == [workspace_id]
        assert response.secret is None
        mock_manager._repository.find_by_id.assert_called_once_with("token-123")


@pytest.mark.asyncio
async def test_get_service_token_not_found(admin_policy, mock_workspace):
    """Non-existent tokens should raise HTTP 404."""
    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_manager._repository.find_by_id.return_value = None
        mock_get_manager.return_value = mock_manager

        with pytest.raises(HTTPException) as exc_info:
            await get_service_token("nonexistent-token", admin_policy, mock_workspace)

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_get_service_token_other_workspace(admin_policy, mock_workspace):
    """Tokens owned by another workspace should be reported as missing."""
    foreign_record = ServiceTokenRecord(
        identifier="foreign-token",
        secret_hash="hash",
        scopes=frozenset(["read"]),
        workspace_ids=frozenset(["other-workspace"]),
        issued_at=datetime.now(tz=UTC),
        workspace_id="other-workspace",
    )

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_manager._repository.find_by_id.return_value = foreign_record
        mock_get_manager.return_value = mock_manager

        with pytest.raises(HTTPException) as exc_info:
            await get_service_token("foreign-token", admin_policy, mock_workspace)

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_service_token_without_authentication(mock_workspace):
    """Anonymous users should be rejected."""
    anonymous_context = RequestContext.anonymous()
    policy = AuthorizationPolicy(anonymous_context)

    with pytest.raises(AuthenticationError):
        await get_service_token("token-123", policy, mock_workspace)


@pytest.mark.asyncio
async def test_get_service_token_with_all_fields(admin_policy, mock_workspace):
    """All record fields should be surfaced and sorted."""
    workspace_id = str(mock_workspace.workspace_id)
    mock_record = ServiceTokenRecord(
        identifier="complete-token",
        secret_hash="hash",
        scopes=frozenset(["admin", "read", "write"]),
        workspace_ids=frozenset([workspace_id]),
        issued_at=datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC),
        last_used_at=datetime(2025, 6, 15, 12, 30, 0, tzinfo=UTC),
        use_count=999,
        revoked_at=None,
        revocation_reason=None,
        rotated_to=None,
        workspace_id=workspace_id,
    )

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_manager._repository.find_by_id.return_value = mock_record
        mock_get_manager.return_value = mock_manager

        response = await get_service_token(
            "complete-token", admin_policy, mock_workspace
        )

        assert response.identifier == "complete-token"
        assert response.scopes == ["admin", "read", "write"]
        assert response.workspace_ids == [workspace_id]
        assert response.use_count == 999
        assert response.last_used_at == datetime(2025, 6, 15, 12, 30, 0, tzinfo=UTC)
