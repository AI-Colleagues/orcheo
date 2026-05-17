"""Submit and execute a LangGraph Python script via the Orcheo backend.

This example reads the LangGraph definition from vanilla_langgraph.py and
submits it to the Orcheo backend for ingestion, then executes the workflow
and streams the results.

Prerequisites:
    1. Install dependencies: `uv sync --all-groups`
    2. Run the backend locally: `make dev-server`

With the server running, execute this script via
`uv run python examples/ingest_langgraph.py` to register the LangGraph
workflow, stream live execution updates, and inspect the stored version
summary.
"""

from __future__ import annotations
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any
import httpx
import websockets
from websockets import exceptions as ws_exceptions


# Read the LangGraph script from vanilla_langgraph.py
# We need to strip out the if __name__ == "__main__" block since
# RestrictedPython doesn't allow variables starting with "_"
SCRIPT_PATH = Path(__file__).parent / "vanilla_langgraph.py"
script_content = SCRIPT_PATH.read_text()

# Remove the if __name__ == "__main__" block and everything after it
lines = script_content.split("\n")
filtered_lines = []
for line in lines:
    if line.strip().startswith('if __name__ == "__main__"'):
        break
    filtered_lines.append(line)

LANGGRAPH_SCRIPT = "\n".join(filtered_lines)

API_BASE_URL = "http://localhost:2025/api"
WEBSOCKET_BASE_URL = "ws://localhost:2025/ws/workflow"


def _create_workflow(client: httpx.Client) -> str:
    """Create a workflow that will receive the ingested LangGraph version."""
    response = client.post(
        "/workflows",
        json={
            "name": "langgraph-import-demo",
            "slug": "langgraph-import-demo",
            "description": "Workflow imported from a LangGraph Python script",
            "tags": ["example", "langgraph"],
            "actor": "example-script",
        },
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    workflow_id = payload["id"]
    print(f"Created workflow {workflow_id} ({payload['name']})")
    return workflow_id


def _ingest_langgraph_version(client: httpx.Client, workflow_id: str) -> dict[str, Any]:
    """Submit the LangGraph script to create a new workflow version."""
    response = client.post(
        f"/workflows/{workflow_id}/versions/ingest",
        json={
            "script": LANGGRAPH_SCRIPT,
            "entrypoint": "build_graph",
            "metadata": {"language": "python", "source": "langgraph"},
            "notes": "Initial LangGraph import",
            "created_by": "example-script",
        },
    )
    response.raise_for_status()
    version: dict[str, Any] = response.json()
    print(
        f"Registered workflow version {version['version']} for workflow {workflow_id}"
    )
    return version


def _handle_status_update(update: dict[str, Any]) -> tuple[str, bool]:
    """Handle status updates and return (final_status, should_break)."""
    status = update.get("status")
    if not status:
        return ("unknown", False)

    final_status = str(status)
    if status == "error":
        detail = update.get("error") or "Unknown error"
        print(f"[status] error: {detail}")
    elif status == "cancelled":
        reason = update.get("reason") or "No reason provided"
        print(f"[status] cancelled: {reason}")
    else:
        print(f"[status] {status}")

    should_break = status in {"completed", "error", "cancelled"}
    return (final_status, should_break)


async def _stream_workflow_execution(
    workflow_id: str,
    graph_config: dict[str, Any],
    *,
    inputs: dict[str, Any],
) -> str:
    """Run the workflow over WebSocket and print streaming updates."""
    websocket_url = f"{WEBSOCKET_BASE_URL}/{workflow_id}"
    execution_id = str(uuid.uuid4())
    payload = {
        "type": "run_workflow",
        "graph_config": graph_config,
        "inputs": inputs,
        "execution_id": execution_id,
    }

    print("\nStarting workflow execution...")
    print(f"  websocket: {websocket_url}")
    print(f"  execution_id: {execution_id}")
    final_status = "unknown"

    try:
        async with websockets.connect(
            websocket_url,
            open_timeout=5,
            close_timeout=5,
        ) as websocket:
            await websocket.send(json.dumps(payload))

            async for message in websocket:
                update = json.loads(message)
                status = update.get("status")
                if status:
                    final_status, should_break = _handle_status_update(update)
                    if should_break:
                        break
                    continue

                node = update.get("node")
                event = update.get("event")
                payload_data = update.get("payload") or update.get("data")
                if node and event:
                    print(f"[{event}] {node}: {payload_data}")
                else:
                    print(f"Update: {update}")
    except (ConnectionRefusedError, OSError) as exc:
        print(
            "Failed to connect to the Orcheo server. "
            "Ensure `make dev-server` is running before executing this script."
        )
        print(f"Connection error: {exc}")
        final_status = "connection_error"
    except TimeoutError:
        print(
            "Timed out while establishing or closing the WebSocket connection. "
            "Retry once the server is reachable."
        )
        final_status = "timeout"
    except ws_exceptions.InvalidStatusCode as exc:
        print(
            "The server rejected the WebSocket handshake. Verify the workflow "
            "identifier and backend availability."
        )
        print(f"HTTP status: {exc.status_code}")
        final_status = f"http_{exc.status_code}"
    except ws_exceptions.WebSocketException as exc:
        print(f"WebSocket communication error: {exc}")
        final_status = "websocket_error"

    return final_status


def main() -> None:
    """Drive the example ingestion flow."""
    with httpx.Client(base_url=API_BASE_URL, timeout=10.0) as client:
        workflow_id = _create_workflow(client)
        version = _ingest_langgraph_version(client, workflow_id)

    graph_config = version.get("graph")
    if not isinstance(graph_config, dict):
        msg = "Ingested workflow did not return a graph configuration"
        raise RuntimeError(msg)

    execution_inputs = {"name": "LangGraph Developer"}
    status = asyncio.run(
        _stream_workflow_execution(
            workflow_id,
            graph_config,
            inputs=execution_inputs,
        )
    )

    print(f"\nWorkflow execution finished with status: {status}")

    summary = graph_config.get("summary", {})
    print("\nGraph summary:")
    print(f"  nodes: {summary.get('nodes')}")
    print(f"  edges: {summary.get('edges')}")
    print(f"  entrypoint: {summary.get('entrypoint')}")
    print(f"  finish_points: {summary.get('finish_points')}")


if __name__ == "__main__":
    main()
