"""Bootstrap service token context tests."""

from __future__ import annotations
import asyncio
from datetime import UTC, datetime, timedelta
import pytest
from orcheo_backend.app.authentication import reset_authentication_state
from tests.backend.authentication_test_utils import (
    _build_test_authenticator,
    reset_auth_state,
)


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


def test_bootstrap_token_grants_default_admin_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap token has default admin scopes."""

    bootstrap_token = "bootstrap-secret-token"
    monkeypatch.setenv("ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN", bootstrap_token)
    reset_authentication_state()

    authenticator = _build_test_authenticator()
    context = None

    async def _authenticate() -> None:
        nonlocal context
        context = await authenticator.authenticate(bootstrap_token)

    asyncio.run(_authenticate())

    assert context is not None
    assert context.subject == "bootstrap"
    assert context.identity_type == "service"
    assert context.token_id == "bootstrap"
    assert "admin:tokens:read" in context.scopes
    assert "admin:tokens:write" in context.scopes
    assert "workflows:read" in context.scopes
    assert "workflows:write" in context.scopes
    assert "workflows:execute" in context.scopes
    assert "vault:read" in context.scopes
    assert "vault:write" in context.scopes


def test_bootstrap_token_respects_custom_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap token can have custom scopes configured."""

    bootstrap_token = "bootstrap-secret-token"
    monkeypatch.setenv("ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN", bootstrap_token)
    monkeypatch.setenv(
        "ORCHEO_AUTH_BOOTSTRAP_TOKEN_SCOPES", "workflows:read,workflows:write"
    )
    reset_authentication_state()

    authenticator = _build_test_authenticator()
    context = None

    async def _authenticate() -> None:
        nonlocal context
        context = await authenticator.authenticate(bootstrap_token)

    asyncio.run(_authenticate())

    assert context is not None
    assert context.scopes == frozenset(["workflows:read", "workflows:write"])
    assert "admin:tokens:write" not in context.scopes


def test_bootstrap_token_has_no_workspace_restrictions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap token has no workspace restrictions."""

    bootstrap_token = "bootstrap-secret-token"
    monkeypatch.setenv("ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN", bootstrap_token)
    reset_authentication_state()

    authenticator = _build_test_authenticator()
    context = None

    async def _authenticate() -> None:
        nonlocal context
        context = await authenticator.authenticate(bootstrap_token)

    asyncio.run(_authenticate())

    assert context is not None
    assert context.workspace_ids == frozenset()


def test_bootstrap_token_defaults_to_no_expiration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap token does not expire when no expiry is configured."""

    bootstrap_token = "bootstrap-secret-token"
    monkeypatch.setenv("ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN", bootstrap_token)
    reset_authentication_state()

    authenticator = _build_test_authenticator()
    context = None

    async def _authenticate() -> None:
        nonlocal context
        context = await authenticator.authenticate(bootstrap_token)

    asyncio.run(_authenticate())

    assert context is not None
    assert context.expires_at is None
    assert context.issued_at is None


def test_bootstrap_token_honours_future_expiration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap token returns configured expiry when still valid."""

    bootstrap_token = "bootstrap-secret-token"
    expires_at = datetime.now(tz=UTC) + timedelta(minutes=10)
    monkeypatch.setenv("ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN", bootstrap_token)
    monkeypatch.setenv("ORCHEO_AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT", expires_at.isoformat())
    reset_authentication_state()

    authenticator = _build_test_authenticator()
    context = None

    async def _authenticate() -> None:
        nonlocal context
        context = await authenticator.authenticate(bootstrap_token)

    asyncio.run(_authenticate())

    assert context is not None
    assert context.expires_at == expires_at
    assert context.claims.get("expires_at") == expires_at.isoformat()
