"""Cover missing branches in authentication dependencies."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orcheo_backend.app.authentication.dependencies import (
    _parse_dev_identity,
    _try_dev_login_session,
)
from orcheo_backend.app.authentication.settings import AuthSettings


def _base_settings(**overrides: object) -> AuthSettings:
    base: dict[str, object] = dict(
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
        dev_login_scopes=(),
        dev_login_workspace_ids=(),
    )
    base.update(overrides)
    return AuthSettings(**base)  # type: ignore[arg-type]


def test_parse_dev_identity_non_string_subject_falls_back_to_raw() -> None:
    """When JSON subject is not a string, fall back to raw string split."""
    # {"subject": 42} — subject is an integer, not a string
    result = _parse_dev_identity('{"subject": 42}')
    # Falls back to line 197: candidate.split(":", 1)[0].strip() or None
    assert result is not None
    assert result.startswith("{")


def test_try_dev_login_session_skips_cookie_when_header_present() -> None:
    """When x-orcheo-dev-session header is set, cookie lookup is skipped (207->213)."""
    settings = _base_settings()
    # raw_value is NOT None because the header is present
    scope = SimpleNamespace(
        headers={"x-orcheo-dev-session": "alice"},
        cookies={},
    )

    result = _try_dev_login_session(scope, settings)

    assert result is not None
    assert result.subject == "alice"
    assert result.identity_type == "developer"
