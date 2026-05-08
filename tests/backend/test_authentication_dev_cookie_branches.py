"""Cover missing branches in dependencies: dev cookie and default scopes."""

from __future__ import annotations
from types import SimpleNamespace
import pytest
from starlette.requests import Request
from orcheo_backend.app.authentication import AuthenticationError
from orcheo_backend.app.authentication.dependencies import (
    _attempt_bearer_auth_optional,
    _build_dev_context,
    _parse_dev_identity,
    _try_dev_login_session,
)
from orcheo_backend.app.authentication.settings import AuthSettings


def _base_settings(**overrides: object) -> AuthSettings:
    """Create a minimal AuthSettings for tests with reasonable defaults."""
    base = dict(
        mode="optional",
        jwt_secret=None,
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=("HS256",),
        audiences=(),
        issuer=None,
        service_token_backend="sqlite",
        service_token_db_path=None,
        rate_limit_ip=0,
        rate_limit_identity=0,
        rate_limit_interval=60,
        dev_login_enabled=True,
        dev_login_cookie_name="orcheo_dev_session",
        dev_login_scopes=(),  # intentionally empty to trigger defaults
        dev_login_workspace_ids=(),
    )
    base.update(overrides)
    return AuthSettings(**base)  # type: ignore[arg-type]


def test_build_dev_context_uses_internal_default_scopes() -> None:
    """_build_dev_context falls back to built-in default scopes when empty."""

    settings = _base_settings(dev_login_scopes=())
    ctx = _build_dev_context("dev:alice", settings)

    # The internal default scopes include workflows and vault permissions
    assert "workflows:read" in ctx.scopes
    assert "workflows:execute" in ctx.scopes
    assert "vault:write" in ctx.scopes


def test_parse_dev_identity_handles_non_string_and_blank_values() -> None:
    """_parse_dev_identity rejects non-string and blank values."""

    assert _parse_dev_identity(None) is None
    assert _parse_dev_identity(123) is None
    assert _parse_dev_identity("   ") is None


def test_parse_dev_identity_prefers_json_subject() -> None:
    """_parse_dev_identity extracts a JSON subject when present."""

    assert _parse_dev_identity('{"subject": "  alice  "}') == "alice"


def test_parse_dev_identity_falls_back_for_blank_json_subject() -> None:
    """_parse_dev_identity falls back to the raw string when JSON subject is blank."""

    result = _parse_dev_identity('{"subject": "   "}')

    assert result is not None
    assert result.startswith("{")


def test_try_dev_login_session_returns_none_when_cookie_missing() -> None:
    """_try_dev_login_session returns None if the configured cookie is absent."""

    settings = _base_settings()

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],  # no Cookie header present
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request"}

    request = Request(scope, receive)  # type: ignore[arg-type]

    result = _try_dev_login_session(request, settings)
    assert result is None


def test_try_dev_login_session_returns_none_when_cookie_name_missing() -> None:
    """_try_dev_login_session returns None when cookie fallback is disabled."""

    settings = _base_settings(dev_login_cookie_name="")
    scope = SimpleNamespace(headers={}, cookies={})

    assert _try_dev_login_session(scope, settings) is None


def test_try_dev_login_session_returns_none_when_cookie_absent() -> None:
    """_try_dev_login_session returns None when the cookie is missing."""

    settings = _base_settings()
    scope = SimpleNamespace(headers={}, cookies={})

    assert _try_dev_login_session(scope, settings) is None


def test_try_dev_login_session_returns_none_for_invalid_identity() -> None:
    """_try_dev_login_session ignores cookie values that do not parse."""

    settings = _base_settings()
    scope = SimpleNamespace(
        headers={},
        cookies={"orcheo_dev_session": "   "},
    )

    assert _try_dev_login_session(scope, settings) is None


def test_try_dev_login_session_returns_context_when_cookie_present() -> None:
    """_try_dev_login_session constructs a developer context when the cookie exists."""

    settings = _base_settings()

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"cookie", b"orcheo_dev_session=abc123")],
        "client": None,
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request"}

    request = Request(scope, receive)  # type: ignore[arg-type]

    result = _try_dev_login_session(request, settings)
    assert result is not None
    assert result.identity_type == "developer"
    assert result.subject == "abc123"


def test_try_dev_login_session_accepts_json_cookie_subject() -> None:
    """_try_dev_login_session extracts a subject from JSON dev-session values."""

    settings = _base_settings()
    scope = SimpleNamespace(
        headers={},
        cookies={"orcheo_dev_session": '{"subject": "  alice  "}'},
    )

    result = _try_dev_login_session(scope, settings)
    assert result is not None
    assert result.subject == "alice"


@pytest.mark.asyncio
async def test_attempt_bearer_auth_optional_ignores_invalid_token_when_optional() -> (
    None
):
    """_attempt_bearer_auth_optional returns None when optional auth fails."""

    class FakeAuthenticator:
        settings = SimpleNamespace(enforce=False)

        async def authenticate(self, token: str) -> None:
            raise AuthenticationError("boom", code="auth.invalid_token")

    result = await _attempt_bearer_auth_optional(
        FakeAuthenticator(),
        object(),
        "bad-token",
        ip=None,
        now=None,  # type: ignore[arg-type]
    )

    assert result is None
