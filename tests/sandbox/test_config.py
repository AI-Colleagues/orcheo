"""Tests for ``SandboxSettings`` env-var loading."""

from __future__ import annotations
import pytest
from orcheo.sandbox.config import SandboxSettings


def test_defaults_preserve_secure_runsc_choice() -> None:
    """The in-process default must keep the gVisor runtime so production is
    secure even if the operator forgets to set the env var."""
    assert SandboxSettings().container_runtime == "runsc"
    assert (
        SandboxSettings().credential_broker_url
        == "http://sandbox-runtime:9090/credentials/resolve"
    )


def test_from_mapping_overrides_each_documented_field() -> None:
    """All documented ORCHEO_* variables must influence the built settings."""
    source = {
        "ORCHEO_CONTAINER_RUNTIME": "runc",
        "ORCHEO_SANDBOX_IMAGE": "registry.local/orcheo/sandbox:test",
        "ORCHEO_SANDBOX_DEFAULT_CPU_LIMIT": "2.0",
        "ORCHEO_SANDBOX_DEFAULT_MEMORY_LIMIT": "1g",
        "ORCHEO_SANDBOX_DEFAULT_PID_LIMIT": "512",
        "ORCHEO_SANDBOX_DEFAULT_SCRATCH_DISK_LIMIT": "2g",
        "ORCHEO_SANDBOX_DEFAULT_IDLE_TTL_SECONDS": "120",
        "ORCHEO_SANDBOX_DEFAULT_POOL_MIN": "1",
        "ORCHEO_SANDBOX_DEFAULT_POOL_MAX": "8",
        "ORCHEO_EGRESS_PROXY_URL": "http://envoy:3128",
        "ORCHEO_CREDENTIAL_BROKER_URL": "http://backend.local/internal/creds",
        "ORCHEO_SANDBOX_AUDIT_LOGGER_NAME": "custom.audit",
    }

    settings = SandboxSettings.from_mapping(source)

    assert settings.container_runtime == "runc"
    assert settings.image == "registry.local/orcheo/sandbox:test"
    assert settings.default_cpu_limit == "2.0"
    assert settings.default_memory_limit == "1g"
    assert settings.default_pid_limit == 512
    assert settings.default_scratch_disk_limit == "2g"
    assert settings.default_idle_ttl_seconds == 120
    assert settings.default_pool_min == 1
    assert settings.default_pool_max == 8
    assert settings.egress_proxy_url == "http://envoy:3128"
    assert settings.credential_broker_url == "http://backend.local/internal/creds"
    assert settings.audit_logger_name == "custom.audit"


def test_from_mapping_ignores_blank_and_missing_values() -> None:
    """Empty / whitespace overrides fall back to the secure defaults."""
    source = {
        "ORCHEO_CONTAINER_RUNTIME": "   ",  # whitespace → ignored
        # ORCHEO_SANDBOX_IMAGE deliberately omitted
    }

    settings = SandboxSettings.from_mapping(source)

    assert settings.container_runtime == "runsc"
    assert settings.image.endswith("orcheo-workspace-sandbox:latest")


def test_from_env_reads_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """``from_env()`` is the production hook into ``os.environ``."""
    monkeypatch.setenv("ORCHEO_CONTAINER_RUNTIME", "runc")
    monkeypatch.setenv("ORCHEO_SANDBOX_DEFAULT_POOL_MAX", "12")

    settings = SandboxSettings.from_env()

    assert settings.container_runtime == "runc"
    assert settings.default_pool_max == 12
