"""Agentensor evaluation example using Orcheo integration."""

from __future__ import annotations
import asyncio
import uuid
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from orcheo.agentensor.evaluation import (
    EvaluationCase,
    EvaluationContext,
    EvaluationDataset,
    EvaluatorDefinition,
)
from orcheo.agentensor.prompts import TrainablePrompt
from orcheo.graph.state import State
from orcheo.nodes.ai.agentensor import AgentensorNode
from orcheo.nodes.base import TaskNode
from orcheo.runtime.runnable_config import RunnableConfigModel


NODE_NAME = "prompt_echo"


class PromptEchoNode(TaskNode):
    """Echo the configured prompt alongside the user input."""

    prompt_text: str
    input_text: str

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return the combined prompt and input text."""
        reply = f"{self.prompt_text} {self.input_text}".strip()
        return {"reply": reply}


def must_include_input(context: EvaluationContext) -> dict[str, Any]:
    """Require the reply to include the expected snippet."""
    output = context.output if isinstance(context.output, dict) else {}
    node_output = output.get(NODE_NAME, {})
    reply = str(node_output.get("reply", ""))
    expected = str(context.metadata.get("must_include", ""))
    passed = bool(expected) and expected.lower() in reply.lower()
    reason = None
    if not passed:
        reason = f"Expected '{expected}' to appear in reply."
    return {"value": passed, "reason": reason}


def build_graph() -> StateGraph:
    """Construct the demo workflow graph."""
    graph = StateGraph(State)
    graph.add_node(
        NODE_NAME,
        PromptEchoNode(
            name=NODE_NAME,
            prompt_text="{{config.prompts.greeter.text}}",
            input_text="{{inputs.message}}",
        ),
    )
    graph.add_edge(START, NODE_NAME)
    graph.add_edge(NODE_NAME, END)
    return graph


async def run_evaluation() -> None:
    """Run the Agentensor evaluation loop locally."""
    execution_id = f"agentensor-eval-{uuid.uuid4()}"
    runnable_config = RunnableConfigModel(
        run_name="agentensor-eval-demo",
        tags=["agentensor", "evaluation"],
        metadata={"experiment": "agentensor-eval"},
        prompts={
            "greeter": TrainablePrompt(
                text="You are a helpful assistant.",
                requires_grad=True,
                metadata={"locale": "en-US"},
            )
        },
    )
    runtime_config = runnable_config.to_runnable_config(execution_id)
    state_config = runnable_config.to_state_config(execution_id)

    dataset = EvaluationDataset(
        id="demo-eval-dataset",
        cases=[
            EvaluationCase(
                inputs={"message": "Hello there!", "locale": "en-US"},
                metadata={"must_include": "Hello"},
            ),
            EvaluationCase(
                inputs={"message": "Good morning!", "locale": "en-US"},
                metadata={"must_include": "Good"},
            ),
        ],
    )

    evaluators = [
        EvaluatorDefinition(
            id="includes-input",
            entrypoint="__main__:must_include_input",
        )
    ]

    compiled_graph = build_graph().compile()

    async def on_progress(payload: dict[str, Any]) -> None:
        event = payload.get("event", "unknown")
        print(f"[progress] {event}: {payload.get('payload', {})}")

    node = AgentensorNode(
        name="agentensor_eval",
        mode="evaluate",
        prompts=runnable_config.prompts or {},
        dataset=dataset,
        evaluators=evaluators,
        compiled_graph=compiled_graph,
        graph_config={},
        state_config=state_config,
        progress_callback=on_progress,
    )

    state: State = {
        "inputs": {},
        "messages": [],
        "results": {},
        "structured_response": None,
        "config": state_config,
    }

    result = await node(state, runtime_config)
    payload = result["results"][node.name]
    print("\nSummary:", payload["summary"])


def main() -> None:
    """Entrypoint for the evaluation example."""
    asyncio.run(run_evaluation())


if __name__ == "__main__":
    main()
