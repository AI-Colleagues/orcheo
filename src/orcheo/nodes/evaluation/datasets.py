"""Dataset loading nodes for evaluation workflows."""

from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any
import httpx
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, field_validator
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.rag.models import Document
from orcheo.nodes.registry import NodeMetadata, registry


logger = logging.getLogger(__name__)


@registry.register(
    NodeMetadata(
        name="DatasetNode",
        description="Load and filter golden datasets for evaluation workflows.",
        category="conversational_search",
    )
)
class DatasetNode(TaskNode):
    """Load a dataset from inputs or a built-in fallback."""

    dataset_key: str = Field(default="dataset")
    split_key: str = Field(default="split")
    limit_key: str = Field(default="limit")
    references_key: str = Field(default="references")
    keyword_corpus_key: str = Field(default="keyword_corpus")
    dataset: list[dict[str, Any]] = Field(default_factory=list)
    references: dict[str, str] = Field(default_factory=dict)
    keyword_corpus: list[dict[str, str]] = Field(default_factory=list)
    golden_path: str | None = Field(
        default=None, description="Path to golden dataset JSON."
    )
    queries_path: str | None = Field(
        default=None, description="Path to baseline queries JSON."
    )
    labels_path: str | None = Field(
        default=None, description="Path to relevance labels JSON."
    )
    docs_path: str | None = Field(
        default=None, description="Path to knowledge base docs."
    )
    http_timeout: float = Field(
        default=30.0, ge=0.0, description="Timeout in seconds for URL-based datasets."
    )
    split: str | None = Field(
        default=None, description="Optional dataset split when loading from files."
    )
    limit: int | None = Field(
        default=None, ge=1, description="Optional dataset cap when loading from files."
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return a dataset filtered by split and limited when requested."""
        del config
        inputs = state.get("inputs") or {}
        state["inputs"] = inputs

        requested_split = inputs.get(self.split_key, self.split)
        requested_limit = inputs.get(self.limit_key, self.limit)

        dataset = self._dataset_from_inputs(inputs)
        references = self._references_from_inputs(inputs)
        keyword_corpus = self._keyword_corpus_from_inputs(inputs)

        dataset, references, keyword_corpus = await self._load_if_needed(
            dataset, references, keyword_corpus, requested_split, requested_limit
        )
        dataset, references, keyword_corpus = self._validate_inputs(
            dataset, references, keyword_corpus
        )

        dataset = self._filter_dataset(dataset, requested_split, requested_limit)

        self._update_inputs(
            inputs,
            dataset,
            references,
            keyword_corpus,
            requested_split,
            requested_limit,
        )

        return {
            "dataset": dataset,
            "references": references,
            "keyword_corpus": keyword_corpus,
            "count": len(dataset),
            "split": requested_split,
            "limit": requested_limit,
        }

    def _dataset_from_inputs(self, inputs: dict[str, Any]) -> Any:
        dataset = inputs.get(self.dataset_key)
        if dataset is not None:
            return dataset
        return self.dataset

    def _references_from_inputs(self, inputs: dict[str, Any]) -> dict[str, str] | None:
        references = inputs.get(self.references_key)
        if references is not None:
            return references
        return self.references or None

    def _keyword_corpus_from_inputs(
        self, inputs: dict[str, Any]
    ) -> list[dict[str, str]] | None:
        keyword_corpus = inputs.get(self.keyword_corpus_key)
        if keyword_corpus is not None:
            return keyword_corpus
        return self.keyword_corpus or None

    async def _load_if_needed(
        self,
        dataset: Any,
        references: dict[str, str] | None,
        keyword_corpus: list[dict[str, str]] | None,
        split: str | None,
        limit: int | None,
    ) -> tuple[
        list[dict[str, Any]], dict[str, str] | None, list[dict[str, str]] | None
    ]:
        if not self._should_load_from_files(dataset):
            return dataset, references, keyword_corpus

        dataset, loaded_references, loaded_corpus = await self._load_from_files(
            split, limit
        )
        references = references if references is not None else loaded_references
        keyword_corpus = keyword_corpus if keyword_corpus is not None else loaded_corpus
        return dataset, references, keyword_corpus

    def _validate_inputs(
        self,
        dataset: Any,
        references: dict[str, str] | None,
        keyword_corpus: list[dict[str, str]] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, str]]]:
        if not isinstance(dataset, list):
            msg = "DatasetNode expects dataset to be a list"
            raise ValueError(msg)
        if references is None:
            references = {}
        if keyword_corpus is None:
            keyword_corpus = []
        if not isinstance(references, dict):
            msg = "DatasetNode expects references to be a dict"
            raise ValueError(msg)
        if not isinstance(keyword_corpus, list):
            msg = "DatasetNode expects keyword_corpus to be a list"
            raise ValueError(msg)
        return dataset, references, keyword_corpus

    def _update_inputs(
        self,
        inputs: dict[str, Any],
        dataset: list[dict[str, Any]],
        references: dict[str, str] | None,
        keyword_corpus: list[dict[str, str]] | None,
        split: str | None,
        limit: int | None,
    ) -> None:
        inputs[self.dataset_key] = dataset
        inputs[self.references_key] = references or {}
        inputs[self.keyword_corpus_key] = keyword_corpus or []
        if split is not None:
            inputs[self.split_key] = split
        if limit is not None:  # pragma: no branch
            inputs[self.limit_key] = limit

    def _filter_dataset(
        self,
        dataset: list[dict[str, Any]],
        split: str | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        filtered = dataset
        if isinstance(split, str):
            filtered = [row for row in filtered if row.get("split") == split]

        if isinstance(limit, int) and limit > 0:
            filtered = filtered[:limit]
        return filtered

    def _should_load_from_files(self, dataset: Any) -> bool:
        if not self._has_file_sources():
            return False
        if dataset is None:
            return True
        return isinstance(dataset, list) and not dataset

    def _has_file_sources(self) -> bool:
        return any(
            [
                self.golden_path,
                self.queries_path,
                self.labels_path,
                self.docs_path,
            ]
        )

    async def _load_from_files(
        self,
        split: str | None,
        limit: int | None,
    ) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, str]]]:
        missing = [
            name
            for name, value in {
                "golden_path": self.golden_path,
                "queries_path": self.queries_path,
                "labels_path": self.labels_path,
                "docs_path": self.docs_path,
            }.items()
            if not value
        ]
        if missing:
            msg = f"DatasetNode requires {', '.join(missing)} to load from files"
            raise ValueError(msg)

        golden_data = await self._load_json(self.golden_path)
        queries_data = await self._load_json(self.queries_path)
        labels_data = await self._load_json(self.labels_path)

        label_map: dict[str, list[str]] = {}
        for entry in labels_data:
            query_id = str(entry.get("query_id"))
            label_map.setdefault(query_id, [])
            doc_id = str(entry.get("doc_id"))
            label_map[query_id].append(doc_id)

        dataset: list[dict[str, Any]] = []
        references: dict[str, str] = {}
        active_split = split or self.split

        for item in golden_data:
            query_id = str(item.get("id"))
            relevant_ids = [
                str(doc) for doc in item.get("expected_citations", []) if doc
            ]
            dataset.append(
                {
                    "id": query_id,
                    "query": item.get("query", ""),
                    "relevant_ids": relevant_ids,
                    "split": active_split or item.get("split", "test"),
                }
            )
            references[query_id] = item.get("expected_answer", "")

        for item in queries_data:
            query_id = str(item.get("id"))
            dataset.append(
                {
                    "id": query_id,
                    "query": item.get("question", ""),
                    "relevant_ids": label_map.get(query_id, []),
                    "split": active_split or item.get("split", "test"),
                }
            )

        dataset = self._filter_dataset(dataset, active_split, limit)
        keyword_corpus = self.keyword_corpus or self._build_keyword_corpus()

        return dataset, references, keyword_corpus

    async def _load_json(self, path: str | None) -> Any:
        if path is None:
            msg = "JSON path must be provided."
            raise ValueError(msg)
        if self._is_url(path):
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.get(path, follow_redirects=True)
                response.raise_for_status()
                return response.json()
        with Path(path).open("r", encoding="utf-8") as file:
            return json.load(file)

    def _is_url(self, path: str) -> bool:
        return path.startswith(("http://", "https://"))

    def _build_keyword_corpus(self) -> list[dict[str, str]]:
        if self.docs_path is None:
            return []
        corpus: list[dict[str, str]] = []
        for path in sorted(Path(self.docs_path).glob("*.md")):
            content = path.read_text(encoding="utf-8")
            corpus.append(
                {"id": path.name, "content": content.strip(), "source": path.name}
            )
        return corpus


@registry.register(
    NodeMetadata(
        name="MultiDoc2DialCorpusLoaderNode",
        description=(
            "Load MultiDoc2Dial corpus documents from a local path or URL and "
            "normalize them for indexing."
        ),
        category="evaluation",
    )
)
class MultiDoc2DialCorpusLoaderNode(TaskNode):
    """Load MultiDoc2Dial document corpus into conversational-search documents."""

    input_key: str = Field(
        default="md2d_corpus",
        description="Key within ``state.inputs`` containing the corpus JSON payload.",
    )
    output_key: str = Field(
        default="documents",
        description=(
            "Key within ``state.inputs`` where normalized documents are stored."
        ),
    )
    corpus_path: str | None = Field(
        default=None,
        description="Path or URL to ``doc2dial_doc.json``.",
    )
    max_documents: int | str | None = Field(
        default=None,
        description="Limit the number of corpus documents to load for indexing.",
    )
    http_timeout: float = Field(
        default=30.0,
        ge=0.0,
        description="Timeout in seconds for URL-based corpus loading.",
    )

    @field_validator("max_documents", mode="before")
    @classmethod
    def _validate_max_documents(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            if "{{" in value and "}}" in value:
                return value
            try:
                value = int(value)
            except ValueError as exc:
                msg = "max_documents must be an integer"
                raise ValueError(msg) from exc
        return value

    def _resolve_max_documents(self) -> int | None:
        value = self.max_documents
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError as exc:
                msg = "max_documents must resolve to an integer"
                raise ValueError(msg) from exc
        if value < 1:
            msg = "max_documents must be >= 1"
            raise ValueError(msg)
        return value

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Load corpus JSON and emit normalized document payloads."""
        del config
        inputs = state.get("inputs") or {}
        state["inputs"] = inputs

        raw_corpus = inputs.get(self.input_key)
        if raw_corpus is None:
            if self.corpus_path is None:
                msg = (
                    "MultiDoc2DialCorpusLoaderNode requires state.inputs "
                    f"'{self.input_key}' or 'corpus_path'."
                )
                raise ValueError(msg)
            raw_corpus = await self._load_json(self.corpus_path)

        documents = self._parse_corpus(raw_corpus, self.corpus_path)
        max_documents = self._resolve_max_documents()
        if max_documents is not None:
            documents = documents[:max_documents]
        serialized_documents = [document.model_dump() for document in documents]
        inputs[self.output_key] = serialized_documents
        return {
            "documents": serialized_documents,
            "count": len(serialized_documents),
        }

    async def _load_json(self, path: str) -> Any:
        if self._is_url(path):
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.get(path, follow_redirects=True)
                response.raise_for_status()
                return response.json()
        with Path(path).open("r", encoding="utf-8") as file:
            return json.load(file)

    def _parse_corpus(
        self,
        payload: Any,
        source_hint: str | None,
    ) -> list[Document]:
        if not isinstance(payload, dict):
            msg = "MultiDoc2Dial corpus payload must be a mapping"
            raise ValueError(msg)
        doc_data = payload.get("doc_data")
        if not isinstance(doc_data, dict):
            msg = "MultiDoc2Dial corpus payload must include a 'doc_data' mapping"
            raise ValueError(msg)

        documents = [
            document
            for domain, fallback_doc_id, raw_doc in self._iter_raw_documents(doc_data)
            if (
                document := self._build_document(
                    domain=domain,
                    fallback_doc_id=fallback_doc_id,
                    raw_doc=raw_doc,
                    source_hint=source_hint,
                )
            )
            is not None
        ]

        if not documents:
            msg = "MultiDoc2Dial corpus did not contain any indexable documents"
            raise ValueError(msg)
        return documents

    def _build_source(self, source_hint: str | None, document_id: str) -> str:
        source = source_hint or "multidoc2dial"
        return f"{source}#doc_id={document_id}"

    def _is_url(self, path: str) -> bool:
        return path.startswith(("http://", "https://"))

    def _iter_raw_documents(
        self, doc_data: dict[str, Any]
    ) -> list[tuple[str, str, dict[str, Any]]]:
        entries: list[tuple[str, str, dict[str, Any]]] = []
        for domain, docs in doc_data.items():
            if not isinstance(docs, dict):
                continue
            for fallback_doc_id, raw_doc in docs.items():
                if not isinstance(raw_doc, dict):
                    continue
                entries.append((str(domain), str(fallback_doc_id), raw_doc))
        return entries

    def _build_document(
        self,
        domain: str,
        fallback_doc_id: str,
        raw_doc: dict[str, Any],
        source_hint: str | None,
    ) -> Document | None:
        document_id = str(raw_doc.get("doc_id") or fallback_doc_id).strip()
        if not document_id:
            return None
        content = str(raw_doc.get("doc_text", "")).strip()
        if not content:
            return None

        title = str(raw_doc.get("title", "")).strip()
        metadata: dict[str, Any] = {"domain": str(raw_doc.get("domain") or domain)}
        if title:
            metadata["title"] = title
        if isinstance(raw_doc.get("spans"), dict):
            metadata["span_count"] = len(raw_doc["spans"])

        return Document(
            id=document_id,
            content=content,
            metadata=metadata,
            source=self._build_source(source_hint, document_id),
        )


# --- Data Models ---


class QreccTurn(BaseModel):
    """A single turn in a QReCC conversation."""

    turn_id: str
    raw_question: str
    gold_rewrite: str
    context: list[str]
    gold_answer: str


class QreccConversation(BaseModel):
    """A QReCC conversation with ordered turns."""

    conversation_id: str
    turns: list[QreccTurn]


class GroundingSpan(BaseModel):
    """A document span grounding a response in MultiDoc2Dial."""

    doc_id: str
    span_text: str
    start: int
    end: int


class MD2DTurn(BaseModel):
    """A single turn in a MultiDoc2Dial conversation."""

    turn_id: str
    user_utterance: str
    gold_response: str
    grounding_spans: list[GroundingSpan] = Field(default_factory=list)


class MD2DConversation(BaseModel):
    """A MultiDoc2Dial conversation with ordered turns."""

    conversation_id: str
    domain: str
    turns: list[MD2DTurn]


# --- QReCC Dataset Node ---


@registry.register(
    NodeMetadata(
        name="QReCCDatasetNode",
        description=("Load QReCC conversations with gold rewrites for evaluation"),
        category="evaluation",
    )
)
class QReCCDatasetNode(DatasetNode):
    """Loads QReCC conversations and gold rewrites."""

    data_path: str | None = Field(
        default=None,
        description="Path or URL to QReCC JSON data file",
    )
    max_conversations: int | str | None = Field(
        default=None,
        description="Limit conversations to load",
    )

    @field_validator("max_conversations", mode="before")
    @classmethod
    def _validate_max_conversations(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            if "{{" in value and "}}" in value:
                return value
            try:
                value = int(value)
            except ValueError as exc:
                msg = "max_conversations must be an integer"
                raise ValueError(msg) from exc
        return value

    def _resolve_max_conversations(self) -> int | None:
        value = self.max_conversations
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError as exc:
                msg = "max_conversations must resolve to an integer"
                raise ValueError(msg) from exc
        if value < 1:
            msg = "max_conversations must be >= 1"
            raise ValueError(msg)
        return value

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Parse QReCC data and return structured conversations."""
        del config
        inputs = state.get("inputs") or {}
        state["inputs"] = inputs

        raw_data = inputs.get("qrecc_data")
        if raw_data is None and self.data_path:
            raw_data = await self._load_json(self.data_path)

        if not isinstance(raw_data, list):
            msg = "QReCCDatasetNode expects qrecc_data list"
            raise ValueError(msg)

        conversations = self._parse_conversations(raw_data)
        max_conversations = self._resolve_max_conversations()
        if max_conversations is not None:
            conversations = conversations[:max_conversations]

        conv_dicts = [conv.model_dump() for conv in conversations]
        total_turns = sum(len(c.turns) for c in conversations)

        inputs["conversations"] = conv_dicts

        return {
            "conversations": conv_dicts,
            "total_conversations": len(conversations),
            "total_turns": total_turns,
        }

    def _parse_conversations(
        self, raw_data: list[dict[str, Any]]
    ) -> list[QreccConversation]:
        """Group QReCC records into conversations."""
        conv_map: dict[str, list[QreccTurn]] = {}

        for record in raw_data:
            conv_id = str(record.get("Conversation_no", ""))
            turn_id = str(record.get("Turn_no", ""))
            context_raw = record.get("Context", [])
            context = context_raw if isinstance(context_raw, list) else []

            turn = QreccTurn(
                turn_id=turn_id,
                raw_question=str(record.get("Question", "")),
                gold_rewrite=str(
                    record.get("Rewrite", record.get("Truth_rewrite", ""))
                ),
                context=[str(c) for c in context],
                gold_answer=str(record.get("Answer", record.get("Truth_answer", ""))),
            )

            conv_map.setdefault(conv_id, []).append(turn)

        conversations: list[QreccConversation] = []
        for conv_id, turns in sorted(conv_map.items()):
            conversations.append(
                QreccConversation(
                    conversation_id=conv_id,
                    turns=turns,
                )
            )

        return conversations


# --- MultiDoc2Dial Dataset Node ---


@registry.register(
    NodeMetadata(
        name="MultiDoc2DialDatasetNode",
        description=(
            "Load MultiDoc2Dial conversations with gold responses for evaluation"
        ),
        category="evaluation",
    )
)
class MultiDoc2DialDatasetNode(DatasetNode):
    """Loads MultiDoc2Dial conversations, documents, and grounding spans."""

    data_path: str | None = Field(
        default=None,
        description="Path or URL to MultiDoc2Dial JSON data file",
    )
    max_conversations: int | str | None = Field(
        default=None,
        description="Limit conversations to load",
    )

    @field_validator("max_conversations", mode="before")
    @classmethod
    def _validate_max_conversations(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            if "{{" in value and "}}" in value:
                return value
            try:
                value = int(value)
            except ValueError as exc:
                msg = "max_conversations must be an integer"
                raise ValueError(msg) from exc
        return value

    def _resolve_max_conversations(self) -> int | None:
        value = self.max_conversations
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError as exc:
                msg = "max_conversations must resolve to an integer"
                raise ValueError(msg) from exc
        if value < 1:
            msg = "max_conversations must be >= 1"
            raise ValueError(msg)
        return value

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Parse MultiDoc2Dial data and return structured conversations."""
        del config
        inputs = state.get("inputs") or {}
        state["inputs"] = inputs

        raw_data = inputs.get("md2d_data")
        if raw_data is None and self.data_path:
            raw_data = await self._load_json(self.data_path)

        normalized_data = self._normalize_dataset_payload(raw_data)
        if normalized_data is None:
            msg = "MultiDoc2DialDatasetNode expects md2d_data list"
            raise ValueError(msg)

        conversations = self._parse_conversations(normalized_data)
        max_conversations = self._resolve_max_conversations()
        if max_conversations is not None:
            conversations = conversations[:max_conversations]

        conv_dicts = [conv.model_dump() for conv in conversations]
        total_turns = sum(len(c.turns) for c in conversations)

        inputs["conversations"] = conv_dicts

        return {
            "conversations": conv_dicts,
            "total_conversations": len(conversations),
            "total_turns": total_turns,
        }

    def _normalize_dataset_payload(self, raw_data: Any) -> list[dict[str, Any]] | None:
        """Normalize official and pre-processed MultiDoc2Dial payloads."""
        if isinstance(raw_data, list):
            return raw_data
        if not isinstance(raw_data, dict):
            return None

        dial_data = raw_data.get("dial_data")
        if not isinstance(dial_data, dict):
            return None

        return self._flatten_official_dialogs(dial_data)

    def _flatten_official_dialogs(
        self, dial_data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Convert official ``dial_data`` mapping into list-based conversations."""
        normalized: list[dict[str, Any]] = []

        for domain, docs in dial_data.items():
            if not isinstance(docs, dict):
                msg = (
                    "MultiDoc2DialDatasetNode expects 'dial_data' domains to map "
                    "document IDs to dialog lists; "
                    f"got {type(docs).__name__} for domain '{domain}'"
                )
                raise ValueError(msg)
            for doc_id, dialogs in docs.items():
                if not isinstance(dialogs, list):
                    continue
                for dialog in dialogs:
                    if not isinstance(dialog, dict):
                        continue
                    conversation_id = str(dialog.get("dial_id", dialog.get("id", "")))
                    turns = self._normalize_official_turns(
                        dialog.get("turns", []),
                        default_doc_id=str(doc_id),
                    )
                    normalized.append(
                        {
                            "dial_id": conversation_id,
                            "domain": str(dialog.get("domain", domain)),
                            "turns": turns,
                        }
                    )

        return normalized

    def _normalize_official_turns(
        self,
        turns_raw: Any,
        *,
        default_doc_id: str,
    ) -> list[dict[str, Any]]:
        """Build user-query turns from official alternating user/agent turns."""
        if not isinstance(turns_raw, list):
            return []

        normalized_turns: list[dict[str, Any]] = []

        for idx, turn in enumerate(turns_raw):
            if not isinstance(turn, dict):
                continue
            if str(turn.get("role", "")).lower() != "user":
                continue

            response_turn = self._find_next_agent_turn(turns_raw, idx)
            references = (
                response_turn.get("references", [])
                if isinstance(response_turn, dict)
                else []
            )
            grounding_spans = self._normalize_reference_spans(
                references,
                default_doc_id=default_doc_id,
            )

            normalized_turns.append(
                {
                    "turn_id": str(turn.get("turn_id", idx)),
                    "user_utterance": str(
                        turn.get("utterance", turn.get("user_utterance", ""))
                    ),
                    "response": (
                        str(response_turn.get("utterance", ""))
                        if isinstance(response_turn, dict)
                        else ""
                    ),
                    "grounding_spans": grounding_spans,
                }
            )

        return normalized_turns

    def _find_next_agent_turn(
        self, turns_raw: list[Any], user_idx: int
    ) -> dict[str, Any] | None:
        """Return the first agent turn after ``user_idx``."""
        for candidate in turns_raw[user_idx + 1 :]:
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("role", "")).lower() == "agent":
                return candidate
            if str(candidate.get("role", "")).lower() == "user":  # pragma: no branch
                return None
        return None

    def _normalize_reference_spans(
        self,
        references: Any,
        *,
        default_doc_id: str,
    ) -> list[dict[str, Any]]:
        """Convert official references into grounding span dictionaries."""
        if not isinstance(references, list):
            return []

        spans: list[dict[str, Any]] = []
        for reference in references:
            if not isinstance(reference, dict):
                continue
            sp_id = str(reference.get("sp_id", "")).strip()
            label = str(reference.get("label", "")).strip()
            span_text = f"{label}:{sp_id}" if label and sp_id else sp_id
            spans.append(
                {
                    "doc_id": str(reference.get("doc_id", default_doc_id)),
                    "span_text": span_text,
                    "start": self._safe_int(reference.get("start", 0)),
                    "end": self._safe_int(reference.get("end", 0)),
                }
            )
        return spans

    def _safe_int(self, value: Any, default: int = 0) -> int:
        """Parse ``value`` as int with a stable fallback for noisy payloads."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _parse_conversations(
        self, raw_data: list[dict[str, Any]]
    ) -> list[MD2DConversation]:
        """Parse MultiDoc2Dial records into conversations."""
        conversations: list[MD2DConversation] = []

        for record in raw_data:
            conv_id = str(record.get("dial_id", record.get("id", "")))
            domain = str(record.get("domain", "unknown"))
            turns_raw = record.get("turns", [])

            turns: list[MD2DTurn] = []
            for idx, turn_data in enumerate(turns_raw):
                spans_raw = turn_data.get("grounding_spans", [])
                grounding_spans = [
                    GroundingSpan(
                        doc_id=str(s.get("doc_id", "")),
                        span_text=str(s.get("span_text", "")),
                        start=int(s.get("start", 0)),
                        end=int(s.get("end", 0)),
                    )
                    for s in spans_raw
                    if isinstance(s, dict)
                ]

                turns.append(
                    MD2DTurn(
                        turn_id=str(turn_data.get("turn_id", idx)),
                        user_utterance=str(turn_data.get("user_utterance", "")),
                        gold_response=str(turn_data.get("response", "")),
                        grounding_spans=grounding_spans,
                    )
                )

            conversations.append(
                MD2DConversation(
                    conversation_id=conv_id,
                    domain=domain,
                    turns=turns,
                )
            )

        return conversations
