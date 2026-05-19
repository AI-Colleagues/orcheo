"""Configuration for the Sandbox Runtime Manager.

These settings drive the choice of container runtime, default per-workspace
pool sizing, and the network targets that must be denied at L3/L4 regardless
of tenant configuration. Sandboxing is always on; there is no master feature
flag.
"""

from __future__ import annotations
from pydantic import BaseModel, Field, field_validator


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
        default="orcheo/workspace-sandbox:latest",
        description=(
            "OCI image hosting the agent CLIs, Orcheo CLI, and workflow "
            "runner. One image per workspace sandbox."
        ),
    )
    default_cpu_limit: str = Field(default="1.0")
    default_memory_limit: str = Field(default="512m")
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
        default="http://backend:2025/internal/credentials/resolve",
        description="Backend endpoint the Credential Broker listens on.",
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
