"""Retrieval nodes for conversational search workflows."""

from __future__ import annotations
import inspect
import math
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any
import httpx
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.data.utils import _extract_value
from orcheo.nodes.rag.models import (
    DocumentChunk,
    SearchResult,
)
from orcheo.nodes.rag.vector_store import (
    BaseVectorStore,
    InMemoryVectorStore,
)
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.runtime.credentials import (
    get_active_credential_resolver,
    parse_credential_reference,
)
from orcheo.tracing.model_metadata import (
    build_ai_trace_metadata,
    infer_model_name_from_instance,
)


if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from orcheo.nodes.rag.ingestion import EmbeddingVector


@registry.register(
    NodeMetadata(
        name="DenseSearchNode",
        description="Perform embedding-based retrieval via a configured vector store.",
        category="conversational_search",
    )
)
class DenseSearchNode(TaskNode):
    """Node that performs dense retrieval against a vector store."""

    query_key: str = Field(
        default="message",
        description="Key within ``state.inputs`` containing the user message string.",
    )
    vector_store: BaseVectorStore = Field(
        default_factory=InMemoryVectorStore,
        description="Vector store adapter that will be queried.",
    )
    embed_model: str = Field(
        ...,
        description=(
            "Dense embedding model identifier, e.g. openai:text-embedding-3-small"
        ),
    )
    model_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional keyword arguments forwarded to init_embeddings.",
    )
    top_k: int | str = Field(
        default=5, description="Maximum number of results to return"
    )
    score_threshold: float | str = Field(
        default=0.0,
        description="Minimum score required for a result to be returned.",
    )
    filter_metadata: dict[str, Any] | str = Field(
        default_factory=dict,
        description="Optional metadata filters applied to the vector store query.",
    )
    source_name: str = Field(
        default="dense",
        description="Label used to annotate the originating retriever.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Embed the query and perform similarity search."""
        inputs = state.get("inputs", {})
        query = (
            inputs.get(self.query_key)
            or inputs.get("message")
            or inputs.get("user_message")
            or inputs.get("query")
        )
        if not isinstance(query, str) or not query.strip():
            msg = "DenseSearchNode requires a non-empty query string"
            raise ValueError(msg)

        top_k = int(self.top_k)
        score_threshold = float(self.score_threshold)

        query_vector = await self._embed_query(query)
        filter_meta = (
            self.filter_metadata if isinstance(self.filter_metadata, dict) else None
        )
        results = await self.vector_store.search(
            query=query_vector,
            top_k=top_k,
            filter_metadata=filter_meta,
        )

        normalized = [
            SearchResult(
                id=result.id,
                score=result.score,
                text=result.text,
                metadata=result.metadata,
                source=result.source or self.source_name,
                sources=result.sources or [result.source or self.source_name],
            )
            for result in results
            if result.score >= score_threshold
        ]

        return {"results": normalized}

    async def _embed_query(self, text: str) -> list[float]:
        from orcheo.nodes.rag.embeddings import (
            init_dense_embeddings,
        )

        model = init_dense_embeddings(self.embed_model, self.model_kwargs)
        embedding = await model.aembed_query(text)
        self._set_trace_metadata_for_run(
            {
                "ai": build_ai_trace_metadata(
                    kind="embedding",
                    requested_model=self.embed_model,
                    actual_model=(
                        infer_model_name_from_instance(model) or self.embed_model
                    ),
                    operation="query",
                    vector_size=len(embedding),
                )
            }
        )
        return embedding


@registry.register(
    NodeMetadata(
        name="SparseSearchNode",
        description="Perform sparse keyword retrieval using BM25 scoring.",
        category="conversational_search",
    )
)
class SparseSearchNode(TaskNode):
    """Node that computes BM25 scores over in-memory chunks."""

    source_result_key: str = Field(
        default="chunking_strategy",
        description="Name of the upstream result containing chunks.",
    )
    chunks_field: str = Field(
        default="chunks", description="Field containing chunk payloads"
    )
    query_key: str = Field(
        default="message", description="Key within inputs holding the user message"
    )
    top_k: int | str = Field(
        default=5, description="Maximum number of results to return"
    )
    score_threshold: float | str = Field(
        default=0.0,
        description="Minimum BM25 score required for inclusion",
    )
    k1: float | str = Field(default=1.5)
    b: float | str = Field(default=0.75)
    source_name: str = Field(
        default="sparse", description="Label for the sparse retriever."
    )
    embed_model: str = Field(
        default="",
        description=(
            "Dense embedding model used when querying a vector store for "
            "candidate chunks. Required when vector_store is set."
        ),
    )
    model_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional keyword arguments forwarded to init_embeddings.",
    )
    sparse_model: str | None = Field(
        default=None,
        description=(
            "Sparse embedding model identifier, e.g. pinecone:bm25. "
            "When set, uses external sparse encoder instead of built-in BM25."
        ),
    )
    sparse_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional sparse-model kwargs passed to sparse initializer.",
    )
    vector_store: BaseVectorStore | None = Field(
        default=None,
        description="Optional vector store containing pre-indexed chunks to query.",
    )
    vector_store_candidate_k: int | str = Field(
        default=50,
        description="Number of candidates to fetch from the vector store.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Score document chunks with BM25 and return the top matches."""
        inputs = state.get("inputs", {})
        query = (
            inputs.get(self.query_key)
            or inputs.get("message")
            or inputs.get("user_message")
            or inputs.get("query")
        )
        if not isinstance(query, str) or not query.strip():
            msg = "SparseSearchNode requires a non-empty query string"
            raise ValueError(msg)

        if self.vector_store is not None:
            chunks = await self._fetch_chunks_from_vector_store(query)
        else:
            chunks = self._resolve_chunks(state)
        if not chunks:
            warning = (
                "SparseSearchNode did not receive any document chunks; "
                "ensure the configured vector store contains indexed data before "
                "running the demo."
            )
            return {"results": [], "warning": warning, "source": self.source_name}

        tokenized_corpus = [self._tokenize(chunk.content) for chunk in chunks]
        avg_length = sum(len(doc) for doc in tokenized_corpus) / len(tokenized_corpus)

        scores: list[tuple[DocumentChunk, float]] = []
        query_tokens = self._tokenize(query)
        for chunk, tokens in zip(chunks, tokenized_corpus, strict=True):
            score = self._bm25_score(tokens, query_tokens, tokenized_corpus, avg_length)
            scores.append((chunk, score))

        ranked = [
            SearchResult(
                id=chunk.id,
                score=score,
                text=chunk.content,
                metadata=chunk.metadata,
                source=self.source_name,
                sources=[self.source_name],
            )
            for chunk, score in sorted(scores, key=lambda item: item[1], reverse=True)
            if score >= float(self.score_threshold)
        ][: int(self.top_k)]

        return {"results": ranked}

    async def _fetch_chunks_from_vector_store(self, query: str) -> list[DocumentChunk]:
        """Retrieve candidate chunks from the vector store using the query embedding."""
        if self.vector_store is None:
            return []

        query_vector = await self._build_vector_store_query(query)
        results = await self.vector_store.search(
            query=query_vector,
            top_k=int(self.vector_store_candidate_k),
        )
        chunks: list[DocumentChunk] = []
        for match in results:
            metadata = dict(match.metadata or {})
            chunk_text = match.text or metadata.get("text", "")
            if not chunk_text:
                continue
            raw_index = metadata.get("chunk_index", 0)
            if isinstance(raw_index, str):
                try:
                    chunk_index = int(raw_index)
                except ValueError:
                    chunk_index = 0
            elif isinstance(raw_index, int | float):
                chunk_index = int(raw_index)
            else:
                chunk_index = 0
            chunk_index = max(0, chunk_index)
            chunk_metadata = metadata.copy()
            document_id_value = metadata.get("document_id")
            document_id = str(document_id_value) if document_id_value else match.id
            chunks.append(
                DocumentChunk(
                    id=match.id,
                    document_id=document_id,
                    index=chunk_index,
                    content=chunk_text,
                    metadata=chunk_metadata,
                )
            )
        return chunks

    async def _build_vector_store_query(
        self, query: str
    ) -> list[float] | EmbeddingVector:
        """Build sparse or dense query payload for candidate retrieval."""
        from orcheo.nodes.rag.embeddings import (
            init_dense_embeddings,
            init_sparse_embeddings,
            sparse_embed_query,
        )

        if self.sparse_model:
            from orcheo.nodes.rag.ingestion import EmbeddingVector

            self._validate_sparse_query_configuration()
            encoder = init_sparse_embeddings(self.sparse_model, self.sparse_kwargs)
            sparse_query = sparse_embed_query(encoder, query)
            self._set_trace_metadata_for_run(
                {
                    "ai": build_ai_trace_metadata(
                        kind="sparse_embedding",
                        requested_model=self.sparse_model,
                        actual_model=self.sparse_model,
                        operation="query",
                    )
                }
            )
            return EmbeddingVector(values=[], sparse_values=sparse_query)

        if not self.embed_model:
            msg = (
                "SparseSearchNode requires embed_model when vector_store is set "
                "and sparse_model is not configured"
            )
            raise ValueError(msg)

        model = init_dense_embeddings(self.embed_model, self.model_kwargs)
        embedding = await model.aembed_query(query)
        self._set_trace_metadata_for_run(
            {
                "ai": build_ai_trace_metadata(
                    kind="embedding",
                    requested_model=self.embed_model,
                    actual_model=(
                        infer_model_name_from_instance(model) or self.embed_model
                    ),
                    operation="query",
                    vector_size=len(embedding),
                )
            }
        )
        return embedding

    def _validate_sparse_query_configuration(self) -> None:
        """Validate sparse query encoder settings for vector-store retrieval."""
        if self.sparse_model == "pinecone:bm25-default":
            return
        if self.sparse_model != "pinecone:bm25":
            return
        if self.sparse_kwargs.get("encoder_state_path"):
            return
        msg = (
            "SparseSearchNode with sparse_model='pinecone:bm25' requires "
            "sparse_kwargs['encoder_state_path'] so query encoding uses a "
            "pre-fitted BM25 encoder. Use sparse_model='pinecone:bm25-default' "
            "to use Pinecone default parameters without local state files."
        )
        raise ValueError(msg)

    def _resolve_chunks(self, state: State) -> list[DocumentChunk]:
        results = state.get("node_results", {})
        source = results.get(self.source_result_key, {})
        if isinstance(source, dict) and self.chunks_field in source:
            chunks = source[self.chunks_field]
        else:
            chunks = results.get(self.chunks_field)
        if not chunks:
            return []
        if not isinstance(chunks, list):
            msg = "chunks payload must be a list"
            raise ValueError(msg)
        return [DocumentChunk.model_validate(chunk) for chunk in chunks]

    def _bm25_score(
        self,
        document_tokens: list[str],
        query_tokens: list[str],
        corpus: list[list[str]],
        avg_length: float,
    ) -> float:
        score = 0.0
        doc_len = len(document_tokens)
        token_freq: dict[str, int] = defaultdict(int)
        for token in document_tokens:
            token_freq[token] += 1

        k1_float = float(self.k1)
        b_float = float(self.b)
        for token in query_tokens:
            idf = self._idf(token, corpus)
            freq = token_freq.get(token, 0)
            numerator = freq * (k1_float + 1)
            denominator = freq + k1_float * (
                1 - b_float + b_float * (doc_len / avg_length)
            )
            if denominator == 0:
                continue
            score += idf * (numerator / denominator)
        return score

    @staticmethod
    def _idf(token: str, corpus: list[list[str]]) -> float:
        doc_count = sum(1 for document in corpus if token in document)
        return math.log(((len(corpus) - doc_count + 0.5) / (doc_count + 0.5)) + 1)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [token for token in text.lower().split() if token]


@registry.register(
    NodeMetadata(
        name="WebSearchNode",
        description="Perform live web search via the Tavily API.",
        category="conversational_search",
    )
)
class WebSearchNode(TaskNode):
    """Node that retrieves fresh web results using Tavily search."""

    query_key: str = Field(
        default="message",
        description="Key within ``state.inputs`` containing the user message string.",
    )
    provider: str = Field(
        default="tavily",
        description=(
            "Web search provider identifier. Currently only 'tavily' is supported."
        ),
    )
    api_key: str | None = Field(
        default=None,
        description=(
            "Tavily API key. Prefer credential placeholders (for example "
            "``[[tavily_api_key]]``) so the value is resolved from vault at runtime."
        ),
    )
    api_url: str = Field(
        default="https://api.tavily.com/search",
        description="Tavily search endpoint URL.",
    )
    search_depth: str = Field(
        default="basic",
        description="Search depth to request from Tavily ('basic' or 'advanced').",
    )
    max_results: int | str = Field(
        default=5, description="Maximum number of web results to request."
    )
    include_answer: bool = Field(
        default=True,
        description="Request Tavily to include its summarized answer.",
    )
    include_raw_content: bool = Field(
        default=False,
        description="Request raw page content from Tavily responses.",
    )
    days: int | None = Field(
        default=None,
        gt=0,
        description="Restrict results to the past N days when provided.",
    )
    topic: str | None = Field(
        default=None, description="Optional topical boost (e.g., 'news')."
    )
    include_domains: list[str] | None = Field(
        default=None,
        description="Limit results to the provided domains.",
    )
    exclude_domains: list[str] | None = Field(
        default=None,
        description="Exclude results from the provided domains.",
    )
    timeout: float | None = Field(
        default=10.0,
        ge=0.0,
        description="Timeout in seconds for the Tavily request.",
    )
    source_name: str = Field(
        default="web",
        description="Label used to annotate Tavily-sourced results.",
    )
    suppress_errors: bool = Field(
        default=True,
        description=(
            "Return empty results instead of raising when Tavily is unavailable."
        ),
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Run Tavily search, optionally suppressing failures."""
        try:
            return await self._run_search(state)
        except ValueError as exc:
            if not self.suppress_errors:
                raise
            return {"results": [], "warning": str(exc), "source": self.source_name}
        except Exception as exc:  # pragma: no cover - network/runtime guard
            if not self.suppress_errors:
                raise
            return {
                "results": [],
                "warning": f"web search unavailable: {exc!s}",
                "source": self.source_name,
            }

    async def _run_search(self, state: State) -> dict[str, Any]:
        """Issue a Tavily search and normalise the response into SearchResult items."""
        if self.provider != "tavily":
            msg = (
                "WebSearchNode only supports the 'tavily' provider "
                f"(received '{self.provider}')"
            )
            raise ValueError(msg)

        inputs = state.get("inputs", {})
        query = (
            inputs.get(self.query_key)
            or inputs.get("message")
            or inputs.get("user_message")
            or inputs.get("query")
        )
        if not isinstance(query, str) or not query.strip():
            msg = "WebSearchNode requires a non-empty query string"
            raise ValueError(msg)

        api_key = self._resolve_api_key()
        if not api_key:
            msg = (
                "WebSearchNode requires api_key to be configured "
                "(for example [[tavily_api_key]])."
            )
            raise ValueError(msg)

        payload = self._build_payload(query.strip(), api_key)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.api_url, json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = f"Web search request failed: {exc!s}"
            raise ValueError(msg) from exc

        try:
            data = response.json()
        except ValueError as exc:
            msg = "WebSearchNode received non-JSON response"
            raise ValueError(msg) from exc

        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            msg = "WebSearchNode expected 'results' list in response"
            raise ValueError(msg)

        results = [
            self._parse_result(entry, index) for index, entry in enumerate(raw_results)
        ]

        return {
            "results": results,
            "answer": data.get("answer"),
            "source": self.source_name,
        }

    def _resolve_api_key(self) -> str:
        """Return resolved API key, expanding credential placeholders when needed."""
        if not isinstance(self.api_key, str):
            return ""
        candidate = self.api_key.strip()
        if not candidate:
            return ""

        reference = parse_credential_reference(candidate)
        if reference is None:
            return candidate

        resolver = get_active_credential_resolver()
        if resolver is None:
            msg = (
                "WebSearchNode api_key uses a credential placeholder but no active "
                "credential resolver is available."
            )
            raise ValueError(msg)
        resolved = resolver.resolve(reference)
        if not isinstance(resolved, str):
            msg = "Resolved WebSearchNode api_key credential must be a string."
            raise ValueError(msg)
        return resolved.strip()

    def _build_payload(self, query: str, api_key: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "api_key": api_key,
            "query": query,
            "search_depth": self.search_depth,
            "max_results": int(self.max_results),
            "include_answer": self.include_answer,
            "include_raw_content": self.include_raw_content,
        }
        if self.days is not None:
            payload["days"] = self.days
        if self.topic is not None:
            payload["topic"] = self.topic
        if self.include_domains:
            payload["include_domains"] = self.include_domains
        if self.exclude_domains:
            payload["exclude_domains"] = self.exclude_domains
        return payload

    def _parse_result(self, entry: dict[str, Any], index: int) -> SearchResult:
        if not isinstance(entry, dict):
            msg = "Each web search result must be a mapping"
            raise ValueError(msg)

        url = str(entry.get("url") or "").strip()
        title = str(entry.get("title") or "").strip()
        content = str(entry.get("content") or "").strip()
        raw_content = entry.get("raw_content")

        text_parts = [part for part in (title, content) if part]
        text = " - ".join(text_parts)
        if not text:
            fallback = raw_content if isinstance(raw_content, str) else None
            text = fallback or url or f"{self.source_name}-{index}"

        metadata: dict[str, Any] = {}
        if url:
            metadata["url"] = url
        if title:
            metadata["title"] = title
        if isinstance(raw_content, str) and raw_content and self.include_raw_content:
            metadata["raw_content"] = raw_content

        return SearchResult(
            id=url or f"{self.source_name}-{index}",
            score=self._coerce_score(entry.get("score")),
            text=text,
            metadata=metadata,
            source=self.source_name,
            sources=[self.source_name],
        )

    @staticmethod
    def _coerce_score(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


@registry.register(
    NodeMetadata(
        name="HybridFusionNode",
        description="Fuse results from multiple retrievers using RRF or weighted sum.",
        category="conversational_search",
    )
)
class HybridFusionNode(TaskNode):
    """Merge retrieval results using Reciprocal Rank Fusion or weighted scores."""

    results_field: str = Field(
        default="retrieval_results",
        description="Key within results containing retriever outputs to fuse.",
    )
    strategy: str = Field(
        default="rrf",
        description="Fusion strategy: either 'rrf' or 'weighted_sum'.",
    )
    weights: dict[str, float] = Field(
        default_factory=dict,
        description="Optional per-retriever weights for weighted_sum fusion.",
    )
    rrf_k: int | str = Field(
        default=60, description="RRF constant to dampen rank impact"
    )
    top_k: int | str = Field(
        default=10, description="Number of fused results to return"
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Fuse retriever outputs according to the configured strategy."""
        results_map = state.get("node_results", {}).get(self.results_field)
        if not isinstance(results_map, dict) or not results_map:
            msg = "HybridFusionNode requires a mapping of retriever results"
            raise ValueError(msg)

        if self.strategy not in {"rrf", "weighted_sum"}:
            msg = "strategy must be either 'rrf' or 'weighted_sum'"
            raise ValueError(msg)

        normalized: dict[str, list[SearchResult]] = {}
        for source, payload in results_map.items():
            if isinstance(payload, dict) and "results" in payload:
                entries = payload["results"]
            else:
                entries = payload
            if not isinstance(entries, list):
                msg = f"Retriever results for {source} must be a list"
                raise ValueError(msg)
            normalized[source] = [SearchResult.model_validate(item) for item in entries]

        fused = (
            self._reciprocal_rank_fusion(normalized)
            if self.strategy == "rrf"
            else self._weighted_sum_fusion(normalized)
        )

        ranked = sorted(fused.values(), key=lambda item: item.score, reverse=True)
        return {"results": ranked[: int(self.top_k)]}

    def _reciprocal_rank_fusion(
        self, results: dict[str, list[SearchResult]]
    ) -> dict[str, SearchResult]:
        fused: dict[str, SearchResult] = {}
        for source, entries in results.items():
            for rank, entry in enumerate(entries, start=1):
                score = 1 / (int(self.rrf_k) + rank)
                fused.setdefault(
                    entry.id,
                    SearchResult(
                        id=entry.id,
                        score=0.0,
                        text=entry.text,
                        metadata=entry.metadata,
                        source="hybrid",
                        sources=[source],
                    ),
                )
                fused_entry = fused[entry.id]
                fused_entry.score += score
                if source not in fused_entry.sources:
                    fused_entry.sources.append(source)
        return fused

    def _weighted_sum_fusion(
        self, results: dict[str, list[SearchResult]]
    ) -> dict[str, SearchResult]:
        fused: dict[str, SearchResult] = {}
        for source, entries in results.items():
            weight = self.weights.get(source, 1.0)
            for entry in entries:
                fused.setdefault(
                    entry.id,
                    SearchResult(
                        id=entry.id,
                        score=0.0,
                        text=entry.text,
                        metadata=entry.metadata,
                        source="hybrid",
                        sources=[source],
                    ),
                )
                fused_entry = fused[entry.id]
                fused_entry.score += weight * entry.score
                if source not in fused_entry.sources:
                    fused_entry.sources.append(source)
        return fused


@registry.register(
    NodeMetadata(
        name="ReRankerNode",
        description="Apply secondary scoring to retrieval results for better ranking.",
        category="conversational_search",
    )
)
class ReRankerNode(TaskNode):
    """Node that reorders search results using a reranking function."""

    source_result_key: str = Field(
        default="retriever", description="Result entry holding retrieval output"
    )
    results_field: str = Field(
        default="results", description="Field containing SearchResult entries"
    )
    rerank_function: Callable[[SearchResult], float] | None = Field(default=None)
    top_k: int | str = Field(default=10)
    length_penalty: float | str = Field(
        default=0.0,
        description="Penalty applied per token to discourage long passages.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Rerank retrieval results using a scoring function."""
        entries = self._resolve_results(state)
        reranked: list[SearchResult] = []
        for entry in entries:
            score = self._score(entry)
            reranked.append(
                SearchResult(
                    id=entry.id,
                    score=score,
                    text=entry.text,
                    metadata=entry.metadata,
                    source=entry.source,
                    sources=entry.sources,
                )
            )
        reranked.sort(key=lambda item: item.score, reverse=True)
        return {"results": reranked[: int(self.top_k)]}

    def _resolve_results(self, state: State) -> list[SearchResult]:
        return _resolve_retrieval_results(
            state,
            self.source_result_key,
            self.results_field,
            error_message="ReRankerNode requires a list of retrieval results",
        )

    def _score(self, entry: SearchResult) -> float:
        base_score = entry.score
        if self.rerank_function:
            base_score = self.rerank_function(entry)
        length_penalty = float(self.length_penalty) * len(entry.text.split())
        return base_score - length_penalty


@registry.register(
    NodeMetadata(
        name="SourceRouterNode",
        description="Route fused results into per-source buckets with filtering.",
        category="conversational_search",
    )
)
class SourceRouterNode(TaskNode):
    """Partition search results into source-specific groupings."""

    source_result_key: str = Field(
        default="retriever", description="Result entry containing retrieval items"
    )
    results_field: str = Field(default="results")
    min_score: float | str = Field(
        default=0.0, description="Minimum score required to retain entries"
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Group results into per-source buckets while filtering by score."""
        entries = self._resolve_results(state)
        routed: dict[str, list[SearchResult]] = {}
        min_score = float(self.min_score)
        for entry in entries:
            source = entry.source or "unknown"
            bucket = routed.setdefault(source, [])
            if entry.score < min_score:
                continue
            bucket.append(entry)
        return {"routed": routed}

    def _resolve_results(self, state: State) -> list[SearchResult]:
        results = state.get("node_results", {})
        payload = results.get(self.source_result_key, {})
        if isinstance(payload, dict) and self.results_field in payload:
            entries = payload[self.results_field]
        else:
            entries = payload
        if not isinstance(entries, list):
            msg = "SourceRouterNode requires a list of retrieval results"
            raise ValueError(msg)
        return [SearchResult.model_validate(item) for item in entries]


@registry.register(
    NodeMetadata(
        name="SearchResultAdapterNode",
        description="Normalize arbitrary retrieval payloads into SearchResult items.",
        category="conversational_search",
    )
)
class SearchResultAdapterNode(TaskNode):
    """Node that maps structured result dictionaries into SearchResult objects."""

    source_result_key: str = Field(
        default="retriever",
        description="Result entry containing the raw retrieval payload.",
    )
    results_field: str = Field(
        default="results",
        description="Field name containing raw result entries.",
    )
    id_field: str = Field(
        default="id",
        description="Dotted path to the identifier field in each result entry.",
    )
    score_field: str = Field(
        default="score",
        description="Dotted path to the score field in each result entry.",
    )
    text_field: str = Field(
        default="text",
        description="Dotted path to the text field in each result entry.",
    )
    metadata_field: str | None = Field(
        default="metadata",
        description="Dotted path to metadata mapping stored in each result entry.",
    )
    raw_field: str | None = Field(
        default=None,
        description="Fallback dotted path to raw payload metadata.",
    )
    source_field: str = Field(
        default="source",
        description="Dotted path to the source label in each result entry.",
    )
    sources_field: str = Field(
        default="sources",
        description="Dotted path to the list of source labels in each result entry.",
    )
    source_name: str | None = Field(
        default=None,
        description="Optional source label to apply to all normalized results.",
    )
    default_score: float | str = Field(
        default=0.0, description="Score assigned when the payload omits one."
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Convert raw result mappings into SearchResult items."""
        results = state.get("node_results", {})
        payload = results.get(self.source_result_key, {})
        if isinstance(payload, dict) and self.results_field in payload:
            entries = payload[self.results_field]
        else:
            entries = payload
        if entries is None:
            return {"results": []}
        if not isinstance(entries, list):
            msg = "SearchResultAdapterNode requires a list of results"
            raise ValueError(msg)

        normalized: list[SearchResult] = []
        for entry in entries:
            if entry is None:
                continue
            if isinstance(entry, SearchResult):
                normalized.append(self._apply_source_override(entry))
                continue
            if not isinstance(entry, Mapping):
                msg = "SearchResultAdapterNode entries must be mappings"
                raise ValueError(msg)
            normalized.append(self._convert_mapping(entry))

        return {"results": normalized}

    def _apply_source_override(self, entry: SearchResult) -> SearchResult:
        if not self.source_name:
            return entry
        sources = entry.sources or []
        if self.source_name not in sources:
            sources = [self.source_name, *sources] if sources else [self.source_name]
        return SearchResult(
            id=entry.id,
            score=entry.score,
            text=entry.text,
            metadata=entry.metadata,
            source=self.source_name,
            sources=sources,
        )

    def _convert_mapping(self, entry: Mapping[str, Any]) -> SearchResult:
        _, id_value = self._extract(entry, self.id_field)
        _, score_value = self._extract(entry, self.score_field)
        _, text_value = self._extract(entry, self.text_field)

        metadata = self._extract_metadata(entry)
        source = self._extract_source(entry)
        sources = self._extract_sources(entry, source)

        return SearchResult(
            id=str(id_value) if id_value is not None else "",
            score=self._coerce_score(score_value),
            text=str(text_value) if text_value is not None else "",
            metadata=metadata,
            source=source,
            sources=sources,
        )

    def _extract_metadata(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        if self.metadata_field:
            found, metadata_value = self._extract(entry, self.metadata_field)
            if found and isinstance(metadata_value, Mapping):
                return dict(metadata_value)
        if self.raw_field:
            found, raw_value = self._extract(entry, self.raw_field)
            if found and isinstance(raw_value, Mapping):
                return dict(raw_value)
        return {}

    def _extract_source(self, entry: Mapping[str, Any]) -> str | None:
        if self.source_name:
            return self.source_name
        found, source_value = self._extract(entry, self.source_field)
        if found and isinstance(source_value, str) and source_value.strip():
            return source_value.strip()
        return None

    def _extract_sources(
        self, entry: Mapping[str, Any], source: str | None
    ) -> list[str]:
        sources: list[str] = []
        found, sources_value = self._extract(entry, self.sources_field)
        if found:
            if isinstance(sources_value, list):
                sources = [str(item) for item in sources_value if item is not None]
            elif isinstance(sources_value, str):
                sources = [sources_value]
        if not sources and source:
            sources = [source]
        return sources

    def _coerce_score(self, value: Any) -> float:
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return float(self.default_score)
        return float(self.default_score)

    def _extract(self, entry: Mapping[str, Any], path: str) -> tuple[bool, Any]:
        if not path:
            return False, None
        try:
            return _extract_value(entry, path)
        except ValueError:
            return False, None


@registry.register(
    NodeMetadata(
        name="PineconeRerankNode",
        description=(
            "Rerank retrieval results via Pinecone inference for tighter ordering."
        ),
        category="conversational_search",
    )
)
class PineconeRerankNode(TaskNode):
    """Node that uses Pinecone's inference reranker to re-score merged results."""

    model: str = Field(
        default="bge-reranker-v2-m3",
        description="Pinecone hosted reranking model identifier.",
    )
    source_result_key: str = Field(
        default="fusion", description="Result entry holding merged retrieval outputs."
    )
    results_field: str = Field(
        default="results",
        description="Key in the source entry containing SearchResult list.",
    )
    query_key: str = Field(
        default="message",
        description="Key within ``state.inputs`` that contains the active message.",
    )
    rank_fields: list[str] = Field(
        default_factory=lambda: ["chunk_text"],
        description="Document fields evaluated by the reranking model.",
    )
    top_n: int | str = Field(
        default=10,
        description="Maximum reranked results returned from Pinecone.",
    )
    return_documents: bool = Field(
        default=True,
        description="Whether the reranker should return document payloads.",
    )
    parameters: dict[str, Any] | str = Field(
        default_factory=dict,
        description=(
            "Optional parameters passed to the reranking request (e.g., truncate)."
        ),
    )
    client_kwargs: dict[str, Any] | str = Field(
        default_factory=dict,
        description="Keyword arguments forwarded to Pinecone client.",
    )
    document_text_field: str = Field(
        default="chunk_text",
        description="Field name within each document that contains passage text.",
    )
    document_id_field: str = Field(
        default="_id",
        description=(
            "Field name within each document that acts as the unique identifier."
        ),
    )
    client: Any | None = Field(
        default=None,
        description="Optional preconfigured Pinecone client (primarily for testing).",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Call Pinecone inference to rerank merged search results."""
        entries = _resolve_retrieval_results(
            state, self.source_result_key, self.results_field
        )
        if not entries:
            return {"results": []}

        query = self._resolve_query(state)
        documents = self._build_documents(entries)
        client = self._resolve_client()
        inference = getattr(client, "inference", None)
        if inference is None:
            raise RuntimeError(
                "Pinecone client lacks an inference interface for reranking"
            )

        rerank_result = inference.rerank(
            model=self.model,
            query=query,
            documents=documents,
            rank_fields=self.rank_fields,
            top_n=int(self.top_n),
            return_documents=self.return_documents,
            parameters=self.parameters if isinstance(self.parameters, dict) else None,
        )
        if inspect.isawaitable(rerank_result):
            rerank_result = await rerank_result

        data = getattr(rerank_result, "data", None)
        if data is None and isinstance(rerank_result, dict):
            data = rerank_result.get("data", [])
        data = data or []

        entry_map = {entry.id: entry for entry in entries}
        reranked: list[SearchResult] = []
        for row in data:
            doc = (row or {}).get("document") or {}
            doc_id = str(
                doc.get(self.document_id_field)
                or row.get("id")
                or row.get("document_id")
                or ""
            )
            base_entry = entry_map.get(doc_id)
            text = doc.get(self.document_text_field) or (
                base_entry.text if base_entry else ""
            )
            metadata = doc.get("metadata")
            if metadata is None:  # pragma: no branch
                metadata = base_entry.metadata if base_entry else {}
            score = float(row.get("score", 0.0))
            reranked.append(
                SearchResult(
                    id=doc_id
                    or (base_entry.id if base_entry else f"reranker-{len(reranked)}"),
                    score=score,
                    text=text or "",
                    metadata=metadata,
                    source=base_entry.source if base_entry else "reranker",
                    sources=base_entry.sources if base_entry else ["reranker"],
                )
            )
        reranked.sort(key=lambda result: result.score, reverse=True)
        return {"results": reranked[: int(self.top_n)]}

    def _resolve_query(self, state: State) -> str:
        inputs = state.get("inputs", {})
        query = (
            inputs.get(self.query_key)
            or inputs.get("message")
            or inputs.get("user_message")
            or inputs.get("query")
        )
        if not isinstance(query, str) or not query.strip():
            raise ValueError("PineconeRerankNode requires a non-empty query string")
        return query.strip()

    def _build_documents(self, entries: list[SearchResult]) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        for entry in entries:
            document: dict[str, Any] = {
                self.document_id_field: entry.id,
                self.document_text_field: entry.text,
            }
            if entry.metadata:
                document["metadata"] = dict(entry.metadata)
            documents.append(document)
        return documents

    def _resolve_client(self) -> Any:
        if self.client is not None:
            return self.client
        try:
            from pinecone import Pinecone  # type: ignore
        except ImportError as exc:  # pragma: no cover - dependency guard
            msg = (
                "PineconeRerankNode requires the 'pinecone-client' dependency. "
                "Install it or provide a configured client."
            )
            raise ImportError(msg) from exc
        client_kwargs = (
            self.client_kwargs if isinstance(self.client_kwargs, dict) else {}
        )
        self.client = Pinecone(**client_kwargs)
        return self.client


def _resolve_retrieval_results(
    state: State,
    source_result_key: str,
    results_field: str,
    *,
    error_message: str = "Retrieved results must be provided as a list",
) -> list[SearchResult]:
    results = state.get("node_results", {})
    payload = results.get(source_result_key, {})
    if isinstance(payload, dict) and results_field in payload:
        entries = payload[results_field]
    else:
        entries = payload
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ValueError(error_message)
    normalized: list[SearchResult] = []
    for item in entries:
        if item is None:
            continue
        normalized.append(SearchResult.model_validate(item))
    return normalized
