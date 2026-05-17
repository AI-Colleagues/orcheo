"""Shared fixtures and helpers for ChatKit backend tests."""

from __future__ import annotations
from typing import Any
from unittest.mock import AsyncMock
from orcheo.models import Workflow, WorkflowDraftAccess
from orcheo.vault import InMemoryCredentialVault
from orcheo_backend.app.dependencies import set_history_store
from orcheo_backend.app.history import RunHistoryStore
from orcheo_backend.app.chatkit import (
    InMemoryChatKitStore,
    OrcheoChatKitServer,
    create_chatkit_server,
)
from orcheo_backend.app.repository import InMemoryWorkflowRepository


def build_script_graph() -> dict[str, Any]:
    """Return a LangGraph script configuration that echoes the input message."""
    source = """
from langgraph.graph import END, START, StateGraph

def build_graph():
    graph = StateGraph(dict)

    def respond(state):
        message = state.get("message", "")
        return {"reply": f"Echo: {message}"}

    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    graph.set_entry_point("respond")
    graph.set_finish_point("respond")
    return graph
"""
    return {
        "format": "langgraph-script",
        "source": source,
        "entrypoint": "build_graph",
    }


async def create_workflow_with_graph(
    repository: InMemoryWorkflowRepository,
) -> Workflow:
    """Create a workflow and version using the default echo graph."""
    workflow = await repository.create_workflow(
        name="Test workflow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    await repository.create_version(
        workflow.id,
        graph=build_script_graph(),
        metadata={},
        notes=None,
        created_by="tester",
    )
    return workflow


def create_chatkit_test_server(
    repository: InMemoryWorkflowRepository,
    history_store: RunHistoryStore | None = None,
) -> OrcheoChatKitServer:
    """Return an OrcheoChatKitServer backed by in-memory dependencies."""
    if history_store is None:
        history_store = AsyncMock(spec=RunHistoryStore)
    set_history_store(history_store)
    return create_chatkit_server(
        repository,
        InMemoryCredentialVault,
        store=InMemoryChatKitStore(),
    )


__all__ = [
    "build_script_graph",
    "create_chatkit_test_server",
    "create_workflow_with_graph",
]
