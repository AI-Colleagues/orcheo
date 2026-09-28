"""Integration tests for the execution trace endpoint."""

from __future__ import annotations
import asyncio
from datetime import UTC, datetime
from fastapi.testclient import TestClient
from orcheo_backend.app.dependencies import get_history_store


def test_execution_trace_endpoint(api_client: TestClient) -> None:
    """The trace endpoint should return span metadata for an execution."""

    history_store = get_history_store()
    execution_id = "exec-trace"
    workflow_id = api_client.post(
        "/api/workflows", json={"name": "Trace Flow", "actor": "tester"}
    ).json()["id"]
    trace_id = "0af7651916cd43dd8448eb211c80319c"

    async def _prepare() -> None:
        await history_store.clear()
        started_at = datetime.now(tz=UTC)
        await history_store.start_run(
            workflow_id=workflow_id,
            execution_id=execution_id,
            inputs={"input": "value"},
            runnable_config={"configurable": {"thread_id": "thread-exec-1"}},
            trace_id=trace_id,
            trace_started_at=started_at,
        )
        await history_store.append_step(
            execution_id,
            {
                "draft": {
                    "id": "node-1",
                    "display_name": "Draft Answer",
                    "kind": "ai_model",
                    "status": "completed",
                    "latency_ms": 120,
                    "token_usage": {"input": 5, "output": 7},
                    "prompts": ["Hello"],
                    "responses": ["World"],
                    "artifacts": [{"id": "artifact-1"}],
                    "__trace": {
                        "ai": {
                            "kind": "llm",
                            "requested_model": "openai:gpt-4o-mini",
                            "actual_model": "gpt-4o-mini-2024-07-18",
                            "provider": "openai",
                        }
                    },
                }
            },
        )
        await history_store.mark_completed(execution_id)

    asyncio.run(_prepare())

    response = api_client.get(f"/api/executions/{execution_id}/trace")
    assert response.status_code == 200
    payload = response.json()

    assert payload["execution"]["id"] == execution_id
    assert payload["execution"]["trace_id"] == trace_id
    assert payload["execution"]["thread_id"] == "thread-exec-1"
    assert payload["execution"]["token_usage"] == {"input": 5, "output": 7}
    assert payload["page_info"] == {"has_next_page": False, "cursor": None}

    spans = payload["spans"]
    assert len(spans) == 2
    root_span = spans[0]
    assert root_span["parent_span_id"] is None
    assert root_span["attributes"]["orcheo.execution.id"] == execution_id
    assert root_span["attributes"]["orcheo.execution.thread_id"] == "thread-exec-1"

    node_span = spans[1]
    assert node_span["parent_span_id"] == root_span["span_id"]
    assert node_span["attributes"]["orcheo.node.kind"] == "ai_model"
    assert node_span["attributes"]["orcheo.ai.model.requested"] == "openai:gpt-4o-mini"
    assert node_span["attributes"]["orcheo.ai.model.actual"] == "gpt-4o-mini-2024-07-18"
    assert node_span["attributes"]["orcheo.token.input"] == 5
    assert node_span["attributes"]["orcheo.artifact.ids"] == ["artifact-1"]
    event_names = {event["name"] for event in node_span["events"]}
    assert {"prompt", "response"}.issubset(event_names)


def test_execution_trace_not_found(api_client: TestClient) -> None:
    """Unknown executions should return a 404 response."""

    response = api_client.get("/api/executions/missing-trace/trace")
    assert response.status_code == 404
    assert response.json()["detail"] == "Execution history not found"
