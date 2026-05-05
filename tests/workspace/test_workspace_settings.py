"""Tests for the multi-workspace configuration settings."""

from __future__ import annotations
import pytest
from orcheo.config import MultiWorkspaceSettings, get_settings


def test_defaults_disabled_with_default_slug() -> None:
    settings = MultiWorkspaceSettings()
    assert settings.enabled is False
    assert settings.default_workspace_slug == "default"
    assert settings.workspace_header == "X-Orcheo-Workspace"


def test_string_truthy_values_coerce_to_bool() -> None:
    assert MultiWorkspaceSettings(enabled="true").enabled is True
    assert MultiWorkspaceSettings(enabled="0").enabled is False
    assert MultiWorkspaceSettings(enabled=None).enabled is False


def test_slug_is_normalized() -> None:
    s = MultiWorkspaceSettings(default_workspace_slug="Acme")
    assert s.default_workspace_slug == "acme"


def test_invalid_slug_rejected() -> None:
    with pytest.raises(ValueError):
        MultiWorkspaceSettings(default_workspace_slug="Bad Slug!")


def test_loader_picks_up_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_MULTI_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("ORCHEO_MULTI_WORKSPACE_DEFAULT_WORKSPACE_SLUG", "shared")
    settings = get_settings(refresh=True)
    assert bool(settings.get("MULTI_WORKSPACE_ENABLED")) is True
    assert settings.get("MULTI_WORKSPACE_DEFAULT_WORKSPACE_SLUG") == "shared"
    monkeypatch.delenv("ORCHEO_MULTI_WORKSPACE_ENABLED")
    monkeypatch.delenv("ORCHEO_MULTI_WORKSPACE_DEFAULT_WORKSPACE_SLUG")
    get_settings(refresh=True)
