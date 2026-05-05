"""Tests for the workflow websocket entrypoint."""

from unittest.mock import AsyncMock, Mock, patch
import pytest
from fastapi import WebSocket, WebSocketDisconnect
from orcheo_backend.app import workflow_websocket
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.history import InMemoryRunHistoryStore


class _Repository:
    def __init__(self, resolved_workflow_id: str) -> None:
        self._resolved_workflow_id = resolved_workflow_id
        self._tenant_id = "tenant-1"

    async def resolve_workflow_ref(
        self, workflow_ref: str, *, include_archived=True, tenant_id=None
    ):
        del workflow_ref, include_archived
        return self._resolved_workflow_id

    async def get_workflow_tenant_id(self, workflow_id: str):
        del workflow_id
        return self._tenant_id


@pytest.mark.asyncio
async def test_workflow_websocket_routes_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incoming messages trigger execute_workflow with the provided payloads."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    reset_authentication_state()

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_websocket.headers = {}
    mock_websocket.query_params = {}
    mock_websocket.client = Mock(host="127.0.0.1")
    mock_websocket.state = Mock()
    mock_websocket.receive_json.return_value = {
        "type": "run_workflow",
        "graph_config": {"nodes": []},
        "inputs": {"input": "test"},
        "execution_id": "test-execution",
    }

    with (
        patch(
            "orcheo_backend.app.authenticate_websocket", AsyncMock(return_value=Mock())
        ),
        patch(
            "orcheo_backend.app.get_repository",
            return_value=_Repository("test-workflow"),
        ),
        patch(
            "orcheo_backend.app.execute_workflow", new_callable=AsyncMock
        ) as mock_execute,
        patch(
            "orcheo_backend.app._history_store_ref",
            {"store": InMemoryRunHistoryStore()},
        ),
    ):
        await workflow_websocket(mock_websocket, "test-workflow")

    mock_websocket.accept.assert_called_once()
    mock_websocket.receive_json.assert_called_once()
    mock_execute.assert_called_once_with(
        "test-workflow",
        {"nodes": []},
        {"input": "test"},
        "test-execution",
        mock_websocket,
        tenant_id="tenant-1",
        runnable_config=None,
        stored_runnable_config=None,
    )
    mock_websocket.close.assert_called_once()


@pytest.mark.asyncio
async def test_workflow_websocket_routes_evaluation_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incoming evaluation messages route to execute_workflow_evaluation."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    reset_authentication_state()

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_websocket.headers = {}
    mock_websocket.query_params = {}
    mock_websocket.client = Mock(host="127.0.0.1")
    mock_websocket.state = Mock()
    mock_websocket.receive_json.return_value = {
        "type": "evaluate_workflow",
        "graph_config": {"nodes": []},
        "inputs": {"input": "test"},
        "execution_id": "eval-execution",
        "evaluation": {"dataset": {"cases": [{"inputs": {"foo": "bar"}}]}},
    }

    with (
        patch(
            "orcheo_backend.app.authenticate_websocket", AsyncMock(return_value=Mock())
        ),
        patch(
            "orcheo_backend.app.get_repository",
            return_value=_Repository("workflow-abc"),
        ),
        patch(
            "orcheo_backend.app.execute_workflow_evaluation",
            new_callable=AsyncMock,
        ) as mock_execute,
        patch(
            "orcheo_backend.app._history_store_ref",
            {"store": InMemoryRunHistoryStore()},
        ),
    ):
        await workflow_websocket(mock_websocket, "workflow-abc")

    mock_execute.assert_called_once_with(
        "workflow-abc",
        {"nodes": []},
        {"input": "test"},
        "eval-execution",
        mock_websocket,
        evaluation={"dataset": {"cases": [{"inputs": {"foo": "bar"}}]}},
        tenant_id="tenant-1",
        runnable_config=None,
        stored_runnable_config=None,
    )


@pytest.mark.asyncio
async def test_workflow_websocket_routes_training_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incoming training messages route to execute_workflow_training."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    reset_authentication_state()

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_websocket.headers = {}
    mock_websocket.query_params = {}
    mock_websocket.client = Mock(host="127.0.0.1")
    mock_websocket.state = Mock()
    mock_websocket.receive_json.return_value = {
        "type": "train_workflow",
        "graph_config": {"nodes": []},
        "inputs": {"input": "test"},
        "execution_id": "train-execution",
        "training": {"dataset": {"cases": [{"inputs": {"foo": "bar"}}]}},
    }

    with (
        patch(
            "orcheo_backend.app.authenticate_websocket", AsyncMock(return_value=Mock())
        ),
        patch(
            "orcheo_backend.app.get_repository",
            return_value=_Repository("workflow-train"),
        ),
        patch(
            "orcheo_backend.app.execute_workflow_training",
            new_callable=AsyncMock,
        ) as mock_execute,
        patch(
            "orcheo_backend.app._history_store_ref",
            {"store": InMemoryRunHistoryStore()},
        ),
    ):
        await workflow_websocket(mock_websocket, "workflow-train")

    mock_execute.assert_called_once_with(
        "workflow-train",
        {"nodes": []},
        {"input": "test"},
        "train-execution",
        mock_websocket,
        training={"dataset": {"cases": [{"inputs": {"foo": "bar"}}]}},
        tenant_id="tenant-1",
        runnable_config=None,
        stored_runnable_config=None,
    )


@pytest.mark.asyncio
async def test_workflow_websocket_ignores_client_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Client disconnects during receive should exit cleanly."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    reset_authentication_state()

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_websocket.headers = {}
    mock_websocket.query_params = {}
    mock_websocket.client = Mock(host="127.0.0.1")
    mock_websocket.state = Mock()
    mock_websocket.receive_json.side_effect = WebSocketDisconnect()

    with (
        patch(
            "orcheo_backend.app.authenticate_websocket", AsyncMock(return_value=Mock())
        ),
        patch(
            "orcheo_backend.app.get_repository",
            return_value=_Repository("disconnect-workflow"),
        ),
        patch(
            "orcheo_backend.app._history_store_ref",
            {"store": InMemoryRunHistoryStore()},
        ),
    ):
        await workflow_websocket(mock_websocket, "disconnect-workflow")

    mock_websocket.accept.assert_called_once()
    mock_websocket.close.assert_called_once()
