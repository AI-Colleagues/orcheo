"""Authentication settings and caching tests."""

from __future__ import annotations
import pytest
from orcheo_backend.app.authentication import (
    get_authenticator,
    load_auth_settings,
    reset_authentication_state,
)
from orcheo_backend.app.authentication.settings import _parse_bool
from tests.backend.authentication_test_utils import reset_auth_state


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


def test_load_auth_settings_with_defaults() -> None:
    """load_auth_settings applies default values."""

    settings = load_auth_settings(refresh=True)

    assert settings.mode == "optional"
    assert settings.jwks_cache_ttl == 300
    assert settings.jwks_timeout == 5.0
    assert "RS256" in settings.allowed_algorithms
    assert "HS256" in settings.allowed_algorithms


def test_load_auth_settings_with_custom_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_auth_settings reads custom environment variables."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", "my-secret")
    monkeypatch.setenv("ORCHEO_AUTH_JWKS_CACHE_TTL", "600")
    monkeypatch.setenv("ORCHEO_AUTH_JWKS_TIMEOUT", "10.0")
    monkeypatch.setenv("ORCHEO_AUTH_ALLOWED_ALGORITHMS", "RS256,HS256")
    monkeypatch.setenv("ORCHEO_AUTH_AUDIENCE", "api1,api2")
    monkeypatch.setenv("ORCHEO_AUTH_ISSUER", "https://auth.example.com")

    settings = load_auth_settings(refresh=True)

    assert settings.mode == "required"
    assert settings.jwt_secret == "my-secret"
    assert settings.jwks_cache_ttl == 600
    assert settings.jwks_timeout == 10.0
    assert "RS256" in settings.allowed_algorithms
    assert "api1" in settings.audiences
    assert settings.issuer == "https://auth.example.com"


def test_load_auth_settings_strips_trailing_slash_from_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issuer URLs are normalized so Auth0-style trailing slashes still work."""

    monkeypatch.setenv("ORCHEO_AUTH_ISSUER", "https://auth.example.com/")

    settings = load_auth_settings(refresh=True)

    assert settings.issuer == "https://auth.example.com"


def test_get_authenticator_caching() -> None:
    """get_authenticator caches the instance."""

    auth1 = get_authenticator()
    auth2 = get_authenticator()

    assert auth1 is auth2


def test_get_authenticator_refresh() -> None:
    """get_authenticator refreshes when requested."""

    auth1 = get_authenticator()
    auth2 = get_authenticator(refresh=True)

    # Should create new instance
    assert auth1 is not auth2


def test_load_auth_settings_with_jwks_static(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_auth_settings parses JWKS_STATIC configuration."""
    import json

    jwks = {"keys": [{"kty": "RSA", "kid": "test-key"}]}
    monkeypatch.setenv("ORCHEO_AUTH_JWKS_STATIC", json.dumps(jwks))

    settings = load_auth_settings(refresh=True)

    assert len(settings.jwks_static) == 1
    assert settings.jwks_static[0]["kid"] == "test-key"


def test_reset_authentication_state_clears_caches() -> None:
    """reset_authentication_state clears all cached authenticators."""

    # Get authenticator to populate cache
    get_authenticator()

    reset_authentication_state()

    # Cache should be cleared
    from orcheo_backend.app.authentication import (
        _auth_rate_limiter_cache,
        _authenticator_cache,
    )

    assert _authenticator_cache.get("authenticator") is None
    assert _auth_rate_limiter_cache.get("limiter") is None


def test_load_auth_settings_with_jwks_alternative_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_auth_settings accepts AUTH_JWKS as alternative to AUTH_JWKS_STATIC."""
    import json

    jwks = {"keys": [{"kty": "RSA", "kid": "alt-key"}]}
    monkeypatch.setenv("ORCHEO_AUTH_JWKS", json.dumps(jwks))

    settings = load_auth_settings(refresh=True)

    assert len(settings.jwks_static) == 1


def test_load_auth_settings_with_repository_path_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test default service token DB path using repository path (lines 998-1001)."""
    import tempfile
    from pathlib import Path

    # Create a temp directory and file
    temp_dir = Path(tempfile.mkdtemp())
    repo_path = temp_dir / "workflows.sqlite"
    repo_path.touch()

    # Set up repository path without service token DB path
    # Note: Code at line 996 uses settings.get("ORCHEO_REPOSITORY_SQLITE_PATH")
    # which doesn't follow dynaconf conventions, but we test it as written
    monkeypatch.setenv("ORCHEO_ORCHEO_REPOSITORY_SQLITE_PATH", str(repo_path))
    monkeypatch.delenv("ORCHEO_AUTH_SERVICE_TOKEN_DB_PATH", raising=False)

    settings = load_auth_settings(refresh=True)

    # Should default to service_tokens.sqlite in same directory as workflows DB
    assert settings.service_token_db_path is not None
    assert settings.service_token_db_path.endswith("service_tokens.sqlite")
    assert str(temp_dir) in settings.service_token_db_path


def test_parse_bool_unrecognized_string_uses_default() -> None:
    """Unrecognized string input should return the provided default value."""
    assert _parse_bool("not-a-bool", True) is True
    assert _parse_bool("not-a-bool", False) is False
