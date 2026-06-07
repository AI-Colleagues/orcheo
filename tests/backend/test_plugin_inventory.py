from __future__ import annotations
import pytest
from orcheo.plugins import PluginLoadReport, PluginLoadResult
from orcheo_backend.app import plugin_inventory


def test_list_runtime_plugins_merges_inventory_and_load_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime inventory reports enabled/loaded details per plugin."""
    fake_rows = [
        {
            "name": "orcheo-plugin-foo",
            "enabled": True,
            "status": "enabled",
            "version": "1.0.0",
            "exports": ["nodes"],
            "source": "orcheo-plugin-foo",
        },
        {
            "name": "orcheo-plugin-bar",
            "enabled": False,
            "status": "installed",
            "version": "2.0.0",
            "exports": [],
            "source": "orcheo-plugin-bar",
        },
    ]

    class StubManager:
        @staticmethod
        def list_plugins() -> list[dict[str, object]]:
            return fake_rows

    monkeypatch.setattr(plugin_inventory, "PluginManager", lambda: StubManager())
    monkeypatch.setattr(
        plugin_inventory,
        "load_enabled_plugins",
        lambda *, force=False: PluginLoadReport(
            generation=1,
            results=[
                PluginLoadResult(name="orcheo-plugin-foo", loaded=True),
            ],
        ),
    )

    inventory = plugin_inventory.list_runtime_plugins()
    assert len(inventory) == 2
    foo = inventory[0]
    assert foo["name"] == "orcheo-plugin-foo"
    assert foo["loaded"] is True
    assert foo["load_error"] is None
    bar = inventory[1]
    assert bar["name"] == "orcheo-plugin-bar"
    assert bar["loaded"] is False
    assert bar["load_error"] is None


def test_missing_required_plugins_filters_disabled_and_unloaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required plugin detection ignores blanks and flags unavailable items."""
    monkeypatch.setattr(
        plugin_inventory,
        "list_runtime_plugins",
        lambda: [
            {
                "name": "orcheo-plugin-foo",
                "enabled": True,
                "loaded": True,
            },
            {
                "name": "orcheo-plugin-bar",
                "enabled": False,
                "loaded": True,
            },
            {
                "name": "orcheo-plugin-baz",
                "enabled": True,
                "loaded": False,
            },
        ],
    )

    missing = plugin_inventory.missing_required_plugins(
        [
            "orcheo-plugin-foo",
            "orcheo-plugin-bar",
            " orcheo-plugin-baz ",
            "",
            "orcheo-plugin-bar",
        ]
    )
    assert missing == ["orcheo-plugin-bar", "orcheo-plugin-baz"]


def test_missing_required_plugins_flags_not_installed_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A required plugin absent from the inventory is flagged as missing."""
    monkeypatch.setattr(
        plugin_inventory,
        "list_runtime_plugins",
        lambda: [
            {
                "name": "orcheo-plugin-present",
                "enabled": True,
                "loaded": True,
            },
        ],
    )

    missing = plugin_inventory.missing_required_plugins(
        ["orcheo-plugin-present", "orcheo-plugin-missing"]
    )
    assert missing == ["orcheo-plugin-missing"]
