"""Tests for the create_service_token endpoint."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
import pytest
from orcheo_backend.app.authentication import (
    AuthenticationError,
    AuthorizationPolicy,
    RequestContext,
    ServiceTokenRecord,
)
from orcheo_backend.app.service_token_endpoints import (
    CreateServiceTokenRequest,
    create_service_token,
)


@pytest.mark.asyncio
async def test_create_service_token_success(admin_policy, mock_workspace):
    """Endpoint should mint a token scoped to the active workspace."""
    request = CreateServiceTokenRequest(
        identifier="my-token",
        scopes=["read", "write"],
        expires_in_seconds=3600,
    )
    workspace_id = str(mock_workspace.workspace_id)

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_secret = "secret-token-value"
        mock_record = ServiceTokenRecord(
            identifier="my-token",
            secret_hash="hash123",
            scopes=frozenset(["read", "write"]),
            workspace_ids=frozenset([workspace_id]),
            issued_at=datetime.now(tz=UTC),
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            workspace_id=workspace_id,
        )
        mock_manager.mint.return_value = (mock_secret, mock_record)
        mock_get_manager.return_value = mock_manager

        response = await create_service_token(request, admin_policy, mock_workspace)

        assert response.identifier == "my-token"
        assert response.secret == mock_secret
        assert response.workspace_ids == [workspace_id]
        assert "Store this token securely" in response.message
        mock_manager.mint.assert_called_once_with(
            identifier="my-token",
            scopes=["read", "write"],
            workspace_ids=[workspace_id],
            expires_in=3600,
            workspace_id=workspace_id,
        )


@pytest.mark.asyncio
async def test_create_service_token_with_default_values(admin_policy, mock_workspace):
    """Endpoint should allow a minimal payload while still scoping the token."""
    request = CreateServiceTokenRequest()
    workspace_id = str(mock_workspace.workspace_id)

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_secret = "generated-secret"
        mock_record = ServiceTokenRecord(
            identifier="auto-generated-id",
            secret_hash="hash",
            scopes=frozenset(),
            workspace_ids=frozenset([workspace_id]),
            issued_at=datetime.now(tz=UTC),
            workspace_id=workspace_id,
        )
        mock_manager.mint.return_value = (mock_secret, mock_record)
        mock_get_manager.return_value = mock_manager

        response = await create_service_token(request, admin_policy, mock_workspace)

        assert response.identifier == "auto-generated-id"
        assert response.secret == mock_secret
        mock_manager.mint.assert_called_once_with(
            identifier=None,
            scopes=[],
            workspace_ids=[workspace_id],
            expires_in=None,
            workspace_id=workspace_id,
        )


@pytest.mark.asyncio
async def test_create_service_token_allows_non_admin_member(mock_workspace):
    """Any authenticated workspace member may mint a token."""
    context = RequestContext(
        subject="member",
        identity_type="user",
        scopes=frozenset(["workflows:read"]),
    )
    policy = AuthorizationPolicy(context)
    request = CreateServiceTokenRequest(scopes=["workflows:read"])
    workspace_id = str(mock_workspace.workspace_id)

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_record = ServiceTokenRecord(
            identifier="member-token",
            secret_hash="hash",
            scopes=frozenset(["workflows:read"]),
            workspace_ids=frozenset([workspace_id]),
            issued_at=datetime.now(tz=UTC),
            workspace_id=workspace_id,
        )
        mock_manager.mint.return_value = ("member-secret", mock_record)
        mock_get_manager.return_value = mock_manager

        response = await create_service_token(request, policy, mock_workspace)

        assert response.identifier == "member-token"
        assert response.secret == "member-secret"


@pytest.mark.asyncio
async def test_create_service_token_without_authentication(mock_workspace):
    """Anonymous users should be rejected."""
    anonymous_context = RequestContext.anonymous()
    policy = AuthorizationPolicy(anonymous_context)
    request = CreateServiceTokenRequest()

    with pytest.raises(AuthenticationError):
        await create_service_token(request, policy, mock_workspace)
