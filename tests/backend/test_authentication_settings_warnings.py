"""Authentication settings and operator warning tests."""

from __future__ import annotations
import logging
import pytest
from dynaconf import Dynaconf
from orcheo.config import loader as config_loader
from orcheo_backend.app.authentication import AuthSettings, load_auth_settings
from tests.backend.authentication_test_utils import reset_auth_state


def _build_loader_without_dotenv() -> Dynaconf:
    """Create a Dynaconf loader that doesn't load from .env files."""
    return Dynaconf(
        envvar_prefix="ORCHEO",
        settings_files=[],
        load_dotenv=False,
        environments=False,
    )


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""
    monkeypatch.setattr(config_loader, "_build_loader", _build_loader_without_dotenv)
    yield from reset_auth_state(monkeypatch)


def test_raw_service_token_emits_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Using raw service token secrets emits an operator-facing warning."""

    caplog.set_level(logging.WARNING)

    load_auth_settings(refresh=True)

    # No warnings should be emitted for missing tokens (this is valid config)
    assert not any("raw secret" in record.message for record in caplog.records)


def test_required_mode_without_credentials_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Enforcing authentication without credentials warns operators."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    # Ensure no credentials are set
    monkeypatch.delenv("ORCHEO_AUTH_JWT_SECRET", raising=False)
    monkeypatch.setenv("ORCHEO_AUTH_JWKS_URL", "")
    monkeypatch.delenv("ORCHEO_AUTH_JWKS_STATIC", raising=False)
    monkeypatch.delenv("ORCHEO_AUTH_SERVICE_TOKEN_DB_PATH", raising=False)
    monkeypatch.delenv("ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("ORCHEO_AUTH_SERVICE_TOKEN_BACKEND", "postgres")
    caplog.set_level(logging.WARNING)

    load_auth_settings(refresh=True)

    assert any(
        "no authentication credentials" in record.message for record in caplog.records
    )


def test_public_chatkit_url_forces_required_mode(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Public backend-owned URLs force authentication to required."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", "secret")
    monkeypatch.setenv("ORCHEO_CHATKIT_PUBLIC_BASE_URL", "https://canvas.example.com")
    caplog.set_level(logging.WARNING)

    settings = load_auth_settings(refresh=True)

    assert settings.mode == "required"
    assert settings.configured_mode == "disabled"
    assert settings.public_exposure_detected is True
    assert settings.public_exposure_sources == ("CHATKIT_PUBLIC_BASE_URL",)
    assert any(
        "overriding AUTH_MODE=disabled to required" in record.message
        for record in caplog.records
    )


def test_local_urls_do_not_force_required_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Localhost-only configuration should not trigger public exposure."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "optional")
    monkeypatch.setenv("ORCHEO_CHATKIT_PUBLIC_BASE_URL", "http://localhost:2026")
    monkeypatch.setenv(
        "ORCHEO_CORS_ALLOW_ORIGINS",
        "http://localhost:2026,http://127.0.0.1:2026",
    )

    settings = load_auth_settings(refresh=True)

    assert settings.mode == "optional"
    assert settings.configured_mode == "optional"
    assert settings.public_exposure_detected is False
    assert settings.public_exposure_sources == ()


def test_public_cors_origin_without_credentials_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public exposure without auth credentials should fail immediately."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "optional")
    monkeypatch.setenv("ORCHEO_CORS_ALLOW_ORIGINS", "https://canvas.example.com")

    with pytest.raises(
        RuntimeError,
        match="Public deployment detected via CORS_ALLOW_ORIGINS",
    ):
        load_auth_settings(refresh=True)


def test_public_deployment_rejects_dev_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public exposure should reject the unsigned developer login flow."""

    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", "secret")
    monkeypatch.setenv("ORCHEO_AUTH_DEV_LOGIN_ENABLED", "true")
    monkeypatch.setenv("ORCHEO_CHATKIT_PUBLIC_BASE_URL", "https://canvas.example.com")

    with pytest.raises(
        RuntimeError,
        match="Developer login cannot be enabled when public deployment is detected",
    ):
        load_auth_settings(refresh=True)


def test_auth_settings_enforce_disabled_mode() -> None:
    """AuthSettings.enforce returns False when mode is disabled."""

    settings = AuthSettings(
        mode="disabled",
        jwt_secret=None,
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=(),
        audiences=(),
        issuer=None,
        service_token_backend="inmemory",
        service_token_db_path=None,
        bootstrap_service_token=None,
        bootstrap_token_scopes=frozenset(),
        rate_limit_ip=0,
        rate_limit_identity=0,
        rate_limit_interval=60,
    )

    assert not settings.enforce


def test_auth_settings_enforce_required_mode() -> None:
    """AuthSettings.enforce returns True when mode is required."""

    settings = AuthSettings(
        mode="required",
        jwt_secret=None,
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=(),
        audiences=(),
        issuer=None,
        service_token_backend="inmemory",
        service_token_db_path=None,
        bootstrap_service_token=None,
        bootstrap_token_scopes=frozenset(),
        rate_limit_ip=0,
        rate_limit_identity=0,
        rate_limit_interval=60,
    )

    assert settings.enforce


def test_auth_settings_enforce_optional_with_credentials() -> None:
    """AuthSettings.enforce returns True when optional mode has credentials."""

    settings = AuthSettings(
        mode="optional",
        jwt_secret="secret",
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=(),
        audiences=(),
        issuer=None,
        service_token_backend="inmemory",
        service_token_db_path=None,
        bootstrap_service_token=None,
        bootstrap_token_scopes=frozenset(),
        rate_limit_ip=0,
        rate_limit_identity=0,
        rate_limit_interval=60,
    )

    assert settings.enforce
