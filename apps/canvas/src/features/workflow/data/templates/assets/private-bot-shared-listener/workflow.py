from langgraph.graph import END, START, StateGraph
from orcheo.edges import SwitchCase, SwitchEdge
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.connectors.discord import (
    DiscordBotListenerNode,
    MessageDiscordNode,
)
from orcheo.nodes.connectors.qq import MessageQQNode, QQBotListenerNode
from orcheo.nodes.connectors.telegram import (
    MessageTelegramNode,
    TelegramBotListenerNode,
)


def orcheo_workflow() -> StateGraph:
    """Build a shared private-listener workflow for Telegram, Discord, and QQ."""
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
        "qq_listener",
        QQBotListenerNode(
            name="qq_listener",
            app_id="[[qq_app_id]]",
            client_secret="[[qq_client_secret]]",
            allowed_events=[
                "C2C_MESSAGE_CREATE",
                "GROUP_AT_MESSAGE_CREATE",
                "AT_MESSAGE_CREATE",
            ],
            allowed_scene_types=["c2c", "group", "channel"],
            bot_identity_key="qq:primary",
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
    graph.add_node(
        "send_discord",
        MessageDiscordNode(
            name="send_discord",
            token="[[discord_bot_token]]",
            channel_id="{{results.discord_listener.reply_target.channel_id}}",
        ),
    )
    graph.add_node(
        "send_qq",
        MessageQQNode(
            name="send_qq",
            app_id="[[qq_app_id]]",
            client_secret="[[qq_client_secret]]",
            openid="{{results.qq_listener.reply_target.openid}}",
            group_openid="{{results.qq_listener.reply_target.group_openid}}",
            channel_id="{{results.qq_listener.reply_target.channel_id}}",
            guild_id="{{results.qq_listener.reply_target.guild_id}}",
            msg_id="{{results.qq_listener.reply_target.msg_id}}",
        ),
    )

    graph.add_edge(START, "telegram_listener")
    graph.add_edge(START, "discord_listener")
    graph.add_edge(START, "qq_listener")
    graph.add_edge("telegram_listener", "agent_reply")
    graph.add_edge("discord_listener", "agent_reply")
    graph.add_edge("qq_listener", "agent_reply")
    graph.add_conditional_edges(
        "agent_reply",
        SwitchEdge(
            name="reply_route",
            value="{{inputs.platform}}",
            cases=[
                SwitchCase(match="telegram", branch_key="telegram"),
                SwitchCase(match="discord", branch_key="discord"),
                SwitchCase(match="qq", branch_key="qq"),
            ],
            default_branch_key="telegram",
        ),
        {
            "telegram": "send_telegram",
            "discord": "send_discord",
            "qq": "send_qq",
        },
    )
    graph.add_edge("send_telegram", END)
    graph.add_edge("send_discord", END)
    graph.add_edge("send_qq", END)
    return graph
