"""Configuration helper tests for the CLI."""

from __future__ import annotations
from pathlib import Path
import pytest
from orcheo_sdk.cli.config import (
    API_URL_ENV,
    CACHE_DIR_ENV,
    STUDIO_URL_ENV,
    CONFIG_DIR_ENV,
    SERVICE_TOKEN_ENV,
    get_cache_dir,
    get_config_dir,
    load_profiles,
    resolve_settings,
)


def test_get_config_dir_default() -> None:
    import os

    original = os.environ.get(CONFIG_DIR_ENV)
    try:
        os.environ.pop(CONFIG_DIR_ENV, None)
        config_dir = get_config_dir()
        assert ".config/orcheo" in str(config_dir)
    finally:
        if original:
            os.environ[CONFIG_DIR_ENV] = original


def test_get_config_dir_override(tmp_path: Path) -> None:
    import os

    custom_dir = tmp_path / "custom_config"
    original = os.environ.get(CONFIG_DIR_ENV)
    try:
        os.environ[CONFIG_DIR_ENV] = str(custom_dir)
        config_dir = get_config_dir()
        assert config_dir == custom_dir
    finally:
        if original:
            os.environ[CONFIG_DIR_ENV] = original
        else:
            os.environ.pop(CONFIG_DIR_ENV, None)


def test_get_cache_dir_default() -> None:
    import os

    original = os.environ.get(CACHE_DIR_ENV)
    try:
        os.environ.pop(CACHE_DIR_ENV, None)
        cache_dir = get_cache_dir()
        assert ".cache/orcheo" in str(cache_dir)
    finally:
        if original:
            os.environ[CACHE_DIR_ENV] = original


def test_get_cache_dir_override(tmp_path: Path) -> None:
    import os

    custom_dir = tmp_path / "custom_cache"
    original = os.environ.get(CACHE_DIR_ENV)
    try:
        os.environ[CACHE_DIR_ENV] = str(custom_dir)
        cache_dir = get_cache_dir()
        assert cache_dir == custom_dir
    finally:
        if original:
            os.environ[CACHE_DIR_ENV] = original
        else:
            os.environ.pop(CACHE_DIR_ENV, None)


def test_load_profiles_nonexistent(tmp_path: Path) -> None:
    config_path = tmp_path / "nonexistent.toml"
    profiles = load_profiles(config_path)
    assert profiles == {}


def test_load_profiles_success(tmp_path: Path) -> None:
    config_path = tmp_path / "cli.toml"
    config_path.write_text(
        """
[profiles.dev]
api_url = "http://dev.test"
service_token = "dev-token"

[profiles.prod]
api_url = "http://prod.test"
""",
        encoding="utf-8",
    )
    profiles = load_profiles(config_path)
    assert "dev" in profiles
    assert profiles["dev"]["api_url"] == "http://dev.test"
    assert "prod" in profiles


def test_resolve_settings_from_args(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv(CONFIG_DIR_ENV, str(config_dir))
    monkeypatch.delenv(STUDIO_URL_ENV, raising=False)
    settings = resolve_settings(
        profile=None,
        api_url="http://test.com",
        service_token="token123",
        offline=False,
    )
    assert settings.api_url == "http://test.com"
    assert settings.service_token == "token123"
    assert not settings.offline
    assert settings.studio_url is None


def test_resolve_settings_from_env(tmp_path: Path) -> None:
    import os

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    original_config = os.environ.get(CONFIG_DIR_ENV)
    original_url = os.environ.get(API_URL_ENV)
    original_token = os.environ.get(SERVICE_TOKEN_ENV)
    original_chatkit = os.environ.get(STUDIO_URL_ENV)
    try:
        os.environ[CONFIG_DIR_ENV] = str(config_dir)
        os.environ[API_URL_ENV] = "http://env.test"
        os.environ[SERVICE_TOKEN_ENV] = "env-token"
        os.environ.pop(STUDIO_URL_ENV, None)
        settings = resolve_settings(
            profile=None,
            api_url=None,
            service_token=None,
            offline=False,
        )
        assert settings.api_url == "http://env.test"
        assert settings.service_token == "env-token"
        assert settings.studio_url is None
    finally:
        if original_config:
            os.environ[CONFIG_DIR_ENV] = original_config
        else:
            os.environ.pop(CONFIG_DIR_ENV, None)
        if original_url:
            os.environ[API_URL_ENV] = original_url
        else:
            os.environ.pop(API_URL_ENV, None)
        if original_token:
            os.environ[SERVICE_TOKEN_ENV] = original_token
        else:
            os.environ.pop(SERVICE_TOKEN_ENV, None)
        if original_chatkit:
            os.environ[STUDIO_URL_ENV] = original_chatkit
        else:
            os.environ.pop(STUDIO_URL_ENV, None)


def test_resolve_settings_missing_api_url(tmp_path: Path) -> None:
    import os

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    original = os.environ.get(CONFIG_DIR_ENV)
    try:
        os.environ[CONFIG_DIR_ENV] = str(config_dir)
        os.environ.pop(API_URL_ENV, None)
        settings = resolve_settings(
            profile=None,
            api_url=None,
            service_token=None,
            offline=False,
        )
        # Should use default localhost:2025
        assert settings.api_url == "http://localhost:2025"
    finally:
        if original:
            os.environ[CONFIG_DIR_ENV] = original
        else:
            os.environ.pop(CONFIG_DIR_ENV, None)


def test_resolve_settings_uses_studio_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(STUDIO_URL_ENV, raising=False)
    monkeypatch.setenv(STUDIO_URL_ENV, "https://studio.example")
    settings = resolve_settings(
        profile=None,
        api_url="http://localhost:2025/api",
        service_token=None,
        offline=False,
    )
    assert settings.studio_url == "https://studio.example"
