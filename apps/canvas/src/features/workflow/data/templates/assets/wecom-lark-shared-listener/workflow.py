from langgraph.graph import END, START, StateGraph
from orcheo_plugin_lark_listener import LarkListenerPluginNode
from orcheo_plugin_wecom_listener import WeComListenerPluginNode, WeComWsReplyNode
from orcheo.edges import SwitchCase, SwitchEdge
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode, AgentReplyExtractorNode
from orcheo.nodes.lark import LarkSendMessageNode, LarkTenantAccessTokenNode


def orcheo_workflow() -> StateGraph:
    """Build a shared plugin-listener workflow for WeCom and Lark."""
    graph = StateGraph(State)

    graph.add_node(
        "wecom_listener",
        WeComListenerPluginNode(
            name="wecom_listener",
            bot_id="[[wecom_bot_id]]",
            bot_secret="[[wecom_bot_secret]]",
            bot_identity_key="wecom:primary",
        ),
    )
    graph.add_node(
        "lark_listener",
        LarkListenerPluginNode(
            name="lark_listener",
            app_id="[[lark_app_id]]",
            app_secret="[[lark_app_secret]]",
            bot_identity_key="lark:primary",
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
        "extract_reply",
        AgentReplyExtractorNode(
            name="extract_reply",
        ),
    )
    graph.add_node(
        "ws_reply_wecom",
        WeComWsReplyNode(
            name="ws_reply_wecom",
            message="{{results.extract_reply.agent_reply}}",
            raw_event="{{results.wecom_listener.raw_event}}",
            subscription_id="{{inputs.listener.listener_subscription_id}}",
        ),
    )
    graph.add_node(
        "get_lark_tenant_token",
        LarkTenantAccessTokenNode(
            name="get_lark_tenant_token",
            app_id="[[lark_app_id]]",
            app_secret="[[lark_app_secret]]",
        ),
    )
    graph.add_node(
        "send_lark",
        LarkSendMessageNode(
            name="send_lark",
            app_id="[[lark_app_id]]",
            app_secret="[[lark_app_secret]]",
            receive_id="{{results.lark_listener.reply_target.chat_id}}",
            reply_to_message_id="{{results.lark_listener.reply_target.message_id}}",
            thread_id="{{results.lark_listener.reply_target.thread_id}}",
            message="{{results.extract_reply.agent_reply}}",
        ),
    )

    graph.add_conditional_edges(
        START,
        SwitchEdge(
            name="listener_entry_route",
            value="{{inputs.platform}}",
            cases=[
                SwitchCase(match="wecom", branch_key="wecom"),
                SwitchCase(match="lark", branch_key="lark"),
            ],
            default_branch_key="lark",
        ),
        {
            "wecom": "wecom_listener",
            "lark": "lark_listener",
        },
    )
    graph.add_edge("wecom_listener", "agent_reply")
    graph.add_edge("lark_listener", "agent_reply")
    graph.add_edge("agent_reply", "extract_reply")
    graph.add_conditional_edges(
        "extract_reply",
        SwitchEdge(
            name="reply_route",
            value="{{inputs.platform}}",
            cases=[
                SwitchCase(match="wecom", branch_key="wecom"),
                SwitchCase(match="lark", branch_key="lark"),
            ],
            default_branch_key="wecom",
        ),
        {
            "wecom": "ws_reply_wecom",
            "lark": "get_lark_tenant_token",
        },
    )
    graph.add_edge("get_lark_tenant_token", "send_lark")
    graph.add_edge("ws_reply_wecom", END)
    graph.add_edge("send_lark", END)
    return graph
