"""Configuration helpers for the Orcheo CLI."""

from __future__ import annotations
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_DIR_ENV = "ORCHEO_CONFIG_DIR"
CACHE_DIR_ENV = "ORCHEO_CACHE_DIR"
PROFILE_ENV = "ORCHEO_PROFILE"
API_URL_ENV = "ORCHEO_API_URL"
CHATKIT_PUBLIC_BASE_URL_ENV = "ORCHEO_CHATKIT_PUBLIC_BASE_URL"
SERVICE_TOKEN_ENV = "ORCHEO_SERVICE_TOKEN"
CONFIG_FILENAME = "cli.toml"
DEFAULT_PROFILE = "default"


@dataclass(slots=True)
class CLISettings:
    """Resolved configuration for the CLI session."""

    api_url: str
    service_token: str | None
    profile: str | None
    offline: bool = False
    chatkit_public_base_url: str | None = None


def get_config_dir() -> Path:
    """Return the configuration directory used by the CLI."""
    override = os.getenv(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "orcheo"


def get_cache_dir() -> Path:
    """Return the cache directory used by the CLI."""
    override = os.getenv(CACHE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "orcheo"


def load_profiles(config_path: Path) -> dict[str, Any]:
    """Load CLI profiles from ``config_path`` if the file exists."""
    if not config_path.exists():
        return {}
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    profiles = data.get("profiles", {})
    return {
        str(name): value for name, value in profiles.items() if isinstance(value, dict)
    }


def resolve_settings(
    *,
    profile: str | None,
    api_url: str | None,
    service_token: str | None,
    offline: bool,
) -> CLISettings:
    """Resolve CLI settings from explicit values, env vars, and config profiles."""
    env_profile = os.getenv(PROFILE_ENV)
    profile_name = profile or env_profile or DEFAULT_PROFILE

    config_dir = get_config_dir()
    config_path = config_dir / CONFIG_FILENAME
    profiles = load_profiles(config_path)
    profile_data = profiles.get(profile_name, {})

    resolved_api_url = (
        api_url
        or os.getenv(API_URL_ENV)
        or profile_data.get("api_url")
        or "http://localhost:2025"
    )
    resolved_token = (
        service_token
        or os.getenv(SERVICE_TOKEN_ENV)
        or profile_data.get("service_token")
    )
    resolved_public_base_url = os.getenv(
        CHATKIT_PUBLIC_BASE_URL_ENV
    ) or profile_data.get("chatkit_public_base_url")

    return CLISettings(
        api_url=resolved_api_url,
        service_token=resolved_token,
        profile=profile_name,
        offline=offline,
        chatkit_public_base_url=resolved_public_base_url,
    )
