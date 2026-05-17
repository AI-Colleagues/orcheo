"""Tests for embedding registration helpers."""

from __future__ import annotations
import inspect
from pathlib import Path
from typing import Any
import pytest
from langchain_core.embeddings import Embeddings
from orcheo.nodes.rag.embedding_registry import _safe_register
from orcheo.nodes.rag.embeddings import (
    _bm25_encoder_builder,
    _encode_sparse_vectors,
    _splade_encoder_resolver,
    _validate_bm25_configuration,
    _validate_sparse_mode,
    dense_embed_documents,
    dense_embed_query,
    init_dense_embeddings,
    init_sparse_embeddings,
    register_langchain_embedding,
    register_pinecone_bm25_embedding,
    register_pinecone_splade_embedding,
    sparse_embed_documents,
    sparse_embed_query,
)
from orcheo.nodes.rag.ingestion import resolve_embedding_method
from orcheo.nodes.rag.models import SparseValues


class _FakeEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text))]


class _AsyncEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> Any:
        async def _inner() -> list[list[float]]:
            return [[float(len(text))] for text in texts]

        return _inner()

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text))]


class _InvalidStructureEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[str]:
        return ["invalid"] * len(texts)

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text))]


@pytest.mark.asyncio
async def test_register_langchain_embedding_supports_factory() -> None:
    method_name = "langchain-factory"
    register_langchain_embedding(method_name, lambda: _FakeEmbeddings())
    embedder = resolve_embedding_method(method_name)

    result = await embedder(["hello", "world"])
    assert result == [[5.0], [5.0]]


@pytest.mark.asyncio
async def test_register_langchain_embedding_awaits_async_result() -> None:
    method_name = "langchain-awaitable"
    register_langchain_embedding(method_name, _AsyncEmbeddings())
    embedder = resolve_embedding_method(method_name)

    result = await embedder(["short", "longer"])
    assert result == [[5.0], [6.0]]


@pytest.mark.asyncio
async def test_register_langchain_embedding_rejects_non_embeddings_factory() -> None:
    method_name = "langchain-bad-factory"
    register_langchain_embedding(method_name, lambda: object())
    embedder = resolve_embedding_method(method_name)

    with pytest.raises(
        TypeError,
        match="LangChain embedding factories must return Embeddings instances",
    ):
        await embedder(["anything"])


@pytest.mark.asyncio
async def test_register_langchain_embedding_rejects_invalid_output() -> None:
    method_name = "langchain-invalid-output"
    register_langchain_embedding(method_name, _InvalidStructureEmbeddings())
    embedder = resolve_embedding_method(method_name)

    with pytest.raises(
        ValueError,
        match="LangChain embeddings must return List\\[List\\[float\\]\\]",
    ):
        await embedder(["bad"])


@pytest.mark.asyncio
async def test_register_pinecone_bm25_embedding_produces_sparse_vectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeBM25:
        instances: list[FakeBM25] = []

        def __init__(self) -> None:
            self.fit_calls: int = 0
            type(self).instances.append(self)

        def fit(self, texts: list[str]) -> None:
            self.fit_calls += 1

        def encode_documents(self, texts: list[str]) -> list[dict[str, Any]]:
            return [{"indices": [idx], "values": [1.0]} for idx, _ in enumerate(texts)]

        def encode_queries(self, texts: list[str]) -> list[dict[str, Any]]:
            return [{"indices": [0], "values": [0.5]} for _ in texts]

        @classmethod
        def load(cls, path: str) -> FakeBM25:  # pragma: no cover - not used here
            instance = cls()
            instance.loaded_path = path  # type: ignore[attr-defined]
            return instance

    import pinecone_text.sparse as pinecone_sparse

    monkeypatch.setattr(pinecone_sparse, "BM25Encoder", FakeBM25)
    method_name = "bm25-helpers"
    register_pinecone_bm25_embedding(method_name)
    embedder = resolve_embedding_method(method_name)

    result = embedder(["chunk-one"])
    if inspect.isawaitable(result):
        result = await result
    vectors = result
    assert len(vectors) == 1
    assert vectors[0].sparse_values is not None
    assert FakeBM25.instances[0].fit_calls == 1


def test_register_pinecone_bm25_embedding_requires_prefit_for_queries() -> None:
    with pytest.raises(ValueError, match="Query mode requires a pre-fitted encoder"):
        register_pinecone_bm25_embedding("bm25-query", mode="queries")


def test_validate_sparse_mode_rejects_invalid_mode() -> None:
    with pytest.raises(
        ValueError, match="mode must be either 'documents' or 'queries'"
    ):
        _validate_sparse_mode("invalid")


def test_validate_bm25_configuration_accepts_callable_encoder() -> None:
    _validate_bm25_configuration("queries", lambda: object(), None)


def test_validate_bm25_configuration_accepts_state_path() -> None:
    _validate_bm25_configuration("queries", None, "pretrained-state")


def test_bm25_encoder_builder_prefers_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    import pinecone_text.sparse as pinecone_sparse

    class DummyBM25:
        pass

    monkeypatch.setattr(pinecone_sparse, "BM25Encoder", DummyBM25)

    sentinel = object()

    def factory() -> object:
        return sentinel

    builder = _bm25_encoder_builder(factory, None)
    assert builder() is sentinel


def test_bm25_encoder_builder_returns_encoder_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pinecone_text.sparse as pinecone_sparse

    class DummyBM25:
        pass

    monkeypatch.setattr(pinecone_sparse, "BM25Encoder", DummyBM25)
    encoder = object()

    builder = _bm25_encoder_builder(encoder, None)
    assert builder() is encoder


def test_bm25_encoder_builder_loads_state_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import pinecone_text.sparse as pinecone_sparse

    class DummyBM25:
        load_calls: list[str] = []

        def __init__(self) -> None:
            pass

        @classmethod
        def load(cls, path: str) -> DummyBM25:
            cls.load_calls.append(path)
            return cls()

    DummyBM25.load_calls = []
    monkeypatch.setattr(pinecone_sparse, "BM25Encoder", DummyBM25)
    state_path = tmp_path / "state.bin"
    state_path.write_text("state data")

    builder = _bm25_encoder_builder(None, state_path)
    instance = builder()

    assert isinstance(instance, DummyBM25)
    assert DummyBM25.load_calls == [str(state_path)]


def test_bm25_encoder_builder_missing_state_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import pinecone_text.sparse as pinecone_sparse

    class DummyBM25:
        pass

    monkeypatch.setattr(pinecone_sparse, "BM25Encoder", DummyBM25)
    missing_path = tmp_path / "missing.bin"
    builder = _bm25_encoder_builder(None, missing_path)

    with pytest.raises(FileNotFoundError, match="Encoder state path does not exist"):
        builder()


def test_encode_sparse_vectors_accepts_sparse_values_instance() -> None:
    sparse = SparseValues(indices=[0], values=[1.0])
    assert _encode_sparse_vectors(sparse) == [sparse]


def test_encode_sparse_vectors_accepts_dict_payload() -> None:
    payload = {"indices": [1], "values": [0.5]}
    assert _encode_sparse_vectors(payload) == [SparseValues(indices=[1], values=[0.5])]


def test_encode_sparse_vectors_rejects_invalid_payload() -> None:
    with pytest.raises(ValueError, match="Sparse encoder returned an invalid payload"):
        _encode_sparse_vectors("bad")


def test_splade_encoder_resolver_prefers_callable() -> None:
    sentinel = object()
    resolver = _splade_encoder_resolver(lambda: sentinel, 32, "cpu")
    assert resolver() is sentinel


def test_splade_encoder_resolver_prefers_instance() -> None:
    sentinel = object()
    resolver = _splade_encoder_resolver(sentinel, 32, "cpu")
    assert resolver() is sentinel


@pytest.mark.asyncio
async def test_register_pinecone_splade_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSplade:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def encode_documents(self, texts: list[str]) -> list[dict[str, Any]]:
            return [{"indices": [idx], "values": [1.0]} for idx, _ in enumerate(texts)]

        def encode_queries(self, texts: list[str]) -> list[dict[str, Any]]:
            return [{"indices": [0], "values": [0.5]} for _ in texts]

    import pinecone_text.sparse as pinecone_sparse

    monkeypatch.setattr(pinecone_sparse, "SpladeEncoder", FakeSplade)
    method_name = "splade-helpers"
    register_pinecone_splade_embedding(method_name)
    embedder = resolve_embedding_method(method_name)

    result = embedder(["chunk-one", "chunk-two"])
    if inspect.isawaitable(result):
        result = await result
    vectors = result
    assert len(vectors) == 2
    assert all(vector.sparse_values is not None for vector in vectors)


# --- embedding_registry coverage (line 21->exit) ---


def test_safe_register_skips_non_callable_register_fn() -> None:
    """Covers the 21->exit branch where register_fn is not callable."""
    _safe_register("__test_non_callable_registry__", "not-callable")


# --- init_dense_embeddings / dense helpers coverage ---


def test_init_dense_embeddings_passes_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Covers lines 33-34 of embeddings.py."""
    import orcheo.nodes.rag.embeddings as emb_mod

    captured: dict[str, Any] = {}

    def mock_lc_init(model: str, **kwargs: Any) -> str:
        captured["model"] = model
        captured["kwargs"] = kwargs
        return "sentinel"

    monkeypatch.setattr(emb_mod, "_lc_init_embeddings", mock_lc_init)

    result = init_dense_embeddings("openai:text-embedding-3-small", {"dim": 512})
    assert result == "sentinel"
    assert captured["model"] == "openai:text-embedding-3-small"
    assert captured["kwargs"] == {"dim": 512}


def test_init_dense_embeddings_handles_none_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orcheo.nodes.rag.embeddings as emb_mod

    monkeypatch.setattr(emb_mod, "_lc_init_embeddings", lambda model, **kw: "ok")

    result = init_dense_embeddings("test:model")
    assert result == "ok"


@pytest.mark.asyncio
async def test_dense_embed_documents_returns_vectors() -> None:
    """Covers line 42 of embeddings.py."""
    model = _FakeEmbeddings()
    result = await dense_embed_documents(model, ["hi", "there"])
    assert result == [[2.0], [5.0]]


@pytest.mark.asyncio
async def test_dense_embed_query_returns_vector() -> None:
    """Covers line 50 of embeddings.py."""
    model = _FakeEmbeddings()
    result = await dense_embed_query(model, "hello")
    assert result == [5.0]


# --- init_sparse_embeddings / sparse helpers coverage ---


def test_init_sparse_embeddings_rejects_bad_format() -> None:
    """Covers lines 83-85 of embeddings.py."""
    with pytest.raises(
        ValueError,
        match="sparse_model must be in 'provider:model' format",
    ):
        init_sparse_embeddings("no-colon")


def test_init_sparse_embeddings_rejects_unsupported_provider() -> None:
    """Covers lines 89-90 of embeddings.py."""
    with pytest.raises(ValueError, match="Unsupported sparse provider"):
        init_sparse_embeddings("unknown:model")


def test_init_sparse_embeddings_pinecone_bm25_without_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Covers BM25 constructor path in _init_pinecone_sparse."""
    import pinecone_text.sparse as ps

    class FakeBM25:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(ps, "BM25Encoder", FakeBM25)

    result = init_sparse_embeddings("pinecone:bm25")
    assert isinstance(result, FakeBM25)


def test_init_sparse_embeddings_pinecone_bm25_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Covers BM25 default-model path in _init_pinecone_sparse."""
    import pinecone_text.sparse as ps

    class FakeBM25:
        @staticmethod
        def default() -> FakeBM25:
            instance = FakeBM25()
            instance.from_default = True
            return instance

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.from_default = False

    monkeypatch.setattr(ps, "BM25Encoder", FakeBM25)

    result = init_sparse_embeddings("pinecone:bm25-default")
    assert isinstance(result, FakeBM25)
    assert result.from_default is True


def test_init_sparse_embeddings_pinecone_bm25_with_state_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Covers BM25Encoder.load path in _init_pinecone_sparse."""
    import pinecone_text.sparse as ps

    state_file = tmp_path / "state.bin"
    state_file.write_text("data")

    class FakeBM25:
        loaded_from: str | None = None

        def __init__(self, **kwargs: Any) -> None:
            pass

        @staticmethod
        def load(path: str) -> FakeBM25:
            instance = FakeBM25()
            instance.loaded_from = path
            return instance

    monkeypatch.setattr(ps, "BM25Encoder", FakeBM25)

    result = init_sparse_embeddings(
        "pinecone:bm25", {"encoder_state_path": str(state_file)}
    )
    assert isinstance(result, FakeBM25)
    assert result.loaded_from == str(state_file)


def test_init_sparse_embeddings_pinecone_bm25_with_state_path_instance_load(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Covers fallback for BM25 instance-style load API."""
    import pinecone_text.sparse as ps

    state_file = tmp_path / "state.bin"
    state_file.write_text("data")

    class FakeBM25:
        loaded_from: str | None = None

        def __init__(self, **kwargs: Any) -> None:
            pass

        def load(self, path: str) -> FakeBM25:
            self.loaded_from = path
            return self

    monkeypatch.setattr(ps, "BM25Encoder", FakeBM25)

    result = init_sparse_embeddings(
        "pinecone:bm25", {"encoder_state_path": str(state_file)}
    )
    assert isinstance(result, FakeBM25)
    assert result.loaded_from == str(state_file)


def test_init_sparse_embeddings_pinecone_bm25_missing_state_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Covers FileNotFoundError in _init_pinecone_sparse."""
    import pinecone_text.sparse as ps

    monkeypatch.setattr(ps, "BM25Encoder", type("BM25", (), {}))

    with pytest.raises(FileNotFoundError, match="Encoder state path does not exist"):
        init_sparse_embeddings(
            "pinecone:bm25",
            {"encoder_state_path": str(tmp_path / "missing.bin")},
        )


def test_init_sparse_embeddings_pinecone_splade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Covers splade path in _init_pinecone_sparse."""
    import pinecone_text.sparse as ps

    class FakeSplade:
        def __init__(self, **kwargs: Any) -> None:
            pass

    monkeypatch.setattr(ps, "SpladeEncoder", FakeSplade)

    result = init_sparse_embeddings("pinecone:splade")
    assert isinstance(result, FakeSplade)


def test_init_sparse_embeddings_pinecone_unsupported_model() -> None:
    """Covers lines 116-117 of embeddings.py."""
    with pytest.raises(ValueError, match="Unsupported Pinecone sparse model"):
        init_sparse_embeddings("pinecone:unknown")


def test_sparse_embed_documents_fits_encoder() -> None:
    """Covers line 127->129 of embeddings.py (fit=True branch)."""

    class FitEncoder:
        fitted = False

        def fit(self, texts: list[str]) -> None:
            self.fitted = True

        def encode_documents(self, texts: list[str]) -> list[dict[str, Any]]:
            return [{"indices": [i], "values": [1.0]} for i in range(len(texts))]

    encoder = FitEncoder()
    result = sparse_embed_documents(encoder, ["a", "b"], fit=True)
    assert encoder.fitted is True
    assert len(result) == 2


def test_sparse_embed_documents_fit_true_without_fit_method() -> None:
    """Covers 127->129 branch: fit=True but encoder has no fit attribute."""

    class NoFitEncoder:
        def encode_documents(self, texts: list[str]) -> list[dict[str, Any]]:
            return [{"indices": [i], "values": [1.0]} for i in range(len(texts))]

    encoder = NoFitEncoder()
    result = sparse_embed_documents(encoder, ["a", "b"], fit=True)
    assert len(result) == 2


def test_sparse_embed_query_raises_on_empty_result() -> None:
    """Covers lines 141-142 of embeddings.py."""

    class EmptyEncoder:
        def encode_queries(self, texts: list[str]) -> list[Any]:
            return []

    with pytest.raises(ValueError, match="Sparse encoder returned no vectors"):
        sparse_embed_query(EmptyEncoder(), "query")
