"""Shared test helpers for evaluation tests."""

from __future__ import annotations
import pytest
from langchain_core.embeddings import Embeddings


class FakeDenseEmbeddings(Embeddings):
    """Test embeddings for evaluation tests."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t))] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text))]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


@pytest.fixture(autouse=True)
def _mock_embedding_init(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-mock init_dense_embeddings for evaluation tests."""
    import orcheo.nodes.rag.embeddings as emb_mod

    monkeypatch.setattr(
        emb_mod,
        "init_dense_embeddings",
        lambda embed_model, model_kwargs=None: FakeDenseEmbeddings(),
    )
