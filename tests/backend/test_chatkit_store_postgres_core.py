"""Core behavior tests for the PostgreSQL-backed ChatKit store."""

from __future__ import annotations
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import pytest
from chatkit.store import NotFoundError
from chatkit.types import (
    FileAttachment,
    InferenceOptions,
    ThreadMetadata,
    UserMessageInput,
    UserMessageItem,
    UserMessageTextContent,
)
from pydantic import ValidationError
from orcheo_backend.app.chatkit_store_postgres import PostgresChatKitStore
from orcheo_backend.app.chatkit_store_postgres import base as pg_base
from orcheo_backend.app.chatkit_store_postgres.schema import (
    POSTGRES_CHATKIT_SCHEMA,
    ensure_schema,
)
from orcheo_backend.app.chatkit_store_postgres.serialization import (
    serialize_item,
    serialize_thread_status,
)


class FakeCursor:
    def __init__(
        self, *, row: dict[str, Any] | None = None, rows: list[Any] | None = None
    ) -> None:
        self._row = row
        self._rows = list(rows or [])

    async def fetchone(self) -> dict[str, Any] | None:
        return self._row

    async def fetchall(self) -> list[Any]:
        return list(self._rows)


class FakeConnection:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.queries: list[tuple[str, Any | None]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, query: str, params: Any | None = None) -> FakeCursor:
        self.queries.append((query.strip(), params))
        response = self._responses.pop(0) if self._responses else {}
        if isinstance(response, FakeCursor):
            return response
        if isinstance(response, dict):
            return FakeCursor(row=response.get("row"), rows=response.get("rows"))
        if isinstance(response, list):
            return FakeCursor(rows=response)
        return FakeCursor()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        return None


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    async def open(self) -> None:
        return None

    def connection(self) -> FakeConnection:
        return self._connection


def make_store(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[Any],
    *,
    initialized: bool = True,
) -> PostgresChatKitStore:
    monkeypatch.setattr(pg_base, "AsyncConnectionPool", object())
    monkeypatch.setattr(pg_base, "DictRowFactory", object())
    store = PostgresChatKitStore("postgresql://test")
    store._pool = FakePool(FakeConnection(responses))
    store._initialized = initialized
    return store


def _timestamp() -> datetime:
    return datetime.now(tz=UTC)


def _thread_row(thread: ThreadMetadata) -> dict[str, Any]:
    return {
        "id": thread.id,
        "title": thread.title,
        "created_at": thread.created_at,
        "status_json": serialize_thread_status(thread),
        "metadata_json": json.dumps(thread.metadata or {}),
    }


def _item_row(item: UserMessageItem, *, ordinal: int) -> dict[str, Any]:
    return {
        "id": item.id,
        "thread_id": item.thread_id,
        "ordinal": ordinal,
        "item_type": getattr(item, "type", None),
        "item_json": serialize_item(item),
        "created_at": item.created_at,
    }


@pytest.mark.asyncio
async def test_postgres_chatkit_schema_migrates_workspace_index() -> None:
    class SchemaConnection:
        def __init__(self, columns: list[str]) -> None:
            self.columns = columns
            self.statements: list[str] = []

        async def execute(self, stmt: str, params: Any | None = None) -> FakeCursor:
            self.statements.append(stmt.strip())
            if "information_schema.columns" in stmt:
                return FakeCursor(
                    rows=[{"column_name": column} for column in self.columns]
                )
            return FakeCursor()

    conn = SchemaConnection(columns=["id", "workflow_id"])
    await ensure_schema(conn)

    expected = [
        stmt.strip()
        for stmt in POSTGRES_CHATKIT_SCHEMA.strip().split(";")
        if stmt.strip()
    ]
    assert conn.statements[: len(expected)] == expected
    assert "ALTER TABLE chat_threads ADD COLUMN workspace_id TEXT" in conn.statements
    assert (
        "CREATE INDEX IF NOT EXISTS idx_chat_threads_workspace_id ON chat_threads(workspace_id)"
        in conn.statements
    )
    alter_position = conn.statements.index(
        "ALTER TABLE chat_threads ADD COLUMN workspace_id TEXT"
    )
    index_position = conn.statements.index(
        "CREATE INDEX IF NOT EXISTS idx_chat_threads_workspace_id ON chat_threads(workspace_id)"
    )
    assert alter_position < index_position


@pytest.mark.asyncio
async def test_postgres_chatkit_schema_skips_existing_workspace_column() -> None:
    class SchemaConnection:
        def __init__(self, columns: list[str]) -> None:
            self.columns = columns
            self.statements: list[str] = []

        async def execute(self, stmt: str, params: Any | None = None) -> FakeCursor:
            self.statements.append(stmt.strip())
            if "information_schema.columns" in stmt:
                return FakeCursor(
                    rows=[{"column_name": column} for column in self.columns]
                )
            return FakeCursor()

    conn = SchemaConnection(columns=["id", "workflow_id", "workspace_id"])
    await ensure_schema(conn)

    assert not any(
        stmt == "ALTER TABLE chat_threads ADD COLUMN workspace_id TEXT"
        for stmt in conn.statements
    )
    assert (
        "CREATE INDEX IF NOT EXISTS idx_chat_threads_workspace_id ON chat_threads(workspace_id)"
        in conn.statements
    )


@pytest.mark.asyncio
async def test_postgres_store_initializes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[FakeConnection] = []

    async def _stub_schema(conn: FakeConnection) -> None:
        calls.append(conn)

    monkeypatch.setattr(pg_base, "ensure_schema", _stub_schema)
    store = make_store(monkeypatch, responses=[], initialized=False)

    await store._ensure_initialized()
    await store._ensure_initialized()

    assert store._initialized is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_postgres_store_save_thread_merges_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(monkeypatch, responses=[])

    class FakeRequest:
        metadata = {"workflow_id": "wf_123", "extra": "data"}

    context = {"chatkit_request": FakeRequest()}
    thread = ThreadMetadata(id="thr_merge", created_at=_timestamp())

    await store.save_thread(thread, context)

    assert thread.metadata["workflow_id"] == "wf_123"
    assert thread.metadata["extra"] == "data"


@pytest.mark.asyncio
async def test_postgres_store_save_thread_preserves_existing_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(monkeypatch, responses=[])

    class FakeRequest:
        metadata = {"workflow_id": "wf_123"}

    context = {"chatkit_request": FakeRequest()}
    thread = ThreadMetadata(
        id="thr_existing_title",
        created_at=_timestamp(),
        title="Existing Title",
    )

    await store.save_thread(thread, context)

    conn = store._pool.connection()
    assert conn.queries[0][1][1] == "Existing Title"
    assert thread.title == "Existing Title"


@pytest.mark.asyncio
async def test_postgres_extract_title_from_request_branches() -> None:
    from orcheo_backend.app.chatkit_store_postgres.threads import (
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

    context = {"chatkit_request": FakeRequest()}
    blank_context = {"chatkit_request": BlankRequest()}
    assert _extract_title_from_request(context) == "Title from request"
    assert _extract_title_from_request(blank_context) is None


@pytest.mark.asyncio
async def test_postgres_store_load_threads_and_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread1 = ThreadMetadata(
        id="thr_1",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        metadata={"workflow_id": "wf_1"},
    )
    thread2 = ThreadMetadata(
        id="thr_2",
        created_at=datetime(2024, 1, 2, tzinfo=UTC),
        metadata={"workflow_id": "wf_1"},
    )
    responses = [
        {"row": {"created_at": thread1.created_at, "id": "thr_marker"}},
        {"rows": [_thread_row(thread1), _thread_row(thread2)]},
        {"rows": [_thread_row(thread2)]},
    ]
    store = make_store(monkeypatch, responses=responses)
    context: dict[str, object] = {}

    page = await store.load_threads(
        limit=1, after="thr_marker", order="asc", context=context
    )

    assert page.has_more is True
    assert page.after == "thr_1"
    assert page.data[0].id == "thr_1"

    filtered = await store.filter_threads({"workflow_id": "wf_1"}, limit=10)
    assert filtered.data[0].id == "thr_2"


@pytest.mark.asyncio
async def test_postgres_store_load_threads_scoped_by_workflow_after_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker_created_at = datetime(2024, 1, 1, tzinfo=UTC)
    thread = ThreadMetadata(
        id="thr_wf",
        created_at=datetime(2024, 1, 2, tzinfo=UTC),
        metadata={"workflow_id": "wf_1"},
    )
    responses = [
        {"row": {"created_at": marker_created_at, "id": "thr_marker"}},
        {"rows": [_thread_row(thread)]},
    ]
    store = make_store(monkeypatch, responses=responses)
    context: dict[str, object] = {"workflow_id": "wf_1"}

    page = await store.load_threads(
        limit=10, after="thr_marker", order="asc", context=context
    )

    conn = store._pool.connection()
    assert conn.queries[0][0].endswith("AND workflow_id = %s")
    assert conn.queries[0][1] == ("thr_marker", "wf_1")
    assert (
        "WHERE workflow_id = %s AND ((created_at, id) > (%s, %s))" in conn.queries[1][0]
    )
    assert page.data[0].id == "thr_wf"


@pytest.mark.asyncio
async def test_postgres_store_load_threads_scoped_by_workspace_and_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_threads should scope cursor lookups and filters by workspace."""

    marker_created_at = datetime(2024, 1, 1, tzinfo=UTC)
    thread = ThreadMetadata(
        id="thr_workspace",
        created_at=datetime(2024, 1, 2, tzinfo=UTC),
        metadata={"workflow_id": "wf_1"},
    )
    responses = [
        {"row": {"created_at": marker_created_at, "id": "thr_marker"}},
        {"rows": [_thread_row(thread)]},
    ]
    store = make_store(monkeypatch, responses=responses)
    context: dict[str, object] = {
        "workflow_id": "wf_1",
        "workspace_id": "workspace-1",
    }

    page = await store.load_threads(
        limit=10,
        after="thr_marker",
        order="asc",
        context=context,
    )

    conn = store._pool.connection()
    assert conn.queries[0][1] == ("thr_marker", "wf_1", "workspace-1")
    assert "AND workspace_id = %s" in conn.queries[0][0]
    assert "workspace_id = %s" in conn.queries[1][0]
    assert page.data[0].id == "thr_workspace"


@pytest.mark.asyncio
async def test_postgres_store_items_and_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = UserMessageItem(
        id="msg_1",
        thread_id="thr_1",
        created_at=_timestamp(),
        content=[UserMessageTextContent(type="input_text", text="Ping")],
        attachments=[],
        quoted_text=None,
        inference_options=InferenceOptions(),
    )
    responses = [
        {"row": {"current": 0}},
        {},
        {},
        {"row": {"ordinal": 0, "id": "msg_marker"}},
        {"rows": [_item_row(item, ordinal=1)]},
        {"row": _item_row(item, ordinal=1)},
        {},
        {},
        {"rows": [_item_row(item, ordinal=1)]},
    ]
    store = make_store(monkeypatch, responses=responses)
    context: dict[str, object] = {}

    await store.add_thread_item(item.thread_id, item, context)

    page = await store.load_thread_items(
        item.thread_id,
        after="msg_marker",
        limit=10,
        order="asc",
        context=context,
    )
    assert page.data[0].id == item.id

    loaded = await store.load_item(item.thread_id, item.id, context)
    assert loaded.id == item.id

    await store.delete_thread_item(item.thread_id, item.id, context)

    search_page = await store.search_thread_items(item.thread_id, "Ping", limit=5)
    assert search_page.data[0].id == item.id


@pytest.mark.asyncio
async def test_postgres_store_save_item_insert_and_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = UserMessageItem(
        id="msg_save",
        thread_id="thr_save",
        created_at=_timestamp(),
        content=[UserMessageTextContent(type="input_text", text="First")],
        attachments=[],
        quoted_text=None,
        inference_options=InferenceOptions(),
    )

    responses_insert = [
        {"row": None},
        {"row": {"current": -1}},
        {},
        {},
    ]
    store_insert = make_store(monkeypatch, responses=responses_insert)
    await store_insert.save_item(item.thread_id, item, context={})

    responses_update = [
        {"row": {"ordinal": 2}},
        {},
        {},
    ]
    store_update = make_store(monkeypatch, responses=responses_update)
    await store_update.save_item(item.thread_id, item, context={})


@pytest.mark.asyncio
async def test_postgres_store_attachments_and_prune(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attachment = FileAttachment(
        id="att_1",
        name="example.txt",
        mime_type="text/plain",
    )
    stored_path = tmp_path / "example.txt"
    stored_path.write_text("sample", encoding="utf-8")

    responses = [
        {},
        {"row": {"details_json": attachment.model_dump(mode="json")}},
        {},
        {"rows": [{"id": "thr_prune"}]},
        {"rows": [{"storage_path": str(stored_path)}]},
        {},
        {},
    ]
    store = make_store(monkeypatch, responses=responses)

    await store.save_attachment(attachment, context={}, storage_path=str(stored_path))
    loaded = await store.load_attachment(attachment.id, context={})
    assert loaded.id == attachment.id

    await store.delete_attachment(attachment.id, context={})

    pruned = await store.prune_threads_older_than(datetime(2024, 1, 1, tzinfo=UTC))
    assert pruned == 1
    assert stored_path.exists() is False


@pytest.mark.asyncio
async def test_postgres_store_search_pagination_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify search pagination correctly includes thread_id in the marker query."""
    responses = [
        {"row": {"ordinal": 5, "id": "msg_marker"}},  # Response for the marker query
        {"rows": []},  # Response for the search results
    ]
    store = make_store(monkeypatch, responses=responses)

    thread_id = "thr_search"
    after_id = "msg_marker"

    await store.search_thread_items(
        thread_id=thread_id, query="test query", after=after_id
    )

    # Check the queries executed
    # The first query should be the marker resolution
    connection = store._pool.connection()
    assert len(connection.queries) >= 1

    marker_query, marker_params = connection.queries[0]

    # Verify the marker query SQL contains the thread_id check
    assert "SELECT ordinal, id FROM chat_messages" in marker_query
    assert "WHERE id = %s AND thread_id = %s" in marker_query

    # Verify the parameters passed include both the after ID and the thread ID
    assert marker_params[0] == after_id
    assert marker_params[1] == thread_id


@pytest.mark.asyncio
async def test_postgres_store_raises_on_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pg_base, "AsyncConnectionPool", None)
    with pytest.raises(RuntimeError, match="requires psycopg"):
        PostgresChatKitStore("postgresql://test")


@pytest.mark.asyncio
async def test_postgres_store_connection_rollback_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(monkeypatch, responses=[])

    async def invoke_error() -> None:
        async with store._connection():
            raise ValueError("oops")

    with pytest.raises(ValueError, match="oops"):
        await invoke_error()

    conn = store._pool.connection()
    assert conn.rollbacks == 1


@pytest.mark.asyncio
async def test_postgres_store_add_item_validates_thread_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(monkeypatch, responses=[])
    item = UserMessageItem(
        id="i1",
        thread_id="t1",
        created_at=_timestamp(),
        content=[UserMessageTextContent(type="input_text", text="hi")],
        attachments=[],
        quoted_text=None,
        inference_options=InferenceOptions(),
    )

    with pytest.raises(ValueError, match="does not belong"):
        await store.add_thread_item("t2", item, {})


@pytest.mark.asyncio
async def test_postgres_store_load_item_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [{"row": None}]
    store = make_store(monkeypatch, responses=responses)

    with pytest.raises(NotFoundError):
        await store.load_item("t1", "i1", {})


@pytest.mark.asyncio
async def test_postgres_store_load_attachment_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [{"row": None}]
    store = make_store(monkeypatch, responses=responses)

    with pytest.raises(NotFoundError):
        await store.load_attachment("a1", {})


@pytest.mark.asyncio
async def test_postgres_store_prune_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [{"rows": []}]
    store = make_store(monkeypatch, responses=responses)

    count = await store.prune_threads_older_than(_timestamp())
    assert count == 0


@pytest.mark.asyncio
async def test_postgres_store_infer_thread_id_from_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [{}]
    store = make_store(monkeypatch, responses=responses)

    attachment = FileAttachment(id="a1", name="f.txt", mime_type="text/plain")

    class Params:
        thread_id = "t1"

    class Request:
        params = Params()

    context = {"chatkit_request": Request()}

    await store.save_attachment(attachment, context)

    conn = store._pool.connection()
    assert len(conn.queries) > 0
    params = conn.queries[0][1]
    assert params[1] == "t1"


@pytest.mark.asyncio
async def test_postgres_store_infer_thread_id_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [{}]
    store = make_store(monkeypatch, responses=responses)
    attachment = FileAttachment(id="a2", name="f.txt", mime_type="text/plain")

    await store.save_attachment(attachment, {})

    conn = store._pool.connection()
    params = conn.queries[0][1]
    assert params[1] is None


@pytest.mark.asyncio
async def test_get_pool_caching(monkeypatch: pytest.MonkeyPatch) -> None:
    # Test that _get_pool returns existing pool without locking if set
    store = make_store(monkeypatch, responses=[])

    # Pre-set the pool
    store._pool = "existing_pool"  # type: ignore

    # Should resolve immediately without lock
    pool = await store._get_pool()
    assert pool == "existing_pool"

    # Test race condition: pool is None, but gets set while waiting for lock
    store._pool = None

    class SideEffectLock:
        async def __aenter__(self):
            store._pool = "race_pool"  # type: ignore

        async def __aexit__(self, *args):
            pass

    store._pool_lock = SideEffectLock()  # type: ignore

    pool = await store._get_pool()
    assert pool == "race_pool"


@pytest.mark.asyncio
async def test_ensure_initialized_race(monkeypatch: pytest.MonkeyPatch) -> None:
    store = make_store(monkeypatch, responses=[], initialized=False)

    class SideEffectLock:
        async def __aenter__(self):
            store._initialized = True

        async def __aexit__(self, *args):
            pass

    store._schema_lock = SideEffectLock()  # type: ignore

    # Should return early inside lock and NO connection activity (no responses needed)
    await store._ensure_initialized()
    assert store._initialized


@pytest.mark.asyncio
async def test_pagination_marker_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Marker query returns None -> load items query should NOT have extra WHERE clause
    responses = [
        {"row": None},  # marker query (fetchone returns None)
        {"rows": []},  # items query
    ]
    store = make_store(monkeypatch, responses=responses)

    await store.load_thread_items(
        "t1", after="missing", limit=10, order="asc", context={}
    )

    conn = store._pool.connection()
    assert len(conn.queries) == 2
    items_query = conn.queries[1][0]
    # Should not contain tuple comparison for marker
    assert "(ordinal, id) >" not in items_query


@pytest.mark.asyncio
async def test_search_pagination_marker_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Marker query returns None for search
    responses = [
        {"row": None},  # marker query
        {"rows": []},  # search query
    ]
    store = make_store(monkeypatch, responses=responses)

    await store.search_thread_items("t1", "query", after="missing", limit=10)

    conn = store._pool.connection()
    assert len(conn.queries) == 2
    search_query = conn.queries[1][0]
    assert "(ordinal, id) >" not in search_query


@pytest.mark.asyncio
async def test_serialization_edge_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    from orcheo_backend.app.chatkit_store_postgres.serialization import (
        _coerce_datetime,
        _coerce_json,
        item_from_row,
    )

    # Test coerce_json with None
    assert _coerce_json(None) is None

    # Test coerce_datetime with string
    dt_str = "2024-01-01T12:00:00+00:00"
    dt = _coerce_datetime(dt_str)
    assert dt.year == 2024

    # Test item_from_row with missing payload (None item_json)
    # We mock _ITEM_ADAPTER to avoid validation error on empty dict if
    # it requires fields
    # or just provide enough fields in validate_python

    row = {
        "id": "i1",
        "thread_id": "t1",
        "item_json": None,  # Should default to {}
        "created_at": None,
    }

    # The actual item validation requires type/etc, so let's mock validation
    with pytest.raises(ValidationError):
        # Validation error likely, but we want to cover the code path
        # up to validation or we can construct a row that survives validation
        item_from_row(row)

    # Better test for date branches:
    # 1. Row has created_at as string
    row_str_date = {
        "id": "i1",
        "thread_id": "t1",
        "item_json": {
            "type": "user_message",
            "content": [],
            "created_at": dt_str,
            "inference_options": {},
        },  # payload has string date
        "created_at": dt_str,  # row also has string date? No strict typing in FakeRow.
    }
    # item_from_row logic:
    # payload = {} (from json)
    # payload.setdefault created_at from row.
    # if payload[created_at] is str -> parse.

    item = item_from_row(row_str_date)
    assert item.created_at.year == 2024

    # 2. Payload has datetime object already
    row_dt_obj = {
        "id": "i1",
        "thread_id": "t1",
        "item_json": {
            "type": "user_message",
            "content": [],
            "created_at": dt,
            "inference_options": {},
        },
        "created_at": dt,
    }
    item2 = item_from_row(row_dt_obj)
    assert item2.created_at == dt


@pytest.mark.asyncio
async def test_load_thread_found(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    # Use helper to get valid status json
    from chatkit.types import ThreadMetadata

    tm = ThreadMetadata(id="t1", created_at=now)
    status_json = serialize_thread_status(tm)

    row = {
        "id": "t1",
        "title": "Title",
        "created_at": now,
        "status_json": status_json,
        "metadata_json": "{}",
    }
    store = make_store(monkeypatch, responses=[{"row": row}])

    t = await store.load_thread("t1", {})
    assert t.id == "t1"
    assert t.title == "Title"


@pytest.mark.asyncio
async def test_load_thread_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    store = make_store(monkeypatch, responses=[{"row": None}])

    with pytest.raises(NotFoundError):
        await store.load_thread("t1", {})


@pytest.mark.asyncio
async def test_threads_pagination_marker_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        {"row": None},  # Marker missing
        {"rows": []},
    ]
    store = make_store(monkeypatch, responses=responses)

    # invalid after
    await store.load_threads(limit=10, after="miss", order="desc", context={})

    conn = store._pool.connection()
    query = conn.queries[1][0]
    assert "(created_at, id) <" not in query


@pytest.mark.asyncio
async def test_threads_merge_metadata_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    # Test internal helper explicitly to cover branches
    from orcheo_backend.app.chatkit_store_postgres.threads import ThreadStoreMixin

    thread = ThreadMetadata(id="t1", created_at=_timestamp())

    # 1. No context
    res = ThreadStoreMixin._merge_metadata_from_context(thread, None)
    assert res == {}

    # 2. Context but no request
    res = ThreadStoreMixin._merge_metadata_from_context(thread, {"other": 1})
    # context.get("chatkit_request") returns None
    assert res == {}

    # 3. Request but no metadata
    class FakeReq:
        pass

    res = ThreadStoreMixin._merge_metadata_from_context(
        thread, {"chatkit_request": FakeReq()}
    )
    assert res == {}

    # 4. Request with metadata but not dict (e.g. None or empty)
    class FakeReq2:
        metadata = None

    res = ThreadStoreMixin._merge_metadata_from_context(
        thread, {"chatkit_request": FakeReq2()}
    )
    assert res == {}


@pytest.mark.asyncio
async def test_filter_threads_pagination_marker_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Test filter_threads when marker query returns None
    responses = [
        {"row": None},  # Marker missing
        {"rows": []},
    ]
    store = make_store(monkeypatch, responses=responses)

    await store.filter_threads({"workflow_id": "wf_1"}, after="miss")

    conn = store._pool.connection()
    assert len(conn.queries) == 2
    query = conn.queries[1][0]
    # Should not contain the extra pagination condition when marker is missing
    assert "(created_at, id)" not in query or "WHERE metadata_json" in query


@pytest.mark.asyncio
async def test_serialization_coerce_datetime_error() -> None:
    from orcheo_backend.app.chatkit_store_postgres.serialization import _coerce_datetime

    # Test TypeError for unsupported type
    with pytest.raises(TypeError, match="Unsupported datetime value"):
        _coerce_datetime(123)


@pytest.mark.asyncio
async def test_utils_ensure_datetime_with_tzinfo() -> None:
    from orcheo_backend.app.chatkit_store_postgres.utils import ensure_datetime

    # Test datetime already has tzinfo
    dt_aware = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    result = ensure_datetime(dt_aware)
    assert result == dt_aware
    assert result.tzinfo is not None


@pytest.mark.asyncio
async def test_postgres_store_get_pool_creates_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _get_pool creates a new pool when one doesn't exist."""
    pool_created = False

    class FakeAsyncConnectionPool:
        def __init__(self, dsn: str, **kwargs: Any) -> None:
            nonlocal pool_created
            pool_created = True
            self.dsn = dsn
            self.kwargs = kwargs

        async def open(self) -> None:
            pass

    monkeypatch.setattr(pg_base, "AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(pg_base, "DictRowFactory", lambda: None)
    store = PostgresChatKitStore("postgresql://test")
    store._pool = None  # Ensure no pool exists

    pool = await store._get_pool()

    assert pool_created
    assert pool is not None
    assert store._pool is pool


@pytest.mark.asyncio
async def test_postgres_store_get_pool_opens_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _get_pool opens the pool after creation."""
    opened = False

    class FakeAsyncConnectionPool:
        def __init__(self, dsn: str, **kwargs: Any) -> None:
            self.dsn = dsn

        async def open(self) -> None:
            nonlocal opened
            opened = True

    monkeypatch.setattr(pg_base, "AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(pg_base, "DictRowFactory", lambda: None)
    store = PostgresChatKitStore("postgresql://test")
    store._pool = None

    await store._get_pool()

    assert opened


@pytest.mark.asyncio
async def test_postgres_store_delete_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(monkeypatch, responses=[{}])
    context: dict[str, object] = {}

    await store.delete_thread("thr_delete", context)

    conn = store._pool.connection()
    assert len(conn.queries) == 1
    assert "DELETE FROM chat_threads" in conn.queries[0][0]
    assert conn.queries[0][1] == ("thr_delete",)


@pytest.mark.asyncio
async def test_filter_threads_pagination_marker_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    responses = [
        {"row": {"created_at": now, "id": "thr_marker"}},  # Marker found
        {"rows": []},  # Search results
    ]
    store = make_store(monkeypatch, responses=responses)

    await store.filter_threads({"workflow_id": "wf_1"}, after="thr_marker")

    conn = store._pool.connection()
    assert len(conn.queries) == 2
    query = conn.queries[1][0]
    # Default order is desc, so comparator is <
    assert "AND (created_at, id) < (%s, %s)" in query
    params = conn.queries[1][1]
    # Check marker params
    assert params[1] == now
    assert params[2] == "thr_marker"


@pytest.mark.asyncio
async def test_utils_ensure_datetime_naive() -> None:
    from orcheo_backend.app.chatkit_store_postgres.utils import ensure_datetime

    # Test naive datetime gets UTC
    dt_naive = datetime(2024, 1, 1, 12, 0, 0)
    result = ensure_datetime(dt_naive)
    assert result.tzinfo == UTC
    assert result.hour == 12
