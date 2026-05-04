"""Tests for the multi-tenancy configuration settings."""

from __future__ import annotations
import pytest
from orcheo.config import MultiTenancySettings, get_settings


def test_defaults_disabled_with_default_slug() -> None:
    settings = MultiTenancySettings()
    assert settings.enabled is False
    assert settings.default_tenant_slug == "default"
    assert settings.tenant_header == "X-Orcheo-Tenant"


def test_string_truthy_values_coerce_to_bool() -> None:
    assert MultiTenancySettings(enabled="true").enabled is True
    assert MultiTenancySettings(enabled="0").enabled is False
    assert MultiTenancySettings(enabled=None).enabled is False


def test_slug_is_normalized() -> None:
    s = MultiTenancySettings(default_tenant_slug="Acme")
    assert s.default_tenant_slug == "acme"


def test_invalid_slug_rejected() -> None:
    with pytest.raises(ValueError):
        MultiTenancySettings(default_tenant_slug="Bad Slug!")


def test_loader_picks_up_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_MULTI_TENANCY_ENABLED", "true")
    monkeypatch.setenv("ORCHEO_MULTI_TENANCY_DEFAULT_TENANT_SLUG", "shared")
    settings = get_settings(refresh=True)
    assert bool(settings.get("MULTI_TENANCY_ENABLED")) is True
    assert settings.get("MULTI_TENANCY_DEFAULT_TENANT_SLUG") == "shared"
    monkeypatch.delenv("ORCHEO_MULTI_TENANCY_ENABLED")
    monkeypatch.delenv("ORCHEO_MULTI_TENANCY_DEFAULT_TENANT_SLUG")
    get_settings(refresh=True)
