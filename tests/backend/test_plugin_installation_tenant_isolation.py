"""Cross-tenant isolation tests for plugin installation stores."""

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
async def test_inmemory_list_states_filters_by_tenant() -> None:
    store = InMemoryPluginInstallationStore()
    await store.set_plugin_enabled("plugin-a", tenant_id="tenant-1", enabled=True)
    await store.set_plugin_enabled("plugin-a", tenant_id="tenant-2", enabled=False)

    states_1 = await store.list_plugin_states(tenant_id="tenant-1")
    states_2 = await store.list_plugin_states(tenant_id="tenant-2")

    assert len(states_1) == 1
    assert states_1[0].enabled is True
    assert len(states_2) == 1
    assert states_2[0].enabled is False


@pytest.mark.asyncio
async def test_inmemory_get_plugin_enabled_is_per_tenant() -> None:
    store = InMemoryPluginInstallationStore()
    await store.set_plugin_enabled("plugin-x", tenant_id="tenant-a", enabled=True)
    await store.set_plugin_enabled("plugin-x", tenant_id="tenant-b", enabled=False)

    assert await store.get_plugin_enabled("plugin-x", tenant_id="tenant-a") is True
    assert await store.get_plugin_enabled("plugin-x", tenant_id="tenant-b") is False


@pytest.mark.asyncio
async def test_inmemory_get_plugin_enabled_returns_none_when_no_override() -> None:
    store = InMemoryPluginInstallationStore()
    result = await store.get_plugin_enabled("plugin-missing", tenant_id="tenant-x")
    assert result is None


@pytest.mark.asyncio
async def test_inmemory_no_tenant_filter_returns_all() -> None:
    store = InMemoryPluginInstallationStore()
    await store.set_plugin_enabled("plugin-a", tenant_id="tenant-1", enabled=True)
    await store.set_plugin_enabled("plugin-b", tenant_id="tenant-2", enabled=False)

    all_states = await store.list_plugin_states()
    assert len(all_states) == 2


@pytest.mark.asyncio
async def test_inmemory_set_overrides_existing() -> None:
    store = InMemoryPluginInstallationStore()
    await store.set_plugin_enabled("plugin-a", tenant_id="tenant-1", enabled=True)
    await store.set_plugin_enabled("plugin-a", tenant_id="tenant-1", enabled=False)

    result = await store.get_plugin_enabled("plugin-a", tenant_id="tenant-1")
    assert result is False


@pytest.mark.asyncio
async def test_inmemory_tenant_isolation_list_excludes_other_tenants() -> None:
    store = InMemoryPluginInstallationStore()
    await store.set_plugin_enabled("plugin-a", tenant_id="tenant-1", enabled=True)
    await store.set_plugin_enabled("plugin-b", tenant_id="tenant-2", enabled=True)

    states_1 = await store.list_plugin_states(tenant_id="tenant-1")
    names_1 = {s.plugin_name for s in states_1}
    assert "plugin-a" in names_1
    assert "plugin-b" not in names_1


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_list_states_filters_by_tenant(tmp_path: Path) -> None:
    store = SqlitePluginInstallationStore(tmp_path / "plugins.db")
    await store.set_plugin_enabled("plugin-a", tenant_id="tenant-1", enabled=True)
    await store.set_plugin_enabled("plugin-a", tenant_id="tenant-2", enabled=False)

    states_1 = await store.list_plugin_states(tenant_id="tenant-1")
    states_2 = await store.list_plugin_states(tenant_id="tenant-2")

    assert len(states_1) == 1
    assert states_1[0].enabled is True
    assert len(states_2) == 1
    assert states_2[0].enabled is False


@pytest.mark.asyncio
async def test_sqlite_get_plugin_enabled_is_per_tenant(tmp_path: Path) -> None:
    store = SqlitePluginInstallationStore(tmp_path / "plugins.db")
    await store.set_plugin_enabled("plugin-x", tenant_id="tenant-a", enabled=True)
    await store.set_plugin_enabled("plugin-x", tenant_id="tenant-b", enabled=False)

    assert await store.get_plugin_enabled("plugin-x", tenant_id="tenant-a") is True
    assert await store.get_plugin_enabled("plugin-x", tenant_id="tenant-b") is False


@pytest.mark.asyncio
async def test_sqlite_get_plugin_enabled_returns_none_when_no_override(
    tmp_path: Path,
) -> None:
    store = SqlitePluginInstallationStore(tmp_path / "plugins.db")
    result = await store.get_plugin_enabled("plugin-missing", tenant_id="tenant-x")
    assert result is None


@pytest.mark.asyncio
async def test_sqlite_no_tenant_filter_returns_all(tmp_path: Path) -> None:
    store = SqlitePluginInstallationStore(tmp_path / "plugins.db")
    await store.set_plugin_enabled("plugin-a", tenant_id="tenant-1", enabled=True)
    await store.set_plugin_enabled("plugin-b", tenant_id="tenant-2", enabled=False)

    all_states = await store.list_plugin_states()
    assert len(all_states) == 2


@pytest.mark.asyncio
async def test_sqlite_set_overrides_existing(tmp_path: Path) -> None:
    store = SqlitePluginInstallationStore(tmp_path / "plugins.db")
    await store.set_plugin_enabled("plugin-a", tenant_id="tenant-1", enabled=True)
    await store.set_plugin_enabled("plugin-a", tenant_id="tenant-1", enabled=False)

    result = await store.get_plugin_enabled("plugin-a", tenant_id="tenant-1")
    assert result is False


@pytest.mark.asyncio
async def test_sqlite_tenant_isolation_list_excludes_other_tenants(
    tmp_path: Path,
) -> None:
    store = SqlitePluginInstallationStore(tmp_path / "plugins.db")
    await store.set_plugin_enabled("plugin-a", tenant_id="tenant-1", enabled=True)
    await store.set_plugin_enabled("plugin-b", tenant_id="tenant-2", enabled=True)

    states_1 = await store.list_plugin_states(tenant_id="tenant-1")
    names_1 = {s.plugin_name for s in states_1}
    assert "plugin-a" in names_1
    assert "plugin-b" not in names_1
