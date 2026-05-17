"""Implementation of :class:`HttpRequestNode`."""

from __future__ import annotations
import json
from datetime import timedelta
from typing import Any, Literal
import httpx
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


HttpMethod = Literal[
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "HEAD",
    "OPTIONS",
]


@registry.register(
    NodeMetadata(
        name="HttpRequestNode",
        description="Perform an HTTP request and return the response payload.",
        category="data",
    )
)
class HttpRequestNode(TaskNode):
    """Node that performs HTTP requests using httpx."""

    method: HttpMethod = Field(default="GET", description="HTTP method to execute")
    url: str = Field(description="Fully-qualified request URL")
    params: dict[str, Any] | None = Field(
        default=None, description="Optional query parameters to append to the URL"
    )
    headers: dict[str, str] | None = Field(
        default=None, description="Optional HTTP headers to include"
    )
    json_body: Any | None = Field(
        default=None, description="JSON payload supplied for request bodies"
    )
    content: Any | None = Field(
        default=None,
        description="Raw bytes or text content for the request body",
    )
    data: Any | None = Field(
        default=None,
        description="Form data payload (url-encoded or multipart)",
    )
    timeout: float | None = Field(
        default=30.0, ge=0.0, description="Optional timeout in seconds for the request"
    )
    follow_redirects: bool = Field(
        default=True, description="Follow HTTP redirects returned by the server"
    )
    raise_for_status: bool = Field(
        default=False, description="Raise an error when the response is not 2xx"
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Execute the configured HTTP request."""
        request_kwargs: dict[str, Any] = {
            "method": self.method,
            "url": self.url,
            "params": self.params,
            "headers": self.headers,
            "timeout": self.timeout,
            "follow_redirects": self.follow_redirects,
        }

        if self.json_body is not None:
            request_kwargs["json"] = self.json_body
        if self.content is not None:
            request_kwargs["content"] = self.content
        if self.data is not None:
            request_kwargs["data"] = self.data

        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(**request_kwargs)
        except httpx.HTTPError as exc:  # pragma: no cover - network failure guard
            msg = f"HTTP request failed: {exc!s}"
            raise ValueError(msg) from exc

        if self.raise_for_status:
            response.raise_for_status()

        parsed_json: Any | None
        try:
            parsed_json = response.json()
        except json.JSONDecodeError:
            parsed_json = None

        elapsed: float | None = None
        elapsed_source: Any | None
        try:
            elapsed_source = response.elapsed
        except RuntimeError:
            elapsed_source = response.extensions.get("elapsed")

        if isinstance(elapsed_source, timedelta):
            elapsed = elapsed_source.total_seconds()

        try:
            response_url = str(response.url)
        except RuntimeError:
            response_url = self.url

        return {
            "status_code": response.status_code,
            "reason": response.reason_phrase,
            "url": response_url,
            "headers": dict(response.headers),
            "content": response.text,
            "json": parsed_json,
            "elapsed": elapsed,
        }
