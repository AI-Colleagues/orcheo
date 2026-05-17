"""Shared fixtures for node tests."""

from __future__ import annotations
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.storage.mongodb import MongoDBNode


@pytest.fixture
def mock_agent():
    """Async agent mock with default assistant response."""

    agent = AsyncMock()
    agent.ainvoke.return_value = {
        "messages": [{"role": "assistant", "content": "test"}]
    }
    return agent


@pytest.fixture
def mock_mcp_client():
    """MCP client mock returning no tools by default."""

    client = AsyncMock()
    client.get_tools.return_value = []
    return client


@pytest.fixture
def agent():
    """AgentNode fixture shared across AI node tests."""

    return AgentNode(
        name="test_agent",
        ai_model="openai:gpt-4o-mini",
        system_prompt="Test prompt",
    )


@dataclass
class MongoTestContext:
    """Encapsulates shared MongoDBNode test helpers."""

    client: MagicMock
    database: MagicMock
    collection: MagicMock
    mongo_client: MagicMock

    def build_node(
        self,
        *,
        operation: str,
        query: dict[str, Any] | list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> MongoDBNode:
        """Create a MongoDBNode with sensible defaults for tests."""

        payload: dict[str, Any] = dict(
            name="test_node",
            database="test_db",
            collection="test_coll",
            operation=operation,
        )
        if query is not None:
            payload["query"] = query
        payload.update(overrides)
        return MongoDBNode(**payload)


@pytest.fixture
def mongo_context() -> Generator[MongoTestContext, None, None]:
    """Provide patched MongoDB components and a node factory."""

    MongoDBNode._client_cache.clear()
    MongoDBNode._client_ref_counts.clear()
    collection = MagicMock()
    database = MagicMock()
    database.__getitem__.return_value = collection
    client = MagicMock()
    client.__getitem__.return_value = database

    with patch("orcheo.nodes.storage.mongodb.base.MongoClient") as mongo_client_cls:
        mongo_client_cls.return_value = client
        yield MongoTestContext(
            client=client,
            database=database,
            collection=collection,
            mongo_client=mongo_client_cls,
        )
