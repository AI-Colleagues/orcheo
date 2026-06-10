"""OAuth configuration resolution."""

from __future__ import annotations
import os
import tomllib
from dataclasses import dataclass
from typing import Any
from orcheo_sdk.cli.config import (
    CONFIG_FILENAME,
    DEFAULT_PROFILE,
    PROFILE_ENV,
    get_config_dir,
    load_profiles,
)
from orcheo_sdk.cli.errors import CLIConfigurationError


# Environment variable names matching Studio conventions (without VITE_ prefix)
AUTH_ISSUER_ENV = "ORCHEO_AUTH_ISSUER"
AUTH_CLIENT_ID_ENV = "ORCHEO_AUTH_CLIENT_ID"
AUTH_SCOPES_ENV = "ORCHEO_AUTH_SCOPES"
AUTH_AUDIENCE_ENV = "ORCHEO_AUTH_AUDIENCE"
AUTH_ORGANIZATION_ENV = "ORCHEO_AUTH_ORGANIZATION"

AUTH_ISSUER_KEY = "auth_issuer"
AUTH_CLIENT_ID_KEY = "auth_client_id"
AUTH_SCOPES_KEY = "auth_scopes"
AUTH_AUDIENCE_KEY = "auth_audience"
AUTH_ORGANIZATION_KEY = "auth_organization"

DEFAULT_SCOPES = "openid profile email"


@dataclass(slots=True)
class OAuthConfig:
    """OAuth provider configuration."""

    issuer: str
    client_id: str
    scopes: str
    audience: str | None = None
    organization: str | None = None


def _load_profile_oauth_settings(profile: str | None) -> dict[str, Any]:
    """Load OAuth settings from the CLI config profile, if available."""
    profile_name = profile or os.getenv(PROFILE_ENV) or DEFAULT_PROFILE
    config_path = get_config_dir() / CONFIG_FILENAME
    try:
        profiles = load_profiles(config_path)
    except tomllib.TOMLDecodeError as exc:
        raise CLIConfigurationError(f"Invalid TOML in {config_path}.") from exc
    return profiles.get(profile_name, {})


def _coerce_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _coerce_scopes(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if not all(isinstance(scope, str) for scope in value):
            raise CLIConfigurationError(
                f"{AUTH_SCOPES_KEY} must be a string or list of strings."
            )
        return " ".join(value)
    raise CLIConfigurationError(
        f"{AUTH_SCOPES_KEY} must be a string or list of strings."
    )


def _resolve_oauth_required(
    profile_data: dict[str, Any],
) -> tuple[str | None, str | None, str | None]:
    """Resolve required OAuth settings from profile data and environment."""
    issuer = _coerce_str(profile_data.get(AUTH_ISSUER_KEY)) or os.getenv(
        AUTH_ISSUER_ENV
    )
    client_id = _coerce_str(profile_data.get(AUTH_CLIENT_ID_KEY)) or os.getenv(
        AUTH_CLIENT_ID_ENV
    )
    audience = _coerce_str(profile_data.get(AUTH_AUDIENCE_KEY)) or os.getenv(
        AUTH_AUDIENCE_ENV
    )
    return issuer, client_id, audience


def get_oauth_config(*, profile: str | None = None) -> OAuthConfig:
    """Load OAuth configuration from CLI config and environment variables.

    Raises:
        CLIConfigurationError: If required OAuth config is missing.
    """
    profile_data = _load_profile_oauth_settings(profile)

    issuer, client_id, audience = _resolve_oauth_required(profile_data)
    profile_scopes = _coerce_scopes(profile_data.get(AUTH_SCOPES_KEY))
    profile_organization = _coerce_str(profile_data.get(AUTH_ORGANIZATION_KEY))

    if not issuer or not client_id or not audience:
        raise CLIConfigurationError(
            f"OAuth not configured. Set '{AUTH_ISSUER_KEY}',"
            f" '{AUTH_CLIENT_ID_KEY}', and '{AUTH_AUDIENCE_KEY}' in your CLI"
            " profile or set environment variables"
            f" {AUTH_ISSUER_ENV}, {AUTH_CLIENT_ID_ENV}, and {AUTH_AUDIENCE_ENV}."
        )

    scopes = profile_scopes or os.getenv(AUTH_SCOPES_ENV) or DEFAULT_SCOPES

    return OAuthConfig(
        issuer=issuer.rstrip("/"),
        client_id=client_id,
        scopes=scopes,
        audience=audience,
        organization=profile_organization or os.getenv(AUTH_ORGANIZATION_ENV),
    )


def is_oauth_configured(*, profile: str | None = None) -> bool:
    """Check if OAuth config is set via CLI config or environment."""
    try:
        profile_data = _load_profile_oauth_settings(profile)
    except CLIConfigurationError:
        return False
    issuer, client_id, audience = _resolve_oauth_required(profile_data)
    return bool(issuer and client_id and audience)
