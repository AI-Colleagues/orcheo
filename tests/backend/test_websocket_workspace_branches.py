"""Cover websocket router branches where workflow_workspace_id is None
for evaluate_workflow (78->82) and train_workflow (99->103)."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket

from orcheo_backend.app import workflow_websocket


@pytest.mark.asyncio
async def test_evaluate_workflow_with_no_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """evaluate_workflow with workspace_id=None skips rate-limit check (78->82)."""

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_websocket.receive_json.side_effect = [
        {
            "type": "evaluate_workflow",
            "graph_config": {"nodes": []},
            "inputs": {},
            "execution_id": "exec-eval",
        }
    ]
    mock_websocket.state = SimpleNamespace()

    backend_app_module = importlib.import_module("orcheo_backend.app")
    execute_eval_mock = AsyncMock()
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
            get_workflow_workspace_id=AsyncMock(return_value=None),  # None here
        ),
    )
    monkeypatch.setattr(
        backend_app_module, "execute_workflow_evaluation", execute_eval_mock
    )

    await workflow_websocket(mock_websocket, "workflow-id")

    mock_websocket.accept.assert_awaited_once()
    execute_eval_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_train_workflow_with_no_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """train_workflow with workspace_id=None skips rate-limit check (99->103)."""

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_websocket.receive_json.side_effect = [
        {
            "type": "train_workflow",
            "graph_config": {"nodes": []},
            "inputs": {},
            "execution_id": "exec-train",
        }
    ]
    mock_websocket.state = SimpleNamespace()

    backend_app_module = importlib.import_module("orcheo_backend.app")
    execute_train_mock = AsyncMock()
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
            get_workflow_workspace_id=AsyncMock(return_value=None),  # None here
        ),
    )
    monkeypatch.setattr(
        backend_app_module, "execute_workflow_training", execute_train_mock
    )

    await workflow_websocket(mock_websocket, "workflow-id")

    mock_websocket.accept.assert_awaited_once()
    execute_train_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# run_workflow with non-None workspace_id — rate limit check (line 58)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_workflow_with_workspace_id_checks_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_workflow with non-None workspace_id calls check_api_rate_limit (line 58)."""
    from fastapi import WebSocket
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    workspace_id = "ws-abc"
    mock_websocket = AsyncMock(spec=WebSocket)
    mock_websocket.receive_json.side_effect = [
        {
            "type": "run_workflow",
            "graph_config": {"nodes": []},
            "inputs": {},
            "execution_id": "exec-run",
        }
    ]
    mock_websocket.state = SimpleNamespace()

    rate_limit_calls: list[str] = []

    class Governance:
        def check_api_rate_limit(self, wid: str) -> None:
            rate_limit_calls.append(wid)

    backend_app_module = importlib.import_module("orcheo_backend.app")
    execute_run_mock = AsyncMock()
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
            get_workflow_workspace_id=AsyncMock(return_value=workspace_id),
        ),
    )
    monkeypatch.setattr(backend_app_module, "execute_workflow", execute_run_mock)

    from orcheo_backend.app.routers import websocket as ws_module

    monkeypatch.setattr(ws_module, "get_workspace_governance", lambda: Governance())

    await workflow_websocket(mock_websocket, "workflow-id")

    assert rate_limit_calls == [workspace_id]
    execute_run_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# WebSocketDisconnect inside main try block — line 124
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_websocket_disconnect_in_main_try_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WebSocketDisconnect from receive_json is caught silently (line 124)."""
    from fastapi import WebSocket, WebSocketDisconnect
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    mock_websocket = AsyncMock(spec=WebSocket)
    mock_websocket.receive_json.side_effect = WebSocketDisconnect()
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
            get_workflow_workspace_id=AsyncMock(return_value=None),
        ),
    )

    # Should return without raising
    await workflow_websocket(mock_websocket, "workflow-id")

    mock_websocket.accept.assert_awaited_once()
