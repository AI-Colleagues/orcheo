from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableConfig

from orcheo.edges import ResultFieldRouteEdge, ResultFlagEdge
from orcheo.graph.state import State


@pytest.mark.asyncio
async def test_result_flag_edge_routes_on_truthy_flag() -> None:
    state = State({"node_results": {"ingest": {"halt": True}}})
    edge = ResultFlagEdge(
        name="after_ingest",
        result_node="ingest",
        flag="halt",
        true_route="codebook_output",
        false_route="open_coder_prepare",
    )

    result = await edge(state, RunnableConfig())

    assert result == "codebook_output"


@pytest.mark.asyncio
async def test_result_flag_edge_routes_on_false_when_missing() -> None:
    state = State({"node_results": {"ingest": {}}})
    edge = ResultFlagEdge(
        name="after_ingest",
        result_node="ingest",
        flag="halt",
        true_route="codebook_output",
        false_route="open_coder_prepare",
    )

    result = await edge(state, RunnableConfig())

    assert result == "open_coder_prepare"


@pytest.mark.asyncio
async def test_result_field_route_edge_allows_known_routes() -> None:
    state = State(
        {"node_results": {"router_dispatch": {"routing": "generate_codebook"}}}
    )
    edge = ResultFieldRouteEdge(
        name="route_after_dispatch",
        result_node="router_dispatch",
        field="routing",
        allowed_routes={"validate_files", "generate_codebook", "export_codebook"},
        fallback_route="final_reply",
    )

    result = await edge(state, RunnableConfig())

    assert result == "generate_codebook"


@pytest.mark.asyncio
async def test_result_field_route_edge_falls_back_for_unexpected_value() -> None:
    state = State({"node_results": {"router_dispatch": {"routing": "unknown"}}})
    edge = ResultFieldRouteEdge(
        name="route_after_dispatch",
        result_node="router_dispatch",
        field="routing",
        allowed_routes={"validate_files", "generate_codebook", "export_codebook"},
        fallback_route="final_reply",
    )

    result = await edge(state, RunnableConfig())

    assert result == "final_reply"
