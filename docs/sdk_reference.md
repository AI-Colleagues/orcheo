# SDK Reference

This guide covers the Orcheo Python SDK for programmatic workflow management and execution.

## Installation

```bash
pip install orcheo-sdk
# or with uv
uv tool install orcheo-sdk
```

## Quick Start

```python
from orcheo_sdk import OrcheoClient

# Initialize client
client = OrcheoClient(api_url="http://localhost:2025")

# Execute a workflow
result = await client.execute_workflow(
    workflow_id="my-conversational-search-pipeline",
    inputs={"query": "What is RAG?"}
)
```

## Authentication

### Service Token Authentication

```python
import os
from orcheo_sdk import OrcheoClient

client = OrcheoClient(
    api_url="https://orcheo.example.com",
    token=os.environ["ORCHEO_SERVICE_TOKEN"]
)
```

### Environment-Based Configuration

The SDK respects the following environment variables:

| Variable | Description |
|----------|-------------|
| `ORCHEO_API_URL` | Backend API URL |
| `ORCHEO_SERVICE_TOKEN` | Service token for authentication |

## Client Methods

### Workflow Execution

```python
# Synchronous execution (blocking)
result = await client.execute_workflow(
    workflow_id="my-workflow",
    inputs={"query": "search query"},
    config={"temperature": 0.7}  # Optional LangChain config
)

# Access results
print(result.outputs)
print(result.run_id)
```

### Workflow Management

```python
# List workflows
workflows = await client.list_workflows()

# Get workflow details
workflow = await client.get_workflow("workflow-id")

# Upload a workflow from Python file
workflow_id = await client.upload_workflow(
    file_path="my_pipeline.py",
    name="My Pipeline"
)

# Delete a workflow
await client.delete_workflow("workflow-id")
```

### Streaming Execution

For real-time progress monitoring:

```python
async for event in client.stream_workflow(
    workflow_id="my-workflow",
    inputs={"query": "search query"}
):
    if event.type == "node_start":
        print(f"Starting node: {event.node_name}")
    elif event.type == "node_end":
        print(f"Completed node: {event.node_name}")
    elif event.type == "output":
        print(f"Result: {event.data}")
```

### Credential Management

```python
# List credentials
credentials = await client.list_credentials()

# Create a credential
await client.create_credential(
    name="openai-key",
    provider="openai",
    value={"api_key": "sk-..."}
)
```

## State Management

Orcheo workflows maintain a typed state object that flows between nodes:

```python
from typing import Any
from langgraph.graph import MessagesState

class State(MessagesState):
    inputs: dict[str, Any]      # Workflow inputs
    results: dict[str, Any]     # Node outputs (keyed by node name)
    structured_response: Any    # Final output
    config: dict[str, Any]      # Runtime config
```

The `results` dictionary accumulates outputs from TaskNodes, enabling downstream nodes to access upstream outputs via variable interpolation (e.g., `{{results.retriever.documents}}`).

## Error Handling

```python
from orcheo_sdk import OrcheoClient, OrcheoError, AuthenticationError

try:
    result = await client.execute_workflow(
        workflow_id="my-workflow",
        inputs={"query": "test"}
    )
except AuthenticationError:
    print("Invalid or expired token")
except OrcheoError as e:
    print(f"Workflow error: {e}")
```

## Integration Examples

### Conversational Search Pipeline

```python
from orcheo_sdk import OrcheoClient

async def search(query: str, conversation_history: list = None):
    client = OrcheoClient(api_url="http://localhost:2025")

    result = await client.execute_workflow(
        workflow_id="conversational-rag",
        inputs={
            "query": query,
            "history": conversation_history or []
        }
    )

    return {
        "answer": result.outputs.get("response"),
        "sources": result.outputs.get("sources", [])
    }
```

### Batch Processing

```python
import asyncio
from orcheo_sdk import OrcheoClient

async def batch_process(queries: list[str]):
    client = OrcheoClient(api_url="http://localhost:2025")

    tasks = [
        client.execute_workflow(
            workflow_id="query-processor",
            inputs={"query": q}
        )
        for q in queries
    ]

    results = await asyncio.gather(*tasks)
    return [r.outputs for r in results]
```

## See Also

- [CLI Reference](cli_reference.md) - Command-line interface documentation
- [Plugin Author Guide](custom_nodes_and_tools.md) - Extending Orcheo with managed plugins
- [Deployment Guide](deployment.md) - Production deployment recipes
- [Environment Variables](environment_variables.md) - Complete configuration reference
