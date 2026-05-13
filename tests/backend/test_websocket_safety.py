"""Websocket resilience helpers and router behaviors."""

from __future__ import annotations
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import WebSocket, WebSocketDisconnect
from orcheo_backend.app import workflow_websocket
from orcheo_backend.app.errors import (
    WorkspaceQuotaExceededError,
    WorkspaceRateLimitError,
)
from orcheo_backend.app.routers import websocket as websocket_routes


@pytest.mark.asyncio
async def test_safe_send_error_payload_ignores_disconnect() -> None:
    """Websocket disconnections should be swallowed when sending errors."""

    websocket = AsyncMock(spec=WebSocket)
    websocket.send_json.side_effect = WebSocketDisconnect()

    await websocket_routes._safe_send_error_payload(websocket, {"status": "error"})
    websocket.send_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_send_error_payload_ignores_closed_connection() -> None:
    """Runtime errors after close should be ignored."""

    websocket = AsyncMock(spec=WebSocket)
    websocket.send_json.side_effect = RuntimeError(
        websocket_routes._CANNOT_SEND_AFTER_CLOSE
    )

    await websocket_routes._safe_send_error_payload(websocket, {"status": "error"})
    websocket.send_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_close_websocket_ignores_disconnect_and_closed() -> None:
    """Closing an already-closed websocket is a no-op."""

    websocket = AsyncMock(spec=WebSocket)
    websocket.close.side_effect = WebSocketDisconnect()

    await websocket_routes._safe_close_websocket(websocket)
    websocket.close.assert_awaited_once()

    websocket.close.reset_mock()
    websocket.close.side_effect = RuntimeError(
        websocket_routes._CANNOT_SEND_AFTER_CLOSE
    )
    await websocket_routes._safe_close_websocket(websocket)
    websocket.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_send_error_payload_propagates_unexpected_runtime_error() -> None:
    """Unexpected runtime errors should bubble up instead of being swallowed."""

    websocket = AsyncMock(spec=WebSocket)
    websocket.send_json.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await websocket_routes._safe_send_error_payload(websocket, {"status": "error"})


@pytest.mark.asyncio
async def test_safe_close_websocket_propagates_unexpected_runtime_error() -> None:
    """Close errors other than the known constant should still be raised."""

    websocket = AsyncMock(spec=WebSocket)
    websocket.close.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await websocket_routes._safe_close_websocket(websocket)


@pytest.mark.asyncio
async def test_workflow_websocket_handles_client_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disconnect while waiting for a message should simply close the socket."""

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_websocket.receive_json.side_effect = WebSocketDisconnect()
    mock_websocket.state = SimpleNamespace()

    backend_app_module = importlib.import_module("orcheo_backend.app")
    monkeypatch.setattr(
        backend_app_module,
        "authenticate_websocket",
        AsyncMock(return_value={"sub": "tester"}),
    )
    close_mock = AsyncMock()
    monkeypatch.setattr(
        websocket_routes,
        "_safe_close_websocket",
        close_mock,
    )

    await workflow_websocket(mock_websocket, "workflow-id")

    mock_websocket.accept.assert_awaited_once()
    close_mock.assert_awaited_once_with(mock_websocket)


@pytest.mark.asyncio
async def test_workflow_websocket_runs_execute_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_workflow messages should accept and dispatch the workflow executor."""

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_websocket.receive_json.side_effect = [
        {
            "type": "run_workflow",
            "graph_config": {"nodes": []},
            "inputs": {"message": "hi"},
            "execution_id": "exec-1",
        }
    ]
    mock_websocket.state = SimpleNamespace(subprotocol="orcheo-auth")
    backend_app_module = importlib.import_module("orcheo_backend.app")
    execute_mock = AsyncMock()
    monkeypatch.setattr(
        backend_app_module,
        "authenticate_websocket",
        AsyncMock(return_value={"sub": "tester"}),
    )
    monkeypatch.setattr(
        backend_app_module,
        "get_repository",
        lambda: SimpleNamespace(
            resolve_workflow_ref=AsyncMock(return_value="workflow-1"),
            get_workflow_workspace_id=AsyncMock(return_value=None),
        ),
    )
    monkeypatch.setattr(backend_app_module, "execute_workflow", execute_mock)

    await workflow_websocket(mock_websocket, "workflow-id")

    mock_websocket.accept.assert_awaited_once_with(subprotocol="orcheo-auth")
    execute_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_workflow_websocket_handles_rate_limit_and_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace limits should be surfaced as websocket error payloads."""

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_websocket.receive_json.side_effect = [
        {
            "type": "evaluate_workflow",
            "graph_config": {"nodes": []},
            "inputs": {},
            "execution_id": "exec-2",
        }
    ]
    mock_websocket.state = SimpleNamespace()
    backend_app_module = importlib.import_module("orcheo_backend.app")
    monkeypatch.setattr(
        backend_app_module,
        "authenticate_websocket",
        AsyncMock(return_value={"sub": "tester"}),
    )
    monkeypatch.setattr(
        backend_app_module,
        "get_repository",
        lambda: SimpleNamespace(
            resolve_workflow_ref=AsyncMock(return_value="workflow-1"),
            get_workflow_workspace_id=AsyncMock(return_value="workspace-1"),
        ),
    )
    monkeypatch.setattr(
        websocket_routes.get_workspace_governance(),
        "check_api_rate_limit",
        lambda workspace_id: (_ for _ in ()).throw(
            WorkspaceRateLimitError(
                "Too many requests for workspace workspace-1",
                code="workspace.rate_limited",
                retry_after=60,
            )
        ),
    )

    await workflow_websocket(mock_websocket, "workflow-id")

    mock_websocket.send_json.assert_awaited_once()
    sent_payload = mock_websocket.send_json.await_args.args[0]
    assert sent_payload["code"] == "workspace.rate_limited"


@pytest.mark.asyncio
async def test_workflow_websocket_handles_quota_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_workflow messages should report quota errors."""

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_websocket.receive_json.side_effect = [
        {
            "type": "train_workflow",
            "graph_config": {"nodes": []},
            "inputs": {},
            "execution_id": "exec-3",
        }
    ]
    mock_websocket.state = SimpleNamespace()
    backend_app_module = importlib.import_module("orcheo_backend.app")
    monkeypatch.setattr(
        backend_app_module,
        "authenticate_websocket",
        AsyncMock(return_value={"sub": "tester"}),
    )
    monkeypatch.setattr(
        backend_app_module,
        "get_repository",
        lambda: SimpleNamespace(
            resolve_workflow_ref=AsyncMock(return_value="workflow-1"),
            get_workflow_workspace_id=AsyncMock(return_value="workspace-1"),
        ),
    )
    monkeypatch.setattr(
        websocket_routes.get_workspace_governance(),
        "check_api_rate_limit",
        lambda workspace_id: (_ for _ in ()).throw(
            WorkspaceQuotaExceededError(
                "Workspace reached its concurrent run limit",
                code="workspace.quota.concurrent_runs",
                details={"limit": 1, "current": 1},
            )
        ),
    )

    await workflow_websocket(mock_websocket, "workflow-id")

    mock_websocket.send_json.assert_awaited_once()
    sent_payload = mock_websocket.send_json.await_args.args[0]
    assert sent_payload["code"] == "workspace.quota.concurrent_runs"
