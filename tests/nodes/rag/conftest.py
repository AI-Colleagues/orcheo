"""Shared test helpers for conversational search tests."""

from __future__ import annotations
from typing import Any
import pytest
from langchain_core.embeddings import Embeddings


class FakeDenseEmbeddings(Embeddings):
    """Test embeddings returning text length as the single-element vector."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t))] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text))]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


class FakeSparseEncoder:
    """Test sparse encoder returning trivial sparse vectors."""

    def fit(self, texts: list[str]) -> None:
        pass

    def encode_documents(self, texts: list[str]) -> list[dict[str, Any]]:
        return [{"indices": [i], "values": [0.5]} for i, _ in enumerate(texts)]

    def encode_queries(self, texts: list[str]) -> list[dict[str, Any]]:
        return [{"indices": [0], "values": [1.0]} for _ in texts]


@pytest.fixture(autouse=True)
def _mock_embedding_init(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-mock init_dense_embeddings and init_sparse_embeddings for tests."""
    import orcheo.nodes.rag.embeddings as emb_mod

    monkeypatch.setattr(
        emb_mod,
        "init_dense_embeddings",
        lambda embed_model, model_kwargs=None: FakeDenseEmbeddings(),
    )
    monkeypatch.setattr(
        emb_mod,
        "init_sparse_embeddings",
        lambda sparse_model, sparse_kwargs=None: FakeSparseEncoder(),
    )
