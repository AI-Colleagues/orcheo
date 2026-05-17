"""Query processing nodes for conversational search pipelines."""

from __future__ import annotations
from typing import Any
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from agentensor.tensor import TextTensor
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.base import TaskNode
from orcheo.nodes.rag.models import SearchResult
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.runtime.chat_models import normalize_chat_model_kwargs
from orcheo.tracing.model_metadata import (
    build_ai_trace_metadata,
    infer_model_name_from_instance,
)


def _normalize_messages(history: list[Any]) -> list[str]:
    messages: list[str] = []
    for entry in history:
        if isinstance(entry, str):
            content = entry
        elif isinstance(entry, dict):
            content = str(entry.get("content", "")).strip()
        else:
            continue
        if content:
            messages.append(content)
    return messages


@registry.register(
    NodeMetadata(
        name="QueryRewriteNode",
        description=(
            "Rewrite or expand a query using recent conversation context to"
            " improve recall."
        ),
        category="conversational_search",
    )
)
class QueryRewriteNode(AgentNode):
    """Rewrite queries using conversation history and an AI model."""

    system_prompt: str | TextTensor | None = (
        "You are a query rewriter for a conversational search system. "
        "Given the conversation context and the current user query, rewrite "
        "the query to be self-contained by resolving any coreferences, ellipses, "
        "or implicit references to prior conversation. Output only the rewritten "
        "query with no explanation."
    )

    query_key: str = Field(
        default="message",
        description="Key within ``state.inputs`` holding the user message.",
    )
    history_key: str = Field(
        default="history",
        description="Key within ``state.inputs`` containing conversation history.",
    )
    max_history_messages: int | str = Field(
        default=3, description="Number of prior messages to consider."
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Rewrite queries using an AI model with recent conversation history."""
        inputs = state.get("inputs", {})
        query = (
            inputs.get(self.query_key)
            or inputs.get("message")
            or inputs.get("user_message")
            or inputs.get("query")
        )
        if not isinstance(query, str) or not query.strip():
            msg = "QueryRewriteNode requires a non-empty query string"
            raise ValueError(msg)

        history = state.get("inputs", {}).get(self.history_key, []) or []
        if not isinstance(history, list):
            msg = "history must be a list of messages"
            raise ValueError(msg)

        messages = _normalize_messages(history)[-int(self.max_history_messages) :]
        context = " ".join(messages)

        if not context:
            return {
                "original_query": query,
                "query": query.strip(),
                "used_history": False,
                "context": "",
            }

        model = init_chat_model(
            self.ai_model,
            **normalize_chat_model_kwargs(self.ai_model, self.model_kwargs),
        )
        prompt = self._system_prompt_text
        llm_messages = [
            SystemMessage(content=prompt),
            HumanMessage(
                content=(f"Conversation context: {context}\n\nCurrent query: {query}")
            ),
        ]
        response = await model.ainvoke(llm_messages)  # type: ignore[arg-type]
        self._set_trace_metadata_for_run(
            {
                "ai": build_ai_trace_metadata(
                    kind="llm",
                    requested_model=self.ai_model,
                    actual_model=infer_model_name_from_instance(model),
                )
            }
        )
        rewritten = str(getattr(response, "content", response)).strip()
        if not rewritten:
            rewritten = query.strip()

        return {
            "original_query": query,
            "query": rewritten,
            "used_history": True,
            "context": context,
        }


@registry.register(
    NodeMetadata(
        name="CoreferenceResolverNode",
        description="Resolve simple pronouns using prior conversation turns.",
        category="conversational_search",
    )
)
class CoreferenceResolverNode(TaskNode):
    """Resolve pronouns in queries using the latest referenced entity."""

    query_key: str = Field(
        default="message",
        description="Key within ``state.inputs`` holding the user message.",
    )
    history_key: str = Field(
        default="history",
        description="Key within ``state.inputs`` containing conversation history.",
    )
    pronouns: set[str] = Field(
        default_factory=lambda: {"it", "they", "this", "that", "those", "them"},
        description="Pronouns that should be resolved when context exists.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Replace pronouns in the query using a recent referent if available."""
        inputs = state.get("inputs", {})
        query = (
            inputs.get(self.query_key)
            or inputs.get("message")
            or inputs.get("user_message")
            or inputs.get("query")
        )
        if not isinstance(query, str) or not query.strip():
            msg = "CoreferenceResolverNode requires a non-empty query string"
            raise ValueError(msg)

        history = state.get("inputs", {}).get(self.history_key, []) or []
        if not isinstance(history, list):
            msg = "history must be a list of messages"
            raise ValueError(msg)

        referent = self._last_referent(history)
        resolved_query, resolved = self._resolve(query, referent)

        return {
            "query": resolved_query,
            "resolved": resolved,
            "antecedent": referent if resolved else None,
        }

    def _last_referent(self, history: list[Any]) -> str | None:
        messages = _normalize_messages(history)
        if not messages:
            return None
        return messages[-1]

    def _resolve(self, query: str, referent: str | None) -> tuple[str, bool]:
        if not referent:
            return query.strip(), False

        tokens = query.strip().split()
        resolved = False
        for index, token in enumerate(tokens):
            stripped = token.rstrip(".,?!").lower()
            if stripped in self.pronouns:
                suffix = token[len(stripped) :]
                tokens[index] = f"{referent}{suffix}"
                resolved = True
                break
        return " ".join(tokens), resolved


@registry.register(
    NodeMetadata(
        name="QueryClassifierNode",
        description="Classify a query intent to support routing decisions.",
        category="conversational_search",
    )
)
class QueryClassifierNode(TaskNode):
    """Heuristic classifier for determining query intent."""

    query_key: str = Field(
        default="message",
        description="Key within ``state.inputs`` holding the user message.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Classify the query as search, clarification, or finalization."""
        inputs = state.get("inputs", {})
        query = (
            inputs.get(self.query_key)
            or inputs.get("message")
            or inputs.get("user_message")
            or inputs.get("query")
        )
        if not isinstance(query, str) or not query.strip():
            msg = "QueryClassifierNode requires a non-empty query string"
            raise ValueError(msg)

        normalized = query.strip().lower()
        classification = "search"
        confidence = 0.6

        if any(
            token in normalized for token in {"clarify", "more detail", "which one"}
        ):
            classification = "clarification"
            confidence = 0.8
        elif normalized.startswith(("thanks", "thank you", "that helps")):
            classification = "finalization"
            confidence = 0.9

        return {"classification": classification, "confidence": confidence}


@registry.register(
    NodeMetadata(
        name="ContextCompressorNode",
        description=(
            "Summarize retrieved context using an AI model so downstream nodes can "
            "consume a condensed evidence block."
        ),
        category="conversational_search",
    )
)
class ContextCompressorNode(TaskNode):
    """Summarize retrieval results into a compact evidence block."""

    query_key: str = Field(
        default="message",
        description="Key within ``state.inputs`` that holds the active message.",
    )
    results_field: str = Field(
        default="retrieval_results",
        description="Key in ``state.results`` that holds retrieval payloads.",
    )
    max_tokens: int | str = Field(
        default=400,
        description="Maximum whitespace token budget for fallback summaries.",
    )
    max_passages: int | str = Field(
        default=8,
        description="Maximum number of passages to feed into the summarizer.",
    )
    deduplicate: bool = Field(
        default=True, description="Whether to drop duplicate result identifiers."
    )
    ai_model: str | None = Field(
        default=None,
        description=(
            "Optional chat model identifier (e.g., 'openai:gpt-4o-mini') used to "
            "summarize the retrieved context."
        ),
    )
    model_kwargs: dict[str, Any] | str = Field(
        default_factory=dict,
        description="Additional keyword arguments supplied to ``init_chat_model``.",
    )
    summary_prompt: str = Field(
        default=(
            "You are a retrieval summarizer. Given a user query and retrieved "
            "passages, write a concise paragraph (<= {max_tokens} tokens) that "
            "captures the key facts needed to answer the query. Mention important "
            "sources inline using their numeric identifiers (e.g., [1])."
        ),
        description="Prompt prefix for AI-based summarization.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Summarize retrieved passages into a compact evidence block."""
        entries = self._resolve_entries(state)
        if not entries:
            return {"results": [], "summary": "", "original_results": []}

        query = self._resolve_query(state)
        trimmed = entries[: int(self.max_passages)]
        summary_text = await self._summarize(query, trimmed)

        summary_result = SearchResult(
            id="context-summary",
            score=1.0,
            text=summary_text,
            metadata={
                "source_ids": [entry.id for entry in trimmed],
                "summary": True,
            },
            source="summary",
            sources=self._collect_sources(trimmed),
        )

        return {
            "results": [summary_result],
            "summary": summary_text,
            "original_results": trimmed,
            "source_count": len(summary_result.sources),
        }

    def _resolve_entries(self, state: State) -> list[SearchResult]:
        results_payload = state.get("results", {}).get(self.results_field)
        if results_payload is None:
            msg = "ContextCompressorNode requires retrieval results to summarize"
            raise ValueError(msg)

        if isinstance(results_payload, dict) and "results" in results_payload:
            entries = results_payload["results"]
        else:
            entries = results_payload

        if not isinstance(entries, list):
            msg = "retrieval results must be provided as a list"
            raise ValueError(msg)

        normalized = [
            SearchResult.model_validate(item) for item in entries if item is not None
        ]

        sorted_results = sorted(normalized, key=lambda item: item.score, reverse=True)
        if not self.deduplicate:
            return sorted_results

        deduped: list[SearchResult] = []
        seen_ids: set[str] = set()
        for entry in sorted_results:
            if entry.id in seen_ids:
                continue
            deduped.append(entry)
            seen_ids.add(entry.id)
        return deduped

    def _resolve_query(self, state: State) -> str:
        inputs = state.get("inputs", {})
        query = (
            inputs.get(self.query_key)
            or inputs.get("message")
            or inputs.get("user_message")
            or inputs.get("query")
            or ""
        )
        return str(query).strip()

    async def _summarize(self, query: str, entries: list[SearchResult]) -> str:
        context_block = self._build_context_block(entries)
        if not context_block:
            return ""

        if self.ai_model:
            return await self._summarize_with_model(query, context_block)

        return self._fallback_summary(context_block)

    def _build_context_block(self, entries: list[SearchResult]) -> str:
        lines = []
        for index, entry in enumerate(entries, start=1):
            snippet = entry.text.strip().replace("\n", " ")
            lines.append(f"[{index}] {snippet}")
        return "\n".join(lines)

    async def _summarize_with_model(self, query: str, context_block: str) -> str:
        if not self.ai_model:
            msg = "AI model identifier is required for model-based summarization"
            raise ValueError(msg)
        kwargs = self.model_kwargs if isinstance(self.model_kwargs, dict) else {}
        model = init_chat_model(
            self.ai_model,
            **normalize_chat_model_kwargs(self.ai_model, kwargs),
        )

        prompt = (
            "User Query: {query}\n\nRetrieved Context:\n{context}\n\n"
            "Summaries must stay within {max_tokens} tokens."
        ).format(
            query=query or "[unknown]",
            context=context_block,
            max_tokens=self.max_tokens,
        )

        messages = [
            SystemMessage(
                content=self.summary_prompt.format(max_tokens=self.max_tokens)
            ),
            HumanMessage(content=prompt),
        ]
        response = await model.ainvoke(messages)  # type: ignore[arg-type]
        self._set_trace_metadata_for_run(
            {
                "ai": build_ai_trace_metadata(
                    kind="llm",
                    requested_model=self.ai_model,
                    actual_model=infer_model_name_from_instance(model),
                )
            }
        )
        content = getattr(response, "content", response)
        text = str(content).strip()
        if not text:
            msg = "Summarizer model returned an empty response"
            raise ValueError(msg)
        return text

    def _fallback_summary(self, context_block: str) -> str:
        tokens = context_block.split()
        if len(tokens) <= int(self.max_tokens):
            return context_block
        truncated = tokens[: int(self.max_tokens)]
        return " ".join(truncated) + " …"

    @staticmethod
    def _collect_sources(entries: list[SearchResult]) -> list[str]:
        sources: list[str] = []
        for entry in entries:
            if entry.source and entry.source not in sources:
                sources.append(entry.source)
        return sources or ["retrieval"]


@registry.register(
    NodeMetadata(
        name="MultiHopPlannerNode",
        description="Derive sequential sub-queries for multi-hop answering.",
        category="conversational_search",
    )
)
class MultiHopPlannerNode(TaskNode):
    """Decompose complex queries into sequential hop plans."""

    query_key: str = Field(
        default="message", description="Key within inputs containing the user message"
    )
    max_hops: int | str = Field(default=3)
    delimiter: str = Field(default=" and ", description="Delimiter used for splitting")

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Derive sequential hop plan from a composite query."""
        inputs = state.get("inputs", {})
        query = (
            inputs.get(self.query_key)
            or inputs.get("message")
            or inputs.get("user_message")
            or inputs.get("query")
        )
        if not isinstance(query, str) or not query.strip():
            msg = "MultiHopPlannerNode requires a non-empty query"
            raise ValueError(msg)

        raw_parts = [
            part.strip() for part in query.split(self.delimiter) if part.strip()
        ]
        if not raw_parts:
            raw_parts = [query.strip()]

        max_hops_int = int(self.max_hops)
        hops: list[dict[str, Any]] = []
        for index, part in enumerate(raw_parts[:max_hops_int]):
            hops.append(
                {
                    "id": f"hop-{index + 1}",
                    "query": part,
                    "depends_on": hops[-1]["id"] if hops else None,
                }
            )

        return {"plan": hops, "hop_count": len(hops)}
