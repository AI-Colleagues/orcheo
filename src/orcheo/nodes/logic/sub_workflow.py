"""Node for running inline sub-workflows."""

from __future__ import annotations
import copy
from collections.abc import Mapping
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.nodes.state_utils import normalise_state_snapshot


@registry.register(
    NodeMetadata(
        name="SubWorkflowNode",
        description="Execute a mini workflow inline using the node registry.",
        category="utility",
    )
)
class SubWorkflowNode(TaskNode):
    """Execute a series of nodes sequentially within the current workflow."""

    steps: list[Mapping[str, Any]] = Field(
        default_factory=list,
        description="Sequence of node configurations executed as a sub-workflow",
    )
    propagate_to_parent: bool = Field(
        default=False,
        description="Update the parent state with sub-workflow results",
    )
    include_state: bool = Field(
        default=False,
        description="Include the sub-workflow state in the node output",
    )
    result_step: str | None = Field(
        default=None,
        description="Optional name of the step whose result is returned",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Execute the configured sub-workflow sequentially."""
        if not self.steps:
            return {"steps": [], "result": None}

        sub_state = State(
            {
                "inputs": dict(state.get("inputs", {})),
                "results": copy.deepcopy(state.get("results", {})),
                "messages": list(state.get("messages", [])),
                "structured_response": state.get("structured_response"),
                "config": state.get("config"),
            }
        )

        step_results: list[dict[str, Any]] = []
        result_lookup: dict[str, Any] = {}

        for step in self.steps:
            node_type = step.get("type")
            if not isinstance(node_type, str) or not node_type:
                msg = f"Each step must define a non-empty type: {step!r}"
                raise ValueError(msg)

            node_class = registry.get_node(node_type)
            if node_class is None:
                msg = f"Unknown node type {node_type!r} in sub-workflow"
                raise ValueError(msg)

            params = {key: value for key, value in step.items() if key != "type"}
            node_name = str(params.get("name") or step.get("name") or node_type)
            params.setdefault("name", node_name)

            node_instance = node_class(**params)
            output = await node_instance(sub_state, config)
            node_payload = output["results"][node_name]
            sub_state["results"][node_name] = node_payload

            step_results.append({"name": node_name, "result": node_payload})
            result_lookup[node_name] = node_payload

        if self.propagate_to_parent:
            parent_results = state.setdefault("results", {})
            if isinstance(parent_results, Mapping):
                parent_results.update(sub_state["results"])
            else:
                state["results"] = sub_state["results"]

        final_step = self.result_step or step_results[-1]["name"]
        final_result = result_lookup.get(final_step)

        payload: dict[str, Any] = {
            "steps": step_results,
            "result": final_result,
        }
        if self.include_state:
            payload["state"] = normalise_state_snapshot(sub_state)
        return payload


__all__ = ["SubWorkflowNode"]
