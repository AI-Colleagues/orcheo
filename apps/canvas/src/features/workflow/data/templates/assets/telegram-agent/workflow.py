from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.connectors.telegram import MessageTelegramNode


class TelegramMessageInput(BaseModel):
    """Input for the Telegram send tool."""

    message: str = Field(description="Message to send to Telegram.")


def build_telegram_tool_graph() -> StateGraph:
    """Build a Telegram tool subworkflow."""
    graph = StateGraph(State)
    graph.add_node(
        "send_telegram_message",
        MessageTelegramNode(
            name="send_telegram_message",
            token="[[telegram_token]]",
            chat_id="{{config.configurable.telegram_chat_id}}",
            message="{{inputs.message}}",
        ),
    )
    graph.add_edge(START, "send_telegram_message")
    graph.add_edge("send_telegram_message", END)
    return graph


def orcheo_workflow() -> StateGraph:
    """Build a Telegram agent workflow."""
    graph = StateGraph(State)

    agent = AgentNode(
        name="telegram_agent",
        ai_model="{{config.configurable.ai_model}}",
        system_prompt="{{config.configurable.system_prompt}}",
        model_kwargs={"api_key": "[[openai_api_key]]"},
        workflow_tools=[
            {
                "name": "send_telegram_message",
                "description": (
                    "Send a Telegram message to the configured chat when the "
                    "user asks for a notification or delivery."
                ),
                "graph": build_telegram_tool_graph(),
                "args_schema": TelegramMessageInput,
            }
        ],
    )

    graph.add_node("telegram_agent", agent)
    graph.add_edge(START, "telegram_agent")
    graph.add_edge("telegram_agent", END)
    return graph
