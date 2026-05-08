"""Cross-workspace isolation tests for the execution history store."""

from __future__ import annotations
from pathlib import Path
import pytest
from orcheo_backend.app.history.in_memory import InMemoryRunHistoryStore
from orcheo_backend.app.history.sqlite_store import SqliteRunHistoryStore


# ---------------------------------------------------------------------------
# InMemory store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmemory_list_histories_filters_by_workspace() -> None:
    store = InMemoryRunHistoryStore()
    await store.start_run(
        workflow_id="wf-1", execution_id="exec-a", workspace_id="workspace-a"
    )
    await store.start_run(
        workflow_id="wf-1", execution_id="exec-b", workspace_id="workspace-b"
    )

    records_a = await store.list_histories("wf-1", workspace_id="workspace-a")
    records_b = await store.list_histories("wf-1", workspace_id="workspace-b")

    assert len(records_a) == 1
    assert records_a[0].execution_id == "exec-a"
    assert len(records_b) == 1
    assert records_b[0].execution_id == "exec-b"


@pytest.mark.asyncio
async def test_inmemory_list_histories_unscoped_visible_to_all_workspaces() -> None:
    store = InMemoryRunHistoryStore()
    await store.start_run(
        workflow_id="wf-1", execution_id="exec-unscoped", workspace_id=None
    )
    await store.start_run(
        workflow_id="wf-1", execution_id="exec-a", workspace_id="workspace-a"
    )

    records_a = await store.list_histories("wf-1", workspace_id="workspace-a")
    records_b = await store.list_histories("wf-1", workspace_id="workspace-b")

    # workspace-a sees its own record AND the unscoped record
    exec_ids_a = {r.execution_id for r in records_a}
    assert "exec-unscoped" in exec_ids_a
    assert "exec-a" in exec_ids_a

    # workspace-b sees only the unscoped record (not workspace-a's)
    exec_ids_b = {r.execution_id for r in records_b}
    assert "exec-unscoped" in exec_ids_b
    assert "exec-a" not in exec_ids_b


@pytest.mark.asyncio
async def test_inmemory_start_run_records_workspace_id() -> None:
    store = InMemoryRunHistoryStore()
    record = await store.start_run(
        workflow_id="wf-1", execution_id="exec-1", workspace_id="workspace-x"
    )
    assert record.workspace_id == "workspace-x"

    fetched = await store.get_history("exec-1")
    assert fetched.workspace_id == "workspace-x"


@pytest.mark.asyncio
async def test_inmemory_list_histories_no_workspace_filter_returns_all() -> None:
    store = InMemoryRunHistoryStore()
    await store.start_run(
        workflow_id="wf-1", execution_id="exec-a", workspace_id="workspace-a"
    )
    await store.start_run(
        workflow_id="wf-1", execution_id="exec-b", workspace_id="workspace-b"
    )

    all_records = await store.list_histories("wf-1")
    assert len(all_records) == 2


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_list_histories_filters_by_workspace(tmp_path: Path) -> None:
    store = SqliteRunHistoryStore(tmp_path / "history.db")
    await store.start_run(
        workflow_id="wf-1", execution_id="exec-a", workspace_id="workspace-a"
    )
    await store.start_run(
        workflow_id="wf-1", execution_id="exec-b", workspace_id="workspace-b"
    )

    records_a = await store.list_histories("wf-1", workspace_id="workspace-a")
    records_b = await store.list_histories("wf-1", workspace_id="workspace-b")

    assert len(records_a) == 1
    assert records_a[0].execution_id == "exec-a"
    assert len(records_b) == 1
    assert records_b[0].execution_id == "exec-b"


@pytest.mark.asyncio
async def test_sqlite_list_histories_unscoped_visible_to_all_workspaces(
    tmp_path: Path,
) -> None:
    store = SqliteRunHistoryStore(tmp_path / "history.db")
    await store.start_run(
        workflow_id="wf-1", execution_id="exec-unscoped", workspace_id=None
    )
    await store.start_run(
        workflow_id="wf-1", execution_id="exec-a", workspace_id="workspace-a"
    )

    records_a = await store.list_histories("wf-1", workspace_id="workspace-a")
    records_b = await store.list_histories("wf-1", workspace_id="workspace-b")

    exec_ids_a = {r.execution_id for r in records_a}
    assert "exec-a" in exec_ids_a
    assert "exec-unscoped" not in exec_ids_a

    exec_ids_b = {r.execution_id for r in records_b}
    assert "exec-a" not in exec_ids_b
    assert "exec-unscoped" not in exec_ids_b


@pytest.mark.asyncio
async def test_sqlite_start_run_records_workspace_id(tmp_path: Path) -> None:
    store = SqliteRunHistoryStore(tmp_path / "history.db")
    record = await store.start_run(
        workflow_id="wf-1", execution_id="exec-1", workspace_id="workspace-x"
    )
    assert record.workspace_id == "workspace-x"

    fetched = await store.get_history("exec-1")
    assert fetched.workspace_id == "workspace-x"


@pytest.mark.asyncio
async def test_sqlite_list_histories_no_workspace_filter_returns_all(
    tmp_path: Path,
) -> None:
    store = SqliteRunHistoryStore(tmp_path / "history.db")
    await store.start_run(
        workflow_id="wf-1", execution_id="exec-a", workspace_id="workspace-a"
    )
    await store.start_run(
        workflow_id="wf-1", execution_id="exec-b", workspace_id="workspace-b"
    )

    all_records = await store.list_histories("wf-1")
    assert len(all_records) == 2
