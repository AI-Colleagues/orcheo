from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.connectors.discord import (
    DiscordBotListenerNode,
    MessageDiscordNode,
)


def orcheo_workflow() -> StateGraph:
    """Build a private Discord listener workflow."""
    graph = StateGraph(State)

    graph.add_node(
        "discord_listener",
        DiscordBotListenerNode(
            name="discord_listener",
            token="[[discord_bot_token]]",
            intents=[
                "guilds",
                "guild_messages",
                "direct_messages",
                "message_content",
            ],
            include_direct_messages=True,
            allowed_message_types=["DEFAULT", "REPLY"],
            bot_identity_key="discord:primary",
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
        "send_discord",
        MessageDiscordNode(
            name="send_discord",
            token="[[discord_bot_token]]",
            channel_id="{{results.discord_listener.reply_target.channel_id}}",
        ),
    )

    graph.add_edge(START, "discord_listener")
    graph.add_edge("discord_listener", "agent_reply")
    graph.add_edge("agent_reply", "send_discord")
    graph.add_edge("send_discord", END)
    return graph
