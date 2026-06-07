"""Helpers for inspecting plugin availability in the current backend process."""

from __future__ import annotations
import logging
from collections.abc import Iterable
from typing import Any
from orcheo.plugins import PluginManager, load_enabled_plugins

logger = logging.getLogger(__name__)


def list_runtime_plugins() -> list[dict[str, Any]]:
    """Return plugin inventory plus current-process load status."""
    rows = PluginManager().list_plugins()
    report = load_enabled_plugins(force=False)
    results = {result.name: result for result in report.results}
    plugins: list[dict[str, Any]] = []
    for row in rows:
        name = str(row["name"])
        load_result = results.get(name)
        plugins.append(
            {
                "name": name,
                "enabled": bool(row["enabled"]),
                "status": str(row["status"]),
                "version": str(row["version"]),
                "exports": [str(item) for item in row["exports"]],
                "loaded": bool(load_result.loaded)
                if load_result is not None
                else False,
                "load_error": load_result.error if load_result is not None else None,
            }
        )
    return plugins


def required_plugins_from_metadata(metadata: dict[str, Any]) -> list[str]:
    """Extract template plugin prerequisites from workflow-version metadata."""
    template_metadata = metadata.get("template")
    if not isinstance(template_metadata, dict):
        return []
    raw_required = template_metadata.get("requiredPlugins")
    if raw_required is None:
        raw_required = template_metadata.get("required_plugins")
    if not isinstance(raw_required, list):
        return []
    return [
        str(plugin_name).strip()
        for plugin_name in raw_required
        if str(plugin_name).strip()
    ]


def missing_required_plugins(required_plugins: Iterable[str]) -> list[str]:
    """Return required plugin names unavailable in the current backend process."""
    required = sorted(
        {str(name).strip() for name in required_plugins if str(name).strip()}
    )
    if not required:
        return []
    inventory = {plugin["name"]: plugin for plugin in list_runtime_plugins()}
    missing: list[str] = []
    for name in required:
        plugin = inventory.get(name)
        if plugin is None:
            logger.warning("Required plugin '%s' is not installed", name)
            missing.append(name)
        elif not plugin["enabled"]:
            logger.warning("Required plugin '%s' is installed but not enabled", name)
            missing.append(name)
        elif not plugin["loaded"]:
            logger.warning(
                "Required plugin '%s' is enabled but failed to load: %s", 
                name, 
                plugin.get("load_error", "unknown error")
            )
            missing.append(name)
    return missing


__all__ = [
    "list_runtime_plugins",
    "missing_required_plugins",
    "required_plugins_from_metadata",
]
