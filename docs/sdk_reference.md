# SDK Reference

This guide covers the Orcheo Python SDK (`orcheo-sdk`) for authoring workflows
and triggering runs against the Orcheo backend over HTTP.

The SDK is intentionally small and synchronous. Day-to-day workflow and
credential management is performed with the [`orcheo` CLI](cli_reference.md);
the Python SDK focuses on:

- Authoring workflows programmatically (`Workflow`, `WorkflowNode`).
- Composing backend URLs and request payloads (`OrcheoClient`).
- Triggering runs and inspecting credentials over HTTP
  (`HttpWorkflowExecutor`).

## Installation

```bash
pip install orcheo-sdk
# or with uv
uv tool install orcheo-sdk
```

## Public API

The SDK exports the following symbols from `orcheo_sdk`:

| Symbol | Purpose |
|--------|---------|
| `OrcheoClient` | URL/header/payload helper for backend requests |
| `HttpWorkflowExecutor` | Synchronous HTTP runner for workflow triggers and credential checks |
| `WorkflowExecutionError` | Raised when triggering a run fails |
| `Workflow` | Builder for assembling a graph from typed nodes |
| `WorkflowNode` | Base class for authoring typed nodes |
| `DeploymentRequest` | Dataclass describing an HTTP deploy request |

```python
from orcheo_sdk import (
    DeploymentRequest,
    HttpWorkflowExecutor,
    OrcheoClient,
    Workflow,
    WorkflowExecutionError,
    WorkflowNode,
)
```

## OrcheoClient

`OrcheoClient` is a lightweight, dataclass-based helper that composes URLs and
headers for the Orcheo backend. It does not perform any I/O on its own.

```python
from orcheo_sdk import OrcheoClient

client = OrcheoClient(
    base_url="http://localhost:8000",
    default_headers={"X-Tenant": "demo"},  # optional
    request_timeout=30.0,                   # optional, seconds
)
```

Constructor fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `base_url` | `str` | required | Backend base URL (e.g. `http://localhost:8000`) |
| `default_headers` | `MutableMapping[str, str]` | `{}` | Headers merged into every request |
| `request_timeout` | `float` | `30.0` | Default request timeout in seconds |

Methods:

- `workflow_trigger_url(workflow_id)` → URL for `POST /api/workflows/{id}/runs`
- `workflow_collection_url()` → URL for `/api/workflows`
- `credential_health_url(workflow_id)` → URL for the credential health report
- `credential_validation_url(workflow_id)` → URL for on-demand validation
- `websocket_url(workflow_id)` → `ws(s)://…/ws/workflow/{id}` for live streaming
- `prepare_headers(overrides=None)` → merge default headers with per-request overrides
- `build_payload(graph_config, inputs, execution_id=None)` → JSON payload for the WebSocket protocol
- `build_deployment_request(workflow, *, workflow_id=None, metadata=None, headers=None)` → `DeploymentRequest` for `POST` (create) or `PUT` (update)

## HttpWorkflowExecutor

`HttpWorkflowExecutor` triggers workflow runs and queries credential health
over HTTP. It is **synchronous** and uses `httpx` with retry/backoff for
transient 5xx errors.

```python
import os
from orcheo_sdk import HttpWorkflowExecutor, OrcheoClient

client = OrcheoClient(base_url="http://localhost:8000")
executor = HttpWorkflowExecutor(
    client=client,
    auth_token=os.environ.get("ORCHEO_SERVICE_TOKEN"),
    timeout=30.0,
    max_retries=3,
    backoff_factor=0.5,
)

result = executor.trigger_run(
    workflow_id="my-workflow",
    workflow_version_id="v1",
    triggered_by="sdk-user",
    inputs={"query": "What is RAG?"},
)
print(result)  # {"run_id": "...", ...} as returned by the backend
```

Key methods:

- `trigger_run(workflow_id, *, workflow_version_id, triggered_by, inputs=None, headers=None, runnable_config=None)` — `POST /api/workflows/{id}/runs`. Retries on `500/502/503/504` up to `max_retries` with exponential backoff.
- `get_credential_health(workflow_id, *, headers=None)` — `GET` the credential health report.
- `validate_credentials(workflow_id, *, actor="system", headers=None)` — trigger a credential validation pass.

When `auth_token` is set, the executor automatically adds
`Authorization: Bearer <token>` to outgoing requests unless an explicit
`Authorization` header is provided.

### Errors

```python
from orcheo_sdk import HttpWorkflowExecutor, WorkflowExecutionError

try:
    executor.trigger_run(
        workflow_id="my-workflow",
        workflow_version_id="v1",
        triggered_by="sdk-user",
        inputs={"query": "test"},
    )
except WorkflowExecutionError as exc:
    print(f"Run trigger failed (status={exc.status_code}): {exc}")
```

`WorkflowExecutionError.status_code` is set when the backend returned a
non-2xx response; for network-level failures it is `None`.

## Authoring Workflows

`Workflow` and `WorkflowNode` provide a typed builder for assembling graphs
that can be deployed to the backend.

```python
from pydantic import BaseModel
from orcheo_sdk import OrcheoClient, Workflow, WorkflowNode


class EchoConfig(BaseModel):
    message: str


class EchoNode(WorkflowNode[EchoConfig]):
    type_name = "echo"


workflow = Workflow(name="hello-world")
workflow.add_node(EchoNode(name="greet", config=EchoConfig(message="hi")))

graph_config = workflow.to_graph_config()  # nodes + edges (with START/END)

client = OrcheoClient(base_url="http://localhost:8000")
deploy = client.build_deployment_request(workflow)
# deploy.method, deploy.url, deploy.json, deploy.headers — send via httpx, etc.
```

Nodes without explicit `depends_on` are wired from `START`; terminal nodes
(those with no dependents) are wired to `END` automatically.

## Workflow & Credential Management

The Python SDK does not expose async client methods for listing or mutating
workflows and credentials. These operations live in the
[`orcheo` CLI](cli_reference.md):

- Workflows: `orcheo workflow list|show|run|publish|schedule|listen|...`
- Credentials: `orcheo credential list|create|update|delete`

The CLI reuses the same backend HTTP API that `HttpWorkflowExecutor` calls,
so you can mix SDK-driven runs with CLI-driven authoring.

## Live Telemetry (WebSocket)

For real-time run telemetry, connect to the WebSocket URL produced by
`OrcheoClient.websocket_url(workflow_id)` and send the payload returned by
`build_payload(...)`:

```python
import asyncio
import json
import websockets
from orcheo_sdk import OrcheoClient


async def stream(workflow_id: str, graph_config: dict, inputs: dict) -> None:
    client = OrcheoClient(base_url="http://localhost:8000")
    url = client.websocket_url(workflow_id)
    payload = client.build_payload(graph_config, inputs)

    async with websockets.connect(url) as ws:
        await ws.send(json.dumps(payload))
        async for raw in ws:
            event = json.loads(raw)
            print(event)


asyncio.run(stream("my-workflow", graph_config={...}, inputs={"query": "hi"}))
```

The WebSocket endpoint is implemented at
`/ws/workflow/{workflow_id}` on the backend.

## State Model

Orcheo workflows pass a typed state object between nodes at runtime:

```python
from typing import Any
from langgraph.graph import MessagesState


class State(MessagesState):
    inputs: dict[str, Any]      # Workflow inputs
    results: dict[str, Any]     # Node outputs (keyed by node name)
    structured_response: Any    # Final output
    config: dict[str, Any]      # Runtime config
```

Downstream nodes reference upstream outputs via variable interpolation, e.g.
`{{results.retriever.documents}}`.

## Environment Variables

The SDK respects the following environment variables when used by helper
scripts and the CLI:

| Variable | Description |
|----------|-------------|
| `ORCHEO_API_URL` | Backend API URL |
| `ORCHEO_SERVICE_TOKEN` | Service token for authentication |

See [Environment Variables](environment_variables.md) for the full reference.

## See Also

- [CLI Reference](cli_reference.md) — `orcheo` / `horcheo` command-line tools
- [Plugin Author Guide](custom_nodes_and_tools.md) — extend Orcheo with managed plugins
- [Deployment Guide](deployment.md) — production deployment recipes
- [Environment Variables](environment_variables.md) — complete configuration reference
