"""Configuration for the Sandbox Runtime Manager.

These settings drive the choice of container runtime, default per-workspace
pool sizing, and the network targets that must be denied at L3/L4 regardless
of tenant configuration. Sandboxing is always on; there is no master feature
flag.
"""

from __future__ import annotations
import os
from collections.abc import Mapping
from typing import Any, ClassVar
from urllib.parse import urlparse
from pydantic import BaseModel, Field, field_validator, model_validator


# CIDRs and hostnames that must never be reachable from inside a sandbox.
# `169.254.0.0/16` covers the cloud metadata endpoint on AWS/GCP/Azure.
DEFAULT_DENY_CIDRS: tuple[str, ...] = (
    "169.254.0.0/16",
    "fd00:ec2::/32",
)

DEFAULT_DENY_HOSTNAMES: tuple[str, ...] = (
    "redis",
    "postgres",
    "backend",
)


class SandboxSettings(BaseModel):
    """Validated sandbox-runtime configuration."""

    container_runtime: str = Field(
        default="runsc",
        description=(
            "Docker runtime name to spawn sandboxes under. ``runsc`` is gVisor; "
            "``runc`` may be used for local dev when gVisor is unavailable."
        ),
    )
    image: str = Field(
        default="ghcr.io/ai-colleagues/orcheo-workspace-sandbox:latest",
        description=(
            "OCI image hosting the agent CLIs, Orcheo CLI, and workflow "
            "runner. One image per workspace sandbox."
        ),
    )
    default_cpu_limit: str = Field(default="1.0")
    # ``/scratch``, ``/workspace``, and ``/home/orcheo`` are mounted as tmpfs
    # (see ``DockerContainerRuntime._build_host_config``). On Linux, tmpfs
    # pages are charged to the container's memory cgroup, so the memory limit
    # has to cover the resident-set size of the workflow runner PLUS the
    # working set of all three tmpfs mounts (npm cache in ``~/.npm``, the
    # managed agent-runtime tree in ``/scratch/agent-runtimes``, anything the
    # agent writes under ``/workspace``). 512 MiB OOM-killed any run that did
    # a real ``npm install``; 2 GiB leaves enough headroom for the agent CLIs
    # plus a fresh provider install.
    default_memory_limit: str = Field(default="2g")
    default_pid_limit: int = Field(default=256, ge=1)
    default_scratch_disk_limit: str = Field(default="1g")
    default_idle_ttl_seconds: int = Field(default=900, ge=1)
    default_pool_min: int = Field(default=0, ge=0)
    default_pool_max: int = Field(default=4, ge=1)
    denied_cidrs: tuple[str, ...] = Field(default=DEFAULT_DENY_CIDRS)
    denied_hostnames: tuple[str, ...] = Field(default=DEFAULT_DENY_HOSTNAMES)
    egress_proxy_url: str | None = Field(
        default=None,
        description=(
            "Forward-proxy URL (Envoy) for permitted outbound HTTP/HTTPS. "
            "``None`` disables L7 egress entirely."
        ),
    )
    credential_broker_url: str = Field(
        default="http://sandbox-runtime:9090/credentials/resolve",
        description=(
            "Credential endpoint workspace sandboxes call. Must be reachable "
            "from the ``sandbox-egress`` network; the default points at the "
            "sandbox-runtime relay, which is on both the default and "
            "sandbox-egress networks. Injected into every spawned child "
            "container as ``ORCHEO_CREDENTIAL_BROKER_URL``."
        ),
    )
    credential_broker_forward_url: str = Field(
        default="http://backend:2025/internal/credentials/resolve",
        description=(
            "Upstream broker URL the sandbox-runtime relay forwards resolved "
            "credential requests to. Reachable from the default network only; "
            "must NOT be exposed to child sandboxes."
        ),
    )
    audit_logger_name: str = Field(default="orcheo.sandbox.audit")

    @field_validator("default_pool_max")
    @classmethod
    def _validate_pool_max(cls, value: int) -> int:
        """Ensure pool max is positive (min may be 0)."""
        if value < 1:
            msg = "default_pool_max must be >= 1"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_broker_url_not_denied(self) -> SandboxSettings:
        """Reject a child-facing broker URL that targets a denied hostname.

        Child sandboxes only attach to the ``sandbox-egress`` network and the
        L3/L4 deny ruleset blocks ``denied_hostnames``. A child-facing broker
        URL pointing at any of those hosts is a configuration bug: the call
        will fail with a DNS error at first credential resolve. Fail fast at
        boot instead.
        """
        host = urlparse(self.credential_broker_url).hostname
        if host is not None and host in self.denied_hostnames:
            msg = (
                f"credential_broker_url host {host!r} is in denied_hostnames "
                f"{self.denied_hostnames!r}; child sandboxes cannot reach it. "
                "Point ORCHEO_CREDENTIAL_BROKER_URL at the sandbox-runtime "
                "relay and set ORCHEO_CREDENTIAL_BROKER_FORWARD_URL for the "
                "relay's upstream target."
            )
            raise ValueError(msg)
        return self

    # Mapping of model field → environment variable. Kept explicit so the
    # documented operator-facing names in
    # ``docs/operators/workspace_runtime_isolation.md`` stay in sync with
    # what the code actually reads. Unset env vars fall back to the field
    # default. Booleans/numbers are coerced by Pydantic's validators.
    _FIELD_TO_ENV: ClassVar[dict[str, str]] = {
        "container_runtime": "ORCHEO_CONTAINER_RUNTIME",
        "image": "ORCHEO_SANDBOX_IMAGE",
        "default_cpu_limit": "ORCHEO_SANDBOX_DEFAULT_CPU_LIMIT",
        "default_memory_limit": "ORCHEO_SANDBOX_DEFAULT_MEMORY_LIMIT",
        "default_pid_limit": "ORCHEO_SANDBOX_DEFAULT_PID_LIMIT",
        "default_scratch_disk_limit": "ORCHEO_SANDBOX_DEFAULT_SCRATCH_DISK_LIMIT",
        "default_idle_ttl_seconds": "ORCHEO_SANDBOX_DEFAULT_IDLE_TTL_SECONDS",
        "default_pool_min": "ORCHEO_SANDBOX_DEFAULT_POOL_MIN",
        "default_pool_max": "ORCHEO_SANDBOX_DEFAULT_POOL_MAX",
        "egress_proxy_url": "ORCHEO_EGRESS_PROXY_URL",
        "credential_broker_url": "ORCHEO_CREDENTIAL_BROKER_URL",
        "credential_broker_forward_url": "ORCHEO_CREDENTIAL_BROKER_FORWARD_URL",
        "audit_logger_name": "ORCHEO_SANDBOX_AUDIT_LOGGER_NAME",
    }

    @classmethod
    def from_mapping(cls, source: Mapping[str, Any]) -> SandboxSettings:
        """Build settings from a ``Mapping`` (e.g. ``os.environ``).

        Unset / blank values fall back to the field default. This is how the
        process bootstraps actually pick up operator env-var overrides; the
        previous ``SandboxSettings()`` call quietly ignored every override.
        """
        kwargs: dict[str, Any] = {}
        for field_name, env_key in cls._FIELD_TO_ENV.items():
            value = source.get(env_key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            kwargs[field_name] = value
        return cls(**kwargs)

    @classmethod
    def from_env(cls) -> SandboxSettings:
        """Build settings from the process environment."""
        return cls.from_mapping(os.environ)
