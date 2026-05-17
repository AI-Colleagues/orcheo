"""Core Orcheo client utilities for URL and payload composition."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class DeploymentRequest:
    """Representation of a workflow deployment HTTP request."""

    method: Literal["POST", "PUT"]
    url: str
    json: dict[str, Any]
    headers: dict[str, str]


@dataclass(slots=True)
class OrcheoClient:
    """Lightweight helper for composing Orcheo backend requests."""

    base_url: str
    default_headers: MutableMapping[str, str] = field(default_factory=dict)
    request_timeout: float = 30.0

    def workflow_trigger_url(self, workflow_id: str) -> str:
        """Return the URL for triggering a workflow execution."""
        workflow_id = workflow_id.strip()
        if not workflow_id:
            msg = "workflow_id cannot be empty"
            raise ValueError(msg)
        return f"{self.base_url.rstrip('/')}/api/workflows/{workflow_id}/runs"

    def workflow_collection_url(self) -> str:
        """Return the base URL for workflow CRUD operations."""
        return f"{self.base_url.rstrip('/')}/api/workflows"

    def websocket_url(self, workflow_id: str) -> str:
        """Return the WebSocket endpoint used for live workflow streaming."""
        workflow_id = workflow_id.strip()
        if not workflow_id:
            msg = "workflow_id cannot be empty"
            raise ValueError(msg)

        if self.base_url.startswith("https://"):
            protocol = "wss://"
            host = self.base_url.removeprefix("https://")
        elif self.base_url.startswith("http://"):
            protocol = "ws://"
            host = self.base_url.removeprefix("http://")
        else:
            protocol = "ws://"
            host = self.base_url

        host = host.rstrip("/")
        return f"{protocol}{host}/ws/workflow/{workflow_id}"

    def prepare_headers(
        self, overrides: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        """Merge default headers with request specific overrides."""
        merged: dict[str, str] = {**self.default_headers}
        if overrides:
            merged.update(overrides)
        return merged

    def build_payload(
        self,
        graph_config: Mapping[str, Any],
        inputs: Mapping[str, Any],
        execution_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the JSON payload required by the workflow WebSocket."""
        payload: dict[str, Any] = {
            "type": "run_workflow",
            "graph_config": deepcopy(graph_config),
            "inputs": deepcopy(inputs),
        }
        if execution_id:
            payload["execution_id"] = execution_id
        return payload

    def build_deployment_request(
        self,
        workflow: Any,
        *,
        workflow_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> DeploymentRequest:
        """Return the HTTP request metadata for deploying a workflow."""
        url = self.workflow_collection_url()
        method: Literal["POST", "PUT"] = "POST"
        if workflow_id:
            workflow_id = workflow_id.strip()
            if not workflow_id:
                msg = "workflow_id cannot be empty"
                raise ValueError(msg)
            url = f"{url}/{workflow_id}"
            method = "PUT"

        payload = workflow.to_deployment_payload(metadata=metadata)
        merged_headers = self.prepare_headers(headers or {})
        return DeploymentRequest(
            method=method,
            url=url,
            json=payload,
            headers=merged_headers,
        )
