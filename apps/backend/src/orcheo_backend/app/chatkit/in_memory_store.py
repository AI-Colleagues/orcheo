"""In-memory ChatKit store used in development and tests."""

from __future__ import annotations
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from chatkit.store import Attachment, NotFoundError, Page, Store
from chatkit.types import Thread, ThreadItem, ThreadMetadata
from orcheo_backend.app.chatkit.context import ChatKitRequestContext


def _extract_title_from_request(context: ChatKitRequestContext | None) -> str | None:
    """Return first 20 chars of the first user text content in the request."""
    if not context:
        return None
    request = context.get("chatkit_request")
    if request is None:
        return None
    params = getattr(request, "params", None)
    user_input = getattr(params, "input", None)
    for item in getattr(user_input, "content", []):
        text = getattr(item, "text", None)
        if text:
            return text[:20].strip() or None
    return None


@dataclass
class _ThreadState:
    """Internal storage for thread metadata and items."""

    thread: ThreadMetadata
    items: list[ThreadItem]
    tenant_id: str | None = None


class InMemoryChatKitStore(Store[ChatKitRequestContext]):
    """Simple in-memory store retaining threads and items for ChatKit."""

    def __init__(self) -> None:
        """Initialise the backing storage structures."""
        self._threads: dict[str, _ThreadState] = {}
        self._lock = asyncio.Lock()

    # -- Helpers ---------------------------------------------------------
    @staticmethod
    def _clone_metadata(thread: ThreadMetadata | Thread) -> ThreadMetadata:
        """Return a metadata-only clone of the thread."""
        data = thread.model_dump()
        data.pop("items", None)
        return ThreadMetadata(**data)

    def _state_for(self, thread_id: str) -> _ThreadState:
        state = self._threads.get(thread_id)
        if state is None:
            state = _ThreadState(
                thread=ThreadMetadata(
                    id=thread_id,
                    created_at=datetime.now(UTC),
                ),
                items=[],
            )
            self._threads[thread_id] = state
        return state

    def _merge_metadata_from_context(
        self, thread: ThreadMetadata, context: ChatKitRequestContext
    ) -> None:
        metadata = getattr(context.get("chatkit_request"), "metadata", None)
        if not metadata:
            return
        merged = {**thread.metadata, **metadata}
        thread.metadata = merged

    # -- Thread metadata -------------------------------------------------
    async def load_thread(
        self, thread_id: str, context: ChatKitRequestContext
    ) -> ThreadMetadata:
        """Return stored metadata for ``thread_id`` or raise if missing."""
        async with self._lock:
            state = self._threads.get(thread_id)
            if state is None:
                raise NotFoundError(f"Thread {thread_id} not found")
            return self._clone_metadata(state.thread)

    async def save_thread(
        self, thread: ThreadMetadata, context: ChatKitRequestContext
    ) -> None:
        """Persist metadata for ``thread`` while merging incoming context metadata."""
        if not thread.title:
            thread.title = _extract_title_from_request(context)
        tenant_id: str | None = context.get("tenant_id") if context else None
        async with self._lock:
            self._merge_metadata_from_context(thread, context)
            existing = self._threads.get(thread.id)
            metadata = self._clone_metadata(thread)
            if existing:
                existing.thread = metadata
                existing.tenant_id = tenant_id
            else:
                self._threads[thread.id] = _ThreadState(
                    thread=metadata, items=[], tenant_id=tenant_id
                )

    async def load_threads(
        self,
        limit: int,
        after: str | None,
        order: str,
        context: ChatKitRequestContext,
    ) -> Page[ThreadMetadata]:
        """Return a page of stored thread metadata scoped to the workflow in context."""
        workflow_id: str | None = context.get("workflow_id") if context else None
        tenant_id: str | None = context.get("tenant_id") if context else None
        async with self._lock:
            all_states = list(self._threads.values())
            if workflow_id:
                all_states = [
                    s
                    for s in all_states
                    if s.thread.metadata.get("workflow_id") == workflow_id
                ]
            if tenant_id is not None:
                all_states = [
                    s
                    for s in all_states
                    if s.tenant_id is None or s.tenant_id == tenant_id
                ]
            all_threads = (self._clone_metadata(s.thread) for s in all_states)
            threads = sorted(
                all_threads,
                key=lambda t: t.created_at or datetime.min,
                reverse=(order == "desc"),
            )

            if after:
                index_map = {thread.id: idx for idx, thread in enumerate(threads)}
                start = index_map.get(after, -1) + 1
            else:
                start = 0

            slice_threads = threads[start : start + limit + 1]
            has_more = len(slice_threads) > limit
            slice_threads = slice_threads[:limit]
            next_after = slice_threads[-1].id if has_more and slice_threads else None
            return Page(
                data=slice_threads,
                has_more=has_more,
                after=next_after,
            )

    async def delete_thread(
        self, thread_id: str, context: ChatKitRequestContext
    ) -> None:
        """Remove the stored thread state if present."""
        async with self._lock:
            self._threads.pop(thread_id, None)

    # -- Thread items ----------------------------------------------------
    async def load_thread_items(
        self,
        thread_id: str,
        after: str | None,
        limit: int,
        order: str,
        context: ChatKitRequestContext,
    ) -> Page[ThreadItem]:
        """Return a page of thread items for ``thread_id``."""
        async with self._lock:
            state = self._state_for(thread_id)
            items = [item.model_copy(deep=True) for item in state.items]
            items.sort(
                key=lambda item: getattr(item, "created_at", datetime.now(UTC)),
                reverse=(order == "desc"),
            )

            if after:
                index_map = {item.id: idx for idx, item in enumerate(items)}
                start = index_map.get(after, -1) + 1
            else:
                start = 0

            slice_items = items[start : start + limit + 1]
            has_more = len(slice_items) > limit
            slice_items = slice_items[:limit]
            next_after = slice_items[-1].id if has_more and slice_items else None
            return Page(data=slice_items, has_more=has_more, after=next_after)

    async def add_thread_item(
        self, thread_id: str, item: ThreadItem, context: ChatKitRequestContext
    ) -> None:
        """Append ``item`` to the stored history for ``thread_id``."""
        async with self._lock:
            self._state_for(thread_id).items.append(item.model_copy(deep=True))

    async def save_item(
        self, thread_id: str, item: ThreadItem, context: ChatKitRequestContext
    ) -> None:
        """Insert or replace ``item`` in the stored history."""
        async with self._lock:
            items = self._state_for(thread_id).items
            for idx, existing in enumerate(items):
                if existing.id == item.id:
                    items[idx] = item.model_copy(deep=True)
                    return
            items.append(item.model_copy(deep=True))

    async def load_item(
        self, thread_id: str, item_id: str, context: ChatKitRequestContext
    ) -> ThreadItem:
        """Return a single stored item or raise if missing."""
        async with self._lock:
            for item in self._state_for(thread_id).items:
                if item.id == item_id:
                    return item.model_copy(deep=True)
        raise NotFoundError(f"Item {item_id} not found")

    async def delete_thread_item(
        self, thread_id: str, item_id: str, context: ChatKitRequestContext
    ) -> None:
        """Remove an item from the stored history if present."""
        async with self._lock:
            state = self._state_for(thread_id)
            state.items = [item for item in state.items if item.id != item_id]

    # -- Attachments -----------------------------------------------------
    async def save_attachment(
        self,
        attachment: Attachment,
        context: ChatKitRequestContext,
        *,
        storage_path: str | None = None,
    ) -> None:
        """Persist an attachment entry."""
        raise NotImplementedError(
            "Attachment upload is not supported. Provide a real store implementation "
            "before enabling file uploads."
        )

    async def load_attachment(
        self, attachment_id: str, context: ChatKitRequestContext
    ) -> Attachment:
        """Return a stored attachment or raise if unsupported."""
        raise NotImplementedError(
            "Attachments are not stored in the in-memory ChatKit store."
        )

    async def delete_attachment(
        self, attachment_id: str, context: ChatKitRequestContext
    ) -> None:
        """Remove a stored attachment."""
        raise NotImplementedError(
            "Attachments are not stored in the in-memory ChatKit store."
        )


__all__ = ["InMemoryChatKitStore"]
