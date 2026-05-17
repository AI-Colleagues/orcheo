"""Runtime loader for enabled Orcheo plugins."""

from __future__ import annotations
import importlib.metadata
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from orcheo.edges.registry import edge_registry
from orcheo.listeners.registry import listener_registry
from orcheo.nodes.ai.tools.registry import tool_registry
from orcheo.nodes.registry import registry
from orcheo.plugins.api import PluginAPI, PluginRegistrations
from orcheo.plugins.compatibility import check_manifest_compatibility
from orcheo.plugins.manager import (
    PLUGIN_ENTRYPOINT_GROUP,
    _distribution_to_manifest,
    _site_packages,
)
from orcheo.plugins.models import PluginManifest
from orcheo.plugins.paths import build_storage_paths
from orcheo.plugins.state import load_desired_state, load_lock_state
from orcheo.triggers.registry import trigger_registry


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PluginLoadResult:
    """Summary for one attempted plugin load."""

    name: str
    loaded: bool
    manifest: PluginManifest | None = None
    error: str | None = None


@dataclass(slots=True)
class PluginLoadReport:
    """Aggregate report for one plugin load pass."""

    generation: int
    results: list[PluginLoadResult]


_LOADED = False
_GENERATION = 0
_REPORT = PluginLoadReport(generation=0, results=[])
_REGISTERED = PluginRegistrations()


def _purge_module_roots(module_roots: Iterable[str]) -> None:
    """Remove loaded plugin modules so a reload can import fresh code."""
    roots = {root for root in module_roots if root}
    if not roots:
        return
    for module_name in sorted(sys.modules, reverse=True):
        if any(
            module_name == root or module_name.startswith(f"{root}.") for root in roots
        ):
            sys.modules.pop(module_name, None)


def _clear_registered_components() -> None:
    """Remove plugin-provided components from process-local registries."""
    global _REGISTERED  # noqa: PLW0603
    for name in reversed(_REGISTERED.nodes):
        registry.unregister(name)
    for name in reversed(_REGISTERED.edges):
        edge_registry.unregister(name)
    for name in reversed(_REGISTERED.agent_tools):
        tool_registry.unregister(name)
    for name in reversed(_REGISTERED.listeners):
        listener_registry.unregister(name)
    for name in reversed(_REGISTERED.triggers):
        trigger_registry.unregister(name)
    _purge_module_roots(_REGISTERED.module_roots)
    _REGISTERED = PluginRegistrations()


def _rollback_plugin_registrations(registrations: PluginRegistrations) -> None:
    """Roll back a plugin's partial registrations after a load failure."""
    for name in reversed(registrations.nodes):
        registry.unregister(name)
    for name in reversed(registrations.edges):
        edge_registry.unregister(name)
    for name in reversed(registrations.agent_tools):
        tool_registry.unregister(name)
    for name in reversed(registrations.listeners):
        listener_registry.unregister(name)
    for name in reversed(registrations.triggers):
        trigger_registry.unregister(name)
    _purge_module_roots(registrations.module_roots)


def _plugin_site_packages() -> Path:
    paths = build_storage_paths()
    return _site_packages(Path(paths.install_dir))


def _enabled_locked_plugin_names() -> set[str]:
    paths = build_storage_paths()
    desired = {
        record.name: record for record in load_desired_state(Path(paths.state_file))
    }
    locked = {record.name: record for record in load_lock_state(Path(paths.lock_file))}
    return {
        name for name, record in desired.items() if record.enabled and name in locked
    }


def _iter_plugin_distributions(
    site_packages: Path,
    *,
    names: Iterable[str],
) -> list[importlib.metadata.Distribution]:
    wanted = {name.lower() for name in names}
    matches: list[importlib.metadata.Distribution] = []
    for distribution in importlib.metadata.distributions(path=[str(site_packages)]):
        metadata_name = str(distribution.metadata.get("Name", ""))
        if metadata_name.lower() not in wanted:
            continue
        if any(
            entry.group == PLUGIN_ENTRYPOINT_GROUP
            for entry in distribution.entry_points
        ):
            matches.append(distribution)
    return matches


def _ensure_sys_path(site_packages: Path) -> None:
    if site_packages.exists() and str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))


def _entry_point_module_root(entry_point: importlib.metadata.EntryPoint) -> str:
    """Return the top-level module path for one plugin entry point."""
    module_name = getattr(entry_point, "module", "")
    if module_name:
        return str(module_name)
    return str(entry_point.value).split(":", maxsplit=1)[0].strip()


def load_enabled_plugins(  # noqa: C901, PLR0915
    *, force: bool = False
) -> PluginLoadReport:
    """Load all enabled plugins from the managed plugin environment."""
    global _GENERATION, _LOADED, _REPORT  # noqa: PLW0603
    if _LOADED and not force:
        return _REPORT
    _clear_registered_components()

    site_packages = _plugin_site_packages()
    enabled_names = _enabled_locked_plugin_names()
    if not enabled_names or not site_packages.exists():
        _GENERATION += 1
        _LOADED = True
        _REPORT = PluginLoadReport(generation=_GENERATION, results=[])
        return _REPORT

    _ensure_sys_path(site_packages)
    results: list[PluginLoadResult] = []
    distributions = _iter_plugin_distributions(site_packages, names=enabled_names)
    discovered_names = {
        str(distribution.metadata.get("Name", "")) for distribution in distributions
    }
    for missing_name in sorted(
        enabled_names - {name.lower() for name in discovered_names}
    ):
        results.append(
            PluginLoadResult(
                name=missing_name,
                loaded=False,
                error=(
                    "Plugin is enabled and locked but not installed in the "
                    "plugin environment."
                ),
            )
        )

    for distribution in distributions:
        api = PluginAPI()
        manifest, _manifest_hash = _distribution_to_manifest(distribution)
        issues = check_manifest_compatibility(manifest)
        if issues:
            error = "; ".join(issues)
            logger.warning("Skipping incompatible plugin %s: %s", manifest.name, error)
            results.append(
                PluginLoadResult(
                    name=manifest.name,
                    loaded=False,
                    manifest=manifest,
                    error=error,
                )
            )
            continue
        entry_points = [
            entry
            for entry in distribution.entry_points
            if entry.group == PLUGIN_ENTRYPOINT_GROUP
        ]
        if not entry_points:
            results.append(
                PluginLoadResult(
                    name=manifest.name,
                    loaded=False,
                    manifest=manifest,
                    error="No plugin entry points registered.",
                )
            )
            continue
        try:
            for entry_point in entry_points:
                plugin = entry_point.load()
                register = getattr(plugin, "register", None)
                if not callable(register):
                    msg = (
                        f"Plugin entry point '{entry_point.name}' has no "
                        "register(api) hook."
                    )
                    raise TypeError(msg)
                register(api)
                module_root = _entry_point_module_root(entry_point)
                if module_root and module_root not in api.registrations.module_roots:
                    api.registrations.module_roots.append(module_root)
        except Exception as exc:  # pragma: no cover - covered in loader tests
            _rollback_plugin_registrations(api.registrations)
            logger.exception("Failed to load plugin %s", manifest.name)
            results.append(
                PluginLoadResult(
                    name=manifest.name,
                    loaded=False,
                    manifest=manifest,
                    error=str(exc),
                )
            )
            continue
        results.append(
            PluginLoadResult(
                name=manifest.name,
                loaded=True,
                manifest=manifest,
            )
        )
        _REGISTERED.nodes.extend(api.registrations.nodes)
        _REGISTERED.edges.extend(api.registrations.edges)
        _REGISTERED.agent_tools.extend(api.registrations.agent_tools)
        _REGISTERED.listeners.extend(api.registrations.listeners)
        _REGISTERED.triggers.extend(api.registrations.triggers)
        _REGISTERED.module_roots.extend(api.registrations.module_roots)

    _GENERATION += 1
    _LOADED = True
    _REPORT = PluginLoadReport(generation=_GENERATION, results=results)
    return _REPORT


def ensure_plugins_loaded() -> PluginLoadReport:
    """Load enabled plugins once per process."""
    return load_enabled_plugins(force=False)


def invalidate_plugin_loader() -> None:
    """Invalidate process-local plugin load state after lifecycle changes."""
    global _LOADED, _REPORT  # noqa: PLW0603
    _LOADED = False
    _REPORT = PluginLoadReport(generation=_GENERATION, results=[])
    _clear_registered_components()


def reset_plugin_loader_for_tests() -> None:
    """Reset the process-local loader state for isolated tests."""
    invalidate_plugin_loader()


__all__ = [
    "PluginLoadReport",
    "PluginLoadResult",
    "ensure_plugins_loaded",
    "invalidate_plugin_loader",
    "load_enabled_plugins",
    "reset_plugin_loader_for_tests",
]
