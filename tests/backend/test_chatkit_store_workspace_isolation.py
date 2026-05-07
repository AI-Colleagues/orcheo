"""Cross-workspace isolation tests for the ChatKit stores."""

from __future__ import annotations
from datetime import UTC, datetime
from pathlib import Path
import pytest
from chatkit.types import ThreadMetadata
from orcheo_backend.app.chatkit.in_memory_store import InMemoryChatKitStore
from orcheo_backend.app.chatkit_store_sqlite.store import SqliteChatKitStore


def _ctx(workflow_id: str = "wf-1", workspace_id: str | None = None) -> dict:
    ctx: dict = {"workflow_id": workflow_id, "actor": "test", "auth_mode": "publish"}
    if workspace_id is not None:
        ctx["workspace_id"] = workspace_id
    return ctx


def _thread(thread_id: str, workflow_id: str = "wf-1") -> ThreadMetadata:
    return ThreadMetadata(
        id=thread_id,
        created_at=datetime.now(UTC),
        metadata={"workflow_id": workflow_id},
    )


# ---------------------------------------------------------------------------
# InMemory store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmemory_load_threads_filters_by_workspace() -> None:
    store = InMemoryChatKitStore()
    await store.save_thread(_thread("t-a"), _ctx(workspace_id="workspace-a"))
    await store.save_thread(_thread("t-b"), _ctx(workspace_id="workspace-b"))

    page_a = await store.load_threads(
        10, None, "desc", _ctx(workspace_id="workspace-a")
    )
    page_b = await store.load_threads(
        10, None, "desc", _ctx(workspace_id="workspace-b")
    )

    ids_a = {t.id for t in page_a.data}
    ids_b = {t.id for t in page_b.data}
    assert "t-a" in ids_a
    assert "t-b" not in ids_a
    assert "t-b" in ids_b
    assert "t-a" not in ids_b


@pytest.mark.asyncio
async def test_inmemory_unscoped_thread_visible_to_all_workspaces() -> None:
    store = InMemoryChatKitStore()
    await store.save_thread(_thread("t-shared"), _ctx())
    await store.save_thread(_thread("t-a"), _ctx(workspace_id="workspace-a"))

    page_a = await store.load_threads(
        10, None, "desc", _ctx(workspace_id="workspace-a")
    )
    page_b = await store.load_threads(
        10, None, "desc", _ctx(workspace_id="workspace-b")
    )

    ids_a = {t.id for t in page_a.data}
    ids_b = {t.id for t in page_b.data}
    assert "t-shared" in ids_a
    assert "t-a" in ids_a
    assert "t-shared" in ids_b
    assert "t-a" not in ids_b


@pytest.mark.asyncio
async def test_inmemory_no_workspace_filter_returns_all() -> None:
    store = InMemoryChatKitStore()
    await store.save_thread(_thread("t-a"), _ctx(workspace_id="workspace-a"))
    await store.save_thread(_thread("t-b"), _ctx(workspace_id="workspace-b"))

    page = await store.load_threads(10, None, "desc", _ctx())
    assert len(page.data) == 2


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_load_threads_filters_by_workspace(tmp_path: Path) -> None:
    store = SqliteChatKitStore(tmp_path / "chatkit.db")
    await store.save_thread(_thread("t-a"), _ctx(workspace_id="workspace-a"))
    await store.save_thread(_thread("t-b"), _ctx(workspace_id="workspace-b"))

    page_a = await store.load_threads(
        10, None, "desc", _ctx(workspace_id="workspace-a")
    )
    page_b = await store.load_threads(
        10, None, "desc", _ctx(workspace_id="workspace-b")
    )

    ids_a = {t.id for t in page_a.data}
    ids_b = {t.id for t in page_b.data}
    assert "t-a" in ids_a
    assert "t-b" not in ids_a
    assert "t-b" in ids_b
    assert "t-a" not in ids_b


@pytest.mark.asyncio
async def test_sqlite_unscoped_thread_visible_to_all_workspaces(tmp_path: Path) -> None:
    store = SqliteChatKitStore(tmp_path / "chatkit.db")
    await store.save_thread(_thread("t-shared"), _ctx())
    await store.save_thread(_thread("t-a"), _ctx(workspace_id="workspace-a"))

    page_a = await store.load_threads(
        10, None, "desc", _ctx(workspace_id="workspace-a")
    )
    page_b = await store.load_threads(
        10, None, "desc", _ctx(workspace_id="workspace-b")
    )

    ids_a = {t.id for t in page_a.data}
    ids_b = {t.id for t in page_b.data}
    assert "t-shared" in ids_a
    assert "t-a" in ids_a
    assert "t-shared" in ids_b
    assert "t-a" not in ids_b


@pytest.mark.asyncio
async def test_sqlite_no_workspace_filter_returns_all(tmp_path: Path) -> None:
    store = SqliteChatKitStore(tmp_path / "chatkit.db")
    await store.save_thread(_thread("t-a"), _ctx(workspace_id="workspace-a"))
    await store.save_thread(_thread("t-b"), _ctx(workspace_id="workspace-b"))

    page = await store.load_threads(10, None, "desc", _ctx())
    assert len(page.data) == 2


@pytest.mark.asyncio
async def test_sqlite_workspace_id_stored_on_thread(tmp_path: Path) -> None:
    store = SqliteChatKitStore(tmp_path / "chatkit.db")
    await store.save_thread(_thread("t-x"), _ctx(workspace_id="workspace-x"))

    page = await store.load_threads(10, None, "desc", _ctx(workspace_id="workspace-x"))
    assert len(page.data) == 1
    assert page.data[0].id == "t-x"
