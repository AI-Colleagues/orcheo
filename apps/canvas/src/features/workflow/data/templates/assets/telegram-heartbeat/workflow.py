from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.connectors.telegram import MessageTelegramNode
from orcheo.nodes.triggers import CronTriggerNode


def orcheo_workflow() -> StateGraph:
    """Build a cron-driven Telegram heartbeat workflow."""
    graph = StateGraph(State)
    graph.add_node(
        "cron_trigger",
        CronTriggerNode(
            name="cron_trigger",
            expression="* * * * *",
            timezone="UTC",
            allow_overlapping=True,
        ),
    )
    graph.add_node(
        "send_heartbeat",
        MessageTelegramNode(
            name="send_heartbeat",
            token="[[telegram_token]]",
            chat_id="[[telegram_chat_id]]",
            message="{{config.configurable.heartbeat_message}}",
        ),
    )
    graph.add_edge(START, "cron_trigger")
    graph.add_edge("cron_trigger", "send_heartbeat")
    graph.add_edge("send_heartbeat", END)
    return graph
