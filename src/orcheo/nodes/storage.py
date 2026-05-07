"""Storage nodes providing database access and graph store operations."""

from __future__ import annotations
import asyncio
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, cast
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _rows_to_dicts(
    columns: Sequence[str], rows: Sequence[Sequence[Any]]
) -> list[dict[str, Any]]:
    """Convert database rows into dictionaries keyed by column name."""
    return [dict(zip(columns, row, strict=False)) for row in rows]


def get_graph_store(config: RunnableConfig | None) -> Any | None:
    """Return the LangGraph runtime store from a :class:`RunnableConfig`.

    Resolution order:

    1. ``config["configurable"]["__pregel_runtime"].store`` (Mapping or attribute)
    2. ``config["configurable"]["__pregel_store"]`` (direct fallback)

    Returns ``None`` when no store is available.
    """
    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        return None

    runtime = configurable.get("__pregel_runtime")
    if runtime is not None:
        if isinstance(runtime, Mapping):
            maybe_store = runtime.get("store")
            if maybe_store is not None:
                return maybe_store
        maybe_store = getattr(runtime, "store", None)
        if maybe_store is not None:
            return maybe_store

    return configurable.get("__pregel_store")


@registry.register(
    NodeMetadata(
        name="PostgresNode",
        description="Execute SQL against a PostgreSQL database using psycopg.",
        category="storage",
    )
)
class PostgresNode(TaskNode):
    """Node encapsulating basic PostgreSQL interactions."""

    dsn: str = Field(
        default="[[postgres_dsn]]",
        description="PostgreSQL DSN, e.g. postgresql://user:pass@host/db",
    )
    query: str = Field(description="SQL query to execute")
    parameters: Mapping[str, Any] | Sequence[Any] | None = Field(
        default=None, description="Parameters bound to the SQL query"
    )
    fetch: Literal["none", "one", "all"] = Field(
        default="all", description="Fetch strategy for returning result rows"
    )
    autocommit: bool = Field(
        default=True, description="Enable autocommit mode for the connection"
    )

    def _execute(self) -> dict[str, Any]:
        """Execute the configured query returning structured results."""
        import psycopg

        with psycopg.connect(self.dsn) as connection:
            connection.autocommit = self.autocommit
            with connection.cursor() as cursor:
                cursor.execute(self.query, self.parameters)

                if self.fetch == "none":
                    return {"rows": [], "rowcount": cursor.rowcount}

                row = cursor.fetchone() if self.fetch == "one" else cursor.fetchall()
                if cursor.description is None:
                    if self.fetch == "one":
                        if row is None:
                            raw_rows: list[Sequence[Any]] = []
                        else:
                            raw_rows = [cast(Sequence[Any], row)]
                    else:
                        raw_rows = list(cast(Sequence[Sequence[Any]], row))
                    return {"rows": raw_rows, "rowcount": cursor.rowcount}

                columns = [column.name for column in cursor.description]
                if self.fetch == "one":
                    if row is None:
                        data_rows: list[Sequence[Any]] = []
                    else:
                        data_rows = [cast(Sequence[Any], row)]
                else:
                    data_rows = list(cast(Sequence[Sequence[Any]], row))

                if data_rows:
                    first_row = data_rows[0]
                    if len(columns) != len(first_row):
                        return {"rows": data_rows, "rowcount": cursor.rowcount}

                mapped_rows = _rows_to_dicts(columns, data_rows)
                return {"rows": mapped_rows, "rowcount": cursor.rowcount}

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Execute the SQL query asynchronously."""
        return await asyncio.to_thread(self._execute)


@registry.register(
    NodeMetadata(
        name="SQLiteNode",
        description="Execute SQL statements against a SQLite database.",
        category="storage",
    )
)
class SQLiteNode(TaskNode):
    """Node providing simple SQLite access suitable for local workflows."""

    database: str = Field(default=":memory:", description="SQLite database path")
    query: str = Field(description="SQL query to execute")
    parameters: Mapping[str, Any] | Sequence[Any] | None = Field(
        default=None, description="Parameters bound to the SQL query"
    )
    fetch: Literal["none", "one", "all"] = Field(
        default="all", description="Fetch strategy for returning result rows"
    )

    def _execute(self) -> dict[str, Any]:
        """Execute the SQL query returning structured results."""
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            cursor = connection.execute(self.query, self.parameters or [])
            connection.commit()

            if self.fetch == "none":
                return {"rows": [], "rowcount": cursor.rowcount}

            if self.fetch == "one":
                row = cursor.fetchone()
                if row is None:
                    return {"rows": [], "rowcount": cursor.rowcount}
                return {
                    "rows": [dict(row)],
                    "rowcount": cursor.rowcount,
                }

            rows = cursor.fetchall()
            return {
                "rows": [dict(item) for item in rows],
                "rowcount": cursor.rowcount,
            }
        finally:
            connection.close()

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Execute the SQLite query asynchronously."""
        return await asyncio.to_thread(self._execute)


@registry.register(
    NodeMetadata(
        name="GraphStoreAppendMessageNode",
        description=(
            "Append a message to a versioned chat history item in the "
            "LangGraph graph store."
        ),
        category="storage",
    )
)
class GraphStoreAppendMessageNode(TaskNode):
    """Append a single message to a versioned graph-store history item.

    All string fields support ``{{template}}`` resolution via Orcheo's
    ``resolved_for_run`` mechanism.  Example usage::

        GraphStoreAppendMessageNode(
            name="persist_history",
            key="telegram:{{for_each_subscriber.current_item.chat_id}}",
            content="{{format_digest.content}}",
        )
    """

    namespace: list[str] = Field(
        default_factory=lambda: ["agent_chat_history"],
        description="Store namespace (converted to tuple internally).",
    )
    key: str = Field(
        description=(
            "Store key identifying the conversation slot. "
            "Supports {{template}} resolution."
        ),
    )
    role: str = Field(
        default="assistant",
        description="Message role, e.g. 'assistant' or 'user'.",
    )
    content: str = Field(
        description="Message content to append. Supports {{template}} resolution.",
    )

    def _namespace_tuple(self, workspace_id: str | None = None) -> tuple[str, ...]:
        """Convert the namespace list to a tuple, filtering blanks."""
        ns = tuple(
            entry.strip()
            for entry in self.namespace
            if isinstance(entry, str) and entry.strip()
        )
        namespace = ns or ("agent_chat_history",)
        if workspace_id is None:
            return namespace
        return (workspace_id, *namespace)

    @staticmethod
    def _extract_payload(item: Any) -> dict[str, Any]:
        """Extract a mutable payload dict from a store item.

        Handles three representations:

        1. ``None`` → fresh payload
        2. Object with ``.value`` attribute (LangGraph ``Item``)
        3. ``Mapping`` with ``"value"`` key (serialised item)
        """
        if item is None:
            return {"version": 0, "messages": []}

        value = getattr(item, "value", None)
        if value is None and isinstance(item, Mapping):
            value = item.get("value")

        if isinstance(value, dict):
            payload = dict(value)
            version = payload.get("version", 0)
            payload["version"] = version if isinstance(version, int | float) else 0

            messages = payload.get("messages", [])
            payload["messages"] = messages if isinstance(messages, list) else []

            return payload

        return {"version": 0, "messages": []}

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Append the message to the graph store and return write status."""
        store = get_graph_store(config)
        if store is None:
            logger.debug(
                "GraphStoreAppendMessageNode '%s': no graph store available.",
                self.name,
            )
            return {"history_written": False}

        key_raw = self.key
        if key_raw is None:
            key = ""
        elif isinstance(key_raw, str):
            key = key_raw.strip()
        elif isinstance(key_raw, int | float | bool):
            key = str(key_raw).strip()
        else:
            key = ""

        if not key or "{{" in key or "}}" in key:
            logger.warning(
                "GraphStoreAppendMessageNode '%s': invalid key after resolution: %r",
                self.name,
                key_raw,
            )
            return {"history_written": False}

        if not self.content:
            logger.warning(
                "GraphStoreAppendMessageNode '%s': empty content after resolution.",
                self.name,
            )
            return {"history_written": False}

        workspace_id = state.get("workspace_id") if isinstance(state, Mapping) else None
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            workspace_id = None
        else:
            workspace_id = workspace_id.strip()

        namespace = self._namespace_tuple(workspace_id)

        try:
            item = await store.aget(namespace, key)
        except Exception:
            logger.warning(
                "GraphStoreAppendMessageNode '%s': failed to read store "
                "(namespace=%s, key='%s').",
                self.name,
                namespace,
                key,
                exc_info=True,
            )
            return {"history_written": False}

        payload = self._extract_payload(item)
        payload["messages"].append({"role": self.role, "content": self.content})
        payload["version"] = payload.get("version", 0) + 1

        try:
            await store.aput(namespace, key, payload)
        except Exception:
            logger.warning(
                "GraphStoreAppendMessageNode '%s': failed to write store "
                "(namespace=%s, key='%s').",
                self.name,
                namespace,
                key,
                exc_info=True,
            )
            return {"history_written": False}

        return {"history_written": True}


__all__ = [
    "get_graph_store",
    "GraphStoreAppendMessageNode",
    "PostgresNode",
    "SQLiteNode",
]
