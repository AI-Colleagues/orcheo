"""Tests for the list_service_tokens endpoint."""

from __future__ import annotations
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
import pytest
from orcheo_backend.app.authentication import (
    AuthenticationError,
    AuthorizationPolicy,
    RequestContext,
    ServiceTokenRecord,
)
from orcheo_backend.app.service_token_endpoints import list_service_tokens


@pytest.mark.asyncio
async def test_list_service_tokens_success(admin_policy, mock_workspace):
    """Endpoint should return the active workspace's tokens without secrets."""
    workspace_id = str(mock_workspace.workspace_id)
    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_records = [
            ServiceTokenRecord(
                identifier="token-1",
                secret_hash="hash1",
                scopes=frozenset(["read"]),
                workspace_ids=frozenset([workspace_id]),
                issued_at=datetime.now(tz=UTC),
                workspace_id=workspace_id,
            ),
            ServiceTokenRecord(
                identifier="token-2",
                secret_hash="hash2",
                scopes=frozenset(["write"]),
                workspace_ids=frozenset([workspace_id]),
                issued_at=datetime.now(tz=UTC),
                workspace_id=workspace_id,
            ),
        ]
        mock_manager._repository.list_for_workspace.return_value = mock_records
        mock_get_manager.return_value = mock_manager

        response = await list_service_tokens(admin_policy, mock_workspace)

        assert response.total == 2
        assert len(response.tokens) == 2
        assert response.tokens[0].identifier == "token-1"
        assert response.tokens[1].identifier == "token-2"
        assert response.tokens[0].secret is None
        assert response.tokens[1].secret is None
        mock_manager._repository.list_for_workspace.assert_called_once_with(
            workspace_id
        )


@pytest.mark.asyncio
async def test_list_service_tokens_empty(admin_policy, mock_workspace):
    """A workspace with no tokens should return zero results."""
    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_manager._repository.list_for_workspace.return_value = []
        mock_get_manager.return_value = mock_manager

        response = await list_service_tokens(admin_policy, mock_workspace)

        assert response.total == 0
        assert len(response.tokens) == 0


@pytest.mark.asyncio
async def test_list_service_tokens_without_authentication(mock_workspace):
    """Anonymous users cannot list tokens."""
    anonymous_context = RequestContext.anonymous()
    policy = AuthorizationPolicy(anonymous_context)

    with pytest.raises(AuthenticationError):
        await list_service_tokens(policy, mock_workspace)
