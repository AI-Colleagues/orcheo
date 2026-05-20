"""Tests for the multi-workspace configuration settings."""

from __future__ import annotations
import pytest
from orcheo.config import MultiWorkspaceSettings, get_settings


def test_defaults() -> None:
    """Defaults provide the slug and header used by multi-workspace requests."""
    settings = MultiWorkspaceSettings()
    assert settings.workspace_header == "X-Orcheo-Workspace"


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
    monkeypatch.setenv("ORCHEO_MULTI_WORKSPACE_WORKSPACE_HEADER", "X-Test-Workspace")
    settings = get_settings(refresh=True)
    assert settings.get("MULTI_WORKSPACE_WORKSPACE_HEADER") == "X-Test-Workspace"
    monkeypatch.delenv("ORCHEO_MULTI_WORKSPACE_WORKSPACE_HEADER")
    get_settings(refresh=True)
