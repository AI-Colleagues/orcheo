"""Shared helpers for authentication tests."""

from __future__ import annotations
import hashlib
import sys
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from orcheo.models import AesGcmCredentialCipher
from fastapi.testclient import TestClient
from orcheo.workspace import InMemoryWorkspaceRepository
from orcheo.workspace.models import Role, Workspace, WorkspaceContext
from orcheo_backend.app import create_app
from orcheo_backend.app.dependencies import (
    get_checkpoint_store,
    get_credential_service,
    get_history_store,
    get_plugin_installation_store,
    get_repository,
    get_vault,
    set_checkpoint_store,
    set_credential_service,
    set_history_store,
    set_plugin_installation_store,
    set_repository,
    set_vault,
)
from orcheo_backend.app.authentication import (
    Authenticator,
    ServiceTokenRecord,
    ServiceTokenManager,
    load_auth_settings,
    reset_authentication_state,
)
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.service_token_repository import InMemoryServiceTokenRepository
from orcheo.vault import InMemoryCredentialVault
from orcheo.vault.oauth import OAuthCredentialService
from orcheo_backend.app.workspace.dependencies import (
    reset_workspace_state,
    resolve_workspace_context,
    set_workspace_repository,
)


_TEST_WORKSPACE = WorkspaceContext(
    workspace_id=uuid4(),
    workspace_slug="test",
    user_id="test-user",
    role=Role.OWNER,
)
_SERVICE_TOKEN_REPOSITORY: InMemoryServiceTokenRepository | None = None
_AUTH_MODULE = sys.modules["orcheo_backend.app.authentication"]
_AUTH_DEPENDENCIES_MODULE = sys.modules[
    "orcheo_backend.app.authentication.dependencies"
]
_ORIGINAL_GET_AUTHENTICATOR = _AUTH_MODULE.get_authenticator
_ORIGINAL_DEP_GET_AUTHENTICATOR = _AUTH_DEPENDENCIES_MODULE.get_authenticator
_TEST_APP = None


def _restore_authenticator_hooks() -> None:
    """Restore the real authenticator functions after client test shims."""

    _AUTH_MODULE.get_authenticator = _ORIGINAL_GET_AUTHENTICATOR
    _AUTH_DEPENDENCIES_MODULE.get_authenticator = _ORIGINAL_DEP_GET_AUTHENTICATOR


def _build_test_authenticator(refresh: bool = False) -> Authenticator:
    """Build an authenticator backed by the in-memory service token repository."""

    global _SERVICE_TOKEN_REPOSITORY  # noqa: PLW0603
    if _SERVICE_TOKEN_REPOSITORY is None:
        _SERVICE_TOKEN_REPOSITORY = InMemoryServiceTokenRepository()
    settings = load_auth_settings(refresh=refresh)
    return Authenticator(
        settings,
        ServiceTokenManager(_SERVICE_TOKEN_REPOSITORY),
    )


def _install_test_authenticator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route authenticator lookups to the shared in-memory test repository."""

    app_auth_module = sys.modules["orcheo_backend.app.authentication"]
    app_auth_dependencies = sys.modules[
        "orcheo_backend.app.authentication.dependencies"
    ]
    monkeypatch.setattr(app_auth_module, "get_authenticator", _build_test_authenticator)
    monkeypatch.setattr(
        app_auth_dependencies, "get_authenticator", _build_test_authenticator
    )


def _setup_test_workspace_repository() -> None:
    """Populate an in-memory workspace repository with the shared test workspace."""
    ws_repo = InMemoryWorkspaceRepository()
    ws_repo.create_workspace(
        Workspace(id=_TEST_WORKSPACE.workspace_id, slug="test", name="Test Workspace")
    )
    set_workspace_repository(ws_repo)


def reset_auth_state(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Reset authentication-related environment between tests."""

    global _SERVICE_TOKEN_REPOSITORY  # noqa: PLW0603
    _SERVICE_TOKEN_REPOSITORY = None
    for key in (
        "ORCHEO_AUTH_SERVICE_TOKENS",
        "ORCHEO_AUTH_JWT_SECRET",
        "ORCHEO_AUTH_MODE",
        "ORCHEO_AUTH_ALLOWED_ALGORITHMS",
        "ORCHEO_AUTH_AUDIENCE",
        "ORCHEO_AUTH_ISSUER",
        "ORCHEO_AUTH_JWKS_URL",
        "ORCHEO_AUTH_JWKS",
        "ORCHEO_AUTH_JWKS_STATIC",
        "ORCHEO_AUTH_RATE_LIMIT_IP",
        "ORCHEO_AUTH_RATE_LIMIT_IDENTITY",
        "ORCHEO_AUTH_RATE_LIMIT_INTERVAL",
        "ORCHEO_AUTH_SERVICE_TOKEN_DB_PATH",
        "ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN",
        "ORCHEO_AUTH_DEV_LOGIN_ENABLED",
        "ORCHEO_AUTH_DEV_COOKIE_NAME",
        "ORCHEO_AUTH_DEV_SCOPES",
        "ORCHEO_AUTH_DEV_WORKSPACE_IDS",
        "ORCHEO_WORKSPACE_BACKEND",
        "ORCHEO_STUDIO_URL",
        "ORCHEO_CORS_ALLOW_ORIGINS",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("ORCHEO_WORKSPACE_BACKEND", "postgres")
    _restore_authenticator_hooks()
    reset_authentication_state()
    try:
        yield
    finally:
        monkeypatch.undo()
        reset_workspace_state()
        _restore_authenticator_hooks()
        _SERVICE_TOKEN_REPOSITORY = None
    reset_authentication_state()
    _restore_authenticator_hooks()


def _get_test_app():
    """Return the reusable auth-test application."""

    global _TEST_APP  # noqa: PLW0603
    if _TEST_APP is None:
        _TEST_APP = create_app()
    return _TEST_APP


def create_test_client() -> TestClient:
    """Build a FastAPI test client wired to the in-memory repository."""

    _setup_test_workspace_repository()
    global _SERVICE_TOKEN_REPOSITORY  # noqa: PLW0603
    if _SERVICE_TOKEN_REPOSITORY is None:
        _SERVICE_TOKEN_REPOSITORY = InMemoryServiceTokenRepository()
    cipher = AesGcmCredentialCipher(key="test-key")
    vault = InMemoryCredentialVault(cipher=cipher)
    service = OAuthCredentialService(vault, token_ttl_seconds=600, providers={})
    repository = InMemoryWorkflowRepository(credential_service=service)
    history_store = AsyncMock()
    checkpoint_store = AsyncMock()
    plugin_installation_store = AsyncMock()

    app_auth_module = sys.modules["orcheo_backend.app.authentication"]
    app_auth_dependencies = sys.modules[
        "orcheo_backend.app.authentication.dependencies"
    ]
    original_get_authenticator = app_auth_module.get_authenticator
    original_dep_get_authenticator = app_auth_dependencies.get_authenticator
    app_auth_module.get_authenticator = _build_test_authenticator
    app_auth_dependencies.get_authenticator = _build_test_authenticator

    set_repository(repository)
    set_history_store(history_store)
    set_checkpoint_store(checkpoint_store)
    set_plugin_installation_store(plugin_installation_store)
    set_credential_service(service)
    set_vault(vault)

    app = _get_test_app()
    app.dependency_overrides.clear()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_history_store] = lambda: history_store
    app.dependency_overrides[get_checkpoint_store] = lambda: checkpoint_store
    app.dependency_overrides[get_plugin_installation_store] = (
        lambda: plugin_installation_store
    )
    app.dependency_overrides[get_credential_service] = lambda: service
    app.dependency_overrides[get_vault] = lambda: vault
    app.state.vault = vault
    app.state.credential_service = service
    app.dependency_overrides[resolve_workspace_context] = lambda: _TEST_WORKSPACE
    client = TestClient(app)

    def _cleanup() -> None:
        global _SERVICE_TOKEN_REPOSITORY  # noqa: PLW0603
        app_auth_module.get_authenticator = original_get_authenticator
        app_auth_dependencies.get_authenticator = original_dep_get_authenticator
        _SERVICE_TOKEN_REPOSITORY = None
        client.close()

    import weakref

    weakref.finalize(client, _cleanup)
    return client


def _setup_service_token(
    monkeypatch: pytest.MonkeyPatch,
    token_secret: str,
    *,
    identifier: str | None = None,
    scopes: list[str] | None = None,
    workspace_ids: list[str] | None = None,
    expires_at: datetime | None = None,
) -> tuple[str, str]:
    """Set up a service token for testing."""

    repo = InMemoryServiceTokenRepository()
    monkeypatch.setattr(
        sys.modules[__name__],
        "_SERVICE_TOKEN_REPOSITORY",
        repo,
        raising=False,
    )
    monkeypatch.setenv("ORCHEO_AUTH_SERVICE_TOKEN_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")

    record = ServiceTokenRecord(
        identifier=identifier or "test-token",
        secret_hash=hashlib.sha256(token_secret.encode("utf-8")).hexdigest(),
        scopes=frozenset(scopes or []),
        workspace_ids=frozenset(workspace_ids or []),
        issued_at=datetime.now(tz=UTC),
        expires_at=expires_at,
    )
    repo._tokens[record.identifier] = record  # type: ignore[attr-defined]

    return "postgres", token_secret


__all__ = ["reset_auth_state", "create_test_client", "_setup_service_token"]
