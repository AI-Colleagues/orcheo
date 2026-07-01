"""Tests for plugin manager helper functions and PluginManager public API."""

from __future__ import annotations
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from orcheo.plugins.manager import (
    PluginError,
    PluginManager,
    _distribution_to_manifest,
    _find_manifest_file,
    _install_refs_into_venv,
    _load_manifest_payload,
    _replace_directory,
    _temporary_sys_path,
    hash_install_source,
)
from orcheo.plugins.models import (
    PLUGIN_API_VERSION,
    DesiredPluginRecord,
    LockedPluginRecord,
    PluginManifest,
    PluginStoragePaths,
)
from orcheo.plugins.state import save_desired_state, save_lock_state
from tests.plugin_fast_install import install_fast_fixture_plugins


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "plugin_fixtures"

_uv_available = shutil.which("uv") is not None
requires_uv = pytest.mark.skipif(not _uv_available, reason="uv not installed")


@pytest.fixture(autouse=True)
def _fast_plugin_fixture_installs(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if request.node.get_closest_marker("skipif") is not None:
        install_fast_fixture_plugins(monkeypatch)


def _copy_fixture(tmp_path: Path, fixture_name: str) -> Path:
    source = FIXTURE_ROOT / fixture_name
    destination = tmp_path / fixture_name
    shutil.copytree(source, destination)
    return destination


def _make_manager(tmp_path: Path) -> PluginManager:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    paths = PluginStoragePaths(
        plugin_dir=str(plugin_dir),
        state_file=str(plugin_dir / "plugins.toml"),
        lock_file=str(plugin_dir / "plugin-lock.toml"),
        install_dir=str(plugin_dir / "venv"),
        wheels_dir=str(plugin_dir / "wheels"),
        manifests_dir=str(plugin_dir / "manifests"),
        cache_dir=str(plugin_dir / "cache"),
        downloads_dir=str(plugin_dir / "downloads"),
        metadata_dir=str(plugin_dir / "metadata"),
    )
    return PluginManager(paths)


def _make_desired(
    name: str,
    source: str = "/fake/path",
    *,
    enabled: bool = True,
) -> DesiredPluginRecord:
    return DesiredPluginRecord(name=name, source=source, enabled=enabled)


def _make_locked(name: str, version: str = "1.0.0") -> LockedPluginRecord:
    return LockedPluginRecord(
        name=name,
        version=version,
        plugin_api_version=1,
        orcheo_version=">=0.0.0",
        location="/fake",
        wheel_sha256="",
        manifest_sha256="abc",
        exports=["nodes"],
        description="",
        author="",
        entry_points=[],
    )


# ---------------------------------------------------------------------------
# hash_install_source (lines 854, 857)
# ---------------------------------------------------------------------------


def test_hash_install_source_file(tmp_path: Path) -> None:
    """hash_install_source returns SHA-256 for a file path (line 854)."""
    f = tmp_path / "myfile.whl"
    f.write_bytes(b"binary content")
    result = hash_install_source(str(f))
    assert len(result) == 64


def test_hash_install_source_non_path_string() -> None:
    """hash_install_source hashes the source string when it's not a path (line 857)."""
    result = hash_install_source("https://github.com/example/plugin.git")
    assert len(result) == 64
    import hashlib

    expected = hashlib.sha256(b"https://github.com/example/plugin.git").hexdigest()
    assert result == expected


# ---------------------------------------------------------------------------
# _load_manifest_payload (line 98)
# ---------------------------------------------------------------------------


def test_load_manifest_payload_nested_tool_section(tmp_path: Path) -> None:
    """_load_manifest_payload falls back to [tool.orcheo.plugin] when keys absent."""
    toml_content = (
        "[tool.orcheo.plugin]\n"
        "plugin_api_version = 1\n"
        'orcheo_version = ">=0.0.0"\n'
        'exports = ["nodes"]\n'
    )
    manifest_file = tmp_path / "pyproject.toml"
    manifest_file.write_text(toml_content, encoding="utf-8")
    payload = _load_manifest_payload(manifest_file)
    assert payload["plugin_api_version"] == 1
    assert payload["exports"] == ["nodes"]


# ---------------------------------------------------------------------------
# _temporary_sys_path (lines 110-111)
# ---------------------------------------------------------------------------


def test_temporary_sys_path_handles_value_error() -> None:
    """_temporary_sys_path silently ignores ValueError on remove (lines 110-111)."""
    fake_path = Path("/nonexistent/fake/path")
    with _temporary_sys_path(fake_path):
        # Remove the path manually to simulate concurrent modification
        try:
            sys.path.remove(str(fake_path))
        except ValueError:
            pass
    # No exception should be raised from the context manager's finally block


# ---------------------------------------------------------------------------
# _find_manifest_file (lines 120-123)
# ---------------------------------------------------------------------------


def test_find_manifest_file_falls_back_to_pyproject(tmp_path: Path) -> None:
    """_find_manifest_file finds pyproject.toml when orcheo_plugin.toml absent."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool]\n", encoding="utf-8")

    mock_file_orcheo = MagicMock()
    mock_file_orcheo.name = "other_file.txt"

    mock_file_pyproject = MagicMock()
    mock_file_pyproject.name = "pyproject.toml"

    mock_dist = MagicMock()
    mock_dist.files = [mock_file_orcheo, mock_file_pyproject]
    mock_dist.locate_file.return_value = pyproject

    result = _find_manifest_file(mock_dist)
    assert result == pyproject


def test_find_manifest_file_returns_none_when_not_found() -> None:
    """_find_manifest_file returns None when neither manifest file is found."""
    mock_file = MagicMock()
    mock_file.name = "README.md"

    mock_dist = MagicMock()
    mock_dist.files = [mock_file]

    result = _find_manifest_file(mock_dist)
    assert result is None


# ---------------------------------------------------------------------------
# _distribution_to_manifest (lines 132-133, 136-137)
# ---------------------------------------------------------------------------


def test_distribution_to_manifest_missing_file_raises() -> None:
    """_distribution_to_manifest raises PluginError when manifest file not found."""
    mock_dist = MagicMock()
    mock_dist.metadata.__getitem__ = lambda self, key: "my-plugin"

    with patch("orcheo.plugins.manager._find_manifest_file", return_value=None):
        with pytest.raises(PluginError, match="missing orcheo_plugin.toml"):
            _distribution_to_manifest(mock_dist)


def test_distribution_to_manifest_empty_payload_raises(tmp_path: Path) -> None:
    """_distribution_to_manifest raises PluginError for empty manifest."""
    empty_file = tmp_path / "orcheo_plugin.toml"
    empty_file.write_text("", encoding="utf-8")

    mock_dist = MagicMock()
    mock_dist.metadata.__getitem__ = lambda self, key: "my-plugin"
    mock_dist.entry_points = []

    with patch("orcheo.plugins.manager._find_manifest_file", return_value=empty_file):
        with patch("orcheo.plugins.manager._load_manifest_payload", return_value={}):
            with pytest.raises(PluginError, match="empty plugin manifest"):
                _distribution_to_manifest(mock_dist)


# ---------------------------------------------------------------------------
# _install_refs_into_venv (lines 204, 215)
# ---------------------------------------------------------------------------


def test_install_refs_into_venv_empty_refs_returns_early(tmp_path: Path) -> None:
    """_install_refs_into_venv returns immediately when refs is empty (line 204)."""
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    # Should not raise or call any subprocess
    with patch("orcheo.plugins.manager._run_command") as mock_cmd:
        _install_refs_into_venv(venv_dir, [])
    mock_cmd.assert_not_called()


def test_install_refs_into_venv_error_raises(tmp_path: Path) -> None:
    """_install_refs_into_venv raises PluginError when uv pip fails (line 215)."""
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "ERROR: Could not find package foo"

    with patch("orcheo.plugins.manager._run_command", return_value=mock_result):
        with pytest.raises(PluginError, match="Could not find package foo"):
            _install_refs_into_venv(venv_dir, ["foo==1.0.0"])


def test_manager_install_passes_progress_callback(tmp_path: Path) -> None:
    """PluginManager.install forwards progress callbacks to the build step."""
    manager = _make_manager(tmp_path)
    progress = MagicMock()

    with patch.object(
        manager,
        "_activate_build",
        return_value=(
            [
                PluginManifest(
                    name="plugin-a",
                    version="1.0.0",
                    description="desc",
                    author="author",
                    plugin_api_version=PLUGIN_API_VERSION,
                    orcheo_version=">=0.0.0",
                    exports=["nodes"],
                )
            ],
            [_make_locked("plugin-a")],
        ),
    ) as mocked_activate:
        with patch.object(manager, "_reconcile_desired_and_lock"):
            with patch.object(
                manager,
                "show_plugin",
                return_value={"name": "plugin-a"},
            ):
                manager.install("plugin-a", progress=progress)

    assert mocked_activate.call_args.kwargs["progress"] is progress


# ---------------------------------------------------------------------------
# _replace_directory (line 298)
# ---------------------------------------------------------------------------


def test_replace_directory_removes_pre_existing_backup(tmp_path: Path) -> None:
    """_replace_directory removes a pre-existing backup dir (line 298)."""
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "dest"
    destination.mkdir()
    backup = tmp_path / "dest.bak"
    backup.mkdir()
    (backup / "old.txt").write_text("old content", encoding="utf-8")

    _replace_directory(source, destination)

    assert not backup.exists()
    assert destination.exists()


# ---------------------------------------------------------------------------
# PluginManager.list_plugins (line 332)
# ---------------------------------------------------------------------------


def test_list_plugins_error_status_when_enabled_but_no_lock(tmp_path: Path) -> None:
    """list_plugins sets status=error when plugin is enabled but has no lock record."""
    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("my-plugin", enabled=True)])
    # No lock state → lock file empty

    rows = manager.list_plugins()
    assert len(rows) == 1
    assert rows[0]["status"] == "error"


# ---------------------------------------------------------------------------
# PluginManager.show_plugin (line 352)
# ---------------------------------------------------------------------------


def test_show_plugin_raises_for_unknown(tmp_path: Path) -> None:
    """show_plugin raises PluginError for plugins not in desired or locked state."""
    manager = _make_manager(tmp_path)
    with pytest.raises(PluginError, match="nonexistent"):
        manager.show_plugin("nonexistent")


# ---------------------------------------------------------------------------
# PluginManager.preview_update (lines 510, 518)
# ---------------------------------------------------------------------------


def test_preview_update_raises_for_unknown_plugin(tmp_path: Path) -> None:
    """preview_update raises PluginError when plugin is not installed (line 510)."""
    manager = _make_manager(tmp_path)
    with pytest.raises(PluginError, match="not installed"):
        manager.preview_update("nonexistent")


# ---------------------------------------------------------------------------
# PluginManager.update_all / preview_update_all (lines 529-549, 553-561)
# ---------------------------------------------------------------------------


def test_preview_update_all_empty_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """preview_update_all returns empty list when no plugins are configured."""
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(tmp_path / "config"))
    for d in ["plugins", "cache", "config"]:
        (tmp_path / d).mkdir()

    manager = PluginManager()
    with patch("orcheo.plugins.manager._ensure_venv"):
        result = manager.preview_update_all()
    assert result == []


def test_update_all_empty_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """update_all returns empty list when no plugins are configured."""
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(tmp_path / "config"))
    for d in ["plugins", "cache", "config"]:
        (tmp_path / d).mkdir()

    manager = PluginManager()
    with patch.object(manager, "_activate_build", return_value=([], [])):
        result = manager.update_all()
    assert result == []


# ---------------------------------------------------------------------------
# PluginManager.preview_uninstall (lines 590, 592)
# ---------------------------------------------------------------------------


def test_preview_uninstall_raises_when_not_desired(tmp_path: Path) -> None:
    """preview_uninstall raises PluginError when plugin not in desired state."""
    manager = _make_manager(tmp_path)
    with pytest.raises(PluginError, match="not installed"):
        manager.preview_uninstall("nonexistent")


def test_preview_uninstall_raises_when_not_locked(tmp_path: Path) -> None:
    """preview_uninstall raises PluginError when plugin not in lock state (line 592)."""
    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("my-plugin")])
    # No lock record

    with pytest.raises(PluginError, match="not locked"):
        manager.preview_uninstall("my-plugin")


# ---------------------------------------------------------------------------
# PluginManager.preview_set_enabled (lines 627, 629)
# ---------------------------------------------------------------------------


def test_preview_set_enabled_raises_when_not_desired(tmp_path: Path) -> None:
    """preview_set_enabled raises PluginError when plugin not installed (line 627)."""
    manager = _make_manager(tmp_path)
    with pytest.raises(PluginError, match="not installed"):
        manager.preview_set_enabled("nonexistent", enabled=True)


def test_preview_set_enabled_raises_when_not_locked(tmp_path: Path) -> None:
    """preview_set_enabled raises PluginError when plugin not locked (line 629)."""
    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("my-plugin")])
    # No lock record

    with pytest.raises(PluginError, match="not locked"):
        manager.preview_set_enabled("my-plugin", enabled=False)


# ---------------------------------------------------------------------------
# PluginManager.doctor (lines 666-690, 693-808)
# ---------------------------------------------------------------------------


def test_doctor_reports_venv_version_check_when_venv_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """doctor checks venv python version when venv exists (lines 666-690)."""
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(tmp_path / "config"))
    for d in ["plugins", "cache", "config"]:
        (tmp_path / d).mkdir()

    manager = PluginManager()
    # Set up a fake venv with a python binary so the branch triggers
    venv_python = manager.install_dir / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()

    current_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    mock_result = MagicMock()
    mock_result.stdout = current_version

    with patch("orcheo.plugins.manager._run_command", return_value=mock_result):
        report = manager.doctor()

    check_names = {check.name for check in report.checks}
    assert "plugin_venv_python_version" in check_names


def test_doctor_checks_site_packages_when_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """doctor runs site-packages checks when venv exists (lines 693-808)."""
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(tmp_path / "config"))
    for d in ["plugins", "cache", "config"]:
        (tmp_path / d).mkdir()

    manager = PluginManager()

    # Create fake site-packages and a plugin in desired + locked state
    site_pkgs = (
        manager.install_dir
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    site_pkgs.mkdir(parents=True)

    save_desired_state(manager.state_file, [_make_desired("my-plugin")])
    save_lock_state(manager.lock_file, [_make_locked("my-plugin")])

    with patch("orcheo.plugins.manager._import_plugin_entry_points", return_value={}):
        with patch("orcheo.plugins.manager._load_plugin_manifests", return_value=[]):
            with patch(
                "orcheo.plugins.manager._find_disabled_dependencies", return_value={}
            ):
                report = manager.doctor()

    check_names = {check.name for check in report.checks}
    assert "plugin_importable:my-plugin" in check_names
    assert "lock_consistency:my-plugin" in check_names


def test_doctor_skips_disabled_plugins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """doctor skips detailed checks for disabled plugins (line 718)."""
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(tmp_path / "config"))
    for d in ["plugins", "cache", "config"]:
        (tmp_path / d).mkdir()

    manager = PluginManager()
    site_pkgs = (
        manager.install_dir
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    site_pkgs.mkdir(parents=True)

    save_desired_state(manager.state_file, [_make_desired("my-plugin", enabled=False)])
    save_lock_state(manager.lock_file, [_make_locked("my-plugin")])

    with patch("orcheo.plugins.manager._import_plugin_entry_points", return_value={}):
        with patch("orcheo.plugins.manager._load_plugin_manifests", return_value=[]):
            with patch(
                "orcheo.plugins.manager._find_disabled_dependencies", return_value={}
            ):
                report = manager.doctor()

    check_names = {check.name for check in report.checks}
    # disabled plugin should not have importable check (line 718 skip)
    assert "plugin_importable:my-plugin" not in check_names


def test_doctor_skips_lock_consistency_when_no_lock_or_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """doctor skips detailed checks when lock_record or manifest_entry is None."""
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(tmp_path / "config"))
    for d in ["plugins", "cache", "config"]:
        (tmp_path / d).mkdir()

    manager = PluginManager()
    site_pkgs = (
        manager.install_dir
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    site_pkgs.mkdir(parents=True)

    save_desired_state(manager.state_file, [_make_desired("my-plugin")])
    # No lock record → lock_record is None → should continue (skip manifest checks)

    with patch("orcheo.plugins.manager._import_plugin_entry_points", return_value={}):
        with patch("orcheo.plugins.manager._load_plugin_manifests", return_value=[]):
            with patch(
                "orcheo.plugins.manager._find_disabled_dependencies", return_value={}
            ):
                report = manager.doctor()

    check_names = {check.name for check in report.checks}
    # lock_consistency check should be present but manifest_sha256 should not
    assert "lock_consistency:my-plugin" in check_names
    assert "manifest_sha256:my-plugin" not in check_names


def test_doctor_adds_disabled_dependency_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """doctor adds a warning when a disabled plugin is required by an enabled one."""
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(tmp_path / "config"))
    for d in ["plugins", "cache", "config"]:
        (tmp_path / d).mkdir()

    manager = PluginManager()
    site_pkgs = (
        manager.install_dir
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    site_pkgs.mkdir(parents=True)

    save_desired_state(manager.state_file, [_make_desired("my-plugin")])

    with patch("orcheo.plugins.manager._import_plugin_entry_points", return_value={}):
        with patch("orcheo.plugins.manager._load_plugin_manifests", return_value=[]):
            with patch(
                "orcheo.plugins.manager._find_disabled_dependencies",
                return_value={"disabled-lib": {"my-plugin"}},
            ):
                report = manager.doctor()

    check_names = {check.name for check in report.checks}
    assert "disabled_dependency:disabled-lib" in check_names
    disabled_check = next(
        c for c in report.checks if c.name == "disabled_dependency:disabled-lib"
    )
    assert disabled_check.severity == "WARN"
    assert disabled_check.ok is False


# ---------------------------------------------------------------------------
# Integration tests using actual plugin fixtures
# ---------------------------------------------------------------------------


@requires_uv
def test_manager_list_plugins_with_installed_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """update_all and preview_update_all work with installed plugins."""
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(tmp_path / "config"))
    for d in ["plugins", "cache", "config"]:
        (tmp_path / d).mkdir()

    manager = PluginManager()
    fixture_path = _copy_fixture(tmp_path, "node_plugin")
    manager.install(str(fixture_path))

    # Test preview_update_all
    preview = manager.preview_update_all()
    assert len(preview) == 1
    assert preview[0]["name"] == "orcheo-plugin-fixture-node"

    # Test update_all
    result = manager.update_all()
    assert len(result) == 1
    assert result[0]["plugin"]["name"] == "orcheo-plugin-fixture-node"


@requires_uv
def test_manager_doctor_with_installed_plugin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """doctor runs full site-packages checks with an installed plugin."""
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(tmp_path / "config"))
    for d in ["plugins", "cache", "config"]:
        (tmp_path / d).mkdir()

    manager = PluginManager()
    fixture_path = _copy_fixture(tmp_path, "node_plugin")
    manager.install(str(fixture_path))

    report = manager.doctor()
    check_names = {check.name for check in report.checks}
    assert "plugin_venv_exists" in check_names
    assert "plugin_venv_python_version" in check_names
    assert any("plugin_importable" in name for name in check_names)
    assert any("lock_consistency" in name for name in check_names)
    assert not report.has_errors


# ---------------------------------------------------------------------------
# Additional unit tests for missing coverage
# ---------------------------------------------------------------------------


def test_ensure_venv_error_raises() -> None:
    """_ensure_venv raises PluginError when uv venv fails (lines 65-66)."""
    from orcheo.plugins.manager import _ensure_venv

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "Failed to create venv"

    with patch("orcheo.plugins.manager._run_command", return_value=mock_result):
        with pytest.raises(PluginError, match="Failed to create venv"):
            _ensure_venv(Path("/fake/path"))


def test_iter_plugin_distributions_finds_matching(tmp_path: Path) -> None:
    """_iter_plugin_distributions returns matching distributions (lines 164-172)."""
    from orcheo.plugins.manager import (
        PLUGIN_ENTRYPOINT_GROUP,
        _iter_plugin_distributions,
    )

    mock_ep = MagicMock()
    mock_ep.group = PLUGIN_ENTRYPOINT_GROUP
    mock_dist = MagicMock()
    mock_dist.entry_points = [mock_ep]

    with patch(
        "orcheo.plugins.manager.importlib.metadata.distributions",
        return_value=[mock_dist],
    ):
        result = _iter_plugin_distributions(tmp_path)

    assert mock_dist in result


def test_load_plugin_manifests_calls_distribution_to_manifest(
    tmp_path: Path,
) -> None:
    """_load_plugin_manifests returns manifests for each distribution (line 177)."""
    from orcheo.plugins.manager import _load_plugin_manifests
    from orcheo.plugins.models import PLUGIN_API_VERSION, PluginManifest

    mock_manifest = PluginManifest(
        name="test",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )
    with patch(
        "orcheo.plugins.manager._iter_plugin_distributions",
        return_value=[MagicMock()],
    ):
        with patch(
            "orcheo.plugins.manager._distribution_to_manifest",
            return_value=(mock_manifest, "abc"),
        ):
            result = _load_plugin_manifests(tmp_path)

    assert len(result) == 1
    assert result[0][0].name == "test"


def test_import_plugin_entry_points_loads_successfully(tmp_path: Path) -> None:
    """_import_plugin_entry_points returns empty on success (lines 185-198)."""
    from orcheo.plugins.manager import (
        PLUGIN_ENTRYPOINT_GROUP,
        _import_plugin_entry_points,
    )

    mock_ep = MagicMock()
    mock_ep.group = PLUGIN_ENTRYPOINT_GROUP
    mock_dist = MagicMock()
    mock_dist.entry_points = [mock_ep]

    with patch(
        "orcheo.plugins.manager._iter_plugin_distributions",
        return_value=[mock_dist],
    ):
        result = _import_plugin_entry_points(tmp_path)

    assert result == {}


def test_install_refs_into_venv_success(tmp_path: Path) -> None:
    """_install_refs_into_venv succeeds without raising (line 214->exit)."""
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()

    mock_result = MagicMock()
    mock_result.returncode = 0

    with patch("orcheo.plugins.manager._run_command", return_value=mock_result):
        _install_refs_into_venv(venv_dir, ["my-plugin==1.0.0"])
    # No exception = success


def test_validate_manifests_returns_issues() -> None:
    """_validate_manifests returns compatibility issues per plugin (lines 220-225)."""
    from orcheo.plugins.manager import _validate_manifests
    from orcheo.plugins.models import PLUGIN_API_VERSION, PluginManifest

    manifest = PluginManifest(
        name="bad-plugin",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION + 99,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )
    result = _validate_manifests([manifest])
    assert "bad-plugin" in result
    assert any("mismatch" in issue for issue in result["bad-plugin"])


def test_write_manifest_cache_creates_json(tmp_path: Path) -> None:
    """_write_manifest_cache writes one JSON file per manifest (lines 232-245)."""
    from orcheo.plugins.manager import _write_manifest_cache
    from orcheo.plugins.models import PLUGIN_API_VERSION, PluginManifest

    manifest = PluginManifest(
        name="my-plugin",
        version="2.0.0",
        description="A plugin",
        author="Author",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=["register=my_plugin:register"],
    )
    manifests_dir = tmp_path / "manifests"
    _write_manifest_cache(manifests_dir, [(manifest, "sha256abc")])

    cache_file = manifests_dir / "my-plugin.json"
    assert cache_file.exists()
    import json

    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    assert payload["name"] == "my-plugin"
    assert payload["version"] == "2.0.0"


def test_replace_directory_no_backup_no_destination(tmp_path: Path) -> None:
    """_replace_directory works when no backup or destination exist."""
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "dest"
    # Neither backup nor destination exist

    _replace_directory(source, destination)

    assert destination.exists()
    assert not (tmp_path / "dest.bak").exists()


def test_list_plugins_installed_status(tmp_path: Path) -> None:
    """list_plugins returns installed status when enabled plugin has lock record."""
    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("my-plugin", enabled=True)])
    save_lock_state(manager.lock_file, [_make_locked("my-plugin")])

    rows = manager.list_plugins()
    assert len(rows) == 1
    assert rows[0]["status"] == "installed"


def test_show_plugin_returns_disabled_status(tmp_path: Path) -> None:
    """show_plugin returns disabled status for a disabled plugin."""
    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("my-plugin", enabled=False)])
    save_lock_state(manager.lock_file, [_make_locked("my-plugin")])

    result = manager.show_plugin("my-plugin")
    assert result["status"] == "disabled"
    assert result["enabled"] is False


def test_reconcile_desired_and_lock_updates_wheel_sha256(tmp_path: Path) -> None:
    """_reconcile_desired_and_lock updates wheel_sha256 from hash_install_source."""
    manager = _make_manager(tmp_path)
    source_file = tmp_path / "plugin.whl"
    source_file.write_bytes(b"content")

    desired = [_make_desired("p1", source=str(source_file))]
    locked = [_make_locked("p1")]
    locked[0].wheel_sha256 = ""

    manager._reconcile_desired_and_lock(desired, locked)

    # Reload lock state to verify wheel_sha256 was written
    from orcheo.plugins.state import load_lock_state

    updated = load_lock_state(manager.lock_file)
    assert len(updated) == 1
    assert updated[0].wheel_sha256 != ""


def test_activate_build_with_validate_and_activate(tmp_path: Path) -> None:
    """_activate_build calls validate and activates (lines 422, 425-434, 437)."""
    from orcheo.plugins.models import PLUGIN_API_VERSION, PluginManifest

    manager = _make_manager(tmp_path)
    mock_manifest = PluginManifest(
        name="p1",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )
    mock_locked = _make_locked("p1")

    validated = []

    def my_validate(manifests: list) -> None:
        validated.extend(manifests)

    with patch(
        "orcheo.plugins.manager._rebuild_environment",
        return_value=([mock_manifest], [mock_locked]),
    ):
        with patch("orcheo.plugins.manager._replace_directory"):
            manifests, locked = manager._activate_build(
                desired_records=[_make_desired("p1")],
                validate=my_validate,
                activate=True,
            )

    assert validated == [mock_manifest]
    assert manifests == [mock_manifest]


def test_install_returns_plugin_and_impact(tmp_path: Path) -> None:
    """install builds desired state and returns plugin+impact dict (lines 463-486)."""
    from orcheo.plugins.models import PLUGIN_API_VERSION, PluginManifest

    manager = _make_manager(tmp_path)
    new_manifest = PluginManifest(
        name="new-plugin",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )
    mock_locked = _make_locked("new-plugin")

    with patch.object(
        manager, "_activate_build", return_value=([new_manifest], [mock_locked])
    ):
        result = manager.install("./new-plugin")

    assert result["plugin"]["name"] == "new-plugin"
    assert result["impact"] is not None


def test_update_returns_plugin_and_impact(tmp_path: Path) -> None:
    """update rebuilds environment and returns plugin+impact dict (lines 493-500)."""
    from orcheo.plugins.models import PLUGIN_API_VERSION, PluginManifest

    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("my-plugin")])
    save_lock_state(manager.lock_file, [_make_locked("my-plugin")])

    updated_manifest = PluginManifest(
        name="my-plugin",
        version="2.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )
    mock_locked = _make_locked("my-plugin", version="2.0.0")

    with patch.object(
        manager, "_activate_build", return_value=([updated_manifest], [mock_locked])
    ):
        result = manager.update("my-plugin")

    assert result["plugin"]["name"] == "my-plugin"
    assert result["impact"] is not None


def test_preview_update_raises_when_plugin_absent_from_result(
    tmp_path: Path,
) -> None:
    """preview_update raises when updated env no longer provides the plugin."""
    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("my-plugin")])
    save_lock_state(manager.lock_file, [_make_locked("my-plugin")])

    # activate_build returns a manifest for a DIFFERENT plugin
    from orcheo.plugins.models import PLUGIN_API_VERSION, PluginManifest

    other_manifest = PluginManifest(
        name="other-plugin",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )
    with patch.object(manager, "_activate_build", return_value=([other_manifest], [])):
        with pytest.raises(PluginError, match="no longer provides"):
            manager.preview_update("my-plugin")


def test_update_all_returns_results_with_manifests(tmp_path: Path) -> None:
    """update_all returns per-plugin results for matching manifests (lines 540-543)."""
    from orcheo.plugins.models import PLUGIN_API_VERSION, PluginManifest

    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("my-plugin")])
    save_lock_state(manager.lock_file, [_make_locked("my-plugin")])

    updated_manifest = PluginManifest(
        name="my-plugin",
        version="2.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )
    mock_locked = _make_locked("my-plugin", version="2.0.0")

    with patch.object(
        manager, "_activate_build", return_value=([updated_manifest], [mock_locked])
    ):
        result = manager.update_all()

    assert len(result) == 1
    assert result[0]["plugin"]["name"] == "my-plugin"


def test_uninstall_returns_impact(tmp_path: Path) -> None:
    """uninstall rebuilds env without the plugin and returns impact (lines 576-583)."""
    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("my-plugin")])
    save_lock_state(manager.lock_file, [_make_locked("my-plugin")])

    with patch.object(manager, "_activate_build", return_value=([], [])):
        impact = manager.uninstall("my-plugin")

    assert impact is not None
    assert impact.change_type == "remove"


def test_preview_uninstall_returns_impact(tmp_path: Path) -> None:
    """preview_uninstall computes impact without mutation (lines 593-608)."""
    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("my-plugin")])
    save_lock_state(manager.lock_file, [_make_locked("my-plugin")])

    impact = manager.preview_uninstall("my-plugin")

    assert impact.change_type == "remove"
    assert impact is not None


def test_set_enabled_rebuilds_env(tmp_path: Path) -> None:
    """set_enabled disables plugin and rebuilds env (lines 612-620)."""
    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("my-plugin")])
    save_lock_state(manager.lock_file, [_make_locked("my-plugin")])

    with patch.object(manager, "_activate_build", return_value=([], [])):
        impact = manager.set_enabled("my-plugin", enabled=False)

    assert impact is not None


def test_preview_set_enabled_returns_impact(tmp_path: Path) -> None:
    """preview_set_enabled computes impact (lines 630-640)."""
    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("my-plugin")])
    save_lock_state(manager.lock_file, [_make_locked("my-plugin")])

    impact = manager.preview_set_enabled("my-plugin", enabled=False)

    assert impact is not None
    assert impact.activation_mode in {"confirm_hot_reload", "restart_required"}


def test_find_disabled_dependencies_detects_disabled(tmp_path: Path) -> None:
    """_find_disabled_dependencies detects disabled plugins (lines 843-847)."""
    from orcheo.plugins.manager import _find_disabled_dependencies
    from orcheo.plugins.models import DesiredPluginRecord

    desired = {
        "enabled-plugin": DesiredPluginRecord(
            name="enabled-plugin", source="./ep", enabled=True
        ),
        "disabled-dep": DesiredPluginRecord(
            name="disabled-dep", source="./dd", enabled=False
        ),
    }

    mock_dist = MagicMock()
    mock_dist.metadata.get.return_value = "enabled-plugin"
    mock_dist.requires = ["disabled-dep >= 1.0"]

    with patch(
        "orcheo.plugins.manager._iter_plugin_distributions",
        return_value=[mock_dist],
    ):
        result = _find_disabled_dependencies(desired=desired, site_packages=tmp_path)

    assert "disabled-dep" in result
    assert "enabled-plugin" in result["disabled-dep"]


def test_hash_install_source_directory(tmp_path: Path) -> None:
    """hash_install_source returns hash for a directory path (line 856)."""
    plugin_dir = tmp_path / "plugin_src"
    plugin_dir.mkdir()
    (plugin_dir / "module.py").write_text("print('hello')", encoding="utf-8")

    result = hash_install_source(str(plugin_dir))
    assert len(result) == 64


def test_doctor_manifest_hash_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """doctor detects manifest hash mismatch (lines 735-776)."""
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(tmp_path / "config"))
    for d in ["plugins", "cache", "config"]:
        (tmp_path / d).mkdir()

    manager = PluginManager()
    site_pkgs = (
        manager.install_dir
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    site_pkgs.mkdir(parents=True)

    save_desired_state(manager.state_file, [_make_desired("my-plugin")])
    locked = _make_locked("my-plugin")
    locked.manifest_sha256 = "old-hash"
    save_lock_state(manager.lock_file, [locked])

    from orcheo.plugins.models import PLUGIN_API_VERSION, PluginManifest

    manifest = PluginManifest(
        name="my-plugin",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )
    with patch("orcheo.plugins.manager._import_plugin_entry_points", return_value={}):
        with patch(
            "orcheo.plugins.manager._load_plugin_manifests",
            return_value=[(manifest, "new-hash")],
        ):
            with patch(
                "orcheo.plugins.manager._find_disabled_dependencies", return_value={}
            ):
                report = manager.doctor()

    check_names = {check.name: check for check in report.checks}
    assert "manifest_sha256:my-plugin" in check_names
    assert check_names["manifest_sha256:my-plugin"].ok is False


# ---------------------------------------------------------------------------
# _run_command (line 53)
# ---------------------------------------------------------------------------


def test_run_command_returns_completed_process() -> None:
    """_run_command runs a subprocess and returns the result (line 53)."""
    from orcheo.plugins.manager import _run_command

    result = _run_command(["echo", "hello"])
    assert result.returncode == 0
    assert "hello" in result.stdout


# ---------------------------------------------------------------------------
# _ensure_venv success branch (line 65->exit)
# ---------------------------------------------------------------------------


def test_ensure_venv_success_does_not_raise() -> None:
    """_ensure_venv does not raise when uv venv returns returncode=0 (line 65->exit)."""
    from orcheo.plugins.manager import _ensure_venv

    mock_result = MagicMock()
    mock_result.returncode = 0

    with patch("orcheo.plugins.manager._run_command", return_value=mock_result):
        _ensure_venv(Path("/fake/path"))  # Should not raise


# ---------------------------------------------------------------------------
# _load_manifest_payload direct return (line 97)
# ---------------------------------------------------------------------------


def test_load_manifest_payload_direct_top_level_keys(tmp_path: Path) -> None:
    """_load_manifest_payload returns payload directly for top-level keys (line 97)."""
    manifest_file = tmp_path / "orcheo_plugin.toml"
    manifest_file.write_text(
        'plugin_api_version = 1\norcheo_version = ">=0.0.0"\nexports = ["nodes"]\n',
        encoding="utf-8",
    )
    payload = _load_manifest_payload(manifest_file)
    assert payload["plugin_api_version"] == 1
    assert "exports" in payload


# ---------------------------------------------------------------------------
# _find_manifest_file orcheo_plugin.toml found (line 119)
# ---------------------------------------------------------------------------


def test_find_manifest_file_finds_orcheo_plugin_toml(tmp_path: Path) -> None:
    """_find_manifest_file returns orcheo_plugin.toml path when found (line 119)."""
    orcheo_toml = tmp_path / "orcheo_plugin.toml"
    orcheo_toml.write_text("", encoding="utf-8")

    mock_file = MagicMock()
    mock_file.name = "orcheo_plugin.toml"

    mock_dist = MagicMock()
    mock_dist.files = [mock_file]
    mock_dist.locate_file.return_value = orcheo_toml

    result = _find_manifest_file(mock_dist)
    assert result == orcheo_toml


# ---------------------------------------------------------------------------
# _distribution_to_manifest success path (lines 138-157)
# ---------------------------------------------------------------------------


def test_distribution_to_manifest_success(tmp_path: Path) -> None:
    """_distribution_to_manifest returns manifest + sha256 on success."""
    from orcheo.plugins.manager import PLUGIN_ENTRYPOINT_GROUP

    manifest_file = tmp_path / "orcheo_plugin.toml"
    manifest_file.write_text(
        f"plugin_api_version = {PLUGIN_API_VERSION}\n"
        'orcheo_version = ">=0.0.0"\n'
        'exports = ["nodes"]\n',
        encoding="utf-8",
    )

    mock_ep = MagicMock()
    mock_ep.group = PLUGIN_ENTRYPOINT_GROUP
    mock_ep.name = "register"
    mock_ep.value = "my_plugin:register"

    mock_dist = MagicMock()
    mock_dist.entry_points = [mock_ep]
    mock_dist.version = "1.0.0"
    mock_dist.metadata = MagicMock()
    mock_dist.metadata.get.side_effect = lambda key, default="": {
        "Name": "test-plugin",
        "Summary": "A plugin",
        "Author": "Author",
    }.get(key, default)

    with patch(
        "orcheo.plugins.manager._find_manifest_file", return_value=manifest_file
    ):
        manifest, sha256 = _distribution_to_manifest(mock_dist)

    assert manifest.name == "test-plugin"
    assert manifest.version == "1.0.0"
    assert manifest.exports == ["nodes"]
    assert sha256 != ""


# ---------------------------------------------------------------------------
# _iter_plugin_distributions no-match branch (line 167->166)
# ---------------------------------------------------------------------------


def test_iter_plugin_distributions_skips_non_matching(tmp_path: Path) -> None:
    """_iter_plugin_distributions skips distributions with no plugin entry points."""
    from orcheo.plugins.manager import _iter_plugin_distributions

    mock_ep = MagicMock()
    mock_ep.group = "other.group"  # NOT the plugin entrypoint group
    mock_dist = MagicMock()
    mock_dist.entry_points = [mock_ep]

    with patch(
        "orcheo.plugins.manager.importlib.metadata.distributions",
        return_value=[mock_dist],
    ):
        result = _iter_plugin_distributions(tmp_path)

    assert result == []


# ---------------------------------------------------------------------------
# _import_plugin_entry_points continue branch (line 190)
# ---------------------------------------------------------------------------


def test_import_plugin_entry_points_skips_wrong_group(tmp_path: Path) -> None:
    """_import_plugin_entry_points skips entry points with wrong group (line 190)."""
    from orcheo.plugins.manager import (
        PLUGIN_ENTRYPOINT_GROUP,
        _import_plugin_entry_points,
    )

    # One entry point with the RIGHT group (plugin_ep), one with WRONG group
    plugin_ep = MagicMock()
    plugin_ep.group = PLUGIN_ENTRYPOINT_GROUP

    other_ep = MagicMock()
    other_ep.group = "some.other.group"

    mock_dist = MagicMock()
    mock_dist.entry_points = [plugin_ep, other_ep]

    with patch(
        "orcheo.plugins.manager._iter_plugin_distributions",
        return_value=[mock_dist],
    ):
        result = _import_plugin_entry_points(tmp_path)

    # other_ep.load() should NOT have been called
    other_ep.load.assert_not_called()
    assert result == {}


# ---------------------------------------------------------------------------
# _validate_manifests no-issue branch (line 223->221)
# ---------------------------------------------------------------------------


def test_validate_manifests_no_issues_for_compatible_plugin() -> None:
    """_validate_manifests returns empty dict for compatible manifests."""
    from orcheo.plugins.manager import _validate_manifests

    manifest = PluginManifest(
        name="ok-plugin",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )

    with patch(
        "orcheo.plugins.manager.check_manifest_compatibility",
        return_value=[],  # no issues
    ):
        result = _validate_manifests([manifest])

    assert result == {}


# ---------------------------------------------------------------------------
# _rebuild_environment (lines 256-291)
# ---------------------------------------------------------------------------


def test_rebuild_environment_success(tmp_path: Path) -> None:
    """_rebuild_environment builds a venv and returns manifests (lines 256-291)."""
    from orcheo.plugins.manager import _rebuild_environment

    target_dir = tmp_path / "venv"
    wheels_dir = tmp_path / "wheels"
    manifests_dir = tmp_path / "manifests"

    manifest = PluginManifest(
        name="test-plugin",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )

    with patch("orcheo.plugins.manager._ensure_venv"):
        with patch("orcheo.plugins.manager._install_refs_into_venv"):
            with patch(
                "orcheo.plugins.manager._load_plugin_manifests",
                return_value=[(manifest, "sha256abc")],
            ):
                with patch("orcheo.plugins.manager._write_manifest_cache"):
                    manifests, locked_records = _rebuild_environment(
                        refs=["./test-plugin"],
                        target_dir=target_dir,
                        wheels_dir=wheels_dir,
                        manifests_dir=manifests_dir,
                    )

    assert len(manifests) == 1
    assert manifests[0].name == "test-plugin"
    assert len(locked_records) == 1
    assert locked_records[0].name == "test-plugin"


def test_rebuild_environment_removes_existing_dirs(tmp_path: Path) -> None:
    """_rebuild_environment removes existing target_dir and wheels_dir."""
    from orcheo.plugins.manager import _rebuild_environment

    target_dir = tmp_path / "venv"
    wheels_dir = tmp_path / "wheels"
    manifests_dir = tmp_path / "manifests"
    target_dir.mkdir()
    wheels_dir.mkdir()

    with patch("orcheo.plugins.manager._ensure_venv"):
        with patch("orcheo.plugins.manager._install_refs_into_venv"):
            with patch(
                "orcheo.plugins.manager._load_plugin_manifests", return_value=[]
            ):
                with patch("orcheo.plugins.manager._write_manifest_cache"):
                    manifests, locked_records = _rebuild_environment(
                        refs=["./test-plugin"],
                        target_dir=target_dir,
                        wheels_dir=wheels_dir,
                        manifests_dir=manifests_dir,
                    )

    assert not target_dir.exists()
    assert manifests == []
    assert locked_records == []


def test_rebuild_environment_raises_on_compatibility_issues(tmp_path: Path) -> None:
    """_rebuild_environment raises PluginError when manifests have issues."""
    from orcheo.plugins.manager import _rebuild_environment

    target_dir = tmp_path / "venv"
    wheels_dir = tmp_path / "wheels"
    manifests_dir = tmp_path / "manifests"

    manifest = PluginManifest(
        name="bad-plugin",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION + 99,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )

    with patch("orcheo.plugins.manager._ensure_venv"):
        with patch("orcheo.plugins.manager._install_refs_into_venv"):
            with patch(
                "orcheo.plugins.manager._load_plugin_manifests",
                return_value=[(manifest, "sha256abc")],
            ):
                with pytest.raises(PluginError, match="mismatch"):
                    _rebuild_environment(
                        refs=["./bad-plugin"],
                        target_dir=target_dir,
                        wheels_dir=wheels_dir,
                        manifests_dir=manifests_dir,
                    )


# ---------------------------------------------------------------------------
# _reconcile_desired_and_lock no-match branch (line 389->387)
# ---------------------------------------------------------------------------


def test_reconcile_desired_and_lock_skips_unmatched_locked(tmp_path: Path) -> None:
    """_reconcile_desired_and_lock skips locked records not in desired."""
    manager = _make_manager(tmp_path)
    desired = [_make_desired("plugin-a")]
    locked = [_make_locked("plugin-b")]  # "plugin-b" not in desired

    manager._reconcile_desired_and_lock(desired, locked)

    from orcheo.plugins.state import load_lock_state

    updated_lock = load_lock_state(manager.lock_file)
    assert updated_lock[0].wheel_sha256 == ""


# ---------------------------------------------------------------------------
# _activate_build copy branches (lines 427, 429, 431, 433)
# ---------------------------------------------------------------------------


def test_activate_build_copies_and_replaces_existing_dirs(tmp_path: Path) -> None:
    """_activate_build copies temp wheels/manifests and removes existing dirs."""
    manager = _make_manager(tmp_path)
    # Pre-create wheels_dir and manifests_dir so their rmtree branches trigger
    manager.wheels_dir.mkdir(parents=True)
    manager.manifests_dir.mkdir(parents=True)

    mock_manifest = PluginManifest(
        name="p1",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )
    mock_locked = _make_locked("p1")

    def rebuild_side_effect(
        *,
        refs: list[str],
        target_dir: Path,
        wheels_dir: Path,
        manifests_dir: Path,
        progress: object | None = None,
    ) -> tuple[list[PluginManifest], list[LockedPluginRecord]]:
        # Create the temp wheels and manifests dirs so copytree branches trigger
        del progress
        wheels_dir.mkdir(parents=True, exist_ok=True)
        manifests_dir.mkdir(parents=True, exist_ok=True)
        return [mock_manifest], [mock_locked]

    with patch(
        "orcheo.plugins.manager._rebuild_environment", side_effect=rebuild_side_effect
    ):
        with patch("orcheo.plugins.manager._replace_directory"):
            manifests, locked = manager._activate_build(
                desired_records=[_make_desired("p1")],
                activate=True,
            )

    assert manifests == [mock_manifest]


def test_activate_build_creates_isolated_environment(tmp_path: Path) -> None:
    """_activate_build handles empty desired records by creating a venv."""
    manager = _make_manager(tmp_path)
    progress: list[str] = []

    def record_progress(message: str) -> None:
        progress.append(message)

    with (
        patch("orcheo.plugins.manager._ensure_venv") as mock_ensure,
        patch("orcheo.plugins.manager._replace_directory"),
    ):
        mock_ensure.side_effect = lambda path: path.mkdir(parents=True, exist_ok=True)
        manifests, locked = manager._activate_build(
            desired_records=[], progress=record_progress
        )

    assert manifests == []
    assert locked == []
    assert progress == [
        "Creating isolated plugin environment",
        "Activating rebuilt plugin environment",
    ]
    mock_ensure.assert_called_once()


# ---------------------------------------------------------------------------
# _validate_single_new_plugin body (lines 446-452)
# ---------------------------------------------------------------------------


def test_validate_single_new_plugin_succeeds_in_install(tmp_path: Path) -> None:
    """install calls _validate_single_new_plugin to validate exactly one new plugin."""
    manager = _make_manager(tmp_path)
    new_manifest = PluginManifest(
        name="new-plugin",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )
    mock_locked = _make_locked("new-plugin")

    # Mock _rebuild_environment so _activate_build calls validate callback
    with patch(
        "orcheo.plugins.manager._rebuild_environment",
        return_value=([new_manifest], [mock_locked]),
    ):
        with patch("orcheo.plugins.manager._replace_directory"):
            result = manager.install("./new-plugin")

    assert result["plugin"]["name"] == "new-plugin"


def test_validate_single_new_plugin_raises_for_zero_new(tmp_path: Path) -> None:
    """install raises when no new plugin is found in the built manifests (line 452)."""
    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("existing-plugin")])
    save_lock_state(manager.lock_file, [_make_locked("existing-plugin")])

    # _rebuild_environment returns only the existing plugin (no NEW plugin)
    existing_manifest = PluginManifest(
        name="existing-plugin",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=["nodes"],
        entry_points=[],
    )
    mock_locked = _make_locked("existing-plugin")

    with patch(
        "orcheo.plugins.manager._rebuild_environment",
        return_value=([existing_manifest], [mock_locked]),
    ):
        with patch("orcheo.plugins.manager._replace_directory"):
            with pytest.raises(PluginError, match="exactly one new plugin"):
                manager.install("./existing-plugin")


# ---------------------------------------------------------------------------
# update_all continue when manifest is None (line 542)
# ---------------------------------------------------------------------------


def test_update_all_skips_missing_manifests(tmp_path: Path) -> None:
    """update_all skips plugins whose manifest is absent from rebuilt env (line 542)."""
    manager = _make_manager(tmp_path)
    save_desired_state(manager.state_file, [_make_desired("my-plugin")])
    save_lock_state(manager.lock_file, [_make_locked("my-plugin")])

    # _activate_build returns no manifests → my-plugin will be skipped
    with patch.object(manager, "_activate_build", return_value=([], [])):
        result = manager.update_all()

    assert result == []


# ---------------------------------------------------------------------------
# _find_disabled_dependencies OK cases (lines 823, 828, 836->830)
# ---------------------------------------------------------------------------


def test_find_disabled_dependencies_returns_empty_when_none_disabled(
    tmp_path: Path,
) -> None:
    """_find_disabled_dependencies returns {} when no plugins are disabled."""
    from orcheo.plugins.manager import _find_disabled_dependencies
    from orcheo.plugins.models import DesiredPluginRecord

    desired = {
        "plugin-a": DesiredPluginRecord(name="plugin-a", source="./pa", enabled=True),
        "plugin-b": DesiredPluginRecord(name="plugin-b", source="./pb", enabled=True),
    }
    result = _find_disabled_dependencies(desired=desired, site_packages=tmp_path)
    assert result == {}


def test_find_disabled_dependencies_skips_distribution_not_in_desired(
    tmp_path: Path,
) -> None:
    """_find_disabled_dependencies skips distributions not in desired state."""
    from orcheo.plugins.manager import _find_disabled_dependencies
    from orcheo.plugins.models import DesiredPluginRecord

    desired = {
        "enabled-plugin": DesiredPluginRecord(
            name="enabled-plugin", source="./ep", enabled=True
        ),
        "disabled-dep": DesiredPluginRecord(
            name="disabled-dep", source="./dd", enabled=False
        ),
    }

    mock_dist = MagicMock()
    mock_dist.metadata.get.return_value = "unknown-dist"  # NOT in desired
    mock_dist.requires = []

    with patch(
        "orcheo.plugins.manager._iter_plugin_distributions", return_value=[mock_dist]
    ):
        result = _find_disabled_dependencies(desired=desired, site_packages=tmp_path)

    assert result == {}


def test_find_disabled_dependencies_skips_non_disabled_requirement(
    tmp_path: Path,
) -> None:
    """_find_disabled_dependencies skips requirements not in disabled_names."""
    from orcheo.plugins.manager import _find_disabled_dependencies
    from orcheo.plugins.models import DesiredPluginRecord

    desired = {
        "enabled-plugin": DesiredPluginRecord(
            name="enabled-plugin", source="./ep", enabled=True
        ),
        "disabled-dep": DesiredPluginRecord(
            name="disabled-dep", source="./dd", enabled=False
        ),
    }

    mock_dist = MagicMock()
    mock_dist.metadata.get.return_value = "enabled-plugin"
    mock_dist.requires = ["some-other-lib >= 1.0"]  # NOT disabled-dep

    with patch(
        "orcheo.plugins.manager._iter_plugin_distributions", return_value=[mock_dist]
    ):
        result = _find_disabled_dependencies(desired=desired, site_packages=tmp_path)

    assert result == {}
