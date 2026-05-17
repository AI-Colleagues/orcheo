from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.connectors.telegram import (
    MessageTelegramNode,
    TelegramBotListenerNode,
)


def orcheo_workflow() -> StateGraph:
    """Build a private Telegram listener workflow."""
    graph = StateGraph(State)

    graph.add_node(
        "telegram_listener",
        TelegramBotListenerNode(
            name="telegram_listener",
            token="[[telegram_token]]",
            allowed_updates=["message"],
            allowed_chat_types=["private"],
            poll_timeout_seconds=30,
            bot_identity_key="telegram:primary",
        ),
    )
    graph.add_node(
        "agent_reply",
        AgentNode(
            name="agent_reply",
            ai_model="{{config.configurable.ai_model}}",
            system_prompt="{{config.configurable.system_prompt}}",
            model_kwargs={"api_key": "[[openai_api_key]]"},
            use_graph_chat_history=True,
        ),
    )
    graph.add_node(
        "send_telegram",
        MessageTelegramNode(
            name="send_telegram",
            token="[[telegram_token]]",
            chat_id="{{results.telegram_listener.reply_target.chat_id}}",
        ),
    )

    graph.add_edge(START, "telegram_listener")
    graph.add_edge("telegram_listener", "agent_reply")
    graph.add_edge("agent_reply", "send_telegram")
    graph.add_edge("send_telegram", END)
    return graph
