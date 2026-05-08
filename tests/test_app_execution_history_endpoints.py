"""Tests for execution history REST endpoints exposed by the FastAPI app."""

import asyncio
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from orcheo.workspace import InMemoryWorkspaceRepository
from orcheo.workspace.models import Role, Workspace, WorkspaceContext
from orcheo_backend.app import create_app
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.history import InMemoryRunHistoryStore
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.workspace.dependencies import (
    resolve_workspace_context,
    set_workspace_repository,
)


_TEST_WORKSPACE = WorkspaceContext(
    workspace_id=uuid4(),
    workspace_slug="test",
    user_id="test-user",
    role=Role.OWNER,
)


def _setup_workspace() -> None:
    ws_repo = InMemoryWorkspaceRepository()
    ws_repo.create_workspace(
        Workspace(id=_TEST_WORKSPACE.workspace_id, slug="test", name="Test Workspace")
    )
    set_workspace_repository(ws_repo)


def test_execution_history_endpoints_return_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execution history endpoints expose stored replay data."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    reset_authentication_state()

    repository = InMemoryWorkflowRepository()
    history_store = InMemoryRunHistoryStore()

    execution_id = "exec-123"

    async def seed_history() -> None:
        await history_store.start_run(
            workflow_id="wf-1", execution_id=execution_id, inputs={"foo": "bar"}
        )
        await history_store.append_step(execution_id, {"node": "first"})
        await history_store.append_step(execution_id, {"node": "second"})
        await history_store.append_step(execution_id, {"status": "completed"})
        await history_store.mark_completed(execution_id)

    asyncio.run(seed_history())

    app = create_app(repository, history_store=history_store)
    app.dependency_overrides[resolve_workspace_context] = lambda: _TEST_WORKSPACE
    client = TestClient(app)

    history_response = client.get(f"/api/executions/{execution_id}/history")
    assert history_response.status_code == 200
    history = history_response.json()
    assert history["execution_id"] == execution_id
    assert history["status"] == "completed"
    assert len(history["steps"]) == 3
    assert history["steps"][0]["payload"] == {"node": "first"}

    replay_response = client.post(
        f"/api/executions/{execution_id}/replay", json={"from_step": 1}
    )
    assert replay_response.status_code == 200
    replay = replay_response.json()
    assert len(replay["steps"]) == 2
    assert replay["steps"][0]["index"] == 1
    assert replay["steps"][0]["payload"] == {"node": "second"}


def test_execution_history_not_found_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing history records return a 404 response."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    reset_authentication_state()

    repository = InMemoryWorkflowRepository()
    history_store = InMemoryRunHistoryStore()
    app = create_app(repository, history_store=history_store)
    app.dependency_overrides[resolve_workspace_context] = lambda: _TEST_WORKSPACE
    client = TestClient(app)

    response = client.get("/api/executions/missing/history")
    assert response.status_code == 404
    assert response.json()["detail"] == "Execution history not found"


def test_replay_execution_not_found_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay API mirrors 404 behaviour for unknown executions."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    reset_authentication_state()

    repository = InMemoryWorkflowRepository()
    history_store = InMemoryRunHistoryStore()
    app = create_app(repository, history_store=history_store)
    app.dependency_overrides[resolve_workspace_context] = lambda: _TEST_WORKSPACE
    client = TestClient(app)

    response = client.post("/api/executions/missing/replay", json={"from_step": 0})
    assert response.status_code == 404
    assert response.json()["detail"] == "Execution history not found"
