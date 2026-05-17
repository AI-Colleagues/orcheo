"""MongoDB Atlas Search nodes."""

from __future__ import annotations
import json
from collections.abc import Mapping
from typing import Any, Literal
from langchain_core.runnables import RunnableConfig
from pydantic import Field, field_validator, model_validator
from pymongo.command_cursor import CommandCursor
from orcheo.graph.state import State
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.nodes.storage.mongodb.base import MongoDBClientNode


IndexMode = Literal["ensure", "ensure_or_update", "force_rebuild"]


def _default_index_name(collection: str, suffix: str) -> str:
    return f"orcheo_{collection}_{suffix}"


def _normalize_for_compare(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _extract_index_definition(index: Mapping[str, Any]) -> dict[str, Any] | None:
    definition = index.get("latestDefinition")
    if isinstance(definition, Mapping):
        return dict(definition)
    definition = index.get("definition")
    if isinstance(definition, Mapping):
        return dict(definition)
    return None


def _definitions_match(
    existing_definition: Mapping[str, Any] | None,
    desired_definition: Mapping[str, Any],
) -> bool:
    if existing_definition is None:
        return False
    return _normalize_for_compare(existing_definition) == _normalize_for_compare(
        desired_definition
    )


def _resolve_index_payload(
    definition: Mapping[str, Any],
    index_name: str | None,
) -> tuple[str, dict[str, Any]]:
    name: str | None = None
    if "definition" in definition and isinstance(definition["definition"], Mapping):
        nested_definition = dict(definition["definition"])
        raw_name = definition.get("name")
        if isinstance(raw_name, str) and raw_name:
            name = raw_name
    else:
        nested_definition = dict(definition)

    if index_name:
        name = index_name

    if not name:
        msg = "index_name is required to manage search indexes"
        raise ValueError(msg)

    return name, nested_definition


@registry.register(
    NodeMetadata(
        name="MongoDBEnsureSearchIndexNode",
        description="Ensure a MongoDB Atlas Search index exists.",
        category="mongodb",
    )
)
class MongoDBEnsureSearchIndexNode(MongoDBClientNode):
    """Ensure a MongoDB Atlas Search index is present."""

    index_name: str | None = Field(
        default=None,
        description="Name of the search index (defaults to orcheo_{collection}_fts).",
    )
    definition: dict[str, Any] = Field(
        description="Atlas Search index definition (mappings, analyzers, etc.)."
    )
    mode: IndexMode = Field(
        default="ensure",
        description="ensure, ensure_or_update, or force_rebuild",
    )

    @model_validator(mode="after")
    def _validate_definition(self) -> MongoDBEnsureSearchIndexNode:
        if not self.definition:
            msg = "definition is required for search index management"
            raise ValueError(msg)
        return self

    def _resolve_index_definition(self) -> tuple[str, dict[str, Any]]:
        if self.index_name:
            return _resolve_index_payload(self.definition, self.index_name)
        try:
            return _resolve_index_payload(self.definition, None)
        except ValueError:
            return _resolve_index_payload(
                self.definition, _default_index_name(self.collection, "fts")
            )

    def _list_search_indexes(self) -> list[dict[str, Any]]:
        assert self._collection is not None
        cursor = self._collection.list_search_indexes()
        if isinstance(cursor, CommandCursor):
            return [dict(item) for item in cursor]
        return [dict(item) for item in cursor]

    def _find_index(
        self, indexes: list[dict[str, Any]], name: str
    ) -> dict[str, Any] | None:
        for index in indexes:
            if index.get("name") == name:
                return index
        return None

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Ensure the search index exists or is updated."""
        self._ensure_collection()
        assert self._collection is not None
        collection = self._collection

        index_name, definition = self._resolve_index_definition()
        context = (
            f"search index={index_name}, "
            f"database={self.database}, "
            f"collection={self.collection}"
        )

        indexes = self._execute_operation(
            context=f"list_search_indexes ({context})",
            operation=self._list_search_indexes,
        )
        existing = self._find_index(indexes, index_name)

        if self.mode == "force_rebuild":
            if existing is not None:
                self._execute_operation(
                    context=f"drop_search_index ({context})",
                    operation=lambda: collection.drop_search_index(index_name),
                )
            self._execute_operation(
                context=f"create_search_index ({context})",
                operation=lambda: collection.create_search_index(
                    {"name": index_name, "definition": definition}
                ),
            )
            return {"status": "created", "index_name": index_name}

        if existing is None:
            self._execute_operation(
                context=f"create_search_index ({context})",
                operation=lambda: collection.create_search_index(
                    {"name": index_name, "definition": definition}
                ),
            )
            return {"status": "created", "index_name": index_name}

        if self.mode == "ensure":
            return {"status": "skipped", "index_name": index_name}

        existing_definition = _extract_index_definition(existing)
        if not _definitions_match(existing_definition, definition):
            self._execute_operation(
                context=f"update_search_index ({context})",
                operation=lambda: collection.update_search_index(
                    index_name, definition
                ),
            )
            return {"status": "updated", "index_name": index_name}

        return {"status": "skipped", "index_name": index_name}


@registry.register(
    NodeMetadata(
        name="MongoDBEnsureVectorIndexNode",
        description="Ensure a MongoDB Atlas vector search index exists.",
        category="mongodb",
    )
)
class MongoDBEnsureVectorIndexNode(MongoDBClientNode):
    """Ensure a MongoDB Atlas Search vector index is present."""

    index_name: str | None = Field(
        default=None,
        description="Name of the vector index (defaults to orcheo_{collection}_vec).",
    )
    definition: dict[str, Any] | None = Field(
        default=None,
        description="Optional full index definition to use as-is.",
    )
    dimensions: int | str | None = Field(
        default=None,
        description="Vector dimensions required when definition is omitted.",
    )
    similarity: Literal["cosine", "dotProduct", "euclidean"] | None = Field(
        default=None,
        description="Vector similarity metric used when definition is omitted.",
    )
    path: str = Field(
        default="embedding",
        description="Document field containing vectors when definition is omitted.",
    )
    mode: IndexMode = Field(
        default="ensure",
        description="ensure, ensure_or_update, or force_rebuild",
    )

    @model_validator(mode="after")
    def _validate_definition(self) -> MongoDBEnsureVectorIndexNode:
        if self.definition is None and self.dimensions is None:
            msg = "dimensions is required when definition is not provided"
            raise ValueError(msg)
        if self.definition is None and self.similarity is None:
            msg = "similarity is required when definition is not provided"
            raise ValueError(msg)
        return self

    @field_validator("dimensions", mode="before")
    @classmethod
    def _validate_dimensions(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            if "{{" in value and "}}" in value:
                return value
            try:
                value = int(value)
            except ValueError as exc:
                msg = "dimensions must be an integer"
                raise ValueError(msg) from exc
        if isinstance(value, int) and value <= 0:
            msg = "dimensions must be > 0"
            raise ValueError(msg)
        return value

    def _build_default_definition(self) -> dict[str, Any]:
        if self.dimensions is None or self.similarity is None:
            msg = "definition, dimensions, and similarity must be provided"
            raise ValueError(msg)
        if isinstance(self.dimensions, str):
            msg = "dimensions must resolve to an integer before execution"
            raise ValueError(msg)
        return {
            "mappings": {
                "dynamic": False,
                "fields": {
                    self.path: {
                        "type": "vector",
                        "numDimensions": self.dimensions,
                        "similarity": self.similarity,
                    }
                },
            }
        }

    def _resolve_index_definition(self) -> tuple[str, dict[str, Any]]:
        definition = self.definition or self._build_default_definition()
        if self.index_name:
            return _resolve_index_payload(definition, self.index_name)
        try:
            return _resolve_index_payload(definition, None)
        except ValueError:
            return _resolve_index_payload(
                definition, _default_index_name(self.collection, "vec")
            )

    def _list_search_indexes(self) -> list[dict[str, Any]]:
        assert self._collection is not None
        cursor = self._collection.list_search_indexes()
        if isinstance(cursor, CommandCursor):
            return [dict(item) for item in cursor]
        return [dict(item) for item in cursor]

    def _find_index(
        self, indexes: list[dict[str, Any]], name: str
    ) -> dict[str, Any] | None:
        for index in indexes:
            if index.get("name") == name:
                return index
        return None

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Ensure the vector search index exists or is updated."""
        self._ensure_collection()
        assert self._collection is not None
        collection = self._collection

        index_name, definition = self._resolve_index_definition()
        context = (
            f"vector index={index_name}, "
            f"database={self.database}, "
            f"collection={self.collection}"
        )

        indexes = self._execute_operation(
            context=f"list_search_indexes ({context})",
            operation=self._list_search_indexes,
        )
        existing = self._find_index(indexes, index_name)

        if self.mode == "force_rebuild":
            if existing is not None:
                self._execute_operation(
                    context=f"drop_search_index ({context})",
                    operation=lambda: collection.drop_search_index(index_name),
                )
            self._execute_operation(
                context=f"create_search_index ({context})",
                operation=lambda: collection.create_search_index(
                    {"name": index_name, "definition": definition}
                ),
            )
            return {"status": "created", "index_name": index_name}

        if existing is None:
            self._execute_operation(
                context=f"create_search_index ({context})",
                operation=lambda: collection.create_search_index(
                    {"name": index_name, "definition": definition}
                ),
            )
            return {"status": "created", "index_name": index_name}

        if self.mode == "ensure":
            return {"status": "skipped", "index_name": index_name}

        existing_definition = _extract_index_definition(existing)
        if not _definitions_match(existing_definition, definition):
            self._execute_operation(
                context=f"update_search_index ({context})",
                operation=lambda: collection.update_search_index(
                    index_name, definition
                ),
            )
            return {"status": "updated", "index_name": index_name}

        return {"status": "skipped", "index_name": index_name}


@registry.register(
    NodeMetadata(
        name="MongoDBHybridSearchNode",
        description=(
            "Execute a hybrid search over text and vector indexes asynchronously."
        ),
        category="mongodb",
    )
)
class MongoDBHybridSearchNode(MongoDBClientNode):
    """Run hybrid search with reciprocal rank fusion."""

    text_query: str | None = Field(
        default=None, description="Full-text query string for Atlas Search."
    )
    vector: list[float] | str | None = Field(
        default=None,
        description=(
            "Query embedding vector for vector search. "
            "Template strings resolve at runtime."
        ),
    )
    text_paths: list[str] | str = Field(
        min_length=1,
        description=(
            "Document fields to query for full-text search. "
            "Template strings resolve at runtime."
        ),
    )
    vector_path: str = Field(
        default="embedding", description="Document field containing vectors."
    )
    text_index_name: str | None = Field(
        default=None,
        description="Atlas Search index name for text queries.",
    )
    vector_index_name: str | None = Field(
        default=None,
        description="Atlas Search index name for vector queries.",
    )
    top_k: int = Field(default=10, gt=0, description="Number of results to return.")
    num_candidates: int = Field(
        default=100, gt=0, description="Number of candidates for vector search."
    )
    rrf_k: int = Field(default=60, gt=0, description="Reciprocal rank fusion constant.")
    filter: dict[str, Any] | None = Field(
        default=None, description="Optional MongoDB filter applied post-search."
    )

    @field_validator("vector", mode="before")
    @classmethod
    def _allow_template_vector(cls, value: Any) -> Any:
        if isinstance(value, str) and "{{" in value and "}}" in value:
            return value
        return value

    @field_validator("text_paths", mode="before")
    @classmethod
    def _allow_template_text_paths(cls, value: Any) -> Any:
        if isinstance(value, str) and "{{" in value and "}}" in value:
            return value
        return value

    def _resolve_text_index_name(self) -> str:
        return self.text_index_name or _default_index_name(self.collection, "fts")

    def _resolve_vector_index_name(self) -> str:
        return self.vector_index_name or _default_index_name(self.collection, "vec")

    def _build_text_pipeline(self, *, include_rrf: bool) -> list[dict[str, Any]]:
        if self.text_query is None:
            return []
        if isinstance(self.text_paths, str):
            msg = "text_paths must resolve to a list before execution"
            raise ValueError(msg)
        pipeline: list[dict[str, Any]] = [
            {
                "$search": {
                    "index": self._resolve_text_index_name(),
                    "text": {"query": self.text_query, "path": self.text_paths},
                }
            }
        ]
        if self.filter:
            pipeline.append({"$match": self.filter})
        pipeline.append({"$limit": self.top_k})
        if include_rrf:
            pipeline.append({"$addFields": {"_score": {"$meta": "searchScore"}}})
            pipeline.append(
                {
                    "$setWindowFields": {
                        "sortBy": {"_score": -1},
                        "output": {"_rank": {"$rank": {}}},
                    }
                }
            )
            pipeline.append(
                {
                    "$addFields": {
                        "rrf_score": {
                            "$divide": [1.0, {"$add": [self.rrf_k, "$_rank"]}]
                        }
                    }
                }
            )
        else:
            pipeline.append({"$addFields": {"score": {"$meta": "searchScore"}}})
        return pipeline

    def _build_vector_pipeline(self, *, include_rrf: bool) -> list[dict[str, Any]]:
        if self.vector is None:
            return []
        if isinstance(self.vector, str):
            msg = "vector must resolve to a list before execution"
            raise ValueError(msg)
        pipeline: list[dict[str, Any]] = [
            {
                "$vectorSearch": {
                    "index": self._resolve_vector_index_name(),
                    "path": self.vector_path,
                    "queryVector": self.vector,
                    "numCandidates": self.num_candidates,
                    "limit": self.top_k,
                }
            }
        ]
        if self.filter:
            pipeline.append({"$match": self.filter})
        if include_rrf:
            pipeline.append({"$addFields": {"_score": {"$meta": "vectorSearchScore"}}})
            pipeline.append(
                {
                    "$setWindowFields": {
                        "sortBy": {"_score": -1},
                        "output": {"_rank": {"$rank": {}}},
                    }
                }
            )
            pipeline.append(
                {
                    "$addFields": {
                        "rrf_score": {
                            "$divide": [1.0, {"$add": [self.rrf_k, "$_rank"]}]
                        }
                    }
                }
            )
        else:
            pipeline.append({"$addFields": {"score": {"$meta": "vectorSearchScore"}}})
        return pipeline

    def _build_pipeline(self) -> list[dict[str, Any]]:
        if self.text_query is None and self.vector is None:
            msg = "MongoDBHybridSearchNode requires text_query or vector inputs"
            raise ValueError(msg)

        if self.text_query is not None and self.vector is not None:
            text_pipeline = self._build_text_pipeline(include_rrf=True)
            vector_pipeline = self._build_vector_pipeline(include_rrf=True)
            return [
                *text_pipeline,
                {
                    "$unionWith": {
                        "coll": self.collection,
                        "pipeline": vector_pipeline,
                    }
                },
                {
                    "$group": {
                        "_id": "$_id",
                        "score": {"$sum": "$rrf_score"},
                        "raw": {"$first": "$$ROOT"},
                    }
                },
                {"$sort": {"score": -1}},
                {"$limit": self.top_k},
            ]

        if self.text_query is not None:
            pipeline = self._build_text_pipeline(include_rrf=False)
            pipeline.append({"$sort": {"score": -1}})
            pipeline.append({"$limit": self.top_k})
            return pipeline

        pipeline = self._build_vector_pipeline(include_rrf=False)
        pipeline.append({"$sort": {"score": -1}})
        pipeline.append({"$limit": self.top_k})
        return pipeline

    def _normalize_results(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in items:
            raw_doc: Any
            if "raw" in item and "score" in item:
                raw = item.get("raw")
                score = item.get("score")
                if isinstance(raw, dict):
                    raw_doc = dict(raw)
                    doc_id = item.get("_id") or raw_doc.get("_id")
                else:
                    raw_doc = raw
                    doc_id = item.get("_id")
            else:
                raw_doc = dict(item)
                score = raw_doc.pop("score", None)
                if score is None:
                    score = raw_doc.pop("_score", None)
                doc_id = raw_doc.get("_id")

            normalized.append(
                {
                    "id": str(doc_id) if doc_id is not None else "",
                    "score": float(score) if score is not None else 0.0,
                    "raw": self._encode_bson(raw_doc),
                }
            )
        return normalized

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Execute the hybrid search pipeline asynchronously and normalize results."""
        self._ensure_async_collection()
        assert self._async_collection is not None
        collection = self._async_collection

        pipeline = self._build_pipeline()
        context = (
            f"hybrid search (async), database={self.database}, "
            f"collection={self.collection}"
        )

        async def _operation() -> list[dict[str, Any]]:
            cursor = collection.aggregate(pipeline)
            return await cursor.to_list(length=self.top_k)

        results = await self._execute_async_operation(
            context=context, operation=_operation
        )
        return {"results": self._normalize_results(results)}


__all__ = [
    "MongoDBEnsureSearchIndexNode",
    "MongoDBEnsureVectorIndexNode",
    "MongoDBHybridSearchNode",
]
