"""Vector store abstractions used by conversational search nodes."""

from __future__ import annotations
import inspect
import math
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any
from pydantic import BaseModel, ConfigDict, Field
from orcheo.nodes.rag.models import SearchResult, VectorRecord


if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from orcheo.nodes.rag.ingestion import EmbeddingVector


class BaseVectorStore(ABC, BaseModel):
    """Abstract interface for vector store adapters."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @abstractmethod
    async def upsert(self, records: Iterable[VectorRecord]) -> None:
        """Persist ``records`` into the backing vector store."""

    @abstractmethod
    async def search(
        self,
        query: EmbeddingVector | list[float],
        top_k: int = 10,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Return the top matching records for ``query``."""


class InMemoryVectorStore(BaseVectorStore):
    """Simple in-memory vector store useful for testing and local dev."""

    records: dict[str, VectorRecord] = Field(default_factory=dict)

    async def upsert(self, records: Iterable[VectorRecord]) -> None:
        """Store ``records`` in the in-memory dictionary."""
        for record in records:
            self.records[record.id] = record

    async def search(
        self,
        query: EmbeddingVector | list[float],
        top_k: int = 10,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Perform cosine similarity search over in-memory vectors."""
        if not isinstance(query, list):
            if not query.values:
                if query.sparse_values is not None:
                    msg = (
                        "InMemoryVectorStore only supports dense query vectors "
                        "for search"
                    )
                    raise ValueError(msg)
                msg = "dense embeddings must include non-empty float values"
                raise ValueError(msg)
            dense_query = query.values
        else:
            dense_query = query
        candidates = list(self.records.values())
        if filter_metadata:
            candidates = [
                record
                for record in candidates
                if all(
                    record.metadata.get(key) == value
                    for key, value in filter_metadata.items()
                )
            ]

        scored: list[SearchResult] = []
        for record in candidates:
            if not record.values:
                continue
            score = self._cosine_similarity(dense_query, record.values)
            scored.append(
                SearchResult(
                    id=record.id,
                    score=score,
                    text=record.text,
                    metadata=record.metadata,
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]

    def list(self) -> list[VectorRecord]:  # pragma: no cover - helper
        """Return a copy of stored records for inspection."""
        return list(self.records.values())

    @staticmethod
    def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
        left_vector = list(left)
        right_vector = list(right)

        if not left_vector or not right_vector:
            return 0.0
        if len(left_vector) != len(right_vector):
            msg = "Vector dimensions must match for similarity search"
            raise ValueError(msg)

        dot = 0.0
        for left_value, right_value in zip(left_vector, right_vector, strict=True):
            dot += left_value * right_value

        left_norm = math.sqrt(sum(value * value for value in left_vector))
        right_norm = math.sqrt(sum(value * value for value in right_vector))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return dot / (left_norm * right_norm)


class PineconeVectorStore(BaseVectorStore):
    """Lightweight Pinecone adapter that defers client loading until use."""

    index_name: str
    namespace: str | None = None
    client: Any | None = None

    client_kwargs: dict[str, Any] | str = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    async def upsert(self, records: Iterable[VectorRecord]) -> None:
        """Upsert ``records`` into Pinecone with dependency guards."""
        client = self._resolve_client()
        index = self._resolve_index(client)
        payload: list[dict[str, Any]] = []
        for record in records:
            if not record.values and record.sparse_values is None:
                msg = "Vector records must include dense values or sparse_values"
                raise ValueError(msg)
            entry: dict[str, Any] = {
                "id": record.id,
                "metadata": record.metadata | {"text": record.text},
            }
            if record.values:
                entry["values"] = record.values
            if record.sparse_values is not None:
                entry["sparse_values"] = record.sparse_values.model_dump()
            payload.append(entry)
        result = index.upsert(vectors=payload, namespace=self.namespace)
        if inspect.iscoroutine(result):
            await result

    async def search(
        self,
        query: EmbeddingVector | list[float],
        top_k: int = 10,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Query Pinecone for the most similar vectors."""
        client = self._resolve_client()
        index = self._resolve_index(client)
        if isinstance(query, list):
            vector_payload = query
            sparse_payload = None
        else:
            vector_payload = query.values
            sparse_payload = query.sparse_values

        if not vector_payload and sparse_payload is None:
            msg = "query embeddings must include dense or sparse values"
            raise ValueError(msg)

        query_kwargs: dict[str, Any] = {
            "vector": vector_payload or None,
            "sparse_vector": sparse_payload.model_dump() if sparse_payload else None,
            "top_k": top_k,
            "namespace": self.namespace,
            "include_metadata": True,
            "filter": filter_metadata or None,
        }
        result = index.query(**query_kwargs)
        if inspect.iscoroutine(result):
            result = await result

        matches = getattr(result, "matches", None)
        if matches is None and isinstance(result, dict):
            matches = result.get("matches", [])
        matches = matches or []
        normalized: list[SearchResult] = []
        for match in matches:
            metadata = getattr(match, "metadata", None)
            if metadata is None and isinstance(match, dict):
                metadata = match.get("metadata", {})
            metadata = metadata or {}

            score = getattr(match, "score", None)
            if score is None and isinstance(match, dict):
                score = match.get("score", 0.0)

            text = metadata.get("text", "")
            match_id = getattr(match, "id", None)
            if match_id is None and isinstance(match, dict):
                match_id = match.get("id")
            if match_id is None:
                continue

            normalized.append(
                SearchResult(
                    id=match_id,
                    score=float(score) if score is not None else 0.0,
                    text=text,
                    metadata=metadata,
                )
            )
        return normalized

    def _resolve_client(self) -> Any:
        if self.client is not None:
            return self.client
        try:
            from pinecone import Pinecone  # type: ignore
        except ImportError as exc:  # pragma: no cover - dependency guard
            msg = (
                "PineconeVectorStore requires the 'pinecone-client' dependency. "
                "Install it or provide a pre-configured client."
            )
            raise ImportError(msg) from exc
        client_kwargs = (
            self.client_kwargs if isinstance(self.client_kwargs, dict) else {}
        )
        self.client = Pinecone(**client_kwargs)
        return self.client

    def _resolve_index(self, client: Any) -> Any:
        try:
            return client.Index(self.index_name)
        except Exception as exc:  # pragma: no cover - runtime guard
            msg = f"Unable to open Pinecone index '{self.index_name}': {exc!s}"
            raise RuntimeError(msg) from exc
