"""Compatibility and impact helpers for the plugin subsystem."""

from __future__ import annotations
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from orcheo.plugins.models import (
    PLUGIN_API_VERSION,
    LockedPluginRecord,
    PluginImpactSummary,
    PluginManifest,
)


HOT_RELOADABLE_EXPORTS = {"nodes", "edges", "agent_tools"}
RESTART_REQUIRED_EXPORTS = {"listeners", "triggers"}


def get_running_orcheo_version() -> str:
    """Return the installed Orcheo core version."""
    try:
        return package_version("orcheo")
    except PackageNotFoundError:
        return "0.0.0"


def check_manifest_compatibility(
    manifest: PluginManifest,
) -> list[str]:
    """Return compatibility issues for ``manifest``."""
    issues: list[str] = []
    if manifest.plugin_api_version != PLUGIN_API_VERSION:
        issues.append(
            "plugin API mismatch: "
            f"plugin requires {manifest.plugin_api_version}, "
            f"current is {PLUGIN_API_VERSION}"
        )
    try:
        specifier = SpecifierSet(manifest.orcheo_version)
    except InvalidSpecifier:
        issues.append(f"invalid Orcheo version specifier: {manifest.orcheo_version}")
        return issues
    running = get_running_orcheo_version()
    if not specifier.contains(running, prereleases=True):
        issues.append(
            f"orcheo version {running} does not satisfy {manifest.orcheo_version}"
        )
    return issues


def classify_plugin_change(
    *,
    previous: LockedPluginRecord | None,
    current: PluginManifest,
    operation: str,
) -> PluginImpactSummary:
    """Return an impact summary for install, update, disable, or uninstall."""
    previous_exports = set(previous.exports if previous is not None else [])
    current_exports = set(current.exports)
    affected_kinds = sorted(previous_exports | current_exports)
    affected_ids = sorted(affected_kinds)
    restart_required = bool(RESTART_REQUIRED_EXPORTS & set(affected_kinds))

    if operation == "install":
        change_type = "additive"
    elif operation == "uninstall":
        change_type = "remove"
    elif current_exports == previous_exports:
        change_type = "replace"
    elif current_exports >= previous_exports:
        change_type = "additive"
    elif current_exports <= previous_exports:
        change_type = "remove"
    else:
        change_type = "mixed"

    if restart_required:
        return PluginImpactSummary(
            change_type=change_type,
            affected_component_kinds=affected_kinds,
            affected_component_ids=affected_ids,
            activation_mode="restart_required",
            prompt_required=operation in {"update", "disable", "uninstall"},
            restart_required=True,
        )

    if operation == "install" and set(affected_kinds) <= HOT_RELOADABLE_EXPORTS:
        activation_mode = "silent_hot_reload"
        prompt_required = False
    elif set(affected_kinds) <= HOT_RELOADABLE_EXPORTS:
        activation_mode = "confirm_hot_reload"
        prompt_required = operation in {"update", "disable", "uninstall"}
    else:
        activation_mode = "silent_hot_reload"
        prompt_required = False

    return PluginImpactSummary(
        change_type=change_type,
        affected_component_kinds=affected_kinds,
        affected_component_ids=affected_ids,
        activation_mode=activation_mode,
        prompt_required=prompt_required,
        restart_required=False,
    )


__all__ = [
    "HOT_RELOADABLE_EXPORTS",
    "RESTART_REQUIRED_EXPORTS",
    "check_manifest_compatibility",
    "classify_plugin_change",
    "get_running_orcheo_version",
]
