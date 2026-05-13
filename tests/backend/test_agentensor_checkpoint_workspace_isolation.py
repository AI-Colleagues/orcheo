"""Cross-workspace isolation tests for the Agentensor checkpoint stores."""

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
async def test_inmemory_list_checkpoints_filters_by_workspace() -> None:
    store = InMemoryAgentensorCheckpointStore()
    await store.record_checkpoint(
        workflow_id="wf-1", workspace_id="workspace-a", **_COMMON_KWARGS
    )
    await store.record_checkpoint(
        workflow_id="wf-1", workspace_id="workspace-b", **_COMMON_KWARGS
    )

    results_a = await store.list_checkpoints("wf-1", workspace_id="workspace-a")
    results_b = await store.list_checkpoints("wf-1", workspace_id="workspace-b")

    assert len(results_a) == 1
    assert results_a[0].workspace_id == "workspace-a"
    assert len(results_b) == 1
    assert results_b[0].workspace_id == "workspace-b"


@pytest.mark.asyncio
async def test_inmemory_unscoped_checkpoint_visible_to_all_workspaces() -> None:
    store = InMemoryAgentensorCheckpointStore()
    await store.record_checkpoint(
        workflow_id="wf-1", workspace_id=None, **_COMMON_KWARGS
    )
    await store.record_checkpoint(
        workflow_id="wf-1", workspace_id="workspace-a", **_COMMON_KWARGS
    )

    results_a = await store.list_checkpoints("wf-1", workspace_id="workspace-a")
    results_b = await store.list_checkpoints("wf-1", workspace_id="workspace-b")

    tids_a = {c.workspace_id for c in results_a}
    tids_b = {c.workspace_id for c in results_b}
    assert None in tids_a
    assert "workspace-a" in tids_a
    assert None in tids_b
    assert "workspace-a" not in tids_b


@pytest.mark.asyncio
async def test_inmemory_record_checkpoint_stores_workspace_id() -> None:
    store = InMemoryAgentensorCheckpointStore()
    cp = await store.record_checkpoint(
        workflow_id="wf-1", workspace_id="workspace-x", **_COMMON_KWARGS
    )
    assert cp.workspace_id == "workspace-x"


@pytest.mark.asyncio
async def test_inmemory_no_workspace_filter_returns_all() -> None:
    store = InMemoryAgentensorCheckpointStore()
    await store.record_checkpoint(
        workflow_id="wf-1", workspace_id="workspace-a", **_COMMON_KWARGS
    )
    await store.record_checkpoint(
        workflow_id="wf-1", workspace_id="workspace-b", **_COMMON_KWARGS
    )
    all_cps = await store.list_checkpoints("wf-1")
    assert len(all_cps) == 2


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_list_checkpoints_filters_by_workspace(tmp_path: Path) -> None:
    store = SqliteAgentensorCheckpointStore(tmp_path / "cp.db")
    await store.record_checkpoint(
        workflow_id="wf-1", workspace_id="workspace-a", **_COMMON_KWARGS
    )
    await store.record_checkpoint(
        workflow_id="wf-1", workspace_id="workspace-b", **_COMMON_KWARGS
    )

    results_a = await store.list_checkpoints("wf-1", workspace_id="workspace-a")
    results_b = await store.list_checkpoints("wf-1", workspace_id="workspace-b")

    assert len(results_a) == 1
    assert results_a[0].workspace_id == "workspace-a"
    assert len(results_b) == 1
    assert results_b[0].workspace_id == "workspace-b"


@pytest.mark.asyncio
async def test_sqlite_unscoped_checkpoint_visible_to_all_workspaces(
    tmp_path: Path,
) -> None:
    store = SqliteAgentensorCheckpointStore(tmp_path / "cp.db")
    await store.record_checkpoint(
        workflow_id="wf-1", workspace_id=None, **_COMMON_KWARGS
    )
    await store.record_checkpoint(
        workflow_id="wf-1", workspace_id="workspace-a", **_COMMON_KWARGS
    )

    results_a = await store.list_checkpoints("wf-1", workspace_id="workspace-a")
    results_b = await store.list_checkpoints("wf-1", workspace_id="workspace-b")

    tids_a = {c.workspace_id for c in results_a}
    tids_b = {c.workspace_id for c in results_b}
    assert "workspace-a" in tids_a
    assert None not in tids_a
    assert "workspace-a" not in tids_b
    assert None not in tids_b


@pytest.mark.asyncio
async def test_sqlite_record_checkpoint_stores_workspace_id(tmp_path: Path) -> None:
    store = SqliteAgentensorCheckpointStore(tmp_path / "cp.db")
    cp = await store.record_checkpoint(
        workflow_id="wf-1", workspace_id="workspace-x", **_COMMON_KWARGS
    )
    assert cp.workspace_id == "workspace-x"

    fetched = await store.get_checkpoint(cp.id)
    assert fetched.workspace_id == "workspace-x"


@pytest.mark.asyncio
async def test_sqlite_no_workspace_filter_returns_all(tmp_path: Path) -> None:
    store = SqliteAgentensorCheckpointStore(tmp_path / "cp.db")
    await store.record_checkpoint(
        workflow_id="wf-1", workspace_id="workspace-a", **_COMMON_KWARGS
    )
    await store.record_checkpoint(
        workflow_id="wf-1", workspace_id="workspace-b", **_COMMON_KWARGS
    )
    all_cps = await store.list_checkpoints("wf-1")
    assert len(all_cps) == 2
