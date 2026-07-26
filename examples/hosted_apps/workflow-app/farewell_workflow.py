"""Farewell workflow used by the Hosted Apps example."""

from orcheo.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes import CodeNode


class CreateFarewell(CodeNode):
    """Return the structured response consumed by the browser app."""

    async def run(self, state, config):
        """Create a farewell from the workflow inputs."""
        name = state["inputs"]["name"].strip()
        return {
            "structured_response": {
                "farewell": f"Goodbye, {name}!",
                "name": name,
            }
        }


async def orcheo_workflow() -> StateGraph:
    """Build the farewell workflow."""
    graph = StateGraph(State)
    graph.add_node("create_farewell", CreateFarewell(name="create_farewell"))
    graph.add_edge(START, "create_farewell")
    graph.add_edge("create_farewell", END)
    return graph
