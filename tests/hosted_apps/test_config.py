"""Tests for safely disabled Hosted Apps configuration."""

from __future__ import annotations

import pytest
from orcheo.hosted_apps.config import HostedAppsSettings, HostedAppsSettingsError


def test_hosted_apps_default_to_disabled_without_infrastructure() -> None:
    """No accidental traffic exposure occurs when configuration is absent."""
    settings = HostedAppsSettings.from_environment({})
    assert settings.enabled is False
    assert settings.base_domain is None


@pytest.mark.parametrize(
    "environment",
    [
        {"ORCHEO_HOSTED_APPS_ENABLED": "true"},
        {
            "ORCHEO_HOSTED_APPS_ENABLED": "true",
            "ORCHEO_APPS_BASE_DOMAIN": "beta.orcheo.cloud",
        },
        {
            "ORCHEO_HOSTED_APPS_ENABLED": "true",
            "ORCHEO_APPS_BASE_DOMAIN": "https://beta.orcheo.cloud",
            "ORCHEO_APP_BUNDLE_BACKEND": "s3",
        },
    ],
)
def test_enabled_feature_rejects_incomplete_or_unsafe_config(
    environment: dict[str, str],
) -> None:
    """Public hosting requires explicit valid domain and storage configuration."""
    with pytest.raises(HostedAppsSettingsError):
        HostedAppsSettings.from_environment(environment)


def test_filesystem_backend_requires_explicit_private_root() -> None:
    """Local storage cannot silently default to an arbitrary process directory."""
    with pytest.raises(HostedAppsSettingsError, match="FILESYSTEM_ROOT"):
        HostedAppsSettings.from_environment(
            {
                "ORCHEO_HOSTED_APPS_ENABLED": "true",
                "ORCHEO_APPS_BASE_DOMAIN": "apps.localhost",
                "ORCHEO_APP_BUNDLE_BACKEND": "filesystem",
            }
        )


def test_filesystem_backend_is_not_a_multi_node_production_fallback() -> None:
    """Multi-node environments must select S3-compatible immutable storage."""
    with pytest.raises(HostedAppsSettingsError, match="local or single-node"):
        HostedAppsSettings.from_environment(
            {
                "ORCHEO_HOSTED_APPS_ENABLED": "true",
                "ORCHEO_APPS_BASE_DOMAIN": "apps.example.test",
                "ORCHEO_APP_BUNDLE_BACKEND": "filesystem",
                "ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT": "/bundles",
                "ORCHEO_DEPLOYMENT_MODE": "hosted",
            }
        )


def test_complete_local_config_is_accepted() -> None:
    """The local contract supports a deliberate filesystem-only development setup."""
    settings = HostedAppsSettings.from_environment(
        {
            "ORCHEO_HOSTED_APPS_ENABLED": "true",
            "ORCHEO_APPS_BASE_DOMAIN": "apps.localhost",
            "ORCHEO_APP_BUNDLE_BACKEND": "filesystem",
            "ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT": "/tmp/orcheo-app-bundles",
        }
    )
    assert settings.enabled is True
    assert settings.filesystem_root is not None


def test_complete_postgres_config_is_accepted_without_filesystem_root() -> None:
    """PostgreSQL stores package bytes without a machine-local path."""
    settings = HostedAppsSettings.from_environment(
        {
            "ORCHEO_HOSTED_APPS_ENABLED": "true",
            "ORCHEO_APPS_BASE_DOMAIN": "apps.example.test",
            "ORCHEO_APP_BUNDLE_BACKEND": "postgres",
            "ORCHEO_DEPLOYMENT_MODE": "single-node",
        }
    )

    assert settings.enabled is True
    assert settings.bundle_backend == "postgres"
    assert settings.filesystem_root is None
