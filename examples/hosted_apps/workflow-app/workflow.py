# /// orcheo
# name = "Hosted App Greeting"
# handle = "hosted-app-greeting"
# description = "Returns a greeting to the workflow-backed Hosted App example."
# version = "1.0.0"
# entrypoint = "orcheo_workflow"
# ///

from orcheo.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes import CodeNode


class CreateGreeting(CodeNode):
    """Build the visitor-safe greeting returned by the hosted app."""

    async def run(self, state, config):
        """Normalize the submitted name and return a structured response."""
        name = state["inputs"]["name"].strip()
        return {
            "structured_response": {
                "greeting": "Hello, " + name + "!",
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
