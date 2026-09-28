"""Helpers for exercising the backend-hosted MCP server over HTTP."""

from __future__ import annotations
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from itertools import count
from typing import Any
import httpx
from fastapi import FastAPI
from orcheo_backend.app.mcp_server import mcp_lifespan


MCP_HEADERS = {"Accept": "application/json, text/event-stream"}

SIMPLE_SCRIPT = """
from langgraph.graph import END, START, StateGraph

def build_graph():
    graph = StateGraph(dict)
    graph.add_node("start", lambda state: state)
    graph.add_edge(START, "start")
    graph.add_edge("start", END)
    return graph


if __name__ == "__main__":
    raise SystemExit("must be stripped before upload")
""".strip()

CRON_SCRIPT = """
from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.triggers import CronTriggerNode

def build_graph():
    graph = StateGraph(State)
    graph.add_node(
        "cron",
        CronTriggerNode(name="cron", expression="{expression}", timezone="UTC"),
    )
    graph.add_edge(START, "cron")
    graph.add_edge("cron", END)
    return graph
""".strip()


def cron_script(expression: str = "*/5 * * * *") -> str:
    """Return a workflow script declaring a cron trigger."""
    return CRON_SCRIPT.replace("{expression}", expression)


class McpCaller:
    """Minimal JSON-RPC client for the stateless MCP endpoint."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self._ids = count(1)

    async def rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return await self.client.post(
            "/api/mcp",
            json={
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "method": method,
                "params": params or {},
            },
            headers={**MCP_HEADERS, **(headers or {})},
        )

    async def _call(
        self, name: str, arguments: dict[str, Any] | None
    ) -> dict[str, Any]:
        response = await self.rpc(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        assert response.status_code == 200, response.text
        return response.json()["result"]

    async def call_result(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call a tool and return the full CallToolResult, asserting success."""
        result = await self._call(name, arguments)
        assert not result.get("isError"), result["content"][0]["text"]
        return result

    async def call(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call a tool and return its structured result, asserting success."""
        result = await self._call(name, arguments)
        assert not result.get("isError"), result["content"][0]["text"]
        return result["structuredContent"]

    async def call_error(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> str:
        """Call a tool that is expected to fail and return its error text."""
        result = await self._call(name, arguments)
        assert result.get("isError"), result
        return result["content"][0]["text"]


@asynccontextmanager
async def mcp_session(app: FastAPI) -> AsyncIterator[McpCaller]:
    """Run the MCP session manager and yield a caller bound to ``app``."""
    async with mcp_lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield McpCaller(client)
