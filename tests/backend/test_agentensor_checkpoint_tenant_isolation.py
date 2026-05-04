"""Cross-tenant isolation tests for the Agentensor checkpoint stores."""

from __future__ import annotations
from pathlib import Path
import pytest
from orcheo_backend.app.agentensor.checkpoint_store import (
    InMemoryAgentensorCheckpointStore,
    SqliteAgentensorCheckpointStore,
)


_COMMON_KWARGS: dict = {
    "runnable_config": {"alpha": "beta"},
    "metrics": {"score": 1.0},
}


# ---------------------------------------------------------------------------
# InMemory store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmemory_list_checkpoints_filters_by_tenant() -> None:
    store = InMemoryAgentensorCheckpointStore()
    await store.record_checkpoint(
        workflow_id="wf-1", tenant_id="tenant-a", **_COMMON_KWARGS
    )
    await store.record_checkpoint(
        workflow_id="wf-1", tenant_id="tenant-b", **_COMMON_KWARGS
    )

    results_a = await store.list_checkpoints("wf-1", tenant_id="tenant-a")
    results_b = await store.list_checkpoints("wf-1", tenant_id="tenant-b")

    assert len(results_a) == 1
    assert results_a[0].tenant_id == "tenant-a"
    assert len(results_b) == 1
    assert results_b[0].tenant_id == "tenant-b"


@pytest.mark.asyncio
async def test_inmemory_unscoped_checkpoint_visible_to_all_tenants() -> None:
    store = InMemoryAgentensorCheckpointStore()
    await store.record_checkpoint(workflow_id="wf-1", tenant_id=None, **_COMMON_KWARGS)
    await store.record_checkpoint(
        workflow_id="wf-1", tenant_id="tenant-a", **_COMMON_KWARGS
    )

    results_a = await store.list_checkpoints("wf-1", tenant_id="tenant-a")
    results_b = await store.list_checkpoints("wf-1", tenant_id="tenant-b")

    tids_a = {c.tenant_id for c in results_a}
    tids_b = {c.tenant_id for c in results_b}
    assert None in tids_a
    assert "tenant-a" in tids_a
    assert None in tids_b
    assert "tenant-a" not in tids_b


@pytest.mark.asyncio
async def test_inmemory_record_checkpoint_stores_tenant_id() -> None:
    store = InMemoryAgentensorCheckpointStore()
    cp = await store.record_checkpoint(
        workflow_id="wf-1", tenant_id="tenant-x", **_COMMON_KWARGS
    )
    assert cp.tenant_id == "tenant-x"


@pytest.mark.asyncio
async def test_inmemory_no_tenant_filter_returns_all() -> None:
    store = InMemoryAgentensorCheckpointStore()
    await store.record_checkpoint(
        workflow_id="wf-1", tenant_id="tenant-a", **_COMMON_KWARGS
    )
    await store.record_checkpoint(
        workflow_id="wf-1", tenant_id="tenant-b", **_COMMON_KWARGS
    )
    all_cps = await store.list_checkpoints("wf-1")
    assert len(all_cps) == 2


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_list_checkpoints_filters_by_tenant(tmp_path: Path) -> None:
    store = SqliteAgentensorCheckpointStore(tmp_path / "cp.db")
    await store.record_checkpoint(
        workflow_id="wf-1", tenant_id="tenant-a", **_COMMON_KWARGS
    )
    await store.record_checkpoint(
        workflow_id="wf-1", tenant_id="tenant-b", **_COMMON_KWARGS
    )

    results_a = await store.list_checkpoints("wf-1", tenant_id="tenant-a")
    results_b = await store.list_checkpoints("wf-1", tenant_id="tenant-b")

    assert len(results_a) == 1
    assert results_a[0].tenant_id == "tenant-a"
    assert len(results_b) == 1
    assert results_b[0].tenant_id == "tenant-b"


@pytest.mark.asyncio
async def test_sqlite_unscoped_checkpoint_visible_to_all_tenants(
    tmp_path: Path,
) -> None:
    store = SqliteAgentensorCheckpointStore(tmp_path / "cp.db")
    await store.record_checkpoint(workflow_id="wf-1", tenant_id=None, **_COMMON_KWARGS)
    await store.record_checkpoint(
        workflow_id="wf-1", tenant_id="tenant-a", **_COMMON_KWARGS
    )

    results_a = await store.list_checkpoints("wf-1", tenant_id="tenant-a")
    results_b = await store.list_checkpoints("wf-1", tenant_id="tenant-b")

    tids_a = {c.tenant_id for c in results_a}
    tids_b = {c.tenant_id for c in results_b}
    assert None in tids_a
    assert "tenant-a" in tids_a
    assert None in tids_b
    assert "tenant-a" not in tids_b


@pytest.mark.asyncio
async def test_sqlite_record_checkpoint_stores_tenant_id(tmp_path: Path) -> None:
    store = SqliteAgentensorCheckpointStore(tmp_path / "cp.db")
    cp = await store.record_checkpoint(
        workflow_id="wf-1", tenant_id="tenant-x", **_COMMON_KWARGS
    )
    assert cp.tenant_id == "tenant-x"

    fetched = await store.get_checkpoint(cp.id)
    assert fetched.tenant_id == "tenant-x"


@pytest.mark.asyncio
async def test_sqlite_no_tenant_filter_returns_all(tmp_path: Path) -> None:
    store = SqliteAgentensorCheckpointStore(tmp_path / "cp.db")
    await store.record_checkpoint(
        workflow_id="wf-1", tenant_id="tenant-a", **_COMMON_KWARGS
    )
    await store.record_checkpoint(
        workflow_id="wf-1", tenant_id="tenant-b", **_COMMON_KWARGS
    )
    all_cps = await store.list_checkpoints("wf-1")
    assert len(all_cps) == 2
