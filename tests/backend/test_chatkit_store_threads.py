"""Thread-level tests for the in-memory ChatKit store."""

from __future__ import annotations
from datetime import UTC, datetime
import pytest
from chatkit.types import (
    InferenceOptions,
    ThreadMetadata,
    UserMessageInput,
    UserMessageTextContent,
)
from orcheo_backend.app.chatkit import (
    ChatKitRequestContext,
    InMemoryChatKitStore,
)


@pytest.mark.asyncio
async def test_in_memory_store_load_threads_pagination() -> None:
    store = InMemoryChatKitStore()
    context: ChatKitRequestContext = {}

    threads = [
        ThreadMetadata(
            id=f"thr_{i}",
            created_at=datetime(2024, 1, i + 1, tzinfo=UTC),
            metadata={"index": i},
        )
        for i in range(5)
    ]
    for thread in threads:
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
async def test_in_memory_store_load_threads_descending() -> None:
    store = InMemoryChatKitStore()
    context: ChatKitRequestContext = {}

    for i in range(3):
        thread = ThreadMetadata(
            id=f"thr_{i}",
            created_at=datetime(2024, 1, i + 1, tzinfo=UTC),
        )
        await store.save_thread(thread, context)

    page = await store.load_threads(limit=10, after=None, order="desc", context=context)
    assert page.data[0].id == "thr_2"
    assert page.data[-1].id == "thr_0"


@pytest.mark.asyncio
async def test_in_memory_store_delete_thread() -> None:
    store = InMemoryChatKitStore()
    context: ChatKitRequestContext = {}

    thread = ThreadMetadata(id="thr_delete", created_at=datetime.now(UTC))
    await store.save_thread(thread, context)

    await store.delete_thread("thr_delete", context)

    from chatkit.store import NotFoundError

    with pytest.raises(NotFoundError):
        await store.load_thread("thr_delete", context)


@pytest.mark.asyncio
async def test_in_memory_store_attachment_methods_not_implemented() -> None:
    store = InMemoryChatKitStore()
    context: ChatKitRequestContext = {}

    from chatkit.types import FileAttachment

    attachment = FileAttachment(id="atc_1", name="test.txt", mime_type="text/plain")

    with pytest.raises(NotImplementedError):
        await store.save_attachment(attachment, context)

    with pytest.raises(NotImplementedError):
        await store.load_attachment("atc_1", context)

    with pytest.raises(NotImplementedError):
        await store.delete_attachment("atc_1", context)


@pytest.mark.asyncio
async def test_in_memory_store_merge_metadata_from_context() -> None:
    store = InMemoryChatKitStore()

    class FakeRequest:
        metadata = {"workflow_id": "wf_123", "extra": "data"}

    context: ChatKitRequestContext = {
        "chatkit_request": FakeRequest()  # type: ignore[typeddict-item]
    }

    thread = ThreadMetadata(
        id="thr_merge",
        created_at=datetime.now(UTC),
        metadata={"existing": "value"},
    )
    await store.save_thread(thread, context)

    assert thread.metadata["workflow_id"] == "wf_123"
    assert thread.metadata["existing"] == "value"


@pytest.mark.asyncio
async def test_in_memory_store_merge_metadata_without_request() -> None:
    store = InMemoryChatKitStore()
    context: ChatKitRequestContext = {}

    thread = ThreadMetadata(
        id="thr_no_request",
        created_at=datetime.now(UTC),
        metadata={"existing": "value"},
    )
    await store.save_thread(thread, context)

    loaded = await store.load_thread("thr_no_request", context)
    assert loaded.metadata["existing"] == "value"


@pytest.mark.asyncio
async def test_in_memory_store_save_thread_updates_existing_thread() -> None:
    store = InMemoryChatKitStore()
    context: ChatKitRequestContext = {}

    initial = ThreadMetadata(
        id="thr_existing",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        metadata={"version": 1},
    )
    await store.save_thread(initial, context)

    updated = ThreadMetadata(
        id="thr_existing",
        created_at=datetime(2024, 1, 2, tzinfo=UTC),
        metadata={"version": 2},
    )
    await store.save_thread(updated, context)

    loaded = await store.load_thread("thr_existing", context)
    assert loaded.metadata["version"] == 2


@pytest.mark.asyncio
async def test_in_memory_extract_title_from_request_branches() -> None:
    from orcheo_backend.app.chatkit.in_memory_store import (
        _extract_title_from_request,
    )

    assert _extract_title_from_request(None) is None
    assert _extract_title_from_request({"chatkit_request": None}) is None

    class FakeParams:
        input = UserMessageInput(
            content=[
                UserMessageTextContent(text=""),
                UserMessageTextContent(text="  Title from request should trim  "),
            ],
            attachments=[],
            inference_options=InferenceOptions(),
        )

    class FakeRequest:
        metadata: dict = {}
        params = FakeParams()

    class BlankParams:
        input = UserMessageInput(
            content=[UserMessageTextContent(text="   ")],
            attachments=[],
            inference_options=InferenceOptions(),
        )

    class BlankRequest:
        metadata: dict = {}
        params = BlankParams()

    context: ChatKitRequestContext = {
        "chatkit_request": FakeRequest(),  # type: ignore[typeddict-item]
    }
    blank_context: ChatKitRequestContext = {
        "chatkit_request": BlankRequest(),  # type: ignore[typeddict-item]
    }

    assert _extract_title_from_request(context) == "Title from request"
    assert _extract_title_from_request(blank_context) is None


@pytest.mark.asyncio
async def test_in_memory_store_load_threads_isolated_by_workflow() -> None:
    """load_threads should only return threads belonging to the requested workflow."""
    store = InMemoryChatKitStore()

    class FakeRequest:
        def __init__(self, wf_id: str) -> None:
            self.metadata = {"workflow_id": wf_id}

    ctx_a: ChatKitRequestContext = {
        "chatkit_request": FakeRequest("wf_aaa"),  # type: ignore[typeddict-item]
        "workflow_id": "wf_aaa",
    }
    ctx_b: ChatKitRequestContext = {
        "chatkit_request": FakeRequest("wf_bbb"),  # type: ignore[typeddict-item]
        "workflow_id": "wf_bbb",
    }

    thr_a = ThreadMetadata(id="thr_a", created_at=datetime.now(UTC))
    thr_b = ThreadMetadata(id="thr_b", created_at=datetime.now(UTC))

    await store.save_thread(thr_a, ctx_a)
    await store.save_thread(thr_b, ctx_b)

    page_a = await store.load_threads(limit=10, after=None, order="asc", context=ctx_a)
    assert [t.id for t in page_a.data] == ["thr_a"]

    page_b = await store.load_threads(limit=10, after=None, order="asc", context=ctx_b)
    assert [t.id for t in page_b.data] == ["thr_b"]


@pytest.mark.asyncio
async def test_in_memory_store_load_threads_no_workflow_returns_all() -> None:
    """load_threads without workflow_id in context should return all threads."""
    store = InMemoryChatKitStore()
    context: ChatKitRequestContext = {}

    for i in range(3):
        thr = ThreadMetadata(
            id=f"thr_all_{i}",
            created_at=datetime(2024, 1, i + 1, tzinfo=UTC),
        )
        await store.save_thread(thr, context)

    page = await store.load_threads(limit=10, after=None, order="asc", context=context)
    assert len(page.data) == 3


@pytest.mark.asyncio
async def test_in_memory_store_save_thread_sets_title_from_first_user_message() -> None:
    """save_thread should derive title from the first user message when unset."""
    store = InMemoryChatKitStore()

    class FakeParams:
        input = UserMessageInput(
            content=[UserMessageTextContent(text="Hello, world! This is long")],
            attachments=[],
            inference_options=InferenceOptions(),
        )

    class FakeRequest:
        metadata: dict = {}
        params = FakeParams()

    context: ChatKitRequestContext = {
        "chatkit_request": FakeRequest(),  # type: ignore[typeddict-item]
    }

    thread = ThreadMetadata(id="thr_title", created_at=datetime.now(UTC))
    await store.save_thread(thread, context)

    loaded = await store.load_thread("thr_title", context)
    assert loaded.title == "Hello, world! This i"


@pytest.mark.asyncio
async def test_in_memory_store_save_thread_preserves_existing_title() -> None:
    """save_thread should not overwrite a title that is already set."""
    store = InMemoryChatKitStore()

    class FakeParams:
        input = UserMessageInput(
            content=[UserMessageTextContent(text="New message content")],
            attachments=[],
            inference_options=InferenceOptions(),
        )

    class FakeRequest:
        metadata: dict = {}
        params = FakeParams()

    context: ChatKitRequestContext = {
        "chatkit_request": FakeRequest(),  # type: ignore[typeddict-item]
    }

    thread = ThreadMetadata(
        id="thr_existing_title", created_at=datetime.now(UTC), title="Existing Title"
    )
    await store.save_thread(thread, context)

    loaded = await store.load_thread("thr_existing_title", context)
    assert loaded.title == "Existing Title"
