"""Hosted Apps stack preflight tests."""

from pathlib import Path

import pytest

from orcheo.hosted_apps.setup_validation import validate_hosted_apps_setup


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "ORCHEO_HOSTED_APPS_ENABLED": "true",
        "ORCHEO_APPS_BASE_DOMAIN": "apps.example.test",
        "ORCHEO_APP_BUNDLE_BACKEND": "filesystem",
        "ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT": str(tmp_path),
        "ORCHEO_DEPLOYMENT_MODE": "single-node",
        "ORCHEO_APP_GATEWAY_SECRET": "x" * 32,
        "ORCHEO_POSTGRES_DSN": "postgresql://database/orcheo",
        "ORCHEO_HOSTED_APPS_VALIDATION_QUEUE": "hosted-app-validation",
        "ORCHEO_APP_TLS_METHOD": "local",
    }


def test_preflight_accepts_complete_single_node_setup(tmp_path: Path) -> None:
    facts = validate_hosted_apps_setup(_environment(tmp_path), check_dns=False)
    assert "bundle_backend=filesystem" in facts


def test_preflight_accepts_postgres_bundle_storage(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    environment["ORCHEO_APP_BUNDLE_BACKEND"] = "postgres"
    environment.pop("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT")

    facts = validate_hosted_apps_setup(environment, check_dns=False)

    assert "bundle_backend=postgres" in facts


def test_preflight_requires_dedicated_identity_and_consistent_proxy(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    environment["ORCHEO_APP_GATEWAY_SECRET"] = "short"
    with pytest.raises(ValueError, match="at least 32"):
        validate_hosted_apps_setup(environment, check_dns=False)
    environment["ORCHEO_APP_GATEWAY_SECRET"] = "x" * 32
    environment["ORCHEO_APP_TRUSTED_PROXY_CIDRS"] = "10.0.0.0/8"
    with pytest.raises(ValueError, match="set together"):
        validate_hosted_apps_setup(environment, check_dns=False)


def test_preflight_requires_complete_s3_configuration(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    environment["ORCHEO_APP_BUNDLE_BACKEND"] = "s3"
    with pytest.raises(ValueError, match="S3 bundle storage is incomplete"):
        validate_hosted_apps_setup(environment, check_dns=False)
