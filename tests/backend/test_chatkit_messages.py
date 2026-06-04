"""Tests for ChatKit message helpers."""

from __future__ import annotations
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest
from chatkit.errors import CustomStreamError
from chatkit.types import (
    AssistantMessageContent,
    AssistantMessageItem,
    FileAttachment,
    InferenceOptions,
    Page,
    ThreadMetadata,
    UserMessageItem,
    UserMessageTextContent,
)
from orcheo_backend.app.chatkit import messages as chatkit_messages
from orcheo_backend.app.chatkit.context import ChatKitRequestContext
from orcheo_backend.app.chatkit.messages import (
    _selected_model_from_user_item,
    build_assistant_item,
    build_history,
    build_inputs_payload,
    record_run_metadata,
    require_workflow_id,
    resolve_user_item,
    sync_thread_inference_metadata,
)
from orcheo_backend.app.repository import WorkflowRun


@pytest.mark.asyncio
async def test_build_history_converts_thread_items() -> None:
    """Test build_history converts user and assistant messages to ChatML format."""
    thread = ThreadMetadata(
        id="thr_history",
        created_at=datetime.now(UTC),
        metadata={},
    )

    user_msg = UserMessageItem(
        id="msg_user",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Hello AI")],
        inference_options=InferenceOptions(model="gpt-4"),
    )

    assistant_msg = AssistantMessageItem(
        id="msg_asst",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[AssistantMessageContent(text="Hello human!")],
    )

    mock_store = MagicMock()
    mock_store.load_thread_items = AsyncMock(
        return_value=Page(
            data=[user_msg, assistant_msg],
            has_more=False,
        )
    )

    context = ChatKitRequestContext(user_id="user123")  # type: ignore[typeddict-unknown-key]
    history = await build_history(mock_store, thread, context)

    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "Hello AI"}
    assert history[1] == {"role": "assistant", "content": "Hello human!"}

    mock_store.load_thread_items.assert_called_once_with(
        thread.id,
        after=None,
        limit=200,
        order="asc",
        context=context,
    )


def test_require_workflow_id_extracts_valid_uuid() -> None:
    """Test require_workflow_id returns UUID when valid."""
    workflow_id = uuid4()
    thread = ThreadMetadata(
        id="thr_valid",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": str(workflow_id)},
    )

    result = require_workflow_id(thread)
    assert result == workflow_id


def test_require_workflow_id_raises_when_missing() -> None:
    """Test require_workflow_id raises CustomStreamError when workflow_id is missing."""
    thread = ThreadMetadata(
        id="thr_no_workflow",
        created_at=datetime.now(UTC),
        metadata={},
    )

    with pytest.raises(CustomStreamError) as exc_info:
        require_workflow_id(thread)

    assert "No workflow has been associated" in str(exc_info.value)
    assert exc_info.value.allow_retry is False


def test_require_workflow_id_raises_when_invalid() -> None:
    """Test require_workflow_id raises CustomStreamError for invalid UUID."""
    thread = ThreadMetadata(
        id="thr_bad_uuid",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "not-a-uuid"},
    )

    with pytest.raises(CustomStreamError) as exc_info:
        require_workflow_id(thread)

    assert "invalid" in str(exc_info.value).lower()
    assert exc_info.value.allow_retry is False


@pytest.mark.asyncio
async def test_resolve_user_item_returns_provided_item() -> None:
    """Test resolve_user_item returns the item when provided."""
    thread = ThreadMetadata(
        id="thr_resolve",
        created_at=datetime.now(UTC),
        metadata={},
    )

    user_item = UserMessageItem(
        id="msg_provided",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Test")],
        inference_options=InferenceOptions(model="gpt-4"),
    )

    mock_store = MagicMock()
    context = ChatKitRequestContext(user_id="user123")  # type: ignore[typeddict-unknown-key]

    result = await resolve_user_item(mock_store, thread, user_item, context)

    assert result == user_item
    mock_store.load_thread_items.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_user_item_fetches_when_none() -> None:
    """Test resolve_user_item fetches latest user message when item is None."""
    thread = ThreadMetadata(
        id="thr_fetch",
        created_at=datetime.now(UTC),
        metadata={},
    )

    user_msg = UserMessageItem(
        id="msg_fetched",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Latest message")],
        inference_options=InferenceOptions(model="gpt-4"),
    )

    mock_store = MagicMock()
    mock_store.load_thread_items = AsyncMock(
        return_value=Page(data=[user_msg], has_more=False)
    )

    context = ChatKitRequestContext(user_id="user123")  # type: ignore[typeddict-unknown-key]
    result = await resolve_user_item(mock_store, thread, None, context)

    assert result == user_msg
    mock_store.load_thread_items.assert_called_once_with(
        thread.id, after=None, limit=1, order="desc", context=context
    )


@pytest.mark.asyncio
async def test_resolve_user_item_raises_when_no_user_message() -> None:
    """Test resolve_user_item raises error when no user message found."""
    thread = ThreadMetadata(
        id="thr_no_user",
        created_at=datetime.now(UTC),
        metadata={},
    )

    # Return only assistant message
    assistant_msg = AssistantMessageItem(
        id="msg_asst",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[AssistantMessageContent(text="Only assistant")],
    )

    mock_store = MagicMock()
    mock_store.load_thread_items = AsyncMock(
        return_value=Page(data=[assistant_msg], has_more=False)
    )

    context = ChatKitRequestContext(user_id="user123")  # type: ignore[typeddict-unknown-key]

    with pytest.raises(CustomStreamError) as exc_info:
        await resolve_user_item(mock_store, thread, None, context)

    assert "Unable to locate the user message" in str(exc_info.value)
    assert exc_info.value.allow_retry is False


def test_build_inputs_payload_basic() -> None:
    """Test build_inputs_payload creates basic payload without attachments."""
    thread = ThreadMetadata(
        id="thr_basic",
        created_at=datetime.now(UTC),
        metadata={"key": "value"},
    )

    payload = build_inputs_payload(thread, "Hello", [{"role": "user", "content": "Hi"}])

    assert payload["message"] == "Hello"
    assert payload["history"] == [{"role": "user", "content": "Hi"}]
    assert payload["thread_id"] == "thr_basic"
    assert payload["session_id"] == "thr_basic"
    assert payload["metadata"] == {"key": "value"}
    assert "documents" not in payload
    assert "model" not in payload


def test_build_inputs_payload_includes_selected_model() -> None:
    """Selected ChatKit model should be forwarded into workflow inputs."""
    thread = ThreadMetadata(
        id="thr_model",
        created_at=datetime.now(UTC),
        metadata={"key": "value"},
    )
    user_item = UserMessageItem(
        id="msg_model",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Hello")],
        inference_options=InferenceOptions(model="openai:gpt-5"),
    )

    payload = build_inputs_payload(thread, "Hello", [], user_item)

    assert payload["model"] == "openai:gpt-5"


def test_sync_thread_inference_metadata_records_selected_model() -> None:
    """Selected ChatKit model should be persisted on the thread metadata."""
    thread = ThreadMetadata(
        id="thr_sync",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "wf-123"},
    )
    user_item = UserMessageItem(
        id="msg_sync",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Hello")],
        inference_options=InferenceOptions(model="openai:gpt-5-mini"),
    )

    sync_thread_inference_metadata(thread, user_item)

    assert thread.metadata["chatkit_model"] == "openai:gpt-5-mini"


def test_sync_thread_inference_metadata_clears_selected_model() -> None:
    """ChatKit model metadata should be removed when no override is active."""
    thread = ThreadMetadata(
        id="thr_sync_clear",
        created_at=datetime.now(UTC),
        metadata={
            "workflow_id": "wf-123",
            "chatkit_model": "openai:gpt-5-mini",
        },
    )

    sync_thread_inference_metadata(thread, selected_model=None)

    assert thread.metadata == {"workflow_id": "wf-123"}


def test_selected_model_from_user_item_complains_on_missing_model() -> None:
    user_item = UserMessageItem(
        id="msg_model",
        thread_id="thread",
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Echo")],
        inference_options=InferenceOptions(model=None),
    )

    assert _selected_model_from_user_item(user_item) is None


def test_build_inputs_payload_with_no_attachments_attribute() -> None:
    """Test build_inputs_payload when user_item has no attachments attribute."""
    thread = ThreadMetadata(
        id="thr_no_attr",
        created_at=datetime.now(UTC),
        metadata={},
    )

    # Create a mock object without attachments attribute
    user_item = MagicMock(spec=[])

    payload = build_inputs_payload(thread, "Test", [], user_item)

    assert "documents" not in payload


def test_build_inputs_payload_with_none_attachments() -> None:
    """Test build_inputs_payload when attachments is None."""
    thread = ThreadMetadata(
        id="thr_none_attach",
        created_at=datetime.now(UTC),
        metadata={},
    )

    user_item = MagicMock()
    user_item.attachments = None

    payload = build_inputs_payload(thread, "Test", [], user_item)

    assert "documents" not in payload


def test_build_inputs_payload_with_empty_attachments() -> None:
    """Test build_inputs_payload when attachments is empty list."""
    thread = ThreadMetadata(
        id="thr_empty_attach",
        created_at=datetime.now(UTC),
        metadata={},
    )

    user_item = UserMessageItem(
        id="msg_empty",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Test")],
        attachments=[],
        inference_options=InferenceOptions(model="gpt-4"),
    )

    payload = build_inputs_payload(thread, "Test", [], user_item)

    assert "documents" not in payload


def test_build_inputs_payload_with_dict_attachments() -> None:
    """Test build_inputs_payload handles dict-format attachments."""
    thread = ThreadMetadata(
        id="thr_dict",
        created_at=datetime.now(UTC),
        metadata={},
    )

    user_item = MagicMock()
    user_item.attachments = [
        {
            "content": "File content here",
            "filename": "test.txt",
            "content_type": "text/plain",
            "size": 100,
            "file_id": "file_123",
        }
    ]

    payload = build_inputs_payload(thread, "Test", [], user_item)

    assert "documents" in payload
    documents = payload["documents"]
    assert len(documents) == 1
    assert documents[0]["attachment_id"] == "file_123"
    assert documents[0]["source"] == "test.txt"
    assert documents[0]["metadata"]["mime_type"] == "text/plain"
    assert documents[0]["metadata"]["size"] == 100


def test_build_inputs_payload_with_dict_attachments_defaults() -> None:
    """Dict attachments without an id/file_id are skipped."""
    thread = ThreadMetadata(
        id="thr_dict_defaults",
        created_at=datetime.now(UTC),
        metadata={},
    )

    user_item = MagicMock()
    user_item.attachments = [{}]  # Empty dict — no attachment_id, should be skipped

    payload = build_inputs_payload(thread, "Test", [], user_item)

    assert "documents" not in payload


def test_build_inputs_payload_with_string_attachments() -> None:
    """String attachment ids are ignored by the typed payload builder."""
    thread = ThreadMetadata(
        id="thr_string",
        created_at=datetime.now(UTC),
        metadata={},
    )

    user_item = MagicMock()
    user_item.attachments = ["atc_string_1", "  atc_string_2  "]

    payload = build_inputs_payload(thread, "Test", [], user_item)

    assert "documents" not in payload


def test_build_inputs_payload_converts_file_attachments() -> None:
    """File attachments are converted into opaque attachment_id references."""
    thread = ThreadMetadata(
        id="thr_docs",
        created_at=datetime.now(UTC),
        metadata={},
    )
    user_item = UserMessageItem(
        id="msg_docs",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Hello")],
        attachments=[
            FileAttachment(
                id="atc123",
                name="notes.txt",
                mime_type="text/plain",
            )
        ],
        inference_options=InferenceOptions(model="gpt-5"),
    )

    payload = build_inputs_payload(thread, "Hi", [], user_item)

    assert "documents" in payload
    documents = payload["documents"]
    assert isinstance(documents, list)
    assert documents[0]["attachment_id"] == "atc123"
    assert documents[0]["source"] == "notes.txt"
    metadata = documents[0]["metadata"]
    assert metadata["mime_type"] == "text/plain"


def test_build_inputs_payload_with_mixed_attachments() -> None:
    """Test build_inputs_payload handles multiple FileAttachment instances."""
    thread = ThreadMetadata(
        id="thr_mixed",
        created_at=datetime.now(UTC),
        metadata={},
    )

    user_item = UserMessageItem(
        id="msg_mixed",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Hello")],
        attachments=[
            FileAttachment(
                id="atc123",
                name="notes.txt",
                mime_type="text/plain",
            ),
            FileAttachment(
                id="atc456",
                name="data.json",
                mime_type="application/json",
            ),
        ],
        inference_options=InferenceOptions(model="gpt-4"),
    )

    payload = build_inputs_payload(thread, "Hi", [], user_item)

    assert "documents" in payload
    documents = payload["documents"]
    assert len(documents) == 2
    assert documents[0]["attachment_id"] == "atc123"
    assert documents[1]["attachment_id"] == "atc456"


def test_record_run_metadata_with_run() -> None:
    """Test record_run_metadata updates thread metadata with run info."""
    thread = ThreadMetadata(
        id="thr_run",
        created_at=datetime.now(UTC),
        metadata={},
    )

    run = WorkflowRun(
        id=uuid4(),
        workflow_version_id=uuid4(),
        triggered_by="user",
        created_at=datetime.now(UTC),
    )

    record_run_metadata(thread, run)

    assert "last_run_at" in thread.metadata
    assert "last_run_id" in thread.metadata
    assert thread.metadata["last_run_id"] == str(run.id)
    assert "runs" in thread.metadata
    assert str(run.id) in thread.metadata["runs"]


def test_record_run_metadata_without_run() -> None:
    """Test record_run_metadata updates thread metadata without run."""
    thread = ThreadMetadata(
        id="thr_no_run",
        created_at=datetime.now(UTC),
        metadata={},
    )

    record_run_metadata(thread, None)

    assert "last_run_at" in thread.metadata
    assert "last_run_id" not in thread.metadata
    assert "runs" not in thread.metadata


def test_record_run_metadata_preserves_existing_runs() -> None:
    """Test record_run_metadata preserves existing runs list."""
    existing_run_id = str(uuid4())
    thread = ThreadMetadata(
        id="thr_preserve",
        created_at=datetime.now(UTC),
        metadata={"runs": [existing_run_id]},
    )

    run = WorkflowRun(
        id=uuid4(),
        workflow_version_id=uuid4(),
        triggered_by="user",
        created_at=datetime.now(UTC),
    )

    record_run_metadata(thread, run)

    assert len(thread.metadata["runs"]) == 2
    assert existing_run_id in thread.metadata["runs"]
    assert str(run.id) in thread.metadata["runs"]


def test_record_run_metadata_limits_runs_list() -> None:
    """Test record_run_metadata limits runs list to 20 most recent."""
    # Create 21 existing runs
    existing_runs = [str(uuid4()) for _ in range(21)]
    thread = ThreadMetadata(
        id="thr_limit",
        created_at=datetime.now(UTC),
        metadata={"runs": existing_runs},
    )

    run = WorkflowRun(
        id=uuid4(),
        workflow_version_id=uuid4(),
        triggered_by="user",
        created_at=datetime.now(UTC),
    )

    record_run_metadata(thread, run)

    # Should only keep last 20 runs
    assert len(thread.metadata["runs"]) == 20
    # Most recent run should be in the list
    assert str(run.id) in thread.metadata["runs"]
    # First run should be dropped
    assert existing_runs[0] not in thread.metadata["runs"]


def test_record_run_metadata_handles_non_list_runs() -> None:
    """Test record_run_metadata handles when runs is not a list."""
    thread = ThreadMetadata(
        id="thr_bad_runs",
        created_at=datetime.now(UTC),
        metadata={"runs": "not-a-list"},
    )

    run = WorkflowRun(
        id=uuid4(),
        workflow_version_id=uuid4(),
        triggered_by="user",
        created_at=datetime.now(UTC),
    )

    record_run_metadata(thread, run)

    # Should create a new list
    assert isinstance(thread.metadata["runs"], list)
    assert str(run.id) in thread.metadata["runs"]


def test_build_assistant_item() -> None:
    """Test build_assistant_item creates proper AssistantMessageItem."""
    thread = ThreadMetadata(
        id="thr_asst",
        created_at=datetime.now(UTC),
        metadata={},
    )

    mock_store = MagicMock()
    mock_store.generate_item_id.return_value = "msg_generated"

    context = ChatKitRequestContext(user_id="user123")  # type: ignore[typeddict-unknown-key]

    item = build_assistant_item(mock_store, thread, "Assistant reply", context)

    assert isinstance(item, AssistantMessageItem)
    assert item.id == "msg_generated"
    assert item.thread_id == thread.id
    assert len(item.content) == 1
    assert item.content[0].text == "Assistant reply"

    mock_store.generate_item_id.assert_called_once_with("message", thread, context)


def test_sanitize_filename_returns_default_when_normalized_empty() -> None:
    """Ensure filenames without safe characters fall back to default name."""
    from orcheo_backend.app.routers.chatkit import _sanitize_filename

    assert _sanitize_filename("...") == "uploaded_file"


def test_build_inputs_payload_with_non_standard_attachments() -> None:
    """Test build_inputs_payload with invalid attachment types."""
    thread = ThreadMetadata(
        id="thr_non_standard",
        created_at=datetime.now(UTC),
        metadata={},
    )

    # Create a user_item with attachments that are neither dict nor AttachmentBase
    user_item = MagicMock()
    user_item.attachments = [
        "string_attachment",  # Not a dict or AttachmentBase
        123,  # Not a dict or AttachmentBase
        None,  # Not a dict or AttachmentBase
    ]

    payload = build_inputs_payload(thread, "Test", [], user_item)

    # Since none of the attachments are valid types, documents list should not be added
    assert "documents" not in payload


def test_build_history_skips_unknown_item_types() -> None:
    """Items that are neither UserMessageItem nor AssistantMessageItem are skipped (line 54->46)."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock
    from chatkit.types import Page, ThreadMetadata
    from orcheo_backend.app.chatkit.context import ChatKitRequestContext
    from orcheo_backend.app.chatkit.messages import build_history

    thread = ThreadMetadata(
        id="thr_skip",
        created_at=datetime.now(UTC),
        metadata={},
    )

    # Unknown item type - neither UserMessageItem nor AssistantMessageItem
    class _UnknownItem:
        id = "unknown"
        thread_id = thread.id

    mock_store = MagicMock()
    mock_store.load_thread_items = AsyncMock(
        return_value=Page(data=[_UnknownItem()], has_more=False)
    )

    import asyncio

    context = ChatKitRequestContext(user_id="u1")  # type: ignore[typeddict-unknown-key]
    history = asyncio.get_event_loop().run_until_complete(
        build_history(mock_store, thread, context)
    )

    assert history == []  # Unknown items are skipped


@pytest.mark.asyncio
async def test_build_inputs_payload_with_explicit_selected_model() -> None:
    """Explicitly passing selected_model skips _UNSET path (line 135->137)."""
    from datetime import UTC, datetime
    from chatkit.types import ThreadMetadata
    from orcheo_backend.app.chatkit.messages import build_inputs_payload

    thread = ThreadMetadata(
        id="thr_explicit", created_at=datetime.now(UTC), metadata={}
    )

    payload = build_inputs_payload(thread, "hello", [], selected_model="openai:gpt-4o")

    assert payload["model"] == "openai:gpt-4o"


@pytest.mark.asyncio
async def test_build_inputs_payload_with_additional_attachments() -> None:
    """additional_attachments are included in documents (line 148)."""
    from datetime import UTC, datetime
    from chatkit.types import (
        FileAttachment,
        InferenceOptions,
        ThreadMetadata,
        UserMessageItem,
        UserMessageTextContent,
    )
    from orcheo_backend.app.chatkit.messages import build_inputs_payload

    thread = ThreadMetadata(id="thr_add", created_at=datetime.now(UTC), metadata={})

    additional = [
        FileAttachment(id="atc_add", name="extra.txt", mime_type="text/plain")
    ]

    payload = build_inputs_payload(thread, "hi", [], additional_attachments=additional)

    assert "documents" in payload
    assert any(d["attachment_id"] == "atc_add" for d in payload["documents"])


def test_sync_thread_inference_metadata_returns_early_when_no_model_to_clear() -> None:
    """Returns early when resolved_model is falsy and chatkit_model not in metadata (line 219)."""
    from datetime import UTC, datetime
    from chatkit.types import ThreadMetadata
    from orcheo_backend.app.chatkit.messages import sync_thread_inference_metadata

    thread = ThreadMetadata(
        id="thr_no_clear",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "wf-1"},  # no chatkit_model key
    )

    # resolved_model is falsy and no chatkit_model to clear → line 219 early return
    sync_thread_inference_metadata(thread, user_item=None, selected_model=None)

    assert thread.metadata == {"workflow_id": "wf-1"}


def test_dedupe_documents_keeps_no_id_docs() -> None:
    """Documents without attachment_id (or blank/non-string) are always kept."""
    from orcheo_backend.app.chatkit.messages import _dedupe_documents

    docs = [
        {"source": "a"},  # no attachment_id → kept (line 197-198)
        {"attachment_id": None},  # non-string → kept
        {"attachment_id": "   "},  # blank string → kept
        {"attachment_id": 42},  # non-string → kept
    ]
    result = _dedupe_documents(docs)
    assert result == docs


def test_dedupe_documents_skips_duplicate_ids() -> None:
    """Second occurrence of same attachment_id is skipped (line 201)."""
    from orcheo_backend.app.chatkit.messages import _dedupe_documents

    docs = [
        {"attachment_id": "atc_1", "source": "first"},
        {"attachment_id": "atc_1", "source": "duplicate"},  # line 201: continue
        {"attachment_id": "atc_2", "source": "other"},
    ]
    result = _dedupe_documents(docs)
    assert len(result) == 2
    assert result[0]["source"] == "first"
    assert result[1]["source"] == "other"


def test_dedupe_documents_strips_whitespace_in_ids() -> None:
    """Whitespace around attachment_id is normalised for dedup comparison."""
    from orcheo_backend.app.chatkit.messages import _dedupe_documents

    docs = [
        {"attachment_id": " atc_1 "},
        {"attachment_id": "atc_1"},
    ]
    result = _dedupe_documents(docs)
    assert len(result) == 1


def test_build_inputs_payload_with_mixed_valid_invalid_attachments() -> None:
    """Test build_inputs_payload filters invalid attachments."""
    thread = ThreadMetadata(
        id="thr_mixed_valid_invalid",
        created_at=datetime.now(UTC),
        metadata={},
    )

    # Mix valid and invalid attachment types
    user_item = MagicMock()
    user_item.attachments = [
        "invalid_string",  # Invalid type - should be skipped
        {
            "content": "Valid dict attachment",
            "filename": "valid.txt",
            "content_type": "text/plain",
            "size": 50,
            "file_id": "file_abc",
        },  # Valid dict
        None,  # Invalid type - should be skipped
        FileAttachment(
            id="atc789",
            name="valid_file.txt",
            mime_type="text/plain",
        ),  # Valid AttachmentBase
        12345,  # Invalid type - should be skipped
    ]

    payload = build_inputs_payload(thread, "Test", [], user_item)

    # Should only include the two valid attachments
    assert "documents" in payload
    documents = payload["documents"]
    assert len(documents) == 2
    assert documents[0]["attachment_id"] == "file_abc"
    assert documents[0]["source"] == "valid.txt"
    assert documents[1]["attachment_id"] == "atc789"
    assert documents[1]["source"] == "valid_file.txt"
