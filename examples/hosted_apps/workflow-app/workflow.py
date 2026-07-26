"""Greeting workflow used by the Hosted Apps example."""

from orcheo.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes import CodeNode


class CreateGreeting(CodeNode):
    """Return the structured response consumed by the browser app."""

    async def run(self, state, config):
        """Create a greeting from the workflow inputs."""
        name = state["inputs"]["name"].strip()
        return {
            "structured_response": {
                "greeting": f"Hello, {name}!",
                "name": name,
            }
        }


async def orcheo_workflow() -> StateGraph:
    """Build the greeting workflow."""
    graph = StateGraph(State)
    graph.add_node("create_greeting", CreateGreeting(name="create_greeting"))
    graph.add_edge(START, "create_greeting")
    graph.add_edge("create_greeting", END)
    return graph
