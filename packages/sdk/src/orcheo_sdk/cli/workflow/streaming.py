"""Streaming helpers for workflow execution."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from orcheo_sdk.cli.output import print_json, render_json
from orcheo_sdk.cli.state import CLIState
from orcheo_sdk.services import get_latest_workflow_version_data


def _resolve_ws_headers(state: CLIState) -> dict[str, str] | None:
    """Return websocket auth headers if a token is available."""
    token = state.client.get_active_token()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def _connect_websocket(
    websockets: Any,
    websocket_url: str,
    *,
    open_timeout: float,
    close_timeout: float,
    headers: dict[str, str] | None,
) -> Any:
    """Connect to the workflow websocket with compatible header arguments."""
    kwargs = {
        "open_timeout": open_timeout,
        "close_timeout": close_timeout,
        # Workflow nodes can emit payloads above 1 MiB; disable client-side frame limit.
        "max_size": None,
    }
    if not headers:
        return websockets.connect(websocket_url, **kwargs)
    try:
        return websockets.connect(
            websocket_url,
            additional_headers=headers,
            **kwargs,
        )
    except TypeError:
        return websockets.connect(
            websocket_url,
            extra_headers=headers,
            **kwargs,
        )


async def _stream_workflow_run(
    state: CLIState,
    workflow_id: str,
    graph_config: dict[str, Any],
    inputs: Mapping[str, Any],
    *,
    triggered_by: str | None = None,
    runnable_config: Mapping[str, Any] | None = None,
    stored_runnable_config: Mapping[str, Any] | None = None,
) -> str:
    """Stream workflow execution via WebSocket and display node outputs."""
    import json
    import uuid
    import websockets
    from websockets import exceptions as ws_exceptions
    from orcheo_sdk.cli import workflow as workflow_module

    ws_base = state.client.base_url.replace("http://", "ws://").replace(
        "https://", "wss://"
    )
    websocket_url = f"{ws_base}/ws/workflow/{workflow_id}"
    execution_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "type": "run_workflow",
        "graph_config": graph_config,
        "inputs": dict(inputs),
        "execution_id": execution_id,
    }
    if triggered_by is not None:
        payload["triggered_by"] = triggered_by
    if runnable_config is not None:
        payload["runnable_config"] = runnable_config
    if stored_runnable_config is not None:
        payload["stored_runnable_config"] = stored_runnable_config

    if state.verbose_results and not state.human:
        print_json(
            {
                "event": "workflow.execution.start",
                "execution_id": execution_id,
                "workflow_id": workflow_id,
            }
        )
    else:
        state.console.print("[cyan]Starting workflow execution...[/cyan]")
        state.console.print(f"[dim]Execution ID: {execution_id}[/dim]\n")

    try:
        headers = _resolve_ws_headers(state)
        async with _connect_websocket(
            websockets,
            websocket_url,
            open_timeout=5,
            close_timeout=5,
            headers=headers,
        ) as websocket:
            await websocket.send(json.dumps(payload))
            process_messages = getattr(
                workflow_module,
                "_process_stream_messages",
                _process_stream_messages,
            )
            return await process_messages(state, websocket)
    except (ConnectionRefusedError, OSError) as exc:
        state.console.print(
            "[red]Failed to connect to server.[/red]\n"
            "[dim]Ensure the backend is running.[/dim]"
        )
        state.console.print(f"[dim]Error: {exc}[/dim]")
        return "connection_error"
    except getattr(workflow_module, "TimeoutError", TimeoutError):
        state.console.print(
            "[red]Timed out while connecting.[/red]\n"
            "[dim]Retry once the server is reachable.[/dim]"
        )
        return "timeout"
    except ws_exceptions.InvalidStatusCode as exc:  # type: ignore[attr-defined]
        state.console.print(
            f"[red]Server rejected connection (HTTP {exc.status_code}).[/red]\n"
            "[dim]Verify the workflow ID and backend availability.[/dim]"
        )
        return f"http_{exc.status_code}"
    except ws_exceptions.WebSocketException as exc:
        state.console.print(f"[red]WebSocket error: {exc}[/red]")
        return "websocket_error"


async def _stream_workflow_evaluation(
    state: CLIState,
    workflow_id: str,
    graph_config: dict[str, Any],
    inputs: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    *,
    triggered_by: str | None = None,
    runnable_config: Mapping[str, Any] | None = None,
    stored_runnable_config: Mapping[str, Any] | None = None,
) -> str:
    """Stream workflow evaluation via WebSocket."""
    import json
    import uuid
    import websockets
    from websockets import exceptions as ws_exceptions
    from orcheo_sdk.cli import workflow as workflow_module

    ws_base = state.client.base_url.replace("http://", "ws://").replace(
        "https://", "wss://"
    )
    websocket_url = f"{ws_base}/ws/workflow/{workflow_id}"
    execution_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "type": "evaluate_workflow",
        "graph_config": graph_config,
        "inputs": dict(inputs),
        "execution_id": execution_id,
        "evaluation": evaluation,
    }
    if triggered_by is not None:
        payload["triggered_by"] = triggered_by
    if runnable_config is not None:
        payload["runnable_config"] = runnable_config
    if stored_runnable_config is not None:
        payload["stored_runnable_config"] = stored_runnable_config

    if state.verbose_results and not state.human:
        print_json(
            {
                "event": "workflow.evaluation.start",
                "execution_id": execution_id,
                "workflow_id": workflow_id,
            }
        )
    else:
        state.console.print("[cyan]Starting workflow evaluation...[/cyan]")
        state.console.print(f"[dim]Execution ID: {execution_id}[/dim]\n")

    try:
        headers = _resolve_ws_headers(state)
        async with _connect_websocket(
            websockets,
            websocket_url,
            open_timeout=5,
            close_timeout=5,
            headers=headers,
        ) as websocket:
            await websocket.send(json.dumps(payload))
            process_messages = getattr(
                workflow_module,
                "_process_stream_messages",
                _process_stream_messages,
            )
            return await process_messages(state, websocket)
    except (ConnectionRefusedError, OSError) as exc:
        state.console.print(
            "[red]Failed to connect to server.[/red]\n"
            "[dim]Ensure the backend is running.[/dim]"
        )
        state.console.print(f"[dim]Error: {exc}[/dim]")
        return "connection_error"
    except getattr(workflow_module, "TimeoutError", TimeoutError):
        state.console.print(
            "[red]Timed out while connecting.[/red]\n"
            "[dim]Retry once the server is reachable.[/dim]"
        )
        return "timeout"
    except ws_exceptions.InvalidStatusCode as exc:  # type: ignore[attr-defined]
        state.console.print(
            f"[red]Server rejected connection (HTTP {exc.status_code}).[/red]\n"
            "[dim]Verify the workflow ID and backend availability.[/dim]"
        )
        return f"http_{exc.status_code}"
    except ws_exceptions.WebSocketException as exc:
        state.console.print(f"[red]WebSocket error: {exc}[/red]")
        return "websocket_error"


async def _process_stream_messages(state: CLIState, websocket: Any) -> str:
    """Process streaming messages from WebSocket."""
    import json
    from orcheo_sdk.cli import workflow as workflow_module

    handle_status = getattr(
        workflow_module,
        "_handle_status_update",
        _handle_status_update,
    )
    handle_node_event = getattr(
        workflow_module,
        "_handle_node_event",
        _handle_node_event,
    )
    handle_trace_update = getattr(
        workflow_module,
        "_handle_trace_update",
        _handle_trace_update,
    )
    handle_generic_update = getattr(
        workflow_module,
        "_handle_generic_update",
        _handle_generic_update,
    )

    async for message in websocket:
        update = json.loads(message)
        if state.verbose_results and not state.human:
            print_json(update)
            status = update.get("status")
            if status in {"error", "cancelled", "completed"}:
                return str(status)
            continue
        message_type = update.get("type")
        if message_type == "trace:update":
            handle_trace_update(state, update)
            continue
        status = update.get("status")

        if status:
            final_status = handle_status(state, update)
            if final_status:
                return final_status
            continue

        handle_node_event(state, update)
        if isinstance(update, dict) and not (
            update.get("node") and update.get("event")
        ):
            handle_generic_update(state, update)

    return "completed"


def _handle_status_update(state: CLIState, update: dict[str, Any]) -> str | None:
    """Handle status updates. Returns final status if workflow should end."""
    status = update.get("status")

    if status == "error":
        error_detail = update.get("error") or "Unknown error"
        state.console.print(f"[red]✗ Error: {error_detail}[/red]")
        return "error"
    if status == "cancelled":
        reason = update.get("reason") or "No reason provided"
        state.console.print(f"[yellow]⚠ Cancelled: {reason}[/yellow]")
        return "cancelled"
    if status == "completed":
        state.console.print("[green]✓ Workflow completed successfully[/green]")
        return "completed"

    state.console.print(f"[dim]Status: {status}[/dim]")
    return None


def _handle_node_event(state: CLIState, update: dict[str, Any]) -> None:
    """Handle node execution event updates."""
    node = update.get("node")
    event = update.get("event")
    payload_data = update.get("payload") or update.get("data")
    from orcheo_sdk.cli import workflow as workflow_module

    render_output = getattr(
        workflow_module,
        "_render_node_output",
        _render_node_output,
    )

    if not (node and event):
        return

    if event == "on_chain_start":
        state.console.print(f"[blue]→ {node}[/blue] [dim]starting...[/dim]")
    elif event == "on_chain_end":
        state.console.print(f"[green]✓ {node}[/green]")
        if payload_data:
            render_output(state, payload_data)
    elif event == "on_chain_error":
        error_msg = payload_data.get("error") if payload_data else "Unknown"
        state.console.print(f"[red]✗ {node}[/red] [dim]{error_msg}[/dim]")
    else:
        state.console.print(f"[dim][{event}] {node}: {payload_data}[/dim]")


def _handle_trace_update(state: CLIState, update: dict[str, Any]) -> None:
    """Handle trace update messages emitted by the backend."""
    spans = update.get("spans")
    if not isinstance(spans, list) or not spans:
        if update.get("complete"):
            state.console.print("[dim]Trace update: complete[/dim]")
        return

    last_span = spans[-1]
    if not isinstance(last_span, dict):
        return

    name = last_span.get("name") or "workflow"
    status = last_span.get("status")
    status_code = None
    status_message = None
    if isinstance(status, dict):
        status_code = status.get("code")
        status_message = status.get("message")

    status_text = f" ({status_code})" if status_code else ""
    if status_message:
        status_text = f"{status_text} {status_message}".strip()
    if status_text:
        state.console.print(f"[dim]Trace update: {name}{status_text}[/dim]")
    else:
        state.console.print(f"[dim]Trace update: {name}[/dim]")


def _render_results_payload(
    state: CLIState,
    payload: Any,
    *,
    title: str,
    node: str | None = None,
) -> bool:
    """Render results payloads when present."""
    if not isinstance(payload, dict):
        return False

    results = payload.get(node, payload) if node else payload
    from orcheo_sdk.cli import workflow as workflow_module

    render_json_fn = getattr(
        workflow_module,
        "render_json",
        render_json,
    )
    render_json_fn(state.console, results, title=title)
    return True


def _handle_single_update(state: CLIState, node: str, payload: Any) -> None:
    """Handle a single node update payload."""
    if state.verbose_results and isinstance(payload, dict):
        if _render_results_payload(  # pragma: no branch
            state, payload.get("node_results"), title=f"{node} results", node=node
        ):
            return

    detail = ""
    if isinstance(payload, dict):
        keys = sorted(payload.keys())
        if keys:
            detail = f" ({', '.join(keys[:4])})"
            if len(keys) > 4:
                detail = f"{detail[:-1]}, …)"
    state.console.print(f"[dim]• {node}{detail}[/dim]")


def _handle_generic_update(state: CLIState, update: dict[str, Any]) -> None:
    """Handle generic update payloads without explicit node metadata."""
    if not update:
        return
    if state.verbose_results and _render_results_payload(
        state, update.get("node_results"), title="Results"
    ):
        return
    if len(update) == 1:
        node, payload = next(iter(update.items()))
        if (
            state.verbose_results
            and isinstance(payload, dict)
            and _render_results_payload(
                state, payload.get("node_results"), title=f"{node} results", node=node
            )
        ):
            return
        _handle_single_update(state, node, payload)
        return

    keys = sorted(update.keys())
    if keys:  # pragma: no branch
        preview = ", ".join(keys[:4])
        if len(keys) > 4:
            preview = f"{preview}, …"
        state.console.print(f"[dim]Update keys: {preview}[/dim]")


def _render_node_output(state: CLIState, data: Any) -> None:
    """Render node output in a compact, readable format."""
    if not data:
        return

    from orcheo_sdk.cli import workflow as workflow_module

    render_json_fn = getattr(
        workflow_module,
        "render_json",
        render_json,
    )

    if isinstance(data, dict):
        if len(data) <= 3 and all(
            isinstance(v, str | int | float | bool) for v in data.values()
        ):
            items = [f"{k}={v!r}" for k, v in data.items()]
            state.console.print(f"  [dim]{', '.join(items)}[/dim]")
        else:
            render_json_fn(state.console, data, title=None)
    elif isinstance(data, str) and len(data) < 100:
        state.console.print(f"  [dim]{data}[/dim]")
    else:
        import json as json_module

        try:
            formatted = json_module.dumps(data, indent=2, default=str)
            state.console.print(f"[dim]{formatted}[/dim]")
        except Exception:  # pragma: no cover
            state.console.print(f"  [dim]{data!r}[/dim]")


def _prepare_streaming_graph(
    state: CLIState,
    workflow_id: str,
) -> tuple[dict[str, Any], Mapping[str, Any] | None] | None:
    """Fetch latest workflow graph configuration for streaming."""
    latest_version = get_latest_workflow_version_data(state.client, workflow_id)
    graph_raw = latest_version.get("graph")
    if isinstance(graph_raw, Mapping):
        runnable_raw = latest_version.get("runnable_config")
        runnable_config = (
            dict(runnable_raw) if isinstance(runnable_raw, Mapping) else None
        )
        return dict(graph_raw), runnable_config
    return None


__all__ = [
    "_stream_workflow_run",
    "_process_stream_messages",
    "_handle_status_update",
    "_handle_node_event",
    "_render_node_output",
    "_prepare_streaming_graph",
]
