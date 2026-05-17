"""Smoke tests for refactored node package entrypoints."""

from __future__ import annotations


def test_ai_submodule_imports() -> None:
    from orcheo.nodes.ai.llm import LLMNode
    from orcheo.nodes.ai.workflow_tools import WorkflowTool
    from orcheo.nodes.ai.tools import context, registry, tools

    assert LLMNode.__name__ == "LLMNode"
    assert WorkflowTool.__name__ == "WorkflowTool"
    assert hasattr(context, "tool_execution_context")
    assert hasattr(registry, "tool_registry")
    assert hasattr(tools, "greet_user")


def test_connector_wrapper_imports() -> None:
    from orcheo.nodes.connectors.http_request import HttpRequestNode
    from orcheo.nodes.connectors.listener_base import ListenerNode
    from orcheo.nodes.connectors import (
        DiscordBotListenerNode,
        DiscordWebhookNode,
        EmailNode,
        HttpRequestNode,
        LarkSendMessageNode,
        LarkTenantAccessTokenNode,
        LinkedInPostNode,
        ListenerNode,
        MessageDiscord,
        MessageDiscordNode,
        MessageQQ,
        MessageQQNode,
        MessageTelegram,
        MessageTelegramNode,
        QQBotListenerNode,
        RSSNode,
        SlackEventsParserNode,
        SlackNode,
        TelegramBotListenerNode,
        TelegramEventsParserNode,
    )
    from orcheo.nodes.connectors.wecom import (
        WeComAIBotEventsParserNode,
        WeComAIBotPassiveReplyNode,
        WeComAIBotResponseNode,
        WeComAccessTokenNode,
        WeComCustomerServiceSendNode,
        WeComCustomerServiceSyncNode,
        WeComEventsParserNode,
        WeComGroupPushNode,
        WeComSendMessageNode,
    )

    assert ListenerNode.__name__ == "ListenerNode"
    assert HttpRequestNode.__name__ == "HttpRequestNode"
    assert DiscordWebhookNode.__name__ == "DiscordWebhookNode"
    assert MessageTelegramNode.__name__ == "MessageTelegramNode"
    assert TelegramEventsParserNode.__name__ == "TelegramEventsParserNode"
    assert WeComSendMessageNode.__name__ == "WeComSendMessageNode"
    assert WeComAIBotResponseNode.__name__ == "WeComAIBotResponseNode"


def test_logic_wrapper_imports() -> None:
    from orcheo.nodes.logic import DebugNode, SubWorkflowNode

    assert DebugNode.__name__ == "DebugNode"
    assert SubWorkflowNode.__name__ == "SubWorkflowNode"


def test_storage_wrapper_imports() -> None:
    from orcheo.nodes.storage.graph_store import GraphStoreAppendMessageNode
    from orcheo.nodes.storage.postgres import PostgresNode
    from orcheo.nodes.storage.mongodb import (
        MongoDBAggregateNode,
        MongoDBEnsureSearchIndexNode,
        MongoDBEnsureVectorIndexNode,
        MongoDBFindNode,
        MongoDBHybridSearchNode,
        MongoDBInsertManyNode,
        MongoDBNode,
        MongoDBUpdateManyNode,
        MongoDBUpsertManyNode,
    )

    assert PostgresNode.__name__ == "PostgresNode"
    assert GraphStoreAppendMessageNode.__name__ == "GraphStoreAppendMessageNode"
    assert MongoDBNode.__name__ == "MongoDBNode"
    assert MongoDBEnsureVectorIndexNode.__name__ == "MongoDBEnsureVectorIndexNode"
    assert MongoDBHybridSearchNode.__name__ == "MongoDBHybridSearchNode"
