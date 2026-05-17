"""Shared fixtures for CLI and SDK tests."""

from __future__ import annotations
from pathlib import Path
import pytest
from typer.testing import CliRunner
from orcheo_sdk import OrcheoClient


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    config_dir = tmp_path / "config"
    cache_dir = tmp_path / "cache"
    plugin_dir = tmp_path / "plugins"
    stack_dir = tmp_path / "stack"
    config_dir.mkdir()
    cache_dir.mkdir()
    plugin_dir.mkdir()
    stack_dir.mkdir()
    return {
        "ORCHEO_API_URL": "http://api.test",
        "ORCHEO_SERVICE_TOKEN": "token",
        "ORCHEO_CONFIG_DIR": str(config_dir),
        "ORCHEO_CACHE_DIR": str(cache_dir),
        "ORCHEO_PLUGIN_DIR": str(plugin_dir),
        "ORCHEO_STACK_DIR": str(stack_dir),
        "ORCHEO_CHATKIT_PUBLIC_BASE_URL": "",
        "ORCHEO_AUTH_ISSUER": "",
        "ORCHEO_AUTH_CLIENT_ID": "",
        "ORCHEO_AUTH_SCOPES": "",
        "ORCHEO_AUTH_AUDIENCE": "",
        "ORCHEO_AUTH_ORGANIZATION": "",
        "ORCHEO_HUMAN": "1",
        "NO_COLOR": "1",
    }


@pytest.fixture()
def machine_env(tmp_path: Path) -> dict[str, str]:
    """Environment with ORCHEO_HUMAN unset — triggers machine (JSON) output."""
    config_dir = tmp_path / "config"
    cache_dir = tmp_path / "cache"
    plugin_dir = tmp_path / "plugins"
    stack_dir = tmp_path / "stack"
    config_dir.mkdir()
    cache_dir.mkdir()
    plugin_dir.mkdir()
    stack_dir.mkdir()
    return {
        "ORCHEO_API_URL": "http://api.test",
        "ORCHEO_SERVICE_TOKEN": "token",
        "ORCHEO_CONFIG_DIR": str(config_dir),
        "ORCHEO_CACHE_DIR": str(cache_dir),
        "ORCHEO_PLUGIN_DIR": str(plugin_dir),
        "ORCHEO_STACK_DIR": str(stack_dir),
        "ORCHEO_CHATKIT_PUBLIC_BASE_URL": "",
        "ORCHEO_AUTH_ISSUER": "",
        "ORCHEO_AUTH_CLIENT_ID": "",
        "ORCHEO_AUTH_SCOPES": "",
        "ORCHEO_AUTH_AUDIENCE": "",
        "ORCHEO_AUTH_ORGANIZATION": "",
        "ORCHEO_HUMAN": "",
        "NO_COLOR": "1",
    }


@pytest.fixture()
def mock_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Set up mock environment variables for SDK tests."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ORCHEO_API_URL", "http://api.test")
    monkeypatch.setenv("ORCHEO_SERVICE_TOKEN", "test-token")
    monkeypatch.setenv("ORCHEO_HUMAN", "1")
    monkeypatch.delenv("ORCHEO_CHATKIT_PUBLIC_BASE_URL", raising=False)


@pytest.fixture()
def client() -> OrcheoClient:
    """Provide a baseline SDK client with default headers."""
    return OrcheoClient(
        base_url="http://localhost:2025",
        default_headers={"X-Test": "1"},
    )
