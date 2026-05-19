"""Tests for the multi-workspace configuration settings."""

from __future__ import annotations
import pytest
from orcheo.config import MultiWorkspaceSettings, get_settings


def test_defaults() -> None:
    """Defaults provide the slug and header used by multi-workspace requests."""
    settings = MultiWorkspaceSettings()
    assert settings.default_workspace_slug == "default"
    assert settings.workspace_header == "X-Orcheo-Workspace"


def test_slug_is_normalized() -> None:
    s = MultiWorkspaceSettings(default_workspace_slug="Acme")
    assert s.default_workspace_slug == "acme"


def test_empty_slug_uses_default() -> None:
    assert MultiWorkspaceSettings(default_workspace_slug="").default_workspace_slug == (
        "default"
    )


def test_invalid_slug_rejected() -> None:
    with pytest.raises(ValueError):
        MultiWorkspaceSettings(default_workspace_slug="Bad Slug!")


def test_empty_header_uses_default() -> None:
    assert MultiWorkspaceSettings(workspace_header="").workspace_header == (
        "X-Orcheo-Workspace"
    )


def test_blank_header_is_rejected() -> None:
    with pytest.raises(ValueError, match="Workspace header must not be empty"):
        MultiWorkspaceSettings(workspace_header="   ")


def test_loader_picks_up_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_MULTI_WORKSPACE_DEFAULT_WORKSPACE_SLUG", "shared")
    settings = get_settings(refresh=True)
    assert settings.get("MULTI_WORKSPACE_DEFAULT_WORKSPACE_SLUG") == "shared"
    monkeypatch.delenv("ORCHEO_MULTI_WORKSPACE_DEFAULT_WORKSPACE_SLUG")
    get_settings(refresh=True)
