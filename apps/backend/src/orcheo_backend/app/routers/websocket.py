"""Websocket routes for workflow execution streaming."""

from __future__ import annotations
import asyncio
import uuid
from typing import Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from orcheo_backend.app.authentication import AuthenticationError
from orcheo_backend.app.errors import (
    WorkspaceQuotaExceededError,
    WorkspaceRateLimitError,
)
from orcheo_backend.app.workspace_governance import get_workspace_governance


router = APIRouter()

_CANNOT_SEND_AFTER_CLOSE = 'Cannot call "send" once a close message has been sent.'


@router.websocket("/ws/workflow/{workflow_ref}")
async def workflow_websocket(websocket: WebSocket, workflow_ref: str) -> None:  # noqa: C901,PLR0912,PLR0915
    """Handle workflow websocket connections by delegating to the executor."""
    from orcheo_backend.app import (
        authenticate_websocket,
        execute_workflow,
        execute_workflow_evaluation,
        execute_workflow_training,
        get_repository,
    )

    try:
        context = await authenticate_websocket(websocket)
    except AuthenticationError:
        return

    subprotocol = getattr(websocket.state, "subprotocol", None)
    if subprotocol:
        await websocket.accept(subprotocol=subprotocol)
    else:
        await websocket.accept()
    websocket.state.auth = context

    try:
        repository = get_repository()
        resolved_workflow_uuid = await repository.resolve_workflow_ref(workflow_ref)
        resolved_workflow_id = str(resolved_workflow_uuid)
        workflow_workspace_id = await repository.get_workflow_workspace_id(
            resolved_workflow_uuid
        )
        while True:
            data = await websocket.receive_json()

            message_type = data.get("type")
            if message_type == "run_workflow":
                execution_id = data.get("execution_id", str(uuid.uuid4()))
                if workflow_workspace_id is not None:
                    get_workspace_governance().check_api_rate_limit(
                        workflow_workspace_id
                    )
                task = asyncio.create_task(
                    execute_workflow(
                        resolved_workflow_id,
                        data["graph_config"],
                        data["inputs"],
                        execution_id,
                        websocket,
                        workspace_id=workflow_workspace_id,
                        runnable_config=data.get("runnable_config"),
                        stored_runnable_config=data.get("stored_runnable_config"),
                    )
                )

                await task
                break
            if message_type == "evaluate_workflow":
                execution_id = data.get("execution_id", str(uuid.uuid4()))
                if workflow_workspace_id is not None:
                    get_workspace_governance().check_api_rate_limit(
                        workflow_workspace_id
                    )
                task = asyncio.create_task(
                    execute_workflow_evaluation(
                        resolved_workflow_id,
                        data["graph_config"],
                        data.get("inputs", {}),
                        execution_id,
                        websocket,
                        evaluation=data.get("evaluation"),
                        workspace_id=workflow_workspace_id,
                        runnable_config=data.get("runnable_config"),
                        stored_runnable_config=data.get("stored_runnable_config"),
                    )
                )
                await task
                break
            if message_type == "train_workflow":
                execution_id = data.get("execution_id", str(uuid.uuid4()))
                if workflow_workspace_id is not None:
                    get_workspace_governance().check_api_rate_limit(
                        workflow_workspace_id
                    )
                task = asyncio.create_task(
                    execute_workflow_training(
                        resolved_workflow_id,
                        data["graph_config"],
                        data.get("inputs", {}),
                        execution_id,
                        websocket,
                        training=data.get("training"),
                        workspace_id=workflow_workspace_id,
                        runnable_config=data.get("runnable_config"),
                        stored_runnable_config=data.get("stored_runnable_config"),
                    )
                )
                await task
                break

            await _safe_send_error_payload(  # pragma: no cover
                websocket, {"status": "error", "error": "Invalid message type"}
            )

    except WebSocketDisconnect:
        return
    except WorkspaceRateLimitError as exc:
        await _safe_send_error_payload(
            websocket,
            {"status": "error", "error": exc.message, "code": exc.code},
        )
    except WorkspaceQuotaExceededError as exc:
        await _safe_send_error_payload(
            websocket,
            {"status": "error", "error": exc.message, "code": exc.code},
        )
    except Exception as exc:  # pragma: no cover
        await _safe_send_error_payload(
            websocket, {"status": "error", "error": str(exc)}
        )
    finally:
        await _safe_close_websocket(websocket)


async def _safe_send_error_payload(
    websocket: WebSocket,
    payload: dict[str, Any],
) -> None:
    """Send a JSON error payload if the websocket is still open."""
    try:
        await websocket.send_json(payload)
    except WebSocketDisconnect:
        return
    except RuntimeError as exc:
        if str(exc) == _CANNOT_SEND_AFTER_CLOSE:
            return
        raise


async def _safe_close_websocket(websocket: WebSocket) -> None:
    """Close the websocket without raising if the client already closed."""
    try:
        await websocket.close()
    except WebSocketDisconnect:
        return
    except RuntimeError as exc:
        if str(exc) == _CANNOT_SEND_AFTER_CLOSE:
            return
        raise


__all__ = ["router"]
