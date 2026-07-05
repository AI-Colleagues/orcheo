"""MongoDB nodes and shared client management."""

from __future__ import annotations
import atexit
import base64
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, time
from decimal import Decimal
from threading import Lock
from typing import Any, ClassVar, Literal
from uuid import UUID
from bson import ObjectId
from bson.binary import Binary
from bson.decimal128 import Decimal128
from bson.regex import Regex
from bson.timestamp import Timestamp
from langchain_core.runnables import RunnableConfig
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pydantic import Field, PrivateAttr, field_validator
from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.command_cursor import CommandCursor
from pymongo.cursor import Cursor
from pymongo.errors import (
    AutoReconnect,
    ConfigurationError,
    ConnectionFailure,
    NetworkTimeout,
    OperationFailure,
    PyMongoError,
    ServerSelectionTimeoutError,
)
from pymongo.results import (
    BulkWriteResult,
    DeleteResult,
    InsertManyResult,
    InsertOneResult,
    UpdateResult,
)
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


class MongoDBClientNode(TaskNode):
    """Base node with shared MongoDB client handling."""

    connection_string: str = "[[mdb_connection_string]]"
    """Connection string for MongoDB."""
    database: str
    """The database to use."""
    collection: str
    """The collection to use."""

    _client_cache: ClassVar[dict[str, MongoClient]] = {}
    _client_ref_counts: ClassVar[dict[str, int]] = {}
    _client_lock: ClassVar[Lock] = Lock()
    _client: MongoClient | None = PrivateAttr(default=None)
    _collection: Collection | None = PrivateAttr(default=None)
    _client_key: str | None = PrivateAttr(default=None)
    _async_client_cache: ClassVar[dict[str, AsyncIOMotorClient]] = {}
    _async_client_ref_counts: ClassVar[dict[str, int]] = {}
    # Lock protects class-level async client cache mutations during setup/teardown.
    _async_client_lock: ClassVar[Lock] = Lock()
    _async_client: AsyncIOMotorClient | None = PrivateAttr(default=None)
    _async_collection: AsyncIOMotorCollection[Any] | None = PrivateAttr(default=None)
    _async_client_key: str | None = PrivateAttr(default=None)

    @classmethod
    def _encode_special_bson_value(cls, value: Any) -> tuple[bool, Any]:
        """Return encoded scalar BSON-like values when special handling is needed."""
        handled = True
        encoded: Any
        if isinstance(value, ObjectId):
            encoded = str(value)
        elif isinstance(value, (datetime, date, time)):
            encoded = value.isoformat()
        elif isinstance(value, Decimal128):
            encoded = str(value.to_decimal())
        elif isinstance(value, Decimal):
            encoded = str(value)
        elif isinstance(value, Timestamp):
            encoded = {"time": value.time, "inc": value.inc}
        elif isinstance(value, Regex):
            encoded = {"pattern": value.pattern, "flags": value.flags}
        elif isinstance(value, UUID):
            encoded = str(value)
        elif isinstance(value, (Binary, bytes, bytearray, memoryview)):
            encoded = base64.b64encode(bytes(value)).decode("ascii")
        else:
            handled = False
            encoded = value
        return handled, encoded

    @classmethod
    def _encode_bson(cls, value: Any) -> Any:
        handled, encoded = cls._encode_special_bson_value(value)
        if handled:
            return encoded
        if isinstance(value, Mapping):
            return {key: cls._encode_bson(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._encode_bson(item) for item in value]
        return value

    @classmethod
    def _get_shared_client(cls, connection_string: str) -> MongoClient:
        with cls._client_lock:
            client = cls._client_cache.get(connection_string)
            if client is None:
                client = MongoClient(connection_string)
                cls._client_cache[connection_string] = client
                cls._client_ref_counts[connection_string] = 0
            cls._client_ref_counts[connection_string] = (
                cls._client_ref_counts.get(connection_string, 0) + 1
            )
        return client

    @classmethod
    def _release_shared_client(cls, connection_string: str) -> None:
        client: MongoClient | None = None
        with cls._client_lock:
            ref_count = cls._client_ref_counts.get(connection_string)
            if ref_count is None:
                return
            ref_count -= 1
            if ref_count <= 0:
                cls._client_ref_counts.pop(connection_string, None)
                client = cls._client_cache.pop(connection_string, None)
            else:
                cls._client_ref_counts[connection_string] = ref_count
        if client is not None:
            client.close()

    @classmethod
    def _close_all_clients(cls) -> None:
        with cls._client_lock:
            clients = list(cls._client_cache.values())
            cls._client_cache.clear()
            cls._client_ref_counts.clear()
        for client in clients:
            client.close()

    @classmethod
    def _get_shared_async_client(cls, connection_string: str) -> AsyncIOMotorClient:
        with cls._async_client_lock:
            client = cls._async_client_cache.get(connection_string)
            if client is None:
                client = AsyncIOMotorClient(connection_string)
                cls._async_client_cache[connection_string] = client
                cls._async_client_ref_counts[connection_string] = 0
            cls._async_client_ref_counts[connection_string] = (
                cls._async_client_ref_counts.get(connection_string, 0) + 1
            )
        return client

    @classmethod
    def _release_shared_async_client(cls, connection_string: str) -> None:
        client: AsyncIOMotorClient | None = None
        with cls._async_client_lock:
            ref_count = cls._async_client_ref_counts.get(connection_string)
            if ref_count is None:
                return
            ref_count -= 1
            if ref_count <= 0:
                cls._async_client_ref_counts.pop(connection_string, None)
                client = cls._async_client_cache.pop(connection_string, None)
            else:
                cls._async_client_ref_counts[connection_string] = ref_count
        if client is not None:
            client.close()

    @classmethod
    def _close_all_async_clients(cls) -> None:
        with cls._async_client_lock:
            clients = list(cls._async_client_cache.values())
            cls._async_client_cache.clear()
            cls._async_client_ref_counts.clear()
        for client in clients:
            client.close()

    def _release_client(self) -> None:
        if self._client is None or self._client_key is None:
            return
        type(self)._release_shared_client(self._client_key)
        self._client = None
        self._collection = None
        self._client_key = None

    def _release_async_client(self) -> None:
        if self._async_client is None or self._async_client_key is None:
            return
        type(self)._release_shared_async_client(self._async_client_key)
        self._async_client = None
        self._async_collection = None
        self._async_client_key = None

    def _ensure_collection(self) -> None:
        """Ensure the MongoDB collection is initialised."""
        if self._client is None:
            self._client = self._get_shared_client(self.connection_string)
            self._client_key = self.connection_string
        elif self._client_key and self._client_key != self.connection_string:
            self._release_client()
            self._client = self._get_shared_client(self.connection_string)
            self._client_key = self.connection_string
        if self._client is None:
            msg = "MongoDB client could not be initialized"
            raise RuntimeError(msg)
        self._collection = self._client[self.database][self.collection]

    def _ensure_async_collection(self) -> None:
        """Ensure the async MongoDB collection is initialised."""
        if self._async_client is None:
            self._async_client = self._get_shared_async_client(self.connection_string)
            self._async_client_key = self.connection_string
        elif (
            self._async_client_key and self._async_client_key != self.connection_string
        ):
            self._release_async_client()
            self._async_client = self._get_shared_async_client(self.connection_string)
            self._async_client_key = self.connection_string
        self._async_collection = self._async_client[self.database][self.collection]

    def _execute_operation(
        self,
        *,
        context: str,
        operation: Callable[[], Any],
    ) -> Any:
        try:
            return operation()
        except (
            AutoReconnect,
            ConnectionFailure,
            NetworkTimeout,
            ServerSelectionTimeoutError,
        ) as exc:
            msg = f"MongoDB network error during {context}."
            raise RuntimeError(msg) from exc
        except OperationFailure as exc:
            auth_error_codes = {13, 18}
            if exc.code in auth_error_codes:
                msg = f"MongoDB authentication/authorization error during {context}."
            else:
                msg = f"MongoDB operation error during {context}."
            raise RuntimeError(msg) from exc
        except ConfigurationError as exc:
            msg = f"MongoDB configuration error during {context}."
            raise RuntimeError(msg) from exc
        except PyMongoError as exc:
            msg = f"MongoDB error during {context}."
            raise RuntimeError(msg) from exc

    async def _execute_async_operation(
        self,
        *,
        context: str,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        try:
            return await operation()
        except (
            AutoReconnect,
            ConnectionFailure,
            NetworkTimeout,
            ServerSelectionTimeoutError,
        ) as exc:
            msg = f"MongoDB network error during {context}."
            raise RuntimeError(msg) from exc
        except OperationFailure as exc:
            auth_error_codes = {13, 18}
            if exc.code in auth_error_codes:
                msg = f"MongoDB authentication/authorization error during {context}."
            else:
                msg = f"MongoDB operation error during {context}."
            raise RuntimeError(msg) from exc
        except ConfigurationError as exc:
            msg = f"MongoDB configuration error during {context}."
            raise RuntimeError(msg) from exc
        except PyMongoError as exc:
            msg = f"MongoDB error during {context}."
            raise RuntimeError(msg) from exc

    def __del__(self) -> None:
        """Automatic cleanup when object is garbage collected."""
        self._release_async_client()
        self._release_client()


@registry.register(
    NodeMetadata(
        name="MongoDBNode",
        description="MongoDB node",
        category="mongodb",
    )
)
class MongoDBNode(MongoDBClientNode):
    """MongoDB node.

    To use this node, you need to set the following parameters:
    - connection_string: Required.
    """

    operation: Literal[
        "find",
        "find_one",
        "find_raw_batches",
        "insert_one",
        "insert_many",
        "update_one",
        "update_many",
        "replace_one",
        "delete_one",
        "delete_many",
        "aggregate",
        "aggregate_raw_batches",
        "count_documents",
        "estimated_document_count",
        "distinct",
        "find_one_and_delete",
        "find_one_and_replace",
        "find_one_and_update",
        "bulk_write",
        "create_index",
        "create_indexes",
        "drop_index",
        "drop_indexes",
        "list_indexes",
        "index_information",
        "create_search_index",
        "create_search_indexes",
        "drop_search_index",
        "update_search_index",
        "list_search_indexes",
        "drop",
        "rename",
        "options",
        "watch",
    ]
    query: dict | list[dict[str, Any]] = Field(default_factory=dict)
    """Generic payload passed directly to single-argument operations."""
    filter: dict[str, Any] | str | None = Field(
        default=None, description="Filter document for query/update operations"
    )
    update: dict[str, Any] | str | None = Field(
        default=None, description="Update document for update operations"
    )
    pipeline: list[dict[str, Any]] | None = Field(
        default=None, description="Aggregation pipeline for aggregate operations"
    )
    sort: dict[str, int] | list[tuple[str, int]] | None = Field(
        default=None, description="Sort specification for find operations"
    )
    projection: dict[str, Any] | list[str] | str | None = Field(
        default=None,
        description="Projection specification for read operations",
    )
    limit: int | str | None = Field(
        default=None, description="Limit for find operations"
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional pymongo options passed to the operation",
    )

    @field_validator("limit", mode="before")
    @classmethod
    def _validate_limit(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            if cls.contains_template(value):
                return value
            try:
                value = int(value)
            except ValueError as exc:
                msg = "limit must be an integer"
                raise ValueError(msg) from exc
        if isinstance(value, int) and value < 0:
            msg = "limit must be >= 0"
            raise ValueError(msg)
        return value

    def _resolve_filter(self) -> dict[str, Any]:
        """Resolve a filter document from structured inputs."""
        if self.filter is not None:
            if isinstance(self.filter, str):
                msg = "filter must resolve to a dict before execution"
                raise ValueError(msg)
            return dict(self.filter)
        return {}

    def _resolve_update(self) -> dict[str, Any]:
        """Resolve an update/replacement document from inputs."""
        if self.update is not None:
            if isinstance(self.update, str):
                msg = "update must resolve to a dict before execution"
                raise ValueError(msg)
            return dict(self.update)
        msg = "update is required for update operations"
        raise ValueError(msg)

    def _resolve_pipeline(self) -> list[dict[str, Any]]:
        """Resolve an aggregation pipeline from inputs."""
        if self.pipeline is not None:
            return list(self.pipeline)
        if isinstance(self.query, list):
            return list(self.query)
        msg = "pipeline is required for aggregate operations"
        raise ValueError(msg)

    def _normalize_sort(
        self, value: dict[str, int] | list[tuple[str, int]]
    ) -> list[tuple[str, int]]:
        """Normalize sort specifications into list-of-tuples."""
        if isinstance(value, dict):
            return list(value.items())
        return list(value)

    def _resolve_projection(self) -> dict[str, Any] | list[str]:
        """Resolve a projection document or field list from inputs."""
        projection = self.projection
        if isinstance(projection, str):
            msg = "projection must resolve to a dict or list before execution"
            raise ValueError(msg)
        if projection is None:
            msg = "projection is not set for this operation"
            raise ValueError(msg)
        if isinstance(projection, list):
            return list(projection)
        return dict(projection)

    def _build_operation_call(self) -> tuple[list[Any], dict[str, Any]]:
        """Return positional args and kwargs for the configured operation."""
        filter_operations = {
            "find",
            "find_one",
            "find_raw_batches",
            "count_documents",
            "delete_one",
            "delete_many",
            "find_one_and_delete",
        }
        find_operations = {"find", "find_one", "find_raw_batches"}
        update_operations = {
            "update_one",
            "update_many",
            "find_one_and_update",
            "replace_one",
            "find_one_and_replace",
        }
        pipeline_operations = {"aggregate", "aggregate_raw_batches"}
        projection_operations = {
            "find",
            "find_one",
            "find_raw_batches",
            "find_one_and_delete",
            "find_one_and_replace",
            "find_one_and_update",
        }

        if self.operation in pipeline_operations:
            pipeline = self._coerce_object_ids(self._resolve_pipeline())
            return [pipeline], dict(self.options)

        if self.operation in update_operations:
            filter_doc = self._coerce_object_ids(self._resolve_filter())
            update_doc = self._coerce_object_ids(self._resolve_update())
            kwargs = dict(self.options)
            if self.operation in projection_operations and self.projection is not None:
                kwargs["projection"] = self._resolve_projection()
            return [filter_doc, update_doc], kwargs

        if self.operation in filter_operations:
            filter_doc = self._coerce_object_ids(self._resolve_filter())
            kwargs = dict(self.options)
            if self.operation in projection_operations and self.projection is not None:
                kwargs["projection"] = self._resolve_projection()
            if self.operation in find_operations:  # pragma: no branch
                if self.sort is not None:
                    kwargs["sort"] = self._normalize_sort(self.sort)
                if self.limit is not None:
                    kwargs["limit"] = self._resolve_limit()
            return [filter_doc], kwargs

        return [self.query], dict(self.options)

    def _resolve_limit(self) -> int:
        limit = self.limit
        if isinstance(limit, str):
            if self.contains_template(limit):
                msg = "limit must resolve to an integer before execution"
                raise ValueError(msg)
            try:
                return int(limit)
            except ValueError as exc:
                msg = "limit must be an integer"
                raise ValueError(msg) from exc
        if limit is None:
            msg = "limit is not set for find operations"
            raise ValueError(msg)
        return limit

    @classmethod
    def _coerce_object_id_value(cls, value: Any) -> Any:
        if isinstance(value, str) and ObjectId.is_valid(value):
            return ObjectId(value)
        if isinstance(value, list):
            return [cls._coerce_object_id_value(item) for item in value]
        if isinstance(value, dict):
            return {
                key: cls._coerce_object_id_value(item) for key, item in value.items()
            }
        return value

    @classmethod
    def _coerce_object_ids(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (
                    cls._coerce_object_id_value(item)
                    if key == "_id"
                    else cls._coerce_object_ids(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._coerce_object_ids(item) for item in value]
        return value

    def _convert_result_to_dict(self, result: Any) -> dict | list[dict]:
        """Convert MongoDB operation result to dict or list[dict] format."""
        converted_result: dict | list[dict]

        match result:
            case Cursor() | CommandCursor():
                converted_result = [dict(doc) for doc in result]

            case None | int() | float() | str() | bool():
                converted_result = {"result": result}

            case list():
                converted_result = [
                    {"value": item} if not isinstance(item, dict) else dict(item)
                    for item in result
                ]

            case InsertOneResult():
                converted_result = {
                    "operation": "insert_one",
                    "inserted_id": str(result.inserted_id),
                    "acknowledged": result.acknowledged,
                }

            case InsertManyResult():
                converted_result = {
                    "operation": "insert_many",
                    "inserted_ids": [str(id_) for id_ in result.inserted_ids],
                    "acknowledged": result.acknowledged,
                }

            case UpdateResult():
                converted_result = {
                    "operation": "update",
                    "matched_count": result.matched_count,
                    "modified_count": result.modified_count,
                    "upserted_id": str(result.upserted_id)
                    if result.upserted_id
                    else None,
                    "acknowledged": result.acknowledged,
                }

            case DeleteResult():
                converted_result = {
                    "operation": "delete",
                    "deleted_count": result.deleted_count,
                    "acknowledged": result.acknowledged,
                }

            case BulkWriteResult():
                converted_result = {
                    "operation": "bulk_write",
                    "inserted_count": result.inserted_count,
                    "matched_count": result.matched_count,
                    "modified_count": result.modified_count,
                    "deleted_count": result.deleted_count,
                    "upserted_count": result.upserted_count,
                    "upserted_ids": {
                        str(k): str(v) for k, v in (result.upserted_ids or {}).items()
                    },
                    "acknowledged": result.acknowledged,
                }

            case _ if hasattr(result, "__dict__"):
                converted_result = dict(result.__dict__)

            case _:
                converted_result = {"result": str(result)}

        return self._encode_bson(converted_result)

    async def run(self, state: State, config: RunnableConfig) -> dict:
        """Run the MongoDB node with persistent session."""
        context = (
            f"operation={self.operation}, "
            f"database={self.database}, "
            f"collection={self.collection}"
        )

        self._ensure_collection()
        assert self._collection is not None

        def _operation() -> Any:
            operation = getattr(self._collection, self.operation)
            args, kwargs = self._build_operation_call()
            return operation(*args, **kwargs)

        result = self._execute_operation(context=context, operation=_operation)
        return {"data": self._convert_result_to_dict(result)}


@registry.register(
    NodeMetadata(
        name="MongoDBAggregateNode",
        description="MongoDB aggregate wrapper",
        category="mongodb",
    )
)
class MongoDBAggregateNode(MongoDBNode):
    """MongoDB aggregate wrapper with a required pipeline."""

    operation: Literal["aggregate"] = "aggregate"
    pipeline: list[dict[str, Any]]


@registry.register(
    NodeMetadata(
        name="MongoDBFindNode",
        description="MongoDB find wrapper with sort and limit support",
        category="mongodb",
    )
)
class MongoDBFindNode(MongoDBNode):
    """MongoDB find wrapper with filter, sort, and limit fields."""

    operation: Literal["find"] = "find"
    filter: dict[str, Any] = Field(default_factory=dict)


@registry.register(
    NodeMetadata(
        name="MongoDBUpdateManyNode",
        description="MongoDB update_many wrapper",
        category="mongodb",
    )
)
class MongoDBUpdateManyNode(MongoDBNode):
    """MongoDB update_many wrapper with filter and update documents."""

    operation: Literal["update_many"] = "update_many"
    filter: dict[str, Any]
    update: dict[str, Any]

    def _resolve_filter(self) -> dict[str, Any]:
        if self.filter is None:
            msg = "filter is required for update_many operations"
            raise ValueError(msg)
        return dict(self.filter)

    def _resolve_update(self) -> dict[str, Any]:
        if self.update is None:
            msg = "update is required for update_many operations"
            raise ValueError(msg)
        if not self.update:
            msg = "update must not be empty for update_many operations"
            raise ValueError(msg)
        return dict(self.update)


@registry.register(
    NodeMetadata(
        name="MongoDBInsertManyNode",
        description="Insert documents into MongoDB with optional vectors",
        category="mongodb",
    )
)
class MongoDBInsertManyNode(MongoDBClientNode):
    """Insert upstream records into a MongoDB collection.

    Resolves records from workflow state produced by an upstream node.
    Each record is transformed into a MongoDB document with configurable
    field mapping. When ``vector_field`` is provided, vector data from
    each record is included in the document.

    Args:
        source_result_key: Upstream result entry containing records.
        embeddings_field: Field within the upstream result storing records.
        embedding_name: Key within the embeddings mapping to read records
            from.  When ``None``, reads a list directly from
            ``embeddings_field``.
        vector_field: MongoDB document field for the embedding vector.
            When ``None``, vector data is omitted.
        text_field: MongoDB document field for the chunk text.
        include_metadata: Whether to include record metadata in documents.
        metadata_field: If set, nest metadata under this key instead of
            flattening it into the top-level document.
    """

    source_result_key: str = Field(
        description="Upstream result entry containing records to insert.",
    )
    embeddings_field: str = Field(
        default="chunk_embeddings",
        description="Field within the upstream result storing vector records.",
    )
    embedding_name: str | None = Field(
        default=None,
        description=(
            "Key within the embeddings mapping to read records from. "
            "When None, records are read directly from the embeddings_field."
        ),
    )
    vector_field: str | None = Field(
        default=None,
        description=(
            "MongoDB document field name for the embedding vector. "
            "When None, vector data is omitted from documents."
        ),
    )
    text_field: str = Field(
        default="text",
        description="MongoDB document field name for the record text.",
    )
    include_metadata: bool = Field(
        default=True,
        description="Whether to include record metadata in each document.",
    )
    metadata_field: str | None = Field(
        default=None,
        description=(
            "If set, nest metadata under this key. "
            "Otherwise flatten metadata into the top-level document."
        ),
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Read records from state and insert into MongoDB."""
        records = self._resolve_records(state)
        if not records:
            msg = "No records available to insert"
            raise ValueError(msg)

        documents = [self._record_to_document(record) for record in records]

        self._ensure_collection()
        assert self._collection is not None
        collection = self._collection

        result = self._execute_operation(
            context=f"insert_many into {self.database}.{self.collection}",
            operation=lambda: collection.insert_many(documents),
        )

        return {
            "inserted_count": len(result.inserted_ids),
            "inserted_ids": [str(id_) for id_ in result.inserted_ids],
        }

    def _resolve_records(self, state: State) -> list[dict[str, Any]]:
        """Extract records from workflow state."""
        results = state.get("results", {})
        if not isinstance(results, Mapping):
            return []
        source = results.get(self.source_result_key)
        if not isinstance(source, Mapping):
            return []
        payload = source.get(self.embeddings_field)
        if self.embedding_name is not None:
            if not isinstance(payload, Mapping):
                return []
            records = payload.get(self.embedding_name)
        else:
            records = payload
        if not isinstance(records, list):
            return []
        return records

    def _record_to_document(self, record: dict[str, Any]) -> dict[str, Any]:
        """Convert a record dict into a MongoDB document."""
        document: dict[str, Any] = {
            self.text_field: record.get("text", ""),
        }
        if self.vector_field is not None:
            document[self.vector_field] = record.get("values", [])
        if self.include_metadata:
            metadata = record.get("metadata", {})
            if self.metadata_field is not None:
                document[self.metadata_field] = metadata
            else:
                document.update(metadata)
        return document


@registry.register(
    NodeMetadata(
        name="MongoDBUpsertManyNode",
        description="Bulk-upsert upstream records into MongoDB using keyed filters",
        category="mongodb",
    )
)
class MongoDBUpsertManyNode(MongoDBNode):
    """Bulk-upsert upstream records into a MongoDB collection.

    Records are resolved from workflow state and converted into ``UpdateOne``
    operations executed via ``bulk_write``. Each record contributes a filter
    derived from ``filter_fields`` and an update document composed from
    ``set_fields`` or the remaining top-level fields.
    """

    operation: Literal["bulk_write"] = "bulk_write"
    source_result_key: str = Field(
        description="Upstream result entry containing records to upsert.",
    )
    records_field: str = Field(
        default="documents",
        description=(
            "Field within the upstream result storing the list of records. "
            "When the upstream result is already a list, this field is ignored."
        ),
    )
    filter_fields: list[str] = Field(
        default_factory=list,
        description="Record fields copied into the upsert filter document.",
    )
    set_fields: list[str] | None = Field(
        default=None,
        description=(
            "Optional allowlist of record fields written via $set. "
            "When omitted, all top-level fields except filter/excluded fields "
            "are written."
        ),
    )
    exclude_fields: list[str] = Field(
        default_factory=list,
        description="Record fields excluded from the generated $set payload.",
    )
    set_on_insert: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional values written only when a new record is inserted.",
    )
    upsert: bool = Field(
        default=True,
        description="Whether to insert a document when no existing match is found.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Read records from state and bulk-upsert them into MongoDB."""
        del config
        records = self._resolve_records(state)
        if not records:
            msg = "No records available to upsert"
            raise ValueError(msg)
        if not self.filter_fields:
            msg = "filter_fields must contain at least one field"
            raise ValueError(msg)

        operations = [
            self._build_update_one(record=record, index=index)
            for index, record in enumerate(records)
        ]

        self._ensure_collection()
        assert self._collection is not None
        collection = self._collection

        result = self._execute_operation(
            context=f"bulk_upsert into {self.database}.{self.collection}",
            operation=lambda: collection.bulk_write(operations, **dict(self.options)),
        )

        return {"data": self._convert_result_to_dict(result)}

    def _resolve_records(self, state: State) -> list[dict[str, Any]]:
        """Extract the list of records to upsert from workflow state."""
        results = state.get("results", {})
        if not isinstance(results, Mapping):
            return []

        source = results.get(self.source_result_key)
        if isinstance(source, list):
            return [dict(record) for record in source if isinstance(record, Mapping)]
        if not isinstance(source, Mapping):
            return []

        records = source.get(self.records_field)
        if not isinstance(records, list):
            return []

        normalized_records: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, Mapping):
                return []
            normalized_records.append(dict(record))
        return normalized_records

    def _build_update_one(self, *, record: dict[str, Any], index: int) -> UpdateOne:
        """Return the ``UpdateOne`` operation for a single record."""
        filter_doc = self._build_filter(record=record, index=index)
        update_doc = self._build_update(record)
        return UpdateOne(
            self._coerce_object_ids(filter_doc),
            self._coerce_object_ids(update_doc),
            upsert=self.upsert,
        )

    def _build_filter(self, *, record: dict[str, Any], index: int) -> dict[str, Any]:
        """Build the MongoDB filter document for a single record."""
        filter_doc: dict[str, Any] = {}
        for field in self.filter_fields:
            if field not in record:
                msg = f"Record at index {index} is missing filter field {field!r}"
                raise ValueError(msg)
            filter_doc[field] = record[field]
        return filter_doc

    def _build_update(self, record: dict[str, Any]) -> dict[str, Any]:
        """Build the MongoDB update document for a single record."""
        excluded = set(self.exclude_fields)
        if self.set_fields is None:
            set_payload = {
                key: value for key, value in record.items() if key not in excluded
            }
        else:
            set_payload = {
                key: record[key]
                for key in self.set_fields
                if key in record and key not in self.exclude_fields
            }

        update_doc: dict[str, Any] = {}
        if set_payload:
            update_doc["$set"] = set_payload
        if self.set_on_insert:
            update_doc["$setOnInsert"] = dict(self.set_on_insert)
        if not update_doc:
            msg = "Generated update document must not be empty"
            raise ValueError(msg)
        return update_doc


__all__ = [
    "MongoDBAggregateNode",
    "MongoDBFindNode",
    "MongoDBInsertManyNode",
    "MongoDBNode",
    "MongoDBUpsertManyNode",
    "MongoDBUpdateManyNode",
]

atexit.register(MongoDBNode._close_all_clients)
atexit.register(MongoDBNode._close_all_async_clients)
