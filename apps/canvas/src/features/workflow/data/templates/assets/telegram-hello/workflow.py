from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.connectors.telegram import MessageTelegramNode


def orcheo_workflow() -> StateGraph:
    """Build a Telegram hello workflow."""
    graph = StateGraph(State)
    telegram = MessageTelegramNode(
        name="send_telegram_hello",
        token="[[telegram_token]]",
        chat_id="[[telegram_chat_id]]",
        message="Hello",
    )
    graph.add_node("send_telegram_hello", telegram)
    graph.add_edge(START, "send_telegram_hello")
    graph.add_edge("send_telegram_hello", END)
    return graph
