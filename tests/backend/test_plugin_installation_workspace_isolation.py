"""Cross-workspace isolation tests for plugin installation stores."""

from __future__ import annotations
from pathlib import Path
import pytest
from orcheo_backend.app.plugin_installation_store import (
    InMemoryPluginInstallationStore,
    SqlitePluginInstallationStore,
)


# ---------------------------------------------------------------------------
# InMemory store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmemory_list_states_filters_by_workspace() -> None:
    store = InMemoryPluginInstallationStore()
    await store.set_plugin_enabled("plugin-a", workspace_id="workspace-1", enabled=True)
    await store.set_plugin_enabled(
        "plugin-a", workspace_id="workspace-2", enabled=False
    )

    states_1 = await store.list_plugin_states(workspace_id="workspace-1")
    states_2 = await store.list_plugin_states(workspace_id="workspace-2")

    assert len(states_1) == 1
    assert states_1[0].enabled is True
    assert len(states_2) == 1
    assert states_2[0].enabled is False


@pytest.mark.asyncio
async def test_inmemory_get_plugin_enabled_is_per_workspace() -> None:
    store = InMemoryPluginInstallationStore()
    await store.set_plugin_enabled("plugin-x", workspace_id="workspace-a", enabled=True)
    await store.set_plugin_enabled(
        "plugin-x", workspace_id="workspace-b", enabled=False
    )

    assert (
        await store.get_plugin_enabled("plugin-x", workspace_id="workspace-a") is True
    )
    assert (
        await store.get_plugin_enabled("plugin-x", workspace_id="workspace-b") is False
    )


@pytest.mark.asyncio
async def test_inmemory_get_plugin_enabled_returns_none_when_no_override() -> None:
    store = InMemoryPluginInstallationStore()
    result = await store.get_plugin_enabled(
        "plugin-missing", workspace_id="workspace-x"
    )
    assert result is None


@pytest.mark.asyncio
async def test_inmemory_no_workspace_filter_returns_all() -> None:
    store = InMemoryPluginInstallationStore()
    await store.set_plugin_enabled("plugin-a", workspace_id="workspace-1", enabled=True)
    await store.set_plugin_enabled(
        "plugin-b", workspace_id="workspace-2", enabled=False
    )

    all_states = await store.list_plugin_states()
    assert len(all_states) == 2


@pytest.mark.asyncio
async def test_inmemory_set_overrides_existing() -> None:
    store = InMemoryPluginInstallationStore()
    await store.set_plugin_enabled("plugin-a", workspace_id="workspace-1", enabled=True)
    await store.set_plugin_enabled(
        "plugin-a", workspace_id="workspace-1", enabled=False
    )

    result = await store.get_plugin_enabled("plugin-a", workspace_id="workspace-1")
    assert result is False


@pytest.mark.asyncio
async def test_inmemory_workspace_isolation_list_excludes_other_workspaces() -> None:
    store = InMemoryPluginInstallationStore()
    await store.set_plugin_enabled("plugin-a", workspace_id="workspace-1", enabled=True)
    await store.set_plugin_enabled("plugin-b", workspace_id="workspace-2", enabled=True)

    states_1 = await store.list_plugin_states(workspace_id="workspace-1")
    names_1 = {s.plugin_name for s in states_1}
    assert "plugin-a" in names_1
    assert "plugin-b" not in names_1


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_list_states_filters_by_workspace(tmp_path: Path) -> None:
    store = SqlitePluginInstallationStore(tmp_path / "plugins.db")
    await store.set_plugin_enabled("plugin-a", workspace_id="workspace-1", enabled=True)
    await store.set_plugin_enabled(
        "plugin-a", workspace_id="workspace-2", enabled=False
    )

    states_1 = await store.list_plugin_states(workspace_id="workspace-1")
    states_2 = await store.list_plugin_states(workspace_id="workspace-2")

    assert len(states_1) == 1
    assert states_1[0].enabled is True
    assert len(states_2) == 1
    assert states_2[0].enabled is False


@pytest.mark.asyncio
async def test_sqlite_get_plugin_enabled_is_per_workspace(tmp_path: Path) -> None:
    store = SqlitePluginInstallationStore(tmp_path / "plugins.db")
    await store.set_plugin_enabled("plugin-x", workspace_id="workspace-a", enabled=True)
    await store.set_plugin_enabled(
        "plugin-x", workspace_id="workspace-b", enabled=False
    )

    assert (
        await store.get_plugin_enabled("plugin-x", workspace_id="workspace-a") is True
    )
    assert (
        await store.get_plugin_enabled("plugin-x", workspace_id="workspace-b") is False
    )


@pytest.mark.asyncio
async def test_sqlite_get_plugin_enabled_returns_none_when_no_override(
    tmp_path: Path,
) -> None:
    store = SqlitePluginInstallationStore(tmp_path / "plugins.db")
    result = await store.get_plugin_enabled(
        "plugin-missing", workspace_id="workspace-x"
    )
    assert result is None


@pytest.mark.asyncio
async def test_sqlite_no_workspace_filter_returns_all(tmp_path: Path) -> None:
    store = SqlitePluginInstallationStore(tmp_path / "plugins.db")
    await store.set_plugin_enabled("plugin-a", workspace_id="workspace-1", enabled=True)
    await store.set_plugin_enabled(
        "plugin-b", workspace_id="workspace-2", enabled=False
    )

    all_states = await store.list_plugin_states()
    assert len(all_states) == 2


@pytest.mark.asyncio
async def test_sqlite_set_overrides_existing(tmp_path: Path) -> None:
    store = SqlitePluginInstallationStore(tmp_path / "plugins.db")
    await store.set_plugin_enabled("plugin-a", workspace_id="workspace-1", enabled=True)
    await store.set_plugin_enabled(
        "plugin-a", workspace_id="workspace-1", enabled=False
    )

    result = await store.get_plugin_enabled("plugin-a", workspace_id="workspace-1")
    assert result is False


@pytest.mark.asyncio
async def test_sqlite_workspace_isolation_list_excludes_other_workspaces(
    tmp_path: Path,
) -> None:
    store = SqlitePluginInstallationStore(tmp_path / "plugins.db")
    await store.set_plugin_enabled("plugin-a", workspace_id="workspace-1", enabled=True)
    await store.set_plugin_enabled("plugin-b", workspace_id="workspace-2", enabled=True)

    states_1 = await store.list_plugin_states(workspace_id="workspace-1")
    names_1 = {s.plugin_name for s in states_1}
    assert "plugin-a" in names_1
    assert "plugin-b" not in names_1
