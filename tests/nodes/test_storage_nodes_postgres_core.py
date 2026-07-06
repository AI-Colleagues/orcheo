"""Core PostgresNode tests covering typical query flows."""

from __future__ import annotations
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.storage.postgres import PostgresNode
from tests.nodes.storage_node_helpers import DummyConnection, DummyCursor


@pytest.mark.asyncio
async def test_postgres_node_fetches_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dummy_connection = DummyConnection()

    def connect_stub(dsn: str) -> DummyConnection:
        assert dsn == "postgresql://test"
        return dummy_connection

    monkeypatch.setattr("psycopg.connect", connect_stub)

    node = PostgresNode(
        name="pg",
        dsn="postgresql://test",
        query="SELECT id, name FROM people",
        fetch="all",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["pg"]

    assert payload["rows"] == [
        {"id": 1, "name": "Ada"},
        {"id": 2, "name": "Grace"},
    ]
    assert payload["rowcount"] == 1
    assert dummy_connection.cursor_instance.executed == (
        "SELECT id, name FROM people",
        None,
    )


@pytest.mark.asyncio
async def test_postgres_node_fetch_one(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_connection = DummyConnection()

    def connect_stub(_: str) -> DummyConnection:
        return dummy_connection

    monkeypatch.setattr("psycopg.connect", connect_stub)

    node = PostgresNode(
        name="pg",
        dsn="postgresql://test",
        query="SELECT id, name FROM people",
        fetch="one",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["pg"]
    assert payload["rows"] == [{"id": 1, "name": "Ada"}]


@pytest.mark.asyncio
async def test_postgres_node_fetch_none(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = DummyConnection()

    def connect_stub(_: str) -> DummyConnection:
        return connection

    monkeypatch.setattr("psycopg.connect", connect_stub)

    node = PostgresNode(
        name="pg",
        dsn="postgresql://test",
        query="UPDATE table SET value=1",
        fetch="none",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["pg"]
    assert payload == {"rows": [], "rowcount": connection.cursor_instance.rowcount}


@pytest.mark.asyncio
async def test_postgres_node_handles_missing_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_rows = [(1,), (2,)]
    cursor = DummyCursor(description=None, rows=test_rows)
    connection = DummyConnection(cursor=cursor)

    def connect_stub(_: str) -> DummyConnection:
        return connection

    monkeypatch.setattr("psycopg.connect", connect_stub)

    node = PostgresNode(
        name="pg",
        dsn="postgresql://test",
        query="SELECT count(*) FROM table",
        fetch="all",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["pg"]
    assert payload["rows"] == [(1,), (2,)]


@pytest.mark.asyncio
async def test_postgres_node_fetch_one_no_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = DummyCursor(fetchone_result=None)
    connection = DummyConnection(cursor=cursor)

    def connect_stub(_: str) -> DummyConnection:
        return connection

    monkeypatch.setattr("psycopg.connect", connect_stub)

    node = PostgresNode(
        name="pg",
        dsn="postgresql://test",
        query="SELECT id FROM empty",
        fetch="one",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["pg"]
    assert payload == {"rows": [], "rowcount": connection.cursor_instance.rowcount}
