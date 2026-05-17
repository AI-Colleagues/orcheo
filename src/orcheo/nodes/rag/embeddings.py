"""Helper utilities for embedding initialization and execution."""

from __future__ import annotations
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable
from langchain.embeddings import init_embeddings as _lc_init_embeddings
from langchain_core.embeddings import Embeddings
from pydantic import BaseModel, Field
from orcheo.nodes.rag.ingestion import (
    EmbeddingMethod,
    EmbeddingVector,
    register_embedding_method,
)
from orcheo.nodes.rag.models import SparseValues


if TYPE_CHECKING:  # pragma: no cover - used for typing only
    from pinecone_text.sparse import BM25Encoder, SpladeEncoder


# ---------------------------------------------------------------------------
# Dense embedding helpers
# ---------------------------------------------------------------------------


def init_dense_embeddings(
    embed_model: str,
    model_kwargs: dict[str, Any] | None = None,
) -> Embeddings:
    """Initialize a dense embedding model via LangChain ``init_embeddings``."""
    kwargs = dict(model_kwargs or {})
    return _lc_init_embeddings(model=embed_model, **kwargs)


async def dense_embed_documents(
    model: Embeddings,
    texts: list[str],
) -> list[list[float]]:
    """Embed multiple documents using a dense embedding model."""
    return await model.aembed_documents(texts)


async def dense_embed_query(
    model: Embeddings,
    text: str,
) -> list[float]:
    """Embed a single query using a dense embedding model."""
    return await model.aembed_query(text)


# ---------------------------------------------------------------------------
# Sparse embedding helpers
# ---------------------------------------------------------------------------


@runtime_checkable
class SparseEmbedder(Protocol):
    """Protocol for sparse embedding encoders."""

    def encode_documents(self, texts: list[str]) -> Any:
        """Encode documents into sparse vectors."""

    def encode_queries(self, texts: list[str]) -> Any:
        """Encode queries into sparse vectors."""


def init_sparse_embeddings(
    sparse_model: str,
    sparse_kwargs: dict[str, Any] | None = None,
) -> Any:
    """Initialize a sparse embedding encoder.

    Args:
        sparse_model: Identifier in ``provider:model`` format,
            e.g. ``pinecone:bm25`` or ``pinecone:splade``.
        sparse_kwargs: Additional keyword arguments forwarded to the encoder
            constructor.
    """
    kwargs = dict(sparse_kwargs or {})
    parts = sparse_model.split(":", 1)
    if len(parts) != 2:
        msg = f"sparse_model must be in 'provider:model' format, got '{sparse_model}'"
        raise ValueError(msg)
    provider, model = parts
    if provider == "pinecone":
        return _init_pinecone_sparse(model, kwargs)
    msg = f"Unsupported sparse provider: {provider}"
    raise ValueError(msg)


def _init_pinecone_sparse(model: str, kwargs: dict[str, Any]) -> Any:
    """Initialize a Pinecone sparse encoder."""
    if model in ("bm25", "bm25-default"):
        from pinecone_text.sparse import BM25Encoder

        encoder_state_path = kwargs.pop("encoder_state_path", None)
        if encoder_state_path is not None:
            path = Path(encoder_state_path)
            if not path.exists():
                msg = f"Encoder state path does not exist: {encoder_state_path}"
                raise FileNotFoundError(msg)
            return _load_bm25_encoder_from_path(path)
        if model == "bm25-default" and not kwargs:
            return BM25Encoder.default()
        return BM25Encoder(**kwargs)
    if model == "splade":
        try:
            from pinecone_text.sparse import SpladeEncoder
        except ImportError as exc:  # pragma: no cover - dependency guard
            msg = (
                "Sparse model 'pinecone:splade' requires the 'pinecone-text' "
                "package with the 'splade' extra installed."
            )
            raise ImportError(msg) from exc
        return SpladeEncoder(**kwargs)
    msg = f"Unsupported Pinecone sparse model: {model}"
    raise ValueError(msg)


def sparse_embed_documents(
    encoder: Any,
    texts: list[str],
    *,
    fit: bool = False,
) -> list[SparseValues]:
    """Encode documents using a sparse encoder."""
    if fit and hasattr(encoder, "fit"):
        encoder.fit(texts)
    payload = encoder.encode_documents(texts)
    return _encode_sparse_vectors(payload)


def _load_bm25_encoder_from_path(path: Path) -> Any:
    """Load BM25 encoder state from disk across pinecone-text API variants."""
    from pinecone_text.sparse import BM25Encoder

    try:
        # pinecone-text<=0.10 used class-style load calls.
        return BM25Encoder.load(str(path))
    except TypeError:
        # pinecone-text>=0.11 uses an instance load method.
        return BM25Encoder().load(str(path))


def sparse_embed_query(
    encoder: Any,
    text: str,
) -> SparseValues:
    """Encode a single query using a sparse encoder."""
    payload = encoder.encode_queries([text])
    vectors = _encode_sparse_vectors(payload)
    if not vectors:
        msg = "Sparse encoder returned no vectors for query"
        raise ValueError(msg)
    return vectors[0]


# ---------------------------------------------------------------------------
# Embedding spec models
# ---------------------------------------------------------------------------


class DenseEmbeddingSpec(BaseModel):
    """Configuration for a dense embedding method."""

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


class SparseEmbeddingSpec(BaseModel):
    """Configuration for a sparse embedding method."""

    sparse_model: str = Field(
        ...,
        description="Sparse embedding model identifier, e.g. pinecone:bm25",
    )
    sparse_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional sparse-model kwargs passed to sparse initializer.",
    )


# ---------------------------------------------------------------------------
# Embedding registration helpers
# ---------------------------------------------------------------------------

BM25Mode = Literal["documents", "queries"]
SparseEncoderFactory = Callable[[], Any]


def register_langchain_embedding(
    name: str,
    embedding: Embeddings | Callable[[], Embeddings],
) -> EmbeddingMethod:
    """Register a LangChain embedding instance or factory."""

    def _resolve() -> Embeddings:
        instance = embedding() if callable(embedding) else embedding
        if not isinstance(instance, Embeddings):
            msg = "LangChain embedding factories must return Embeddings instances"
            raise TypeError(msg)
        return instance

    async def _embed(texts: list[str]) -> list[list[float]]:
        instance = _resolve()
        result = instance.embed_documents(texts)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, list) or not all(
            isinstance(row, list) for row in result
        ):
            msg = "LangChain embeddings must return List[List[float]]"
            raise ValueError(msg)
        return result

    return register_embedding_method(name, _embed)


def register_pinecone_bm25_embedding(
    name: str,
    *,
    mode: BM25Mode = "documents",
    encoder: BM25Encoder | SparseEncoderFactory | None = None,
    encoder_state_path: str | Path | None = None,
    fit_on_call: bool | None = None,
) -> EmbeddingMethod:
    """Register a Pinecone BM25 encoder as an embedding method."""
    _validate_sparse_mode(mode)
    _validate_bm25_configuration(mode, encoder, encoder_state_path)
    builder = _bm25_encoder_builder(encoder, encoder_state_path)
    should_fit = (
        encoder_state_path is None and mode == "documents"
        if fit_on_call is None
        else fit_on_call
    )
    embed_fn = _bm25_embed_function(builder, mode, should_fit)
    return register_embedding_method(name, embed_fn)


def register_pinecone_splade_embedding(
    name: str,
    *,
    encoder: SpladeEncoder | SparseEncoderFactory | None = None,
    mode: BM25Mode = "documents",
    max_seq_length: int = 256,
    device: str | None = None,
) -> EmbeddingMethod:
    """Register a Pinecone SPLADE encoder as an embedding method."""
    _validate_sparse_mode(mode)
    resolver = _splade_encoder_resolver(encoder, max_seq_length, device)
    embed_fn = _splade_embed_function(resolver, mode)
    return register_embedding_method(name, embed_fn)


def _validate_sparse_mode(mode: str) -> None:
    if mode not in {"documents", "queries"}:
        msg = "mode must be either 'documents' or 'queries'"
        raise ValueError(msg)


def _validate_bm25_configuration(
    mode: BM25Mode,
    encoder: BM25Encoder | SparseEncoderFactory | None,
    encoder_state_path: str | Path | None,
) -> None:
    if mode != "queries":
        return
    if encoder is not None or callable(encoder):
        return
    if encoder_state_path is not None:
        return
    msg = "Query mode requires a pre-fitted encoder or encoder_state_path"
    raise ValueError(msg)


def _bm25_encoder_builder(
    encoder: BM25Encoder | SparseEncoderFactory | None,
    encoder_state_path: str | Path | None,
) -> Callable[[], BM25Encoder]:
    from pinecone_text.sparse import BM25Encoder

    def _builder() -> BM25Encoder:
        if callable(encoder):
            return encoder()
        if encoder is not None:
            return encoder
        if encoder_state_path is not None:
            path = Path(encoder_state_path)
            if not path.exists():
                msg = f"Encoder state path does not exist: {encoder_state_path}"
                raise FileNotFoundError(msg)
            return _load_bm25_encoder_from_path(path)
        return BM25Encoder()

    return _builder


def _bm25_embed_function(
    encoder_builder: Callable[[], BM25Encoder],
    mode: BM25Mode,
    fit_on_call: bool,
) -> EmbeddingMethod:
    def _bm25_embed(texts: list[str]) -> list[EmbeddingVector]:
        encoder_instance = encoder_builder()
        if fit_on_call:  # pragma: no branch
            encoder_instance.fit(texts)
        payload = (
            encoder_instance.encode_documents(texts)
            if mode == "documents"
            else encoder_instance.encode_queries(texts)
        )
        return [
            EmbeddingVector(values=[], sparse_values=sparse)
            for sparse in _encode_sparse_vectors(payload)
        ]

    return _bm25_embed


def _encode_sparse_vectors(payload: Any) -> list[SparseValues]:
    vectors = payload if isinstance(payload, list) else [payload]
    sparse_vectors: list[SparseValues] = []
    for entry in vectors:
        if isinstance(entry, SparseValues):
            sparse_vectors.append(entry)
        elif isinstance(entry, dict):
            sparse_vectors.append(SparseValues.model_validate(entry))
        else:
            msg = "Sparse encoder returned an invalid payload"
            raise ValueError(msg)
    return sparse_vectors


def _splade_encoder_resolver(
    encoder: SpladeEncoder | SparseEncoderFactory | None,
    max_seq_length: int,
    device: str | None,
) -> Callable[[], SpladeEncoder]:
    def _resolver() -> SpladeEncoder:
        if callable(encoder):
            return encoder()
        if encoder is not None:
            return encoder
        try:
            from pinecone_text.sparse import SpladeEncoder
        except ImportError as exc:  # pragma: no cover - dependency guard
            msg = (
                "register_pinecone_splade_embedding requires the 'pinecone-text' "
                "package with the 'splade' extra installed."
            )
            raise ImportError(msg) from exc
        return SpladeEncoder(max_seq_length=max_seq_length, device=device)

    return _resolver


def _splade_embed_function(
    encoder_resolver: Callable[[], SpladeEncoder],
    mode: BM25Mode,
) -> EmbeddingMethod:
    def _splade_embed(texts: list[str]) -> list[EmbeddingVector]:
        encoder_instance = encoder_resolver()
        payload = (
            encoder_instance.encode_documents(texts)
            if mode == "documents"
            else encoder_instance.encode_queries(texts)
        )
        return [
            EmbeddingVector(values=[], sparse_values=sparse)
            for sparse in _encode_sparse_vectors(payload)
        ]

    return _splade_embed
