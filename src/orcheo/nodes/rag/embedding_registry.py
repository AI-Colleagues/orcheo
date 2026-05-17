"""Global embedding identifiers for conversational search."""

from __future__ import annotations
from typing import Final
from orcheo.nodes.rag.embeddings import (
    register_langchain_embedding,
    register_pinecone_bm25_embedding,
)
from orcheo.nodes.rag.ingestion import resolve_embedding_method


# Unified dense model identifiers (compatible with langchain init_embeddings)
OPENAI_TEXT_EMBEDDING_3_SMALL: Final[str] = "openai:text-embedding-3-small"
PINECONE_BM25_DEFAULT: Final[str] = "pinecone:bm25"


def _safe_register(name: str, register_fn: object) -> None:
    try:
        resolve_embedding_method(name)
    except ValueError:
        if callable(register_fn):
            register_fn()


def _register_defaults() -> None:
    from langchain_openai import OpenAIEmbeddings

    _safe_register(
        OPENAI_TEXT_EMBEDDING_3_SMALL,
        lambda: register_langchain_embedding(
            OPENAI_TEXT_EMBEDDING_3_SMALL,
            lambda: OpenAIEmbeddings(
                model="text-embedding-3-small",
                dimensions=512,
            ),
        ),
    )
    _safe_register(
        PINECONE_BM25_DEFAULT,
        lambda: register_pinecone_bm25_embedding(PINECONE_BM25_DEFAULT),
    )


_register_defaults()


__all__ = [
    "OPENAI_TEXT_EMBEDDING_3_SMALL",
    "PINECONE_BM25_DEFAULT",
]
