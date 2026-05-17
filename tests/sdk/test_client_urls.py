"""Tests covering OrcheoClient helpers and payload building."""

from __future__ import annotations
import pytest
from orcheo_sdk import OrcheoClient


def test_workflow_trigger_url(client: OrcheoClient) -> None:
    assert (
        client.workflow_trigger_url("demo")
        == "http://localhost:2025/api/workflows/demo/runs"
    )


def test_workflow_collection_url(client: OrcheoClient) -> None:
    assert client.workflow_collection_url() == "http://localhost:2025/api/workflows"


def test_workflow_trigger_url_requires_identifier(client: OrcheoClient) -> None:
    with pytest.raises(ValueError):
        client.workflow_trigger_url("   ")


def test_websocket_url_from_http(client: OrcheoClient) -> None:
    assert client.websocket_url("abc") == "ws://localhost:2025/ws/workflow/abc"


def test_websocket_url_from_https() -> None:
    client = OrcheoClient(base_url="https://example.com")
    assert client.websocket_url("wf") == "wss://example.com/ws/workflow/wf"


def test_websocket_url_requires_identifier(client: OrcheoClient) -> None:
    with pytest.raises(ValueError):
        client.websocket_url("   ")


def test_websocket_url_from_no_protocol() -> None:
    client = OrcheoClient(base_url="example.com")
    assert client.websocket_url("wf") == "ws://example.com/ws/workflow/wf"


def test_prepare_headers_merges_defaults(client: OrcheoClient) -> None:
    merged = client.prepare_headers({"Authorization": "Bearer token"})
    assert merged == {"X-Test": "1", "Authorization": "Bearer token"}


def test_prepare_headers_without_overrides(client: OrcheoClient) -> None:
    merged = client.prepare_headers()
    assert merged == {"X-Test": "1"}


def test_build_payload_supports_optional_execution_id(client: OrcheoClient) -> None:
    payload = client.build_payload({"nodes": []}, {"foo": "bar"}, execution_id="123")
    assert payload["execution_id"] == "123"
    assert payload["graph_config"] == {"nodes": []}
    assert payload["inputs"] == {"foo": "bar"}


def test_build_payload_without_execution_id(client: OrcheoClient) -> None:
    payload = client.build_payload({"nodes": []}, {"foo": "bar"})
    assert "execution_id" not in payload


def test_build_payload_returns_run_workflow_shape(client: OrcheoClient) -> None:
    graph_config = {"nodes": [{"name": "first"}], "edges": []}
    inputs = {"name": "Ada"}

    payload = client.build_payload(graph_config, inputs)

    assert payload["type"] == "run_workflow"
    assert payload["graph_config"] == graph_config
    assert payload["inputs"] == inputs
    graph_config["nodes"].append({"name": "mutated"})
    inputs["name"] = "Grace"
    assert payload["graph_config"]["nodes"] == [{"name": "first"}]
    assert payload["inputs"] == {"name": "Ada"}
