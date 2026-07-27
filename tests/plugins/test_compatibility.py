"""Tests for the plugin compatibility module."""

from __future__ import annotations
from unittest.mock import patch
from orcheo.plugins.compatibility import (
    check_manifest_compatibility,
    classify_plugin_change,
    get_running_orcheo_version,
)
from orcheo.plugins.models import (
    PLUGIN_API_VERSION,
    LockedPluginRecord,
    PluginManifest,
)


# ---------------------------------------------------------------------------
# get_running_orcheo_version
# ---------------------------------------------------------------------------


def test_get_running_orcheo_version_returns_string() -> None:
    """Returns a version string."""
    version = get_running_orcheo_version()
    assert isinstance(version, str)
    assert len(version) > 0


def test_get_running_orcheo_version_package_not_found() -> None:
    """Returns '0.0.0' when orcheo package is not installed (lines 23-24)."""
    from importlib.metadata import PackageNotFoundError

    with patch(
        "orcheo.plugins.compatibility.package_version",
        side_effect=PackageNotFoundError("orcheo"),
    ):
        result = get_running_orcheo_version()
    assert result == "0.0.0"


# ---------------------------------------------------------------------------
# check_manifest_compatibility
# ---------------------------------------------------------------------------


def _make_manifest(
    *,
    api_version: int = PLUGIN_API_VERSION,
    orcheo_version: str = ">=0.0.0",
    exports: list[str] | None = None,
) -> PluginManifest:
    return PluginManifest(
        name="test-plugin",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=api_version,
        orcheo_version=orcheo_version,
        exports=exports or ["nodes"],
        entry_points=[],
    )


def test_check_manifest_compatibility_ok() -> None:
    """No issues when everything matches."""
    manifest = _make_manifest()
    with patch(
        "orcheo.plugins.compatibility.get_running_orcheo_version", return_value="1.0.0"
    ):
        issues = check_manifest_compatibility(manifest)
    assert issues == []


def test_check_manifest_compatibility_api_version_mismatch() -> None:
    """Reports mismatch when plugin_api_version differs."""
    manifest = _make_manifest(api_version=PLUGIN_API_VERSION + 99)
    issues = check_manifest_compatibility(manifest)
    assert any("plugin API mismatch" in issue for issue in issues)


def test_check_manifest_compatibility_invalid_specifier() -> None:
    """Reports invalid specifier and returns early (lines 40-42)."""
    manifest = _make_manifest(orcheo_version="not_a_specifier!!!")
    issues = check_manifest_compatibility(manifest)
    assert any("invalid Orcheo version specifier" in issue for issue in issues)


def test_check_manifest_compatibility_version_not_satisfied() -> None:
    """Reports version constraint not satisfied (line 45)."""
    manifest = _make_manifest(orcheo_version=">=999.0.0")
    with patch(
        "orcheo.plugins.compatibility.get_running_orcheo_version", return_value="0.1.0"
    ):
        issues = check_manifest_compatibility(manifest)
    assert any("does not satisfy" in issue for issue in issues)


def test_check_manifest_compatibility_running_prerelease_satisfies_specifier() -> None:
    """A prerelease-versioned orcheo core (e.g. alpha builds) still satisfies
    plugin specifiers that don't explicitly opt into prereleases."""
    manifest = _make_manifest(orcheo_version=">=0.25,<1.0")
    with patch(
        "orcheo.plugins.compatibility.get_running_orcheo_version",
        return_value="0.45.0a1",
    ):
        issues = check_manifest_compatibility(manifest)
    assert issues == []


# ---------------------------------------------------------------------------
# classify_plugin_change
# ---------------------------------------------------------------------------


def _make_locked_record(exports: list[str]) -> LockedPluginRecord:
    return LockedPluginRecord(
        name="test-plugin",
        version="1.0.0",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        location="",
        wheel_sha256="",
        manifest_sha256="",
        exports=exports,
        description="",
        author="",
        entry_points=[],
    )


def _make_manifest_for_change(exports: list[str]) -> PluginManifest:
    return PluginManifest(
        name="test-plugin",
        version="1.0.0",
        description="",
        author="",
        plugin_api_version=PLUGIN_API_VERSION,
        orcheo_version=">=0.0.0",
        exports=exports,
        entry_points=[],
    )


def test_classify_install_with_nodes() -> None:
    """Install with hot-reloadable exports → silent_hot_reload, no prompt."""
    manifest = _make_manifest_for_change(["nodes"])
    result = classify_plugin_change(
        previous=None, current=manifest, operation="install"
    )
    assert result.change_type == "additive"
    assert result.activation_mode == "silent_hot_reload"
    assert result.prompt_required is False
    assert result.restart_required is False


def test_classify_update_same_exports() -> None:
    """Update with same exports → replace change_type (line 68)."""
    previous = _make_locked_record(["nodes"])
    manifest = _make_manifest_for_change(["nodes"])
    result = classify_plugin_change(
        previous=previous, current=manifest, operation="update"
    )
    assert result.change_type == "replace"


def test_classify_update_additive_exports() -> None:
    """Update that adds exports → additive (line 70)."""
    previous = _make_locked_record(["nodes"])
    manifest = _make_manifest_for_change(["nodes", "edges"])
    result = classify_plugin_change(
        previous=previous, current=manifest, operation="update"
    )
    assert result.change_type == "additive"


def test_classify_update_remove_exports() -> None:
    """Update that removes exports → remove (line 72-73)."""
    previous = _make_locked_record(["nodes", "edges"])
    manifest = _make_manifest_for_change(["nodes"])
    result = classify_plugin_change(
        previous=previous, current=manifest, operation="update"
    )
    assert result.change_type == "remove"


def test_classify_update_mixed_exports() -> None:
    """Update with mixed adds/removes → mixed (line 74-75)."""
    previous = _make_locked_record(["nodes", "triggers"])
    manifest = _make_manifest_for_change(["nodes", "edges"])
    result = classify_plugin_change(
        previous=previous, current=manifest, operation="update"
    )
    assert result.change_type == "mixed"


def test_classify_with_restart_required_exports() -> None:
    """Listeners/triggers require restart and prompt for update/uninstall."""
    manifest = _make_manifest_for_change(["listeners"])
    result = classify_plugin_change(
        previous=None, current=manifest, operation="install"
    )
    assert result.restart_required is True
    assert result.activation_mode == "restart_required"
    assert result.prompt_required is False  # install doesn't prompt

    result_update = classify_plugin_change(
        previous=_make_locked_record(["listeners"]),
        current=manifest,
        operation="update",
    )
    assert result_update.prompt_required is True


def test_classify_install_empty_exports() -> None:
    """Install with no exports → silent_hot_reload (lines 94-95)."""
    manifest = _make_manifest_for_change([])
    result = classify_plugin_change(
        previous=None, current=manifest, operation="install"
    )
    assert result.activation_mode == "silent_hot_reload"
    assert result.prompt_required is False


def test_classify_update_unknown_export_type_hits_else_branch() -> None:
    """Update with unknown export type hits else → silent_hot_reload."""
    previous = _make_locked_record(["custom_type"])
    manifest = _make_manifest_for_change(["custom_type"])
    result = classify_plugin_change(
        previous=previous, current=manifest, operation="update"
    )
    assert result.activation_mode == "silent_hot_reload"
    assert result.prompt_required is False
    assert result.restart_required is False


def test_classify_uninstall_sets_remove_change_type() -> None:
    """Uninstall operation → change_type is remove (line 67)."""
    previous = _make_locked_record(["nodes"])
    manifest = _make_manifest_for_change([])
    result = classify_plugin_change(
        previous=previous, current=manifest, operation="uninstall"
    )
    assert result.change_type == "remove"


def test_classify_disable_with_hot_reloadable() -> None:
    """Disable with hot-reloadable exports prompts."""
    previous = _make_locked_record(["nodes"])
    manifest = _make_manifest_for_change(["nodes"])
    result = classify_plugin_change(
        previous=previous, current=manifest, operation="disable"
    )
    assert result.activation_mode == "confirm_hot_reload"
    assert result.prompt_required is True
