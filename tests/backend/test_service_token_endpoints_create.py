"""Tests for the create_service_token endpoint."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import pytest
from orcheo.workspace import Role, WorkspaceContext
from orcheo_backend.app.authentication import (
    AuthenticationError,
    AuthorizationError,
    AuthorizationPolicy,
    RequestContext,
    ServiceTokenRecord,
)
from orcheo_backend.app.service_token_endpoints import (
    CreateServiceTokenRequest,
    create_service_token,
)


def _make_workspace_context(workspace_id=None, slug="acme") -> WorkspaceContext:
    return WorkspaceContext(
        workspace_id=workspace_id or uuid4(),
        workspace_slug=slug,
        user_id="user",
        role=Role.ADMIN,
    )


@pytest.mark.asyncio
async def test_create_service_token_success(admin_policy):
    """Endpoint should mint and return a new token."""
    request = CreateServiceTokenRequest(
        identifier="my-token",
        scopes=["read", "write"],
        workspace_ids=["ws-1"],
        expires_in_seconds=3600,
    )

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_secret = "secret-token-value"
        mock_record = ServiceTokenRecord(
            identifier="my-token",
            secret_hash="hash123",
            scopes=frozenset(["read", "write"]),
            workspace_ids=frozenset(["ws-1"]),
            issued_at=datetime.now(tz=UTC),
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        )
        mock_manager.mint.return_value = (mock_secret, mock_record)
        mock_get_manager.return_value = mock_manager

        workspace = _make_workspace_context()
        response = await create_service_token(request, admin_policy, workspace)

        assert response.identifier == "my-token"
        assert response.secret == mock_secret
        assert "Store this token securely" in response.message
        mock_manager.mint.assert_called_once_with(
            identifier="my-token",
            scopes=["read", "write"],
            workspace_ids=["ws-1"],
            expires_in=3600,
            workspace_id=str(workspace.workspace_id),
        )


@pytest.mark.asyncio
async def test_create_service_token_with_default_values(admin_policy):
    """Endpoint should allow minimal payload with defaults."""
    request = CreateServiceTokenRequest()

    with patch(
        "orcheo_backend.app.service_token_endpoints.get_service_token_manager"
    ) as mock_get_manager:
        mock_manager = AsyncMock()
        mock_secret = "generated-secret"
        mock_record = ServiceTokenRecord(
            identifier="auto-generated-id",
            secret_hash="hash",
            scopes=frozenset(),
            workspace_ids=frozenset(),
            issued_at=datetime.now(tz=UTC),
        )
        mock_manager.mint.return_value = (mock_secret, mock_record)
        mock_get_manager.return_value = mock_manager

        workspace = _make_workspace_context()
        response = await create_service_token(request, admin_policy, workspace)

        assert response.identifier == "auto-generated-id"
        assert response.secret == mock_secret
        mock_manager.mint.assert_called_once_with(
            identifier=None,
            scopes=[],
            workspace_ids=[],
            expires_in=None,
            workspace_id=str(workspace.workspace_id),
        )


@pytest.mark.asyncio
async def test_create_service_token_without_authentication():
    """Anonymous users should be rejected."""
    anonymous_context = RequestContext.anonymous()
    policy = AuthorizationPolicy(anonymous_context)
    workspace = _make_workspace_context()
    request = CreateServiceTokenRequest()

    with pytest.raises(AuthenticationError):
        await create_service_token(request, policy, workspace)


@pytest.mark.asyncio
async def test_create_service_token_without_required_scope():
    """Missing admin:tokens:write scope should raise AuthorizationError."""
    context = RequestContext(
        subject="user",
        identity_type="user",
        scopes=frozenset(["read"]),
    )
    policy = AuthorizationPolicy(context)
    workspace = _make_workspace_context()
    request = CreateServiceTokenRequest()

    with pytest.raises(AuthorizationError):
        await create_service_token(request, policy, workspace)
