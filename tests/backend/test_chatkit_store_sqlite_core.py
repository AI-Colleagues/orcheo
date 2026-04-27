"""Core behavior tests for the SQLite-backed ChatKit store."""

from __future__ import annotations
import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
import pytest
from chatkit.store import NotFoundError
from chatkit.types import (
    AssistantMessageContent,
    AssistantMessageItem,
    FileAttachment,
    InferenceOptions,
    ThreadMetadata,
    UserMessageInput,
    UserMessageItem,
    UserMessageTextContent,
)
from orcheo_backend.app.chatkit_store_sqlite import SqliteChatKitStore


def _timestamp() -> datetime:
    return datetime.now(tz=UTC)


@pytest.mark.asyncio
async def test_sqlite_store_persists_conversation(tmp_path: Path) -> None:
    """Threads, items, and attachments should round-trip through SQLite."""

    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)
    context: dict[str, object] = {}

    thread = ThreadMetadata(
        id="thr_sqlite",
        created_at=_timestamp(),
        metadata={"workflow_id": "abcd"},
    )
    await store.save_thread(thread, context)

    loaded_thread = await store.load_thread(thread.id, context)
    assert loaded_thread.metadata["workflow_id"] == "abcd"

    user_item = UserMessageItem(
        id="msg_user",
        thread_id=thread.id,
        created_at=_timestamp(),
        content=[UserMessageTextContent(type="input_text", text="Ping")],
        attachments=[],
        quoted_text=None,
        inference_options=InferenceOptions(),
    )
    await store.add_thread_item(thread.id, user_item, context)

    items_page = await store.load_thread_items(
        thread.id,
        after=None,
        limit=10,
        order="asc",
        context=context,
    )
    assert len(items_page.data) == 1
    assert isinstance(items_page.data[0], UserMessageItem)

    assistant_item = AssistantMessageItem(
        id="msg_assistant",
        thread_id=thread.id,
        created_at=_timestamp(),
        content=[AssistantMessageContent(text="Pong")],
    )
    await store.save_item(thread.id, assistant_item, context)

    loaded_item = await store.load_item(thread.id, assistant_item.id, context)
    assert isinstance(loaded_item, AssistantMessageItem)
    assert loaded_item.content[0].text == "Pong"

    await store.delete_thread_item(thread.id, user_item.id, context)
    items_after_delete = await store.load_thread_items(
        thread.id,
        after=None,
        limit=10,
        order="asc",
        context=context,
    )
    assert len(items_after_delete.data) == 1
    assert items_after_delete.data[0].id == assistant_item.id

    attachment = FileAttachment(
        id="atc_file",
        name="demo.txt",
        mime_type="text/plain",
    )
    await store.save_attachment(attachment, context)

    loaded_attachment = await store.load_attachment(attachment.id, context)
    assert loaded_attachment.name == attachment.name

    await store.delete_attachment(attachment.id, context)
    with pytest.raises(NotFoundError):
        await store.load_attachment(attachment.id, context)

    await store.delete_thread(thread.id, context)
    with pytest.raises(NotFoundError):
        await store.load_thread(thread.id, context)


@pytest.mark.asyncio
async def test_sqlite_store_save_item_update_existing(tmp_path: Path) -> None:
    """Save item should update an existing item."""
    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)
    context: dict[str, object] = {}

    thread = ThreadMetadata(id="thr_update", created_at=_timestamp())
    await store.save_thread(thread, context)

    item = UserMessageItem(
        id="msg_update",
        thread_id=thread.id,
        created_at=_timestamp(),
        content=[UserMessageTextContent(type="input_text", text="Original")],
        attachments=[],
        quoted_text=None,
        inference_options=InferenceOptions(),
    )
    await store.add_thread_item(thread.id, item, context)

    updated_item = UserMessageItem(
        id="msg_update",
        thread_id=thread.id,
        created_at=_timestamp(),
        content=[UserMessageTextContent(type="input_text", text="Updated")],
        attachments=[],
        quoted_text=None,
        inference_options=InferenceOptions(),
    )
    await store.save_item(thread.id, updated_item, context)

    loaded = await store.load_item(thread.id, item.id, context)
    assert loaded.content[0].text == "Updated"


@pytest.mark.asyncio
async def test_sqlite_store_naive_datetime_conversion(tmp_path: Path) -> None:
    """Store should handle naive datetime by adding UTC timezone."""
    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)
    context: dict[str, object] = {}

    naive_dt = datetime(2024, 1, 1, 12, 0, 0)
    thread = ThreadMetadata(
        id="thr_naive",
        created_at=naive_dt,
    )

    await store.save_thread(thread, context)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "SELECT created_at FROM chat_threads WHERE id = ?", ("thr_naive",)
        )
        row = cursor.fetchone()
        assert row is not None
        assert "+00:00" in row[0] or "Z" in row[0] or row[0].endswith("+00:00")


@pytest.mark.asyncio
async def test_sqlite_store_already_initialized(tmp_path: Path) -> None:
    """Store should skip initialization when already initialized."""
    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)
    context: dict[str, object] = {}

    thread = ThreadMetadata(id="thr_init", created_at=_timestamp())
    await store.save_thread(thread, context)

    assert store._initialized is True

    thread2 = ThreadMetadata(id="thr_init2", created_at=_timestamp())
    await store.save_thread(thread2, context)

    loaded = await store.load_thread("thr_init2", context)
    assert loaded.id == "thr_init2"


@pytest.mark.asyncio
async def test_sqlite_store_concurrent_initialization(tmp_path: Path) -> None:
    """Store should handle concurrent initialization attempts safely."""

    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)
    context: dict[str, object] = {}

    async def save_thread_task(thread_id: str) -> None:
        thread = ThreadMetadata(id=thread_id, created_at=_timestamp())
        await store.save_thread(thread, context)

    await asyncio.gather(
        save_thread_task("thr_concurrent_1"),
        save_thread_task("thr_concurrent_2"),
        save_thread_task("thr_concurrent_3"),
    )

    thread1 = await store.load_thread("thr_concurrent_1", context)
    thread2 = await store.load_thread("thr_concurrent_2", context)
    thread3 = await store.load_thread("thr_concurrent_3", context)

    assert thread1.id == "thr_concurrent_1"
    assert thread2.id == "thr_concurrent_2"
    assert thread3.id == "thr_concurrent_3"


@pytest.mark.asyncio
async def test_sqlite_store_load_threads_isolated_by_workflow(
    tmp_path: Path,
) -> None:
    """load_threads should only return threads belonging to the requested workflow."""
    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)

    ctx_a: dict[str, object] = {"workflow_id": "wf-aaa"}
    ctx_b: dict[str, object] = {"workflow_id": "wf-bbb"}

    thr_a = ThreadMetadata(
        id="thr_iso_a",
        created_at=_timestamp(),
        metadata={"workflow_id": "wf-aaa"},
    )
    thr_b = ThreadMetadata(
        id="thr_iso_b",
        created_at=_timestamp(),
        metadata={"workflow_id": "wf-bbb"},
    )

    await store.save_thread(thr_a, ctx_a)
    await store.save_thread(thr_b, ctx_b)

    page_a = await store.load_threads(limit=10, after=None, order="asc", context=ctx_a)
    assert [t.id for t in page_a.data] == ["thr_iso_a"]

    page_b = await store.load_threads(limit=10, after=None, order="asc", context=ctx_b)
    assert [t.id for t in page_b.data] == ["thr_iso_b"]


@pytest.mark.asyncio
async def test_sqlite_store_load_threads_no_workflow_returns_all(
    tmp_path: Path,
) -> None:
    """load_threads without workflow_id in context should return all threads."""
    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)
    context: dict[str, object] = {}

    for i in range(3):
        thr = ThreadMetadata(id=f"thr_nofilter_{i}", created_at=_timestamp())
        await store.save_thread(thr, context)

    page = await store.load_threads(limit=10, after=None, order="asc", context=context)
    assert len(page.data) == 3


@pytest.mark.asyncio
async def test_sqlite_store_save_thread_sets_title_from_first_user_message(
    tmp_path: Path,
) -> None:
    """save_thread should derive title from the first user text when unset."""
    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)

    class FakeParams:
        input = UserMessageInput(
            content=[UserMessageTextContent(text="Hello, world! This is long")],
            attachments=[],
            inference_options=InferenceOptions(),
        )

    class FakeRequest:
        metadata: dict = {}
        params = FakeParams()

    context: dict[str, object] = {"chatkit_request": FakeRequest()}

    thread = ThreadMetadata(id="thr_autotitle", created_at=_timestamp())
    await store.save_thread(thread, context)

    loaded = await store.load_thread("thr_autotitle", context)
    assert loaded.title == "Hello, world! This i"


@pytest.mark.asyncio
async def test_sqlite_store_save_thread_preserves_existing_title(
    tmp_path: Path,
) -> None:
    """save_thread should not overwrite a title that is already set."""
    db_path = tmp_path / "store.sqlite"
    store = SqliteChatKitStore(db_path)

    class FakeParams:
        input = UserMessageInput(
            content=[UserMessageTextContent(text="New message content")],
            attachments=[],
            inference_options=InferenceOptions(),
        )

    class FakeRequest:
        metadata: dict = {}
        params = FakeParams()

    context: dict[str, object] = {"chatkit_request": FakeRequest()}

    thread = ThreadMetadata(
        id="thr_keeptitle", created_at=_timestamp(), title="Existing Title"
    )
    await store.save_thread(thread, context)

    loaded = await store.load_thread("thr_keeptitle", context)
    assert loaded.title == "Existing Title"
