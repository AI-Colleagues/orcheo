"""Tests for ChatKit server behaviour when orchestrating workflow runs."""

from __future__ import annotations
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from chatkit.errors import CustomStreamError
from chatkit.types import (
    InferenceOptions,
    ThreadMetadata,
    UserMessageItem,
    UserMessageTextContent,
)
from orcheo.models.workflow import WorkflowDraftAccess
from orcheo_backend.app.chatkit import ChatKitRequestContext
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from tests.backend.chatkit_test_utils import (
    create_chatkit_test_server,
    create_workflow_with_graph,
)


def _build_thread(workflow_id: str) -> ThreadMetadata:
    return ThreadMetadata(
        id="thr_meta",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": workflow_id},
    )


def _build_user_item(thread_id: str) -> UserMessageItem:
    return UserMessageItem(
        id="msg_meta",
        thread_id=thread_id,
        created_at=datetime.now(UTC),
        content=[UserMessageTextContent(type="input_text", text="Test")],
        attachments=[],
        quoted_text=None,
        inference_options=InferenceOptions(),
    )


@pytest.mark.asyncio
async def test_chatkit_server_records_run_metadata() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    workflow_id = str(workflow.id)

    server = create_chatkit_test_server(repository)
    run = await repository.create_run(
        workflow.id,
        workflow_version_id=(await repository.get_latest_version(workflow.id)).id,
        triggered_by="test",
        input_payload={},
    )
    server._run_workflow = AsyncMock(  # type: ignore[attr-defined]
        return_value=("Reply", {}, run)
    )

    thread = _build_thread(workflow_id)
    context: ChatKitRequestContext = {"workspace_id": "workspace-1"}
    await server.store.save_thread(thread, context)

    user_item = _build_user_item(thread.id)
    await server.store.add_thread_item(thread.id, user_item, context)

    _ = [event async for event in server.respond(thread, user_item, context)]

    server._run_workflow.assert_awaited_once()  # type: ignore[attr-defined]
    _, kwargs = server._run_workflow.await_args  # type: ignore[attr-defined]
    assert kwargs["workspace_id"] == "workspace-1"

    loaded = await server.store.load_thread(thread.id, context)
    assert "last_run_at" in loaded.metadata
    assert "last_run_id" in loaded.metadata
    assert "runs" in loaded.metadata


@pytest.mark.asyncio
async def test_chatkit_server_workflow_not_found() -> None:
    repository = InMemoryWorkflowRepository()
    server = create_chatkit_test_server(repository)

    thread = ThreadMetadata(
        id="thr_notfound",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": str(uuid4())},
    )
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = _build_user_item(thread.id)
    await server.store.add_thread_item(thread.id, user_item, context)

    with pytest.raises(CustomStreamError):
        _ = [event async for event in server.respond(thread, user_item, context)]


@pytest.mark.asyncio
async def test_chatkit_server_workflow_version_not_found() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Test workflow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )

    server = create_chatkit_test_server(repository)

    thread = _build_thread(str(workflow.id))
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = _build_user_item(thread.id)
    await server.store.add_thread_item(thread.id, user_item, context)

    with pytest.raises(CustomStreamError):
        _ = [event async for event in server.respond(thread, user_item, context)]


@pytest.mark.asyncio
async def test_chatkit_server_records_run_metadata_with_existing_runs() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    workflow_id = str(workflow.id)

    server = create_chatkit_test_server(repository)

    initial_run = await repository.create_run(
        workflow.id,
        workflow_version_id=(await repository.get_latest_version(workflow.id)).id,
        triggered_by="test",
        input_payload={},
    )

    server._run_workflow = AsyncMock(  # type: ignore[attr-defined]
        return_value=(
            "Reply",
            {},
            await repository.create_run(
                workflow.id,
                workflow_version_id=(
                    await repository.get_latest_version(workflow.id)
                ).id,
                triggered_by="test",
                input_payload={},
            ),
        )
    )

    thread = ThreadMetadata(
        id="thr_runs",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": workflow_id, "runs": [str(initial_run.id)]},
    )
    context: ChatKitRequestContext = {}
    await server.store.save_thread(thread, context)

    user_item = _build_user_item(thread.id)
    await server.store.add_thread_item(thread.id, user_item, context)

    _ = [event async for event in server.respond(thread, user_item, context)]

    loaded = await server.store.load_thread(thread.id, context)
    assert len(loaded.metadata["runs"]) == 2


@pytest.mark.asyncio
async def test_chatkit_server_uses_latest_request_metadata_for_workflow_inputs() -> (
    None
):
    repository = InMemoryWorkflowRepository()
    workflow = await create_workflow_with_graph(repository)
    workflow_id = str(workflow.id)

    server = create_chatkit_test_server(repository)
    run = await repository.create_run(
        workflow.id,
        workflow_version_id=(await repository.get_latest_version(workflow.id)).id,
        triggered_by="test",
        input_payload={},
    )
    server._run_workflow = AsyncMock(  # type: ignore[attr-defined]
        return_value=("Reply", {}, run)
    )

    thread = ThreadMetadata(
        id="thr_latest_metadata",
        created_at=datetime.now(UTC),
        metadata={
            "workflow_id": workflow_id,
            "context": "The user is on Canvas Gallery.",
        },
    )
    context: ChatKitRequestContext = {
        "workflow_id": workflow_id,
        "chatkit_request": SimpleNamespace(
            metadata={
                "context": "The user is viewing traces for workflow wf-123.",
                "vaultOpen": True,
            }
        ),
    }
    await server.store.save_thread(thread, {})

    user_item = _build_user_item(thread.id)
    await server.store.add_thread_item(thread.id, user_item, {})

    _ = [event async for event in server.respond(thread, user_item, context)]

    _, inputs = server._run_workflow.await_args.args  # type: ignore[attr-defined]
    assert inputs["metadata"]["workflow_id"] == workflow_id
    assert (
        inputs["metadata"]["context"]
        == "The user is viewing traces for workflow wf-123."
    )
    assert inputs["metadata"]["vaultOpen"] is True
