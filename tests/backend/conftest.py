"""Shared pytest fixtures for backend tests."""

from __future__ import annotations
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4
import pytest
from orcheo_backend.app.authentication import (
    AuthorizationPolicy,
    RequestContext,
    ServiceTokenRecord,
)
from tests.backend.app_integration_fixtures import (
    client as integration_client,
)
from tests.backend.app_integration_fixtures import (
    history_store as integration_history_store,
)
from tests.backend.app_integration_fixtures import (
    repository as integration_repository,
)


@pytest.fixture
def sample_token_record() -> ServiceTokenRecord:
    """Create a sample service token record for testing."""
    return ServiceTokenRecord(
        identifier="test-token-123",
        secret_hash="abc123hash",
        scopes=frozenset(["read", "write"]),
        workspace_ids=frozenset(["ws-1", "ws-2"]),
        issued_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
        expires_at=datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC),
    )


@pytest.fixture
def sample_token_with_rotation() -> ServiceTokenRecord:
    """Create a token with rotation details."""
    return ServiceTokenRecord(
        identifier="rotated-token",
        secret_hash="rotatedhash",
        scopes=frozenset(["admin"]),
        workspace_ids=frozenset(),
        issued_at=datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
        rotation_expires_at=datetime(2025, 1, 1, 1, 0, 0, tzinfo=UTC),
        rotated_to="new-token-id",
    )


@pytest.fixture
def sample_revoked_token() -> ServiceTokenRecord:
    """Create a revoked token record."""
    return ServiceTokenRecord(
        identifier="revoked-token",
        secret_hash="revokedhash",
        scopes=frozenset(["read"]),
        workspace_ids=frozenset(["ws-1"]),
        issued_at=datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
        revoked_at=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
        revocation_reason="Security breach",
    )


@pytest.fixture
def authenticated_context() -> RequestContext:
    """Provide an authenticated context with admin token scopes."""
    return RequestContext(
        subject="admin-user",
        identity_type="user",
        scopes=frozenset(["admin:tokens:read", "admin:tokens:write"]),
    )


@pytest.fixture
def admin_policy(authenticated_context: RequestContext) -> AuthorizationPolicy:
    """Return an authorization policy granting admin token scopes."""
    return AuthorizationPolicy(authenticated_context)


@pytest.fixture
def mock_workspace() -> SimpleNamespace:
    """Return a minimal workspace context object usable as WorkspaceContextDep."""
    return SimpleNamespace(
        workspace_id=uuid4(),
        user_id="test-user",
        slug="test-workspace",
        quotas=SimpleNamespace(
            max_credentials=1000,
            max_workflows=1000,
            max_storage_rows=1_000_000,
        ),
    )


# Re-export integration fixtures so dependent tests can resolve them.
client = integration_client
history_store = integration_history_store
repository = integration_repository
