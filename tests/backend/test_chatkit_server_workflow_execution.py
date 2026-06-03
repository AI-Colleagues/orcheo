"""Tests for ChatKit server workflow execution pathways."""

from __future__ import annotations
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from chatkit.errors import CustomStreamError
from chatkit.types import (
    FileAttachment,
    InferenceOptions,
    ThreadMetadata,
    UserMessageItem,
    UserMessageTextContent,
)
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel
from orcheo.models import WorkflowDraftAccess
from orcheo_backend.app.chatkit import OrcheoChatKitServer
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from tests.backend.chatkit_test_utils import (
    create_chatkit_test_server,
    create_workflow_with_graph,
)


class _DummyAsyncContext:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@contextmanager
def _patched_workflow_executor_persistence() -> None:
    with (
        patch(
            "orcheo_backend.app.chatkit.workflow_executor.create_checkpointer",
            lambda settings: _DummyAsyncContext(InMemorySaver()),
        ),
        patch(
            "orcheo_backend.app.chatkit.workflow_executor.create_graph_store",
            lambda settings: _DummyAsyncContext(object()),
        ),
    ):
        yield


@pytest.mark.asyncio
async def test_chatkit_server_run_workflow_end_to_end() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Test workflow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )

    graph_config = {
        "format": "langgraph-script",
        "source": """
from langgraph.graph import END, START, StateGraph

def build_graph():
    graph = StateGraph(dict)

    def respond(state):
        message = state.get("message", "")
        return {"reply": f"Echo: {message}"}

    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    return graph
""",
        "entrypoint": "build_graph",
    }

    await repository.create_version(
        workflow.id,
        graph=graph_config,
        metadata={},
        notes=None,
        created_by="tester",
    )

    server = create_chatkit_test_server(repository)

    with _patched_workflow_executor_persistence():
        reply, state, run = await server._run_workflow(workflow.id, {"message": "Test"})

    assert reply == "Echo: Test"
    assert isinstance(state, dict)
    assert run is not None


@pytest.mark.asyncio
async def test_chatkit_server_run_workflow_without_reply() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Test workflow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )

    graph_config = {
        "format": "langgraph-script",
        "source": """
from langgraph.graph import END, START, StateGraph

def build_graph():
    graph = StateGraph(dict)

    def no_reply(state):
        return {"output": "something else"}

    graph.add_node("no_reply", no_reply)
    graph.add_edge(START, "no_reply")
    graph.add_edge("no_reply", END)
    return graph
""",
        "entrypoint": "build_graph",
    }

    await repository.create_version(
        workflow.id,
        graph=graph_config,
        metadata={},
        notes=None,
        created_by="tester",
    )

    server = create_chatkit_test_server(repository)

    with _patched_workflow_executor_persistence():
        with pytest.raises(CustomStreamError, match="without producing a reply"):
            await server._run_workflow(workflow.id, {})


@pytest.mark.asyncio
async def test_chatkit_server_run_workflow_with_basemodel_state() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)

    server = create_chatkit_test_server(repository)

    class TestState(BaseModel):
        reply: str

    mock_compiled = MagicMock()
    mock_compiled.ainvoke = AsyncMock(return_value=TestState(reply="Test reply"))

    with patch(
        "orcheo_backend.app.chatkit.workflow_executor.build_graph"
    ) as mock_build:
        mock_graph = MagicMock()
        mock_graph.compile.return_value = mock_compiled
        mock_build.return_value = mock_graph

        with _patched_workflow_executor_persistence():
            reply, state, run = await server._run_workflow(
                workflow.id, {"message": "Test"}
            )

    assert reply == "Test reply"
    assert isinstance(state, dict)
    assert run is not None


def test_chatkit_server_records_run_metadata_without_run() -> None:
    thread = ThreadMetadata(
        id="thr_no_run",
        created_at=datetime.now(UTC),
        metadata={},
    )

    OrcheoChatKitServer._record_run_metadata(thread, None)

    assert "last_run_at" in thread.metadata
    assert "last_run_id" not in thread.metadata


@pytest.mark.asyncio
async def test_chatkit_server_run_workflow_with_repository_create_run_failure() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)

    server = create_chatkit_test_server(repository)

    original_create_run = server._repository.create_run
    server._repository.create_run = AsyncMock(side_effect=Exception("DB error"))

    with _patched_workflow_executor_persistence():
        reply, state, run = await server._run_workflow(workflow.id, {"message": "Test"})

    assert reply == "Echo: Test"
    assert isinstance(state, dict)
    assert run is None

    server._repository.create_run = original_create_run


def test_chatkit_server_backfills_workflow_id_from_context() -> None:
    repository = InMemoryWorkflowRepository()
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(
        id="thr_missing_workflow",
        created_at=datetime.now(UTC),
        metadata={},
    )
    context = {"workflow_id": "wf-123"}

    server._ensure_workflow_metadata(thread, context)

    assert thread.metadata["workflow_id"] == "wf-123"


@pytest.mark.asyncio
async def test_chatkit_server_infers_upload_session_from_attachment_rows() -> None:
    repository = InMemoryWorkflowRepository()
    server = create_chatkit_test_server(repository)

    attachment_service = MagicMock()
    attachment_service.link_attachments_to_thread = AsyncMock(return_value=2)
    attachment_service.resolve_upload_session_id = AsyncMock(return_value="ups-123")
    attachment_service.link_upload_session_to_thread = AsyncMock(return_value=2)
    server.store.attachment_service = attachment_service

    thread = ThreadMetadata(
        id="thr_upload_session",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "wf-123"},
    )
    user_item = UserMessageItem(
        id="msg_upload_session",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Hello")],
        attachments=[
            FileAttachment(id="atc_1", name="first.txt", mime_type="text/plain"),
            FileAttachment(id="atc_2", name="second.txt", mime_type="text/plain"),
        ],
        inference_options=InferenceOptions(model="gpt-4"),
    )
    context = {"workspace_id": "ws-1", "workflow_id": "wf-123"}

    await server._link_upload_session(context, thread, user_item)

    assert context["upload_session_id"] == "ups-123"
    attachment_service.link_attachments_to_thread.assert_awaited_once_with(
        ["atc_1", "atc_2"], thread.id, "ws-1"
    )
    attachment_service.resolve_upload_session_id.assert_awaited_once_with(
        ["atc_1", "atc_2"], "ws-1", workflow_id="wf-123"
    )
    attachment_service.link_upload_session_to_thread.assert_awaited_once_with(
        upload_session_id="ups-123",
        thread_id=thread.id,
        workspace_id="ws-1",
    )


@pytest.mark.asyncio
async def test_chatkit_server_links_multi_session_attachments() -> None:
    """Attachments uploaded across separate sessions are linked by id.

    When a single message references files from more than one upload session,
    ``resolve_upload_session_id`` returns ``None`` and the session-based link is
    skipped. The attachments must still be linked to the thread directly so the
    workflow attachment resolver can match them by ``thread_id``.
    """
    repository = InMemoryWorkflowRepository()
    server = create_chatkit_test_server(repository)

    attachment_service = MagicMock()
    attachment_service.link_attachments_to_thread = AsyncMock(return_value=2)
    attachment_service.resolve_upload_session_id = AsyncMock(return_value=None)
    attachment_service.link_upload_session_to_thread = AsyncMock(return_value=0)
    server.store.attachment_service = attachment_service

    thread = ThreadMetadata(
        id="thr_multi_session",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "wf-123"},
    )
    user_item = UserMessageItem(
        id="msg_multi_session",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Code the data")],
        attachments=[
            FileAttachment(id="atc_survey", name="survey.csv", mime_type="text/csv"),
            FileAttachment(
                id="atc_codebook", name="codebook.csv", mime_type="text/csv"
            ),
        ],
        inference_options=InferenceOptions(model="gpt-4"),
    )
    context = {"workspace_id": "ws-1", "workflow_id": "wf-123"}

    await server._link_upload_session(context, thread, user_item)

    attachment_service.link_attachments_to_thread.assert_awaited_once_with(
        ["atc_survey", "atc_codebook"], thread.id, "ws-1"
    )
    # No common session resolves, so the session-based link is never attempted.
    attachment_service.link_upload_session_to_thread.assert_not_awaited()
    assert "upload_session_id" not in context
