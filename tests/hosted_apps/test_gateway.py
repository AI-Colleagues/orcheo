"""Tests for gateway host and static-path isolation helpers."""

from __future__ import annotations

import pytest
from orcheo.hosted_apps import (
    AliasValidationError,
    canonical_app_host,
    derive_client_ip,
    is_safe_app_path,
)


def test_gateway_accepts_one_exact_alias_label() -> None:
    """Only a configured wildcard child may resolve an app descriptor."""
    assert canonical_app_host("portal.beta.orcheo.cloud", "beta.orcheo.cloud") == (
        "portal.beta.orcheo.cloud",
        "portal",
    )


def test_gateway_accepts_local_host_port() -> None:
    """Local Host headers may include the gateway development port."""
    assert canonical_app_host("portal.apps.localhost:2030", "apps.localhost") == (
        "portal.apps.localhost",
        "portal",
    )


def test_forwarded_ip_requires_a_configured_trusted_proxy() -> None:
    """Browser-supplied forwarding headers are ignored at untrusted peers."""
    assert (
        derive_client_ip(
            "10.0.0.2",
            "198.51.100.9, 10.0.0.1",
            trusted_proxy_cidrs=("10.0.0.0/8",),
            trusted_hops=2,
        )
        == "198.51.100.9"
    )
    assert (
        derive_client_ip(
            "203.0.113.5",
            "198.51.100.9",
            trusted_proxy_cidrs=("10.0.0.0/8",),
            trusted_hops=1,
        )
        == "203.0.113.5"
    )


@pytest.mark.parametrize(
    "host", ["beta.orcheo.cloud", "a.b.beta.orcheo.cloud", "portal.example.com"]
)
def test_gateway_rejects_non_wildcard_or_ambiguous_hosts(host: str) -> None:
    """Host resolution cannot be tricked into selecting another tenant."""
    with pytest.raises(AliasValidationError):
        canonical_app_host(host, "beta.orcheo.cloud")


@pytest.mark.parametrize("path", ["/index.html", "/assets/main.js", "/nested/view"])
def test_safe_static_paths(path: str) -> None:
    """Normal static and SPA paths are eligible for manifest-only resolution."""
    assert is_safe_app_path(path)


@pytest.mark.parametrize(
    "path", ["/__orcheo/config", "/../secret", "/assets%2f..%2fsecret"]
)
def test_runtime_and_ambiguous_paths_are_not_static_assets(path: str) -> None:
    """Publisher bundles cannot shadow runtime APIs or traversal spellings."""
    assert not is_safe_app_path(path)
