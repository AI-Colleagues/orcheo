from langgraph.graph import StateGraph
from orcheo.graph.state import State
from orcheo.nodes.ai.deep_agent import DeepAgentNode


def orcheo_workflow() -> StateGraph:
    """Build a deep research agent workflow."""
    graph = StateGraph(State)
    agent = DeepAgentNode(
        name="deep_agent",
        ai_model="openai:gpt-4o",
        system_prompt="You are a deep research assistant.",
        research_prompt=(
            "Plan your research steps before executing. "
            "Use available tools to gather information, then synthesise "
            "a comprehensive answer."
        ),
        max_iterations=50,
        model_kwargs={"api_key": "[[openai_api_key]]"},
    )
    graph.add_node("deep_agent", agent)
    graph.set_entry_point("deep_agent")
    graph.set_finish_point("deep_agent")
    return graph
