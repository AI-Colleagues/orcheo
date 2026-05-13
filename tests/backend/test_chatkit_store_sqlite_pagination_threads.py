"""Thread pagination tests for the SQLite ChatKit store."""

from __future__ import annotations
from datetime import UTC, datetime
from pathlib import Path
import pytest
from chatkit.types import ThreadMetadata
from orcheo_backend.app.chatkit_store_sqlite import SqliteChatKitStore


def _timestamp(hour: int) -> datetime:
    return datetime(2024, 1, 1, hour=hour, tzinfo=UTC)


@pytest.mark.asyncio
async def test_sqlite_store_load_threads_with_pagination(tmp_path: Path) -> None:
    """SQLite store supports cursor-based pagination for threads."""
    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)
    context: dict[str, object] = {}

    for i in range(5):
        thread = ThreadMetadata(
            id=f"thr_{i}",
            created_at=_timestamp(i + 1),
            metadata={"index": i},
        )
        await store.save_thread(thread, context)

    page1 = await store.load_threads(limit=2, after=None, order="asc", context=context)
    assert len(page1.data) == 2
    assert page1.has_more is True
    assert page1.data[0].id == "thr_0"

    page2 = await store.load_threads(
        limit=2, after=page1.data[-1].id, order="asc", context=context
    )
    assert len(page2.data) == 2
    assert page2.data[0].id == "thr_2"


@pytest.mark.asyncio
async def test_sqlite_store_load_threads_descending(tmp_path: Path) -> None:
    """SQLite store supports descending order for threads."""
    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)
    context: dict[str, object] = {}

    for i in range(3):
        thread = ThreadMetadata(
            id=f"thr_{i}",
            created_at=_timestamp(i + 1),
        )
        await store.save_thread(thread, context)

    page = await store.load_threads(limit=10, after=None, order="desc", context=context)
    assert page.data[0].id == "thr_2"
    assert page.data[-1].id == "thr_0"


@pytest.mark.asyncio
async def test_sqlite_store_load_threads_pagination_with_after_marker(
    tmp_path: Path,
) -> None:
    """Load threads should correctly handle pagination with after cursor."""
    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)
    context: dict[str, object] = {}

    for i in range(5):
        thread = ThreadMetadata(
            id=f"thr_{i}",
            created_at=_timestamp(i),
        )
        await store.save_thread(thread, context)

    first_page = await store.load_threads(
        limit=2, after=None, order="asc", context=context
    )
    assert len(first_page.data) == 2
    assert first_page.has_more is True

    second_page = await store.load_threads(
        limit=2, after=first_page.data[-1].id, order="asc", context=context
    )
    assert len(second_page.data) == 2
    assert second_page.data[0].id != first_page.data[-1].id


@pytest.mark.asyncio
async def test_sqlite_store_load_threads_pagination_desc_with_after(
    tmp_path: Path,
) -> None:
    """Load threads descending should correctly handle pagination with after."""
    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)
    context: dict[str, object] = {}

    for i in range(5):
        thread = ThreadMetadata(
            id=f"thr_{i}",
            created_at=_timestamp(i),
        )
        await store.save_thread(thread, context)

    first_page = await store.load_threads(
        limit=2, after=None, order="desc", context=context
    )
    assert len(first_page.data) == 2
    assert first_page.has_more is True

    second_page = await store.load_threads(
        limit=2, after=first_page.data[-1].id, order="desc", context=context
    )
    assert len(second_page.data) == 2
    assert second_page.data[0].id != first_page.data[-1].id


@pytest.mark.asyncio
async def test_sqlite_store_load_threads_with_invalid_after(tmp_path: Path) -> None:
    """Load threads should handle invalid after cursor gracefully."""
    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)
    context: dict[str, object] = {}

    for i in range(3):
        thread = ThreadMetadata(
            id=f"thr_{i}",
            created_at=_timestamp(i),
        )
        await store.save_thread(thread, context)

    page = await store.load_threads(
        limit=10, after="nonexistent_thread", order="asc", context=context
    )
    assert len(page.data) == 3


@pytest.mark.asyncio
async def test_sqlite_store_load_threads_scoped_by_workflow_after_marker(
    tmp_path: Path,
) -> None:
    """Pagination cursor lookups should stay scoped to the workflow."""
    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)

    ctx_a: dict[str, object] = {"workflow_id": "wf-aaa"}
    ctx_b: dict[str, object] = {"workflow_id": "wf-bbb"}

    for thread_id, ctx, hour in [
        ("thr_a_0", ctx_a, 1),
        ("thr_a_1", ctx_a, 2),
        ("thr_b_0", ctx_b, 3),
    ]:
        thread = ThreadMetadata(
            id=thread_id,
            created_at=_timestamp(hour),
            metadata={"workflow_id": ctx["workflow_id"]},
        )
        await store.save_thread(thread, ctx)

    first_page = await store.load_threads(
        limit=1, after=None, order="asc", context=ctx_a
    )
    assert [thread.id for thread in first_page.data] == ["thr_a_0"]

    second_page = await store.load_threads(
        limit=1,
        after=first_page.data[-1].id,
        order="asc",
        context=ctx_a,
    )
    assert [thread.id for thread in second_page.data] == ["thr_a_1"]


@pytest.mark.asyncio
async def test_sqlite_store_load_threads_scoped_by_workspace_after_marker(
    tmp_path: Path,
) -> None:
    """Pagination should remain workspace-scoped when resolving the after marker."""

    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)

    ctx: dict[str, object] = {
        "workflow_id": "wf-aaa",
        "workspace_id": "workspace-1",
    }

    for thread_id, hour in [("thr_a_0", 1), ("thr_a_1", 2), ("thr_b_0", 3)]:
        thread = ThreadMetadata(
            id=thread_id,
            created_at=_timestamp(hour),
            metadata={"workflow_id": ctx["workflow_id"]},
        )
        await store.save_thread(thread, ctx)

    first_page = await store.load_threads(
        limit=1,
        after=None,
        order="asc",
        context=ctx,
    )
    assert [thread.id for thread in first_page.data] == ["thr_a_0"]

    second_page = await store.load_threads(
        limit=1,
        after=first_page.data[-1].id,
        order="asc",
        context=ctx,
    )
    assert [thread.id for thread in second_page.data] == ["thr_a_1"]
