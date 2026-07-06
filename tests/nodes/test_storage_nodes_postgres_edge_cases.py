"""Edge-case PostgresNode tests covering description and row handling."""

from __future__ import annotations
from types import SimpleNamespace
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.storage.postgres import PostgresNode
from tests.nodes.storage_node_helpers import DummyConnection, DummyCursor


@pytest.mark.asyncio
async def test_postgres_node_fetch_one_no_description_no_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = DummyCursor(description=None, rows=[], fetchone_result=None)
    connection = DummyConnection(cursor=cursor)

    def connect_stub(_: str) -> DummyConnection:
        return connection

    monkeypatch.setattr("psycopg.connect", connect_stub)

    node = PostgresNode(
        name="pg",
        dsn="postgresql://test",
        query="SELECT count(*) FROM empty",
        fetch="one",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["pg"]
    assert payload == {"rows": [], "rowcount": connection.cursor_instance.rowcount}


@pytest.mark.asyncio
async def test_postgres_node_fetch_one_no_description_with_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = DummyCursor(description=None, rows=[(42,)], fetchone_result=(42,))
    connection = DummyConnection(cursor=cursor)

    def connect_stub(_: str) -> DummyConnection:
        return connection

    monkeypatch.setattr("psycopg.connect", connect_stub)

    node = PostgresNode(
        name="pg",
        dsn="postgresql://test",
        query="SELECT count(*) FROM table",
        fetch="one",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["pg"]
    assert payload["rows"] == [(42,)]
    assert payload["rowcount"] == connection.cursor_instance.rowcount


@pytest.mark.asyncio
async def test_postgres_node_fetch_all_no_description_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = DummyCursor(description=None, rows=[])
    connection = DummyConnection(cursor=cursor)

    def connect_stub(_: str) -> DummyConnection:
        return connection

    monkeypatch.setattr("psycopg.connect", connect_stub)

    node = PostgresNode(
        name="pg",
        dsn="postgresql://test",
        query="SELECT * FROM empty_table",
        fetch="all",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["pg"]
    assert payload["rows"] == []
    assert payload["rowcount"] == connection.cursor_instance.rowcount


@pytest.mark.asyncio
async def test_postgres_node_fetch_one_with_description_no_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = DummyCursor(
        description=[SimpleNamespace(name="id"), SimpleNamespace(name="name")],
        fetchone_result=None,
    )
    connection = DummyConnection(cursor=cursor)

    def connect_stub(_: str) -> DummyConnection:
        return connection

    monkeypatch.setattr("psycopg.connect", connect_stub)

    node = PostgresNode(
        name="pg",
        dsn="postgresql://test",
        query="SELECT id, name FROM empty_table WHERE id = 999",
        fetch="one",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["pg"]
    assert payload["rows"] == []
    assert payload["rowcount"] == connection.cursor_instance.rowcount


@pytest.mark.asyncio
async def test_postgres_node_column_count_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = DummyCursor(
        description=[SimpleNamespace(name="id"), SimpleNamespace(name="name")],
        rows=[(1, "Ada", "extra"), (2, "Grace", "data")],
    )
    connection = DummyConnection(cursor=cursor)

    def connect_stub(_: str) -> DummyConnection:
        return connection

    monkeypatch.setattr("psycopg.connect", connect_stub)

    node = PostgresNode(
        name="pg",
        dsn="postgresql://test",
        query="SELECT * FROM malformed_table",
        fetch="all",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["pg"]
    assert payload["rows"] == [(1, "Ada", "extra"), (2, "Grace", "data")]
    assert payload["rowcount"] == cursor.rowcount
