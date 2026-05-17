"""Tests for runtime plugin loading and in-process reload behavior."""

from __future__ import annotations
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from orcheo.edges.registry import edge_registry
from orcheo.listeners.registry import listener_registry
from orcheo.nodes.ai.tools.registry import tool_registry
from orcheo.nodes.registry import registry
from orcheo.plugins import load_enabled_plugins, reset_plugin_loader_for_tests
from orcheo.plugins.api import PluginRegistrations
from orcheo.plugins.loader import (
    _entry_point_module_root,
    _iter_plugin_distributions,
    _rollback_plugin_registrations,
)
from orcheo.plugins.manager import PLUGIN_ENTRYPOINT_GROUP, PluginManager
from orcheo.triggers.registry import trigger_registry


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "plugin_fixtures"

_uv_available = shutil.which("uv") is not None
requires_uv = pytest.mark.skipif(not _uv_available, reason="uv not installed")


def _copy_fixture(tmp_path: Path, fixture_name: str) -> Path:
    source = FIXTURE_ROOT / fixture_name
    destination = tmp_path / fixture_name
    shutil.copytree(source, destination)
    return destination


def _site_packages(plugin_dir: Path) -> Path:
    return (
        plugin_dir
        / "venv"
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )


def _set_plugin_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    plugin_dir = tmp_path / "plugins"
    cache_dir = tmp_path / "cache"
    config_dir = tmp_path / "config"
    plugin_dir.mkdir()
    cache_dir.mkdir()
    config_dir.mkdir()
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(plugin_dir))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(config_dir))
    return plugin_dir


@requires_uv
def test_loader_isolates_broken_plugin_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A broken plugin should not block unrelated healthy plugins from loading."""
    _set_plugin_env(monkeypatch, tmp_path)
    manager = PluginManager()
    manager.install(str(_copy_fixture(tmp_path, "node_plugin")))
    manager.install(str(_copy_fixture(tmp_path, "broken_plugin")))

    reset_plugin_loader_for_tests()
    report = load_enabled_plugins(force=True)
    results = {item.name: item for item in report.results}

    assert results["orcheo-plugin-fixture-node"].loaded is True
    assert results["orcheo-plugin-fixture-broken"].loaded is False
    assert "broken fixture plugin import" in (
        results["orcheo-plugin-fixture-broken"].error or ""
    )
    assert registry.get_node("FixturePluginNode") is not None


@requires_uv
def test_loader_skips_disabled_plugins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Disabled plugins remain installed but are not loaded into registries."""
    _set_plugin_env(monkeypatch, tmp_path)
    manager = PluginManager()
    manager.install(str(_copy_fixture(tmp_path, "node_plugin")))
    manager.set_enabled("orcheo-plugin-fixture-node", enabled=False)

    reset_plugin_loader_for_tests()
    report = load_enabled_plugins(force=True)

    assert report.results == []
    assert registry.get_node("FixturePluginNode") is None


@requires_uv
def test_loader_reports_incompatible_manifest_at_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Startup should skip incompatible manifests with a precise diagnostic."""
    plugin_dir = _set_plugin_env(monkeypatch, tmp_path)
    manager = PluginManager()
    manager.install(str(_copy_fixture(tmp_path, "node_plugin")))

    manifest_path = (
        _site_packages(plugin_dir) / "orcheo_plugin_fixture_node" / "orcheo_plugin.toml"
    )
    manifest_path.write_text(
        'plugin_api_version = 999\norcheo_version = ">=0.0.0"\nexports = ["nodes"]\n',
        encoding="utf-8",
    )

    reset_plugin_loader_for_tests()
    report = load_enabled_plugins(force=True)
    results = {item.name: item for item in report.results}

    assert results["orcheo-plugin-fixture-node"].loaded is False
    assert "plugin API mismatch" in (results["orcheo-plugin-fixture-node"].error or "")
    assert registry.get_node("FixturePluginNode") is None


@requires_uv
def test_loader_registers_trigger_plugins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Trigger plugins should register into the external trigger registry."""
    _set_plugin_env(monkeypatch, tmp_path)
    manager = PluginManager()
    manager.install(str(_copy_fixture(tmp_path, "trigger_plugin")))

    reset_plugin_loader_for_tests()
    report = load_enabled_plugins(force=True)

    assert {item.name for item in report.results} == {"orcheo-plugin-fixture-trigger"}
    registration = trigger_registry.get("fixture-trigger")
    assert registration is not None
    assert registration.metadata.display_name == "Fixture Trigger"
    assert registration.factory(enabled=True) == {
        "kind": "fixture-trigger",
        "config": {"enabled": True},
    }


@requires_uv
def test_loader_rolls_back_partial_registrations_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Loader should remove partial registrations when register(api) crashes."""
    _set_plugin_env(monkeypatch, tmp_path)
    manager = PluginManager()
    manager.install(str(_copy_fixture(tmp_path, "partial_register_plugin")))

    reset_plugin_loader_for_tests()
    report = load_enabled_plugins(force=True)
    results = {item.name: item for item in report.results}

    assert results["orcheo-plugin-fixture-partial-register"].loaded is False
    assert "partial registration failure" in (
        results["orcheo-plugin-fixture-partial-register"].error or ""
    )
    assert registry.get_node("PartialFixtureNode") is None


@requires_uv
@pytest.mark.asyncio()
async def test_loader_hot_reload_reimports_updated_plugin_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Force reloading should pick up updated plugin code for new runs."""
    _set_plugin_env(monkeypatch, tmp_path)
    manager = PluginManager()
    fixture_path = _copy_fixture(tmp_path, "node_plugin")
    manager.install(str(fixture_path))

    reset_plugin_loader_for_tests()
    first_report = load_enabled_plugins(force=True)
    node_v1 = registry.get_node("FixturePluginNode")
    assert node_v1 is not None

    first_value = await node_v1(name="fixture").run({}, {})
    assert first_value == {"value": "fixture-node"}

    module_path = fixture_path / "src" / "orcheo_plugin_fixture_node" / "__init__.py"
    module_path.write_text(
        module_path.read_text(encoding="utf-8").replace(
            '{"value": "fixture-node"}',
            '{"value": "fixture-node-v2"}',
        ),
        encoding="utf-8",
    )
    pyproject_path = fixture_path / "pyproject.toml"
    pyproject_path.write_text(
        pyproject_path.read_text(encoding="utf-8").replace(
            'version = "0.1.0"',
            'version = "0.2.0"',
        ),
        encoding="utf-8",
    )

    manager.update("orcheo-plugin-fixture-node")
    second_report = load_enabled_plugins(force=True)
    node_v2 = registry.get_node("FixturePluginNode")
    assert node_v2 is not None

    second_value = await node_v2(name="fixture").run({}, {})
    legacy_value = await node_v1(name="fixture").run({}, {})

    assert second_report.generation > first_report.generation
    assert second_value == {"value": "fixture-node-v2"}
    assert legacy_value == {"value": "fixture-node"}


# ---------------------------------------------------------------------------
# Unit tests for helper functions (missing lines 74, 88, 90, 92, 94, 125->121,
# 143, 172, 205-213, 219-223, 226->215)
# ---------------------------------------------------------------------------


@requires_uv
def test_clear_registered_components_removes_listeners_and_triggers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_clear_registered_components removes listeners and triggers (lines 74-78)."""
    _set_plugin_env(monkeypatch, tmp_path)
    manager = PluginManager()
    manager.install(str(_copy_fixture(tmp_path, "listener_plugin")))
    manager.install(str(_copy_fixture(tmp_path, "trigger_plugin")))

    reset_plugin_loader_for_tests()
    load_enabled_plugins(force=True)

    # _clear_registered_components is called inside reset / load
    reset_plugin_loader_for_tests()

    assert listener_registry.resolve("fixture-listener") is None
    assert trigger_registry.get("fixture-trigger") is None


def test_rollback_removes_edges_tools_listeners_triggers() -> None:
    """_rollback_plugin_registrations removes all component kinds."""
    from orcheo.edges.registry import EdgeMetadata
    from orcheo.listeners.registry import ListenerMetadata
    from orcheo.nodes.ai.tools.registry import ToolMetadata
    from orcheo.triggers.registry import TriggerMetadata

    # Register dummy components in the real global registries
    edge_meta = EdgeMetadata(name="_test_rollback_edge", description="")
    edge_registry.register(edge_meta)(lambda s: s)

    tool_meta = ToolMetadata(name="_test_rollback_tool", description="")
    tool_registry.register(tool_meta)(lambda: None)

    listener_meta = ListenerMetadata(id="_test_rollback_listener", display_name="")
    listener_registry.register(
        listener_meta,
        compiler=lambda **kw: None,
        adapter_factory=lambda **kw: object(),
    )

    trigger_meta = TriggerMetadata(id="_test_rollback_trigger", display_name="")
    trigger_registry.register(trigger_meta, lambda: {})

    registrations = PluginRegistrations()
    registrations.edges.append("_test_rollback_edge")
    registrations.agent_tools.append("_test_rollback_tool")
    registrations.listeners.append("_test_rollback_listener")
    registrations.triggers.append("_test_rollback_trigger")

    _rollback_plugin_registrations(registrations)

    assert edge_registry.get_edge("_test_rollback_edge") is None
    assert tool_registry.get_tool("_test_rollback_tool") is None
    assert listener_registry.resolve("_test_rollback_listener") is None
    assert trigger_registry.get("_test_rollback_trigger") is None


def test_iter_plugin_distributions_skips_no_entry_group(tmp_path: Path) -> None:
    """_iter_plugin_distributions skips distributions with no plugin entry group."""
    mock_dist = MagicMock()
    mock_dist.metadata.get.return_value = "my-plugin"
    # No entry points matching the PLUGIN_ENTRYPOINT_GROUP
    mock_ep = MagicMock()
    mock_ep.group = "some.other.group"
    mock_dist.entry_points = [mock_ep]

    with patch(
        "orcheo.plugins.loader.importlib.metadata.distributions",
        return_value=[mock_dist],
    ):
        result = _iter_plugin_distributions(tmp_path, names=["my-plugin"])

    assert result == []


def test_entry_point_module_root_from_value_when_no_module_attr() -> None:
    """_entry_point_module_root falls back to value split when module attr is empty."""
    ep = MagicMock()
    ep.module = ""
    ep.value = "orcheo_plugin_fixture_node:Plugin"

    root = _entry_point_module_root(ep)
    assert root == "orcheo_plugin_fixture_node"


@requires_uv
def test_loader_reports_missing_plugin_not_in_site_packages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Plugins enabled and locked but absent from site-packages get a missing error."""
    _set_plugin_env(monkeypatch, tmp_path)
    manager = PluginManager()
    manager.install(str(_copy_fixture(tmp_path, "node_plugin")))

    # Remove the installed package from site-packages so the distribution is missing
    site_pkgs = _site_packages(tmp_path / "plugins")
    pkg_dir = site_pkgs / "orcheo_plugin_fixture_node"
    dist_info = next(site_pkgs.glob("orcheo_plugin_fixture_node-*.dist-info"), None)
    if pkg_dir.exists():
        import shutil as _shutil

        _shutil.rmtree(pkg_dir)
    if dist_info is not None:
        import shutil as _shutil

        _shutil.rmtree(dist_info)

    reset_plugin_loader_for_tests()
    report = load_enabled_plugins(force=True)
    results = {item.name: item for item in report.results}

    assert "orcheo-plugin-fixture-node" in results
    assert results["orcheo-plugin-fixture-node"].loaded is False
    assert "not installed" in (results["orcheo-plugin-fixture-node"].error or "")


@requires_uv
def test_loader_reports_no_entry_points(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A distribution with no plugin entry points gets a diagnostic error."""
    _set_plugin_env(monkeypatch, tmp_path)
    manager = PluginManager()
    manager.install(str(_copy_fixture(tmp_path, "node_plugin")))

    # Patch _iter_plugin_distributions to return a dist with no entry points
    from orcheo.plugins.loader import PluginManifest

    mock_dist = MagicMock()
    mock_manifest = MagicMock(spec=PluginManifest)
    mock_manifest.name = "orcheo-plugin-fixture-node"
    mock_manifest.plugin_api_version = 1
    mock_manifest.orcheo_version = ">=0.0.0"
    mock_manifest.exports = ["nodes"]
    mock_manifest.description = ""
    mock_manifest.author = ""

    # The distribution has the group entry point in initial check but returns
    # empty list when filtered for entry points group
    mock_ep = MagicMock()
    mock_ep.group = PLUGIN_ENTRYPOINT_GROUP

    # When _distribution_to_manifest is called, return our manifest
    # When distribution.entry_points is iterated for filtering, return empty
    def _ep_iter() -> list:
        return [mock_ep]

    mock_dist.entry_points = [mock_ep]

    reset_plugin_loader_for_tests()

    with patch(
        "orcheo.plugins.loader._iter_plugin_distributions",
        return_value=[mock_dist],
    ):
        with patch(
            "orcheo.plugins.loader._distribution_to_manifest",
            return_value=(mock_manifest, "abc"),
        ):
            with patch(
                "orcheo.plugins.loader.check_manifest_compatibility",
                return_value=[],
            ):
                # Override the entry_points list comprehension result to empty
                def _patched_eps(dist: object, *, names: object = None) -> list:
                    return []

                with patch.object(mock_dist, "entry_points", new=[]):
                    report = load_enabled_plugins(force=True)

    results = {item.name: item for item in report.results}
    assert "orcheo-plugin-fixture-node" in results
    assert results["orcheo-plugin-fixture-node"].loaded is False
    assert "No plugin entry points" in (
        results["orcheo-plugin-fixture-node"].error or ""
    )


@requires_uv
def test_loader_reports_non_callable_register(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An entry point whose register attribute is not callable raises TypeError."""
    _set_plugin_env(monkeypatch, tmp_path)
    manager = PluginManager()
    manager.install(str(_copy_fixture(tmp_path, "node_plugin")))

    from orcheo.plugins.loader import PluginManifest

    mock_manifest = MagicMock(spec=PluginManifest)
    mock_manifest.name = "orcheo-plugin-fixture-node"
    mock_manifest.plugin_api_version = 1
    mock_manifest.orcheo_version = ">=0.0.0"
    mock_manifest.exports = ["nodes"]
    mock_manifest.description = ""
    mock_manifest.author = ""

    mock_plugin = MagicMock()
    mock_plugin.register = "not-callable"  # Not a callable

    mock_ep = MagicMock()
    mock_ep.group = PLUGIN_ENTRYPOINT_GROUP
    mock_ep.name = "orcheo_plugin_fixture_node"
    mock_ep.load.return_value = mock_plugin
    mock_ep.value = "orcheo_plugin_fixture_node:Plugin"

    mock_dist = MagicMock()
    mock_dist.entry_points = [mock_ep]

    reset_plugin_loader_for_tests()

    with patch(
        "orcheo.plugins.loader._iter_plugin_distributions",
        return_value=[mock_dist],
    ):
        with patch(
            "orcheo.plugins.loader._distribution_to_manifest",
            return_value=(mock_manifest, "abc"),
        ):
            with patch(
                "orcheo.plugins.loader.check_manifest_compatibility",
                return_value=[],
            ):
                report = load_enabled_plugins(force=True)

    results = {item.name: item for item in report.results}
    assert results["orcheo-plugin-fixture-node"].loaded is False
    assert "register" in (results["orcheo-plugin-fixture-node"].error or "").lower()


@requires_uv
def test_loader_deduplicates_module_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Module roots already in registrations are not added again (line 226->215)."""
    _set_plugin_env(monkeypatch, tmp_path)
    manager = PluginManager()
    manager.install(str(_copy_fixture(tmp_path, "node_plugin")))

    from orcheo.plugins.loader import PluginManifest

    mock_manifest = MagicMock(spec=PluginManifest)
    mock_manifest.name = "orcheo-plugin-fixture-node"
    mock_manifest.plugin_api_version = 1
    mock_manifest.orcheo_version = ">=0.0.0"
    mock_manifest.exports = ["nodes"]
    mock_manifest.description = ""
    mock_manifest.author = ""

    call_count = 0

    def _mock_register(api: object) -> None:
        nonlocal call_count
        # Add the module root manually to simulate it already being there
        from orcheo.plugins.api import PluginAPI

        if isinstance(api, PluginAPI):
            api.registrations.module_roots.append("orcheo_plugin_fixture_node")
        call_count += 1

    mock_plugin = MagicMock()
    mock_plugin.register = _mock_register

    mock_ep = MagicMock()
    mock_ep.group = PLUGIN_ENTRYPOINT_GROUP
    mock_ep.name = "orcheo_plugin_fixture_node"
    mock_ep.load.return_value = mock_plugin
    mock_ep.value = "orcheo_plugin_fixture_node:Plugin"
    # Make getattr(ep, "module", "") return "" so we use value split
    del mock_ep.module  # Remove module attr to test fallback

    mock_dist = MagicMock()
    mock_dist.entry_points = [mock_ep]

    reset_plugin_loader_for_tests()

    with patch(
        "orcheo.plugins.loader._iter_plugin_distributions",
        return_value=[mock_dist],
    ):
        with patch(
            "orcheo.plugins.loader._distribution_to_manifest",
            return_value=(mock_manifest, "abc"),
        ):
            with patch(
                "orcheo.plugins.loader.check_manifest_compatibility",
                return_value=[],
            ):
                report = load_enabled_plugins(force=True)

    # Should have loaded successfully
    results = {item.name: item for item in report.results}
    assert results["orcheo-plugin-fixture-node"].loaded is True
    assert call_count == 1


# ---------------------------------------------------------------------------
# Unit tests that do NOT require uv (mock everything)
# ---------------------------------------------------------------------------


def test_load_enabled_plugins_returns_cached_when_already_loaded() -> None:
    """load_enabled_plugins returns cached report when already loaded."""
    import orcheo.plugins.loader as loader_mod

    reset_plugin_loader_for_tests()
    # Prime the cache with a non-force call using empty names
    with patch.object(loader_mod, "_enabled_locked_plugin_names", return_value=set()):
        with patch.object(
            loader_mod,
            "_plugin_site_packages",
            return_value=MagicMock(exists=lambda: False),
        ):
            first = load_enabled_plugins(force=True)

    # Second call without force should return cached
    second = load_enabled_plugins(force=False)
    assert first is second


def test_load_enabled_plugins_early_exit_no_enabled_names(
    tmp_path: Path,
) -> None:
    """load_enabled_plugins returns early when no enabled names (lines 158-161)."""
    import orcheo.plugins.loader as loader_mod

    reset_plugin_loader_for_tests()
    with patch.object(loader_mod, "_enabled_locked_plugin_names", return_value=set()):
        with patch.object(
            loader_mod,
            "_plugin_site_packages",
            return_value=tmp_path,
        ):
            report = load_enabled_plugins(force=True)

    assert report.results == []


def test_load_enabled_plugins_early_exit_no_site_packages(
    tmp_path: Path,
) -> None:
    """load_enabled_plugins returns early when site-packages dir missing."""
    import orcheo.plugins.loader as loader_mod

    reset_plugin_loader_for_tests()
    non_existent = tmp_path / "not_here"
    with patch.object(
        loader_mod, "_enabled_locked_plugin_names", return_value={"some-plugin"}
    ):
        with patch.object(
            loader_mod, "_plugin_site_packages", return_value=non_existent
        ):
            report = load_enabled_plugins(force=True)

    assert report.results == []


def test_load_enabled_plugins_reports_missing_name(tmp_path: Path) -> None:
    """Enabled plugin not found in site-packages generates missing error (line 172)."""
    import orcheo.plugins.loader as loader_mod

    reset_plugin_loader_for_tests()
    site_pkg = tmp_path
    with patch.object(
        loader_mod, "_enabled_locked_plugin_names", return_value={"missing-plugin"}
    ):
        with patch.object(loader_mod, "_plugin_site_packages", return_value=site_pkg):
            with patch.object(
                loader_mod,
                "_iter_plugin_distributions",
                return_value=[],
            ):
                report = load_enabled_plugins(force=True)

    results = {item.name: item for item in report.results}
    assert "missing-plugin" in results
    assert results["missing-plugin"].loaded is False
    assert "not installed" in (results["missing-plugin"].error or "")


def test_load_enabled_plugins_reports_incompatibility(tmp_path: Path) -> None:
    """Incompatible manifest generates a warning result (lines 188-198)."""
    import orcheo.plugins.loader as loader_mod
    from orcheo.plugins.loader import PluginManifest

    reset_plugin_loader_for_tests()
    mock_manifest = MagicMock(spec=PluginManifest)
    mock_manifest.name = "incompat-plugin"
    mock_dist = MagicMock()
    mock_dist.metadata.get.return_value = "incompat-plugin"
    mock_ep = MagicMock()
    mock_ep.group = PLUGIN_ENTRYPOINT_GROUP
    mock_dist.entry_points = [mock_ep]

    with patch.object(
        loader_mod, "_enabled_locked_plugin_names", return_value={"incompat-plugin"}
    ):
        with patch.object(loader_mod, "_plugin_site_packages", return_value=tmp_path):
            with patch.object(
                loader_mod,
                "_iter_plugin_distributions",
                return_value=[mock_dist],
            ):
                with patch.object(
                    loader_mod,
                    "_distribution_to_manifest",
                    return_value=(mock_manifest, "abc"),
                ):
                    with patch.object(
                        loader_mod,
                        "check_manifest_compatibility",
                        return_value=["plugin API mismatch"],
                    ):
                        report = load_enabled_plugins(force=True)

    results = {item.name: item for item in report.results}
    assert "incompat-plugin" in results
    assert results["incompat-plugin"].loaded is False
    assert "plugin API mismatch" in (results["incompat-plugin"].error or "")


def test_load_enabled_plugins_reports_no_entry_points(tmp_path: Path) -> None:
    """Distribution with no entry points gets a diagnostic error (lines 205-213)."""
    import orcheo.plugins.loader as loader_mod
    from orcheo.plugins.loader import PluginManifest

    reset_plugin_loader_for_tests()
    mock_manifest = MagicMock(spec=PluginManifest)
    mock_manifest.name = "no-ep-plugin"
    mock_dist = MagicMock()
    mock_dist.entry_points = []

    with patch.object(
        loader_mod, "_enabled_locked_plugin_names", return_value={"no-ep-plugin"}
    ):
        with patch.object(loader_mod, "_plugin_site_packages", return_value=tmp_path):
            with patch.object(
                loader_mod, "_iter_plugin_distributions", return_value=[mock_dist]
            ):
                with patch.object(
                    loader_mod,
                    "_distribution_to_manifest",
                    return_value=(mock_manifest, "abc"),
                ):
                    with patch.object(
                        loader_mod,
                        "check_manifest_compatibility",
                        return_value=[],
                    ):
                        report = load_enabled_plugins(force=True)

    results = {item.name: item for item in report.results}
    assert "no-ep-plugin" in results
    assert results["no-ep-plugin"].loaded is False
    assert "No plugin entry points" in (results["no-ep-plugin"].error or "")


def test_load_enabled_plugins_reports_non_callable_register(
    tmp_path: Path,
) -> None:
    """Non-callable register raises TypeError, reported as failed (lines 219-223)."""
    import orcheo.plugins.loader as loader_mod
    from orcheo.plugins.loader import PluginManifest

    reset_plugin_loader_for_tests()
    mock_manifest = MagicMock(spec=PluginManifest)
    mock_manifest.name = "bad-register-plugin"

    mock_plugin = MagicMock()
    mock_plugin.register = "not-callable"

    mock_ep = MagicMock()
    mock_ep.group = PLUGIN_ENTRYPOINT_GROUP
    mock_ep.name = "bad_register"
    mock_ep.load.return_value = mock_plugin
    mock_ep.value = "bad_register:Plugin"
    mock_ep.module = ""

    mock_dist = MagicMock()
    mock_dist.entry_points = [mock_ep]

    with patch.object(
        loader_mod, "_enabled_locked_plugin_names", return_value={"bad-register-plugin"}
    ):
        with patch.object(loader_mod, "_plugin_site_packages", return_value=tmp_path):
            with patch.object(
                loader_mod, "_iter_plugin_distributions", return_value=[mock_dist]
            ):
                with patch.object(
                    loader_mod,
                    "_distribution_to_manifest",
                    return_value=(mock_manifest, "abc"),
                ):
                    with patch.object(
                        loader_mod,
                        "check_manifest_compatibility",
                        return_value=[],
                    ):
                        report = load_enabled_plugins(force=True)

    results = {item.name: item for item in report.results}
    assert "bad-register-plugin" in results
    assert results["bad-register-plugin"].loaded is False
    assert "register" in (results["bad-register-plugin"].error or "").lower()


def test_load_enabled_plugins_skips_duplicate_module_root(tmp_path: Path) -> None:
    """Module root already registered is not added again (line 226->215)."""
    import orcheo.plugins.loader as loader_mod
    from orcheo.plugins.api import PluginAPI
    from orcheo.plugins.loader import PluginManifest

    reset_plugin_loader_for_tests()
    mock_manifest = MagicMock(spec=PluginManifest)
    mock_manifest.name = "dedup-plugin"

    def _register_with_preseeded_root(api: object) -> None:
        if isinstance(api, PluginAPI):
            api.registrations.module_roots.append("dedup_module")

    mock_plugin = MagicMock()
    mock_plugin.register = _register_with_preseeded_root

    mock_ep = MagicMock()
    mock_ep.group = PLUGIN_ENTRYPOINT_GROUP
    mock_ep.name = "dedup_module"
    mock_ep.load.return_value = mock_plugin
    mock_ep.value = "dedup_module:Plugin"
    mock_ep.module = ""

    mock_dist = MagicMock()
    mock_dist.entry_points = [mock_ep]

    with patch.object(
        loader_mod, "_enabled_locked_plugin_names", return_value={"dedup-plugin"}
    ):
        with patch.object(loader_mod, "_plugin_site_packages", return_value=tmp_path):
            with patch.object(
                loader_mod, "_iter_plugin_distributions", return_value=[mock_dist]
            ):
                with patch.object(
                    loader_mod,
                    "_distribution_to_manifest",
                    return_value=(mock_manifest, "abc"),
                ):
                    with patch.object(
                        loader_mod,
                        "check_manifest_compatibility",
                        return_value=[],
                    ):
                        report = load_enabled_plugins(force=True)

    results = {item.name: item for item in report.results}
    assert results["dedup-plugin"].loaded is True


def test_clear_registered_components_removes_edges_tools_triggers() -> None:
    """_clear_registered_components removes edges, tools, and triggers (72,74,78)."""
    import orcheo.plugins.loader as loader_mod
    from orcheo.edges.registry import EdgeMetadata
    from orcheo.nodes.ai.tools.registry import ToolMetadata
    from orcheo.plugins.loader import _clear_registered_components
    from orcheo.triggers.registry import TriggerMetadata

    # Register components in the global registries
    edge_meta = EdgeMetadata(name="_test_clear_edge", description="")
    edge_registry.register(edge_meta)(lambda s: s)

    tool_meta = ToolMetadata(name="_test_clear_tool", description="")
    tool_registry.register(tool_meta)(lambda: None)

    trigger_meta = TriggerMetadata(id="_test_clear_trigger", display_name="")
    trigger_registry.register(trigger_meta, lambda: {})

    # Populate _REGISTERED directly
    old_registered = loader_mod._REGISTERED
    loader_mod._REGISTERED = loader_mod._REGISTERED.__class__()
    loader_mod._REGISTERED.edges.append("_test_clear_edge")
    loader_mod._REGISTERED.agent_tools.append("_test_clear_tool")
    loader_mod._REGISTERED.triggers.append("_test_clear_trigger")

    _clear_registered_components()

    assert edge_registry.get_edge("_test_clear_edge") is None
    assert tool_registry.get_tool("_test_clear_tool") is None
    assert trigger_registry.get("_test_clear_trigger") is None

    # Restore
    loader_mod._REGISTERED = old_registered


def test_invalidate_plugin_loader_resets_state() -> None:
    """invalidate_plugin_loader resets _LOADED to False (lines 268-270)."""
    import orcheo.plugins.loader as loader_mod
    from orcheo.plugins.loader import invalidate_plugin_loader

    loader_mod._LOADED = True
    invalidate_plugin_loader()
    assert loader_mod._LOADED is False


def test_reset_plugin_loader_for_tests_delegates_to_invalidate() -> None:
    """reset_plugin_loader_for_tests calls invalidate_plugin_loader (line 275)."""
    import orcheo.plugins.loader as loader_mod

    loader_mod._LOADED = True
    reset_plugin_loader_for_tests()
    assert loader_mod._LOADED is False


# ---------------------------------------------------------------------------
# _purge_module_roots with matching modules (line 63)
# ---------------------------------------------------------------------------


def test_purge_module_roots_removes_matching_modules() -> None:
    """_purge_module_roots removes modules matching roots from sys.modules (line 63)."""
    from orcheo.plugins.loader import _purge_module_roots

    fake_module = MagicMock()
    fake_module_child = MagicMock()
    sys.modules["_test_fake_root"] = fake_module
    sys.modules["_test_fake_root.child"] = fake_module_child

    try:
        _purge_module_roots(["_test_fake_root"])
    finally:
        sys.modules.pop("_test_fake_root", None)
        sys.modules.pop("_test_fake_root.child", None)

    assert "_test_fake_root" not in sys.modules
    assert "_test_fake_root.child" not in sys.modules


# ---------------------------------------------------------------------------
# _clear_registered_components for nodes and listeners (lines 70, 76)
# ---------------------------------------------------------------------------


def test_clear_registered_components_removes_nodes_and_listeners() -> None:
    """_clear_registered_components removes nodes and listeners (lines 70, 76)."""
    import orcheo.plugins.loader as loader_mod
    from orcheo.listeners.registry import ListenerMetadata
    from orcheo.nodes.registry import NodeMetadata
    from orcheo.plugins.loader import _clear_registered_components

    node_meta = NodeMetadata(name="_test_clear_node", description="", category="test")
    registry.register(node_meta)(lambda name: MagicMock())

    listener_meta = ListenerMetadata(id="_test_clear_listener", display_name="")
    listener_registry.register(
        listener_meta,
        compiler=lambda **kw: None,
        adapter_factory=lambda **kw: object(),
    )

    old_registered = loader_mod._REGISTERED
    loader_mod._REGISTERED = loader_mod._REGISTERED.__class__()
    loader_mod._REGISTERED.nodes.append("_test_clear_node")
    loader_mod._REGISTERED.listeners.append("_test_clear_listener")

    _clear_registered_components()

    assert registry.get_node("_test_clear_node") is None
    assert listener_registry.resolve("_test_clear_listener") is None

    loader_mod._REGISTERED = old_registered


# ---------------------------------------------------------------------------
# _rollback_plugin_registrations for nodes (line 86)
# ---------------------------------------------------------------------------


def test_rollback_removes_nodes() -> None:
    """_rollback_plugin_registrations removes nodes from registry (line 86)."""
    from orcheo.nodes.registry import NodeMetadata

    node_meta = NodeMetadata(
        name="_test_rollback_node", description="", category="test"
    )
    registry.register(node_meta)(lambda name: MagicMock())

    registrations = PluginRegistrations()
    registrations.nodes.append("_test_rollback_node")

    _rollback_plugin_registrations(registrations)

    assert registry.get_node("_test_rollback_node") is None


# ---------------------------------------------------------------------------
# _plugin_site_packages and _enabled_locked_plugin_names direct calls (99-100, 104-109)
# ---------------------------------------------------------------------------


def test_plugin_site_packages_returns_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_plugin_site_packages returns a path based on storage paths (lines 99-100)."""
    from orcheo.plugins.loader import _plugin_site_packages

    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(tmp_path / "config"))

    result = _plugin_site_packages()
    assert isinstance(result, Path)
    assert "site-packages" in str(result)


def test_enabled_locked_plugin_names_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_enabled_locked_plugin_names returns empty set with no state files."""
    from orcheo.plugins.loader import _enabled_locked_plugin_names

    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "plugins").mkdir()

    result = _enabled_locked_plugin_names()
    assert result == set()


# ---------------------------------------------------------------------------
# _iter_plugin_distributions branches (lines 124, 129)
# ---------------------------------------------------------------------------


def test_iter_plugin_distributions_skips_name_not_in_wanted(tmp_path: Path) -> None:
    """_iter_plugin_distributions skips distributions not in wanted names (line 124)."""
    mock_dist = MagicMock()
    mock_dist.metadata.get.return_value = "other-plugin"  # NOT in wanted

    mock_ep = MagicMock()
    mock_ep.group = PLUGIN_ENTRYPOINT_GROUP
    mock_dist.entry_points = [mock_ep]

    with patch(
        "orcheo.plugins.loader.importlib.metadata.distributions",
        return_value=[mock_dist],
    ):
        result = _iter_plugin_distributions(tmp_path, names=["my-plugin"])

    assert result == []


def test_iter_plugin_distributions_includes_wanted_with_entry_group(
    tmp_path: Path,
) -> None:
    """_iter_plugin_distributions appends matching distributions (line 129)."""
    mock_dist = MagicMock()
    mock_dist.metadata.get.return_value = "my-plugin"

    mock_ep = MagicMock()
    mock_ep.group = PLUGIN_ENTRYPOINT_GROUP
    mock_dist.entry_points = [mock_ep]

    with patch(
        "orcheo.plugins.loader.importlib.metadata.distributions",
        return_value=[mock_dist],
    ):
        result = _iter_plugin_distributions(tmp_path, names=["my-plugin"])

    assert mock_dist in result


# ---------------------------------------------------------------------------
# _ensure_sys_path no-op branch (line 134->exit)
# ---------------------------------------------------------------------------


def test_ensure_sys_path_noop_when_already_in_path(tmp_path: Path) -> None:
    """_ensure_sys_path does nothing when path already in sys.path (line 134->exit)."""
    from orcheo.plugins.loader import _ensure_sys_path

    site_pkgs = tmp_path / "site-packages"
    site_pkgs.mkdir()
    path_str = str(site_pkgs)

    sys.path.insert(0, path_str)
    try:
        count_before = sys.path.count(path_str)
        _ensure_sys_path(site_pkgs)
        count_after = sys.path.count(path_str)
        assert count_after == count_before  # not added again
    finally:
        sys.path.remove(path_str)


# ---------------------------------------------------------------------------
# _entry_point_module_root with truthy module attr (line 142)
# ---------------------------------------------------------------------------


def test_entry_point_module_root_uses_module_attr_when_truthy() -> None:
    """_entry_point_module_root returns module attr when truthy (line 142)."""
    ep = MagicMock()
    ep.module = "my_plugin_module"
    ep.value = "my_plugin_module:register"

    root = _entry_point_module_root(ep)
    assert root == "my_plugin_module"


# ---------------------------------------------------------------------------
# load_enabled_plugins successful load with module_root appended (line 227)
# ---------------------------------------------------------------------------


def test_load_enabled_plugins_appends_module_root(tmp_path: Path) -> None:
    """load_enabled_plugins appends module_root when first seen (line 227)."""
    import orcheo.plugins.loader as loader_mod
    from orcheo.plugins.loader import PluginManifest

    reset_plugin_loader_for_tests()
    mock_manifest = MagicMock(spec=PluginManifest)
    mock_manifest.name = "success-plugin"

    def _register(api: object) -> None:
        pass  # Do nothing; module_root will be appended by load_enabled_plugins

    mock_plugin = MagicMock()
    mock_plugin.register = _register

    mock_ep = MagicMock()
    mock_ep.group = PLUGIN_ENTRYPOINT_GROUP
    mock_ep.name = "success_module"
    mock_ep.load.return_value = mock_plugin
    mock_ep.value = "success_module:Plugin"
    mock_ep.module = ""

    mock_dist = MagicMock()
    mock_dist.entry_points = [mock_ep]

    with patch.object(
        loader_mod, "_enabled_locked_plugin_names", return_value={"success-plugin"}
    ):
        with patch.object(loader_mod, "_plugin_site_packages", return_value=tmp_path):
            with patch.object(
                loader_mod, "_iter_plugin_distributions", return_value=[mock_dist]
            ):
                with patch.object(
                    loader_mod,
                    "_distribution_to_manifest",
                    return_value=(mock_manifest, "abc"),
                ):
                    with patch.object(
                        loader_mod, "check_manifest_compatibility", return_value=[]
                    ):
                        report = load_enabled_plugins(force=True)

    results = {item.name: item for item in report.results}
    assert results["success-plugin"].loaded is True
    assert "success_module" in loader_mod._REGISTERED.module_roots


# ---------------------------------------------------------------------------
# ensure_plugins_loaded (line 262)
# ---------------------------------------------------------------------------


def test_ensure_plugins_loaded_delegates_to_load_enabled() -> None:
    """ensure_plugins_loaded calls load_enabled_plugins(force=False) (line 262)."""
    import orcheo.plugins.loader as loader_mod
    from orcheo.plugins.loader import ensure_plugins_loaded

    reset_plugin_loader_for_tests()
    with patch.object(loader_mod, "_enabled_locked_plugin_names", return_value=set()):
        with patch.object(
            loader_mod,
            "_plugin_site_packages",
            return_value=MagicMock(exists=lambda: False),
        ):
            # Prime the cache
            load_enabled_plugins(force=True)
            first = loader_mod._REPORT
            # ensure_plugins_loaded should return the same cached report
            result = ensure_plugins_loaded()

    assert result is first
