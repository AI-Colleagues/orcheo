"""Unit tests for conversational search ingestion models."""

import pytest
from orcheo.nodes.rag.models import Document, DocumentChunk


def test_document_trims_content_and_rejects_empty_strings() -> None:
    document = Document(id="doc", content="  trimmed  ")
    assert document.content == "trimmed"

    with pytest.raises(
        ValueError, match="Document content cannot be empty after trimming whitespace"
    ):
        Document(id="doc", content="   ")


def test_document_chunk_token_count_and_validation() -> None:
    chunk = DocumentChunk(
        id="chunk-1",
        document_id="doc-1",
        index=0,
        content="  one two  ",
    )
    assert chunk.token_count == 2

    with pytest.raises(
        ValueError, match="Chunk content cannot be empty after trimming whitespace"
    ):
        DocumentChunk(id="chunk-empty", document_id="doc-1", index=0, content="   ")
