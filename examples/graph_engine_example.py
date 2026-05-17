"""Example of a simple workflow using Orcheo.

To run this example, you need to have the backend running: `make dev-server`.
"""

import asyncio
import json
from typing import Any
import websockets


def create_workflow() -> dict[str, Any]:
    """Create a simple workflow with a TaskNode-based transform."""
    return {
        "nodes": [
            {"name": "START", "type": "START"},
            {
                "name": "code",
                "type": "DataTransformNode",
                "input_data": {"value": "Hello, world!"},
                "transforms": [{"source": "value", "target": "message"}],
            },
            {"name": "END", "type": "END"},
        ],
        "edges": [("START", "code"), ("code", "END")],
    }


async def run_workflow():
    """Run the workflow."""
    uri = "ws://localhost:2025/ws/workflow/test-1"
    async with websockets.connect(uri) as websocket:
        # Send workflow request
        config = create_workflow()
        await websocket.send(
            json.dumps(
                {
                    "type": "run_workflow",
                    "graph_config": config,
                    "inputs": {
                        "messages": [{"type": "human", "content": "Hello!"}],
                        "system_prompt": "You are a helpful AI assistant.",
                    },
                }
            )
        )

        # Receive state updates
        while True:
            response = await websocket.recv()
            state = json.loads(response)
            print(f"State update: {state}")
            if state.get("status") in ["completed", "error"]:
                break


if __name__ == "__main__":
    asyncio.run(run_workflow())
