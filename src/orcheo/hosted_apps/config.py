"""Fail-closed configuration parsing for Hosted Apps infrastructure."""

from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


__all__ = ["HostedAppsSettings", "HostedAppsSettingsError"]


class HostedAppsSettingsError(ValueError):
    """Raised when enabled Hosted Apps infrastructure is incomplete or unsafe."""


@dataclass(frozen=True, slots=True)
class HostedAppsSettings:
    """Configuration shared by the control plane, validator, and gateway.

    A missing or invalid value never silently enables public delivery. The durable
    runtime generation is intentionally not represented here: it is database state
    changed by a scoped platform mutation, not an environment variable.
    """

    enabled: bool
    base_domain: str | None
    bundle_backend: str | None
    filesystem_root: Path | None
    deployment_mode: str
    workspace_allowlist: frozenset[str]
    descriptor_cache_seconds: int
    alias_tombstone_days: int
    max_archive_bytes: int
    max_expanded_bytes: int
    max_file_count: int

    @classmethod
    def from_environment(
        cls, environment: dict[str, str] | None = None
    ) -> HostedAppsSettings:
        """Read environment values and enforce the configuration enablement contract."""
        values = os.environ if environment is None else environment
        enabled = _as_bool(values.get("ORCHEO_HOSTED_APPS_ENABLED", "false"))
        base_domain = _optional(values.get("ORCHEO_APPS_BASE_DOMAIN"))
        bundle_backend = _optional(values.get("ORCHEO_APP_BUNDLE_BACKEND"))
        filesystem_root_raw = _optional(values.get("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT"))
        filesystem_root = (
            Path(filesystem_root_raw).expanduser() if filesystem_root_raw else None
        )
        settings = cls(
            enabled=enabled,
            base_domain=base_domain,
            bundle_backend=bundle_backend,
            filesystem_root=filesystem_root,
            deployment_mode=values.get("ORCHEO_DEPLOYMENT_MODE", "local")
            .strip()
            .lower(),
            workspace_allowlist=frozenset(
                value.strip()
                for value in values.get(
                    "ORCHEO_HOSTED_APPS_WORKSPACE_ALLOWLIST", ""
                ).split(",")
                if value.strip()
            ),
            descriptor_cache_seconds=_as_positive_int(
                values.get("ORCHEO_APP_DESCRIPTOR_CACHE_SECONDS", "30"),
                "ORCHEO_APP_DESCRIPTOR_CACHE_SECONDS",
            ),
            alias_tombstone_days=_as_positive_int(
                values.get("ORCHEO_APP_ALIAS_TOMBSTONE_DAYS", "30"),
                "ORCHEO_APP_ALIAS_TOMBSTONE_DAYS",
            ),
            max_archive_bytes=_as_positive_int(
                values.get("ORCHEO_APP_MAX_ARCHIVE_BYTES", str(50 * 1024 * 1024)),
                "ORCHEO_APP_MAX_ARCHIVE_BYTES",
            ),
            max_expanded_bytes=_as_positive_int(
                values.get("ORCHEO_APP_MAX_EXPANDED_BYTES", str(250 * 1024 * 1024)),
                "ORCHEO_APP_MAX_EXPANDED_BYTES",
            ),
            max_file_count=_as_positive_int(
                values.get("ORCHEO_APP_MAX_FILE_COUNT", "5000"),
                "ORCHEO_APP_MAX_FILE_COUNT",
            ),
        )
        if enabled:
            settings._validate_enabled()
        return settings

    def _validate_enabled(self) -> None:
        """Require a complete, explicitly selected safe infrastructure configuration."""
        if self.base_domain is None:
            raise HostedAppsSettingsError(
                "ORCHEO_APPS_BASE_DOMAIN is required when Hosted Apps is enabled."
            )
        _validate_base_domain(self.base_domain)
        if self.bundle_backend not in {"filesystem", "s3"}:
            raise HostedAppsSettingsError(
                "ORCHEO_APP_BUNDLE_BACKEND must be 's3' or 'filesystem' when "
                "Hosted Apps is enabled."
            )
        if self.bundle_backend == "filesystem" and self.filesystem_root is None:
            raise HostedAppsSettingsError(
                "ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT is required for the filesystem "
                "bundle backend."
            )
        if self.bundle_backend == "filesystem" and self.deployment_mode not in {
            "local",
            "single-node",
        }:
            raise HostedAppsSettingsError(
                "The filesystem bundle backend is allowed only in local or "
                "single-node deployments."
            )
        if self.max_expanded_bytes < self.max_archive_bytes:
            raise HostedAppsSettingsError(
                "ORCHEO_APP_MAX_EXPANDED_BYTES must not be lower than the archive "
                "limit."
            )

    def allows_workspace(self, workspace_id: str) -> bool:
        """Return whether a workspace is eligible while an allowlist is configured."""
        return not self.workspace_allowlist or workspace_id in self.workspace_allowlist


def _optional(value: str | None) -> str | None:
    """Return stripped non-empty configuration strings."""
    if value is None:
        return None
    return value.strip() or None


def _as_bool(value: str) -> bool:
    """Parse strict feature-flag booleans to avoid accidental enablement."""
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no", ""}:
        return False
    raise HostedAppsSettingsError("ORCHEO_HOSTED_APPS_ENABLED must be a boolean.")


def _as_positive_int(value: str, name: str) -> int:
    """Parse a positive bounded configuration integer."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise HostedAppsSettingsError(f"{name} must be an integer.") from exc
    if parsed <= 0:
        raise HostedAppsSettingsError(f"{name} must be greater than zero.")
    return parsed


def _validate_base_domain(value: str) -> None:
    """Reject URLs, wildcard syntax, and invalid labels in an apps base domain."""
    if "://" in value or "/" in value or value.startswith("*."):
        raise HostedAppsSettingsError(
            "ORCHEO_APPS_BASE_DOMAIN must be a bare DNS domain without a wildcard."
        )
    parsed = urlparse(f"//{value}")
    if parsed.hostname != value.lower() or "." not in value:
        raise HostedAppsSettingsError(
            "ORCHEO_APPS_BASE_DOMAIN must be a valid DNS domain."
        )
