"""Cross-workspace isolation tests for the ChatKit stores."""

from __future__ import annotations
from datetime import UTC, datetime
import pytest
from chatkit.types import ThreadMetadata
from orcheo_backend.app.chatkit.in_memory_store import InMemoryChatKitStore


def _ctx(
    workflow_id: str = "wf-1",
    workspace_id: str | None = None,
    owner_key: str | None = None,
) -> dict:
    ctx: dict = {"workflow_id": workflow_id, "actor": "test", "auth_mode": "publish"}
    if workspace_id is not None:
        ctx["workspace_id"] = workspace_id
    if owner_key is not None:
        ctx["owner_key"] = owner_key
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
# Per-owner (per-user / per-visitor) isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmemory_load_threads_filters_by_owner() -> None:
    store = InMemoryChatKitStore()
    await store.save_thread(_thread("t-alice"), _ctx(owner_key="sub:alice"))
    await store.save_thread(_thread("t-bob"), _ctx(owner_key="visitor:bob"))

    page_alice = await store.load_threads(10, None, "desc", _ctx(owner_key="sub:alice"))
    page_bob = await store.load_threads(10, None, "desc", _ctx(owner_key="visitor:bob"))

    assert {t.id for t in page_alice.data} == {"t-alice"}
    assert {t.id for t in page_bob.data} == {"t-bob"}


@pytest.mark.asyncio
async def test_inmemory_load_thread_blocks_other_owner() -> None:
    from chatkit.store import NotFoundError

    store = InMemoryChatKitStore()
    await store.save_thread(_thread("t-alice"), _ctx(owner_key="sub:alice"))

    # The owner can read it; a different owner cannot.
    assert (
        await store.load_thread("t-alice", _ctx(owner_key="sub:alice"))
    ).id == "t-alice"
    with pytest.raises(NotFoundError):
        await store.load_thread("t-alice", _ctx(owner_key="visitor:mallory"))


@pytest.mark.asyncio
async def test_inmemory_delete_thread_blocks_other_owner() -> None:
    store = InMemoryChatKitStore()
    await store.save_thread(_thread("t-alice"), _ctx(owner_key="sub:alice"))

    # A foreign owner's delete is a no-op; the owner's delete succeeds.
    await store.delete_thread("t-alice", _ctx(owner_key="visitor:mallory"))
    assert (
        await store.load_thread("t-alice", _ctx(owner_key="sub:alice"))
    ).id == "t-alice"
    await store.delete_thread("t-alice", _ctx(owner_key="sub:alice"))
    page = await store.load_threads(10, None, "desc", _ctx(owner_key="sub:alice"))
    assert page.data == []


@pytest.mark.asyncio
async def test_inmemory_owner_not_reassigned_on_resave() -> None:
    """A second save with a different owner must not hijack an existing thread."""
    store = InMemoryChatKitStore()
    await store.save_thread(_thread("t-alice"), _ctx(owner_key="sub:alice"))
    await store.save_thread(_thread("t-alice"), _ctx(owner_key="visitor:mallory"))

    page_owner = await store.load_threads(10, None, "desc", _ctx(owner_key="sub:alice"))
    page_attacker = await store.load_threads(
        10, None, "desc", _ctx(owner_key="visitor:mallory")
    )
    assert {t.id for t in page_owner.data} == {"t-alice"}
    assert page_attacker.data == []
