"""Grounded generation node for conversational search pipelines."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any, Literal
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.rag.models import SearchResult
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.runtime.chat_models import normalize_chat_model_kwargs
from orcheo.tracing.model_metadata import (
    build_ai_trace_metadata,
    infer_chat_result_model_name,
    infer_model_name_from_instance,
)


def _truncate_snippet(text: str, limit: int = 160) -> str:
    snippet = text.strip().replace("\n", " ")
    if limit <= 0:
        return ""
    if len(snippet) <= limit:
        return snippet

    ellipsis = "…"
    if limit <= len(ellipsis):
        return ellipsis if limit >= len(ellipsis) else snippet[:limit]

    available = limit - len(ellipsis)
    truncated = snippet[:available].rstrip()
    if not truncated:
        return ellipsis
    return f"{truncated}{ellipsis}"


@registry.register(
    NodeMetadata(
        name="GroundedGeneratorNode",
        description="Generate grounded answers with citations and retry semantics.",
        category="conversational_search",
    )
)
class GroundedGeneratorNode(TaskNode):
    """Node that generates grounded responses using retrieved context."""

    query_key: str = Field(
        default="message",
        description="Key within ``state.inputs`` holding the user message.",
    )
    context_result_key: str = Field(
        default="retriever",
        description="Name of the upstream result entry containing retrieval output.",
    )
    context_field: str = Field(
        default="results",
        description=(
            "Field name under the retrieval result that stores SearchResult items."
        ),
    )
    system_prompt: str = Field(
        default=(
            "You are a grounded answer generator. Use the provided context to answer "
            "the user's query and include citation markers referencing the context "
            "entries."
        ),
        description="Instruction prefix prepended to the prompt.",
    )
    citation_style: Literal["inline", "footnote", "endnote"] | str = Field(
        default="inline", description="Style hint for formatting citations."
    )
    ai_model: str | None = Field(
        default=None,
        description=(
            "Optional model identifier (e.g., 'gpt-4', 'claude-3-5-sonnet-latest'). "
            "When specified, an agent is created using langchain.agents.create_agent. "
            "A deterministic fallback is used when omitted."
        ),
    )
    model_kwargs: dict[str, Any] | str = Field(
        default_factory=dict,
        description="Additional keyword arguments passed to init_chat_model.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Generate a grounded response with citations and retry semantics.

        If no context is available, generates a response without RAG or citations.
        """
        inputs = state.get("inputs", {})
        query = (
            inputs.get(self.query_key)
            or inputs.get("message")
            or inputs.get("user_message")
            or inputs.get("query")
        )
        if not isinstance(query, str) or not query.strip():
            msg = "GroundedGeneratorNode requires a non-empty query string"
            raise ValueError(msg)

        # Extract conversation history from inputs (provided by ChatKit)
        history = inputs.get("history", [])

        context = self._resolve_context(state)

        # Handle non-RAG mode when no context is available
        if not context:
            completion = await self._generate_with_retries(
                query=query.strip(),
                history=history,
                context=None,
            )
            tokens_used = self._estimate_tokens_from_history(history, query, completion)
            return {
                "reply": completion,
                "citations": [],
                "tokens_used": tokens_used,
                "citation_style": self.citation_style,
                "mode": "non_rag",
            }

        # RAG mode with context and citations
        completion = await self._generate_with_retries(
            query=query.strip(),
            history=history,
            context=context,
        )

        citations = self._build_citations(context)
        response = self._attach_citations(completion, citations)
        tokens_used = self._estimate_tokens_from_history(history, query, response)

        return {
            "reply": response,
            "citations": citations,
            "tokens_used": tokens_used,
            "citation_style": self.citation_style,
            "mode": "rag",
        }

    def _resolve_context(self, state: State) -> list[SearchResult]:
        results = state.get("results", {})
        source = results.get(self.context_result_key, {})
        if isinstance(source, dict) and self.context_field in source:
            entries = source[self.context_field]
        else:
            entries = results.get(self.context_field)
        if not entries:
            return []
        if not isinstance(entries, list):
            msg = "Context payload must be a list of retrieval results"
            raise ValueError(msg)
        return [SearchResult.model_validate(item) for item in entries]

    async def _generate_with_retries(
        self,
        query: str,
        history: list[dict] | None = None,
        context: list[SearchResult] | None = None,
    ) -> str:
        return await self._invoke_ai_model(query, history, context)

    def _build_system_message(self, context: list[SearchResult] | None) -> str:
        """Build system message content based on context availability."""
        if context:
            # RAG mode system prompt with context
            context_block = "\n".join(
                f"[{index}] {entry.text}"
                for index, entry in enumerate(context, start=1)
            )
            return (
                f"{self.system_prompt}\n\n"
                f"Context:\n{context_block}\n\n"
                f"Cite sources in {self.citation_style} style using the provided "
                "identifiers."
            )
        # Non-RAG mode system prompt
        return (
            "You are a helpful assistant. Answer the user's question directly "
            "based on your knowledge."
        )

    def _add_history_to_messages(
        self,
        messages: list[Any],
        history: list[dict] | None,
    ) -> None:
        """Add conversation history to messages list."""
        from langchain_core.messages import AIMessage, HumanMessage

        if not history or not isinstance(history, list):
            return

        for turn in history:
            if not isinstance(turn, dict):
                continue
            role = turn.get("role", "")
            content = turn.get("content", "")
            if role == "user" and content:
                messages.append(HumanMessage(content=content))
            elif role == "assistant" and content:
                messages.append(AIMessage(content=content))

    def _extract_response_text(self, result: Any) -> str:
        """Extract text content from agent result."""
        if isinstance(result, dict) and "messages" in result:
            last_message = result["messages"][-1]
            if hasattr(last_message, "content"):
                return last_message.content
            if isinstance(last_message, dict):
                return last_message.get("content", "")
            return str(last_message)
        return str(result)

    async def _invoke_ai_model(
        self,
        query: str,
        history: list[dict] | None = None,
        context: list[SearchResult] | None = None,
    ) -> str:
        if not self.ai_model:
            return self._default_ai_model(query, context)

        from langchain_core.messages import HumanMessage, SystemMessage

        # Initialize chat model
        kwargs = self.model_kwargs if isinstance(self.model_kwargs, dict) else {}
        model = init_chat_model(
            self.ai_model,
            **normalize_chat_model_kwargs(self.ai_model, kwargs),
        )

        # Build messages list
        messages: list[Any] = []
        system_content = self._build_system_message(context)
        messages.append(SystemMessage(content=system_content))

        # Add conversation history
        self._add_history_to_messages(messages, history)

        # Add current query
        messages.append(HumanMessage(content=query))

        # Create and invoke agent
        agent: Runnable = create_agent(model, tools=[], system_prompt="")
        result = await agent.ainvoke({"messages": messages})  # type: ignore[arg-type]
        actual_model = (
            infer_chat_result_model_name(result)
            if isinstance(result, Mapping)
            else None
        )
        if actual_model is None:  # pragma: no branch
            actual_model = infer_model_name_from_instance(model)
        self._set_trace_metadata_for_run(
            {
                "ai": build_ai_trace_metadata(
                    kind="llm",
                    requested_model=self.ai_model,
                    actual_model=actual_model,
                )
            }
        )

        # Extract and validate response
        text = self._extract_response_text(result)
        if not isinstance(text, str) or not text.strip():
            msg = "Agent must return a non-empty string response"
            raise ValueError(msg)
        return text.strip()

    def _default_ai_model(
        self, query: str, context: list[SearchResult] | None = None
    ) -> str:
        if context:
            # Include citation markers for each context entry
            markers = " ".join(f"[{i}]" for i in range(1, len(context) + 1))
            return f"{query}\n\nResponse: See cited context for details. {markers}"
        return f"{query}\n\nResponse: [Default response]"

    def _build_citations(self, context: list[SearchResult]) -> list[dict[str, Any]]:
        citations: list[dict[str, Any]] = []
        for index, entry in enumerate(context, start=1):
            citations.append(
                {
                    "id": str(index),
                    "source_id": entry.id,
                    "snippet": _truncate_snippet(entry.text),
                    "sources": entry.sources
                    or ([entry.source] if entry.source else []),
                    "metadata": entry.metadata,
                }
            )
        return citations

    def _attach_citations(
        self, completion: str, citations: list[dict[str, Any]]
    ) -> str:
        """Attach citation markers for non-inline styles."""
        if not citations:
            return completion

        style = str(self.citation_style).strip().lower()
        if style == "inline":
            return completion

        markers = " ".join(f"[{citation['id']}]" for citation in citations)
        return f"{completion.rstrip()} {markers}"

    @staticmethod
    def _estimate_tokens(prompt: str, completion: str) -> int:
        return len((prompt + completion).split())

    @staticmethod
    def _estimate_tokens_from_history(
        history: list[dict] | None, query: str, completion: str
    ) -> int:
        """Estimate token count from history, query, and completion."""
        total_text = query + " " + completion
        if history and isinstance(history, list):
            for turn in history:
                if isinstance(turn, dict):
                    content = turn.get("content", "")
                    if content:
                        total_text += " " + content
        return len(total_text.split())


@registry.register(
    NodeMetadata(
        name="StreamingGeneratorNode",
        description="Generate responses and stream token chunks with backpressure.",
        category="conversational_search",
    )
)
class StreamingGeneratorNode(TaskNode):
    """Node that streams model output into bounded frames."""

    prompt_key: str = Field(
        default="prompt", description="Key under inputs containing the prompt."
    )
    chunk_size: int | str = Field(
        default=8, description="Maximum tokens per emitted frame"
    )
    buffer_limit: int | str | None = Field(
        default=64,
        description="Optional backpressure cap on total tokens streamed.",
    )
    ai_model: str | None = Field(
        default=None,
        description=(
            "Optional model identifier (e.g., 'gpt-4', 'claude-3-5-sonnet-latest'). "
            "When specified, an agent is created using langchain.agents.create_agent."
        ),
    )
    model_kwargs: dict[str, Any] | str = Field(
        default_factory=dict,
        description="Additional keyword arguments passed to init_chat_model.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Stream a generated response into framed chunks with retries."""
        inputs = state.get("inputs", {})

        prompt = inputs.get(self.prompt_key)
        message = inputs.get("message")
        query = prompt or message

        if not isinstance(query, str) or not query.strip():
            msg = "StreamingGeneratorNode requires a non-empty prompt or message"
            raise ValueError(msg)

        # Extract conversation history from inputs (if available)
        history = inputs.get("history", [])

        completion = await self._generate_with_retries(query.strip(), history)
        stream, frames, truncated = self._stream_tokens(completion)
        return {
            "reply": completion,
            "stream": stream,
            "frames": frames,
            "token_count": len(stream),
            "truncated": truncated,
        }

    async def _generate_with_retries(
        self, query: str, history: list[dict] | None = None
    ) -> str:
        return await self._invoke_ai_model(query, history)

    def _build_streaming_messages(
        self, query: str, history: list[dict] | None = None
    ) -> list[Any]:
        """Build messages list for streaming generation."""
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        messages: list[Any] = [SystemMessage(content="You are a helpful assistant.")]

        # Add conversation history as proper message objects
        if history and isinstance(history, list):
            for turn in history:
                if not isinstance(turn, dict):
                    continue
                role = turn.get("role", "")
                content = turn.get("content", "")
                if role == "user" and content:
                    messages.append(HumanMessage(content=content))
                elif role == "assistant" and content:
                    messages.append(AIMessage(content=content))

        # Add current query
        messages.append(HumanMessage(content=query))
        return messages

    async def _invoke_ai_model(
        self, query: str, history: list[dict] | None = None
    ) -> str:
        if not self.ai_model:
            return self._default_ai_model(query)

        # Initialize chat model
        kwargs = self.model_kwargs if isinstance(self.model_kwargs, dict) else {}
        model = init_chat_model(
            self.ai_model,
            **normalize_chat_model_kwargs(self.ai_model, kwargs),
        )

        # Build messages list
        messages = self._build_streaming_messages(query, history)

        # Create and invoke agent
        agent: Runnable = create_agent(model, tools=[], system_prompt="")
        result = await agent.ainvoke({"messages": messages})  # type: ignore[arg-type]
        actual_model = (
            infer_chat_result_model_name(result)
            if isinstance(result, Mapping)
            else None
        )
        if actual_model is None:  # pragma: no branch
            actual_model = infer_model_name_from_instance(model)
        self._set_trace_metadata_for_run(
            {
                "ai": build_ai_trace_metadata(
                    kind="llm",
                    requested_model=self.ai_model,
                    actual_model=actual_model,
                )
            }
        )

        # Extract text from the last message
        if isinstance(result, dict) and "messages" in result:
            last_message = result["messages"][-1]
            if hasattr(last_message, "content"):
                text = last_message.content
            elif isinstance(last_message, dict):
                text = last_message.get("content", "")
            else:
                text = str(last_message)
        else:
            text = str(result)

        if not isinstance(text, str) or not text.strip():
            msg = "Agent must return a non-empty string response"
            raise ValueError(msg)
        return text.strip()

    def _stream_tokens(
        self, completion: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        tokens = completion.split()
        truncated = False
        buffer_limit_int = (
            int(self.buffer_limit) if self.buffer_limit is not None else None
        )
        if buffer_limit_int and len(tokens) > buffer_limit_int:
            tokens = tokens[:buffer_limit_int]
            truncated = True

        chunk_size_int = int(self.chunk_size)
        stream: list[dict[str, Any]] = []
        frames: list[dict[str, Any]] = []
        buffer: list[str] = []
        for index, token in enumerate(tokens):
            stream.append({"index": index, "token": token})
            buffer.append(token)
            if len(buffer) == chunk_size_int:
                frames.append(
                    {
                        "index": len(frames),
                        "chunk": " ".join(buffer),
                        "size": len(buffer),
                    }
                )
                buffer = []
        if buffer:
            frames.append(
                {
                    "index": len(frames),
                    "chunk": " ".join(buffer),
                    "size": len(buffer),
                }
            )
        return stream, frames, truncated

    def _default_ai_model(self, query: str) -> str:
        return f"{query} :: streamed"


@registry.register(
    NodeMetadata(
        name="HallucinationGuardNode",
        description="Validate generator output for citations and completeness.",
        category="conversational_search",
    )
)
class HallucinationGuardNode(TaskNode):
    """Node that blocks responses missing citations or context alignment."""

    generator_result_key: str = Field(
        default="grounded_generator",
        description="Result entry containing model output and citations.",
    )
    response_field: str = Field(
        default="reply", description="Field containing the model response"
    )
    citations_field: str = Field(
        default="citations", description="Field containing citations metadata"
    )
    require_markers: bool = Field(
        default=True,
        description="Whether citation markers like [1] must appear in the response.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Validate generator output includes citations and markers."""
        payload = self._resolve_payload(state)
        response = self._extract_response(payload)
        citations = self._extract_citations(payload)

        if not citations:
            return self._block("Missing citations for generated response")

        missing_markers = self._missing_markers(response, citations)
        if missing_markers:
            reason = "Response is missing citation markers for ids: " + ", ".join(
                sorted(missing_markers)
            )
            return self._block(reason)

        if any(not self._has_snippet(citation) for citation in citations):
            return self._block("Citation entries must include snippets")

        return {
            "allowed": True,
            "reply": response,
            "citations": citations,
        }

    def _resolve_payload(self, state: State) -> dict[str, Any]:
        payload = state.get("results", {}).get(self.generator_result_key, {})
        if not isinstance(payload, dict):
            msg = "HallucinationGuardNode requires a mapping payload"
            raise ValueError(msg)
        return payload

    def _extract_response(self, payload: dict[str, Any]) -> str:
        response = payload.get(self.response_field)
        if not isinstance(response, str) or not response.strip():
            msg = "Response payload is missing or empty"
            raise ValueError(msg)
        return response.strip()

    def _extract_citations(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        citations = payload.get(self.citations_field)
        if not isinstance(citations, list) or not citations:
            return []
        normalized: list[dict[str, Any]] = []
        for citation in citations:
            if not isinstance(citation, dict):
                msg = "Citations must be dictionaries"
                raise ValueError(msg)
            normalized.append(citation)
        return normalized

    def _missing_markers(
        self, response: str, citations: list[dict[str, Any]]
    ) -> list[str]:
        if not self.require_markers:
            return []
        missing: list[str] = []
        for citation in citations:
            marker = citation.get("id")
            if marker is None:
                continue
            if f"[{marker}]" not in response:
                missing.append(str(marker))
        return missing

    @staticmethod
    def _has_snippet(citation: dict[str, Any]) -> bool:
        snippet = citation.get("snippet") if isinstance(citation, dict) else None
        return bool(snippet)

    def _block(self, reason: str) -> dict[str, Any]:
        fallback = "Unable to provide an answer with proper grounding."
        return {
            "allowed": False,
            "reason": reason,
            "fallback_response": fallback,
            "reply": fallback,
        }


@registry.register(
    NodeMetadata(
        name="CitationsFormatterNode",
        description="Format citation metadata into human-readable strings.",
        category="conversational_search",
    )
)
class CitationsFormatterNode(TaskNode):
    """Node that normalizes and formats citation payloads."""

    source_result_key: str = Field(
        default="grounded_generator",
        description="Result entry containing citations to format.",
    )
    citations_field: str = Field(default="citations")
    include_sources: bool = Field(
        default=True, description="Include source identifiers when available"
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Format citations into human-readable strings."""
        payload = state.get("results", {}).get(self.source_result_key, {})
        if isinstance(payload, dict) and self.citations_field in payload:
            citations = payload[self.citations_field]
        else:
            citations = payload
        if not isinstance(citations, list):
            msg = "CitationsFormatterNode requires a list of citations"
            raise ValueError(msg)

        formatted: list[str] = []
        normalized: list[dict[str, Any]] = []
        references: list[dict[str, str]] = []
        for citation in citations:
            if not isinstance(citation, dict):
                msg = "Citation entries must be mappings"
                raise ValueError(msg)
            citation_id = citation.get("id") or str(len(formatted) + 1)
            snippet = citation.get("snippet", "").strip()
            raw_sources = citation.get("sources")
            if isinstance(raw_sources, list):
                sources = [str(item) for item in raw_sources if item is not None]
            elif raw_sources is None:
                sources = []
            else:
                sources = [str(raw_sources)]
            metadata = citation.get("metadata")
            normalized_metadata = metadata if isinstance(metadata, dict) else {}
            normalized.append(
                {
                    "id": str(citation_id),
                    "snippet": snippet,
                    "sources": sources,
                    "source_id": citation.get("source_id"),
                    "metadata": normalized_metadata,
                }
            )
            base_text = snippet or str(citation_id)
            external_url = self._resolve_reference_url(citation, normalized_metadata)
            show_sources = bool(sources and self.include_sources)
            suffix = self._format_citation_suffix(
                sources if show_sources else [], external_url
            )
            formatted_text = f"[{citation_id}] {base_text}{suffix}".strip()
            formatted.append(formatted_text)
            references.append(
                {
                    "id": str(citation_id),
                    "line": formatted_text,
                }
            )

        base_reply = ""
        if isinstance(payload, dict):
            raw_reply = payload.get("reply")
            if isinstance(raw_reply, str):  # pragma: no branch
                base_reply = raw_reply
        cited_refs = (
            [ref for ref in references if f"[{ref['id']}]" in base_reply]
            if base_reply
            else references
        )
        reply = self._build_markdown_reply(base_reply, cited_refs)

        self._overwrite_source_reply(state, reply, normalized)

        return {"reply": reply, "formatted": formatted, "citations": normalized}

    def _overwrite_source_reply(
        self, state: State, reply: str, citations: list[dict[str, Any]]
    ) -> None:
        """Update the original source payload with the formatted reply."""
        results = state.get("results")
        if not isinstance(results, dict):
            return
        source_payload = results.get(self.source_result_key)
        if not isinstance(source_payload, dict):
            return
        source_payload["reply"] = reply
        source_payload["citations"] = citations

    @staticmethod
    def _format_citation_suffix(sources: list[str], external_url: str | None) -> str:
        """Build the parenthesized suffix for a single citation line."""
        parts: list[str] = []
        if sources:
            parts.append(f"sources: {', '.join(sources)}")
        if external_url:
            parts.append(f"[source]({external_url})")
        if not parts:
            return ""
        return f" ({' | '.join(parts)})"

    def _resolve_reference_url(
        self, citation: dict[str, Any], metadata: dict[str, Any]
    ) -> str | None:
        """Prefer a URL from citation metadata or the citation dictionary."""
        url_fields = (
            "url",
            "link",
            "source_url",
            "permalink",
            "href",
            "source",
        )
        candidates: list[str | None] = []
        for field in url_fields:
            candidate = citation.get(field)
            if candidate is None:  # pragma: no branch
                candidate = metadata.get(field)
            candidates.append(candidate)
        source_id = citation.get("source_id")
        candidates.append(source_id)

        for candidate in candidates:
            if isinstance(candidate, str):
                trimmed = candidate.strip()
                if trimmed.startswith(("http://", "https://")):  # pragma: no branch
                    return trimmed
        return None

    def _build_markdown_reply(
        self, base_reply: str, references: list[dict[str, str]]
    ) -> str:
        """Return the markdown reply that appends formatted reference entries."""
        sections: list[str] = []
        trimmed = base_reply.strip()
        if trimmed:
            sections.append(trimmed)
        if references:  # pragma: no branch
            lines = [f"- {ref['line']}" for ref in references]
            sections.append("References:\n" + "\n".join(lines))
        return "\n\n".join(sections).strip()


@registry.register(
    NodeMetadata(
        name="SearchResultFormatterNode",
        description="Format SearchResult entries into markdown for tool responses.",
        category="conversational_search",
    )
)
class SearchResultFormatterNode(TaskNode):
    """Node that renders search results as a readable markdown list."""

    source_result_key: str = Field(
        default="retriever",
        description="Result entry containing retrieval output.",
    )
    results_field: str = Field(
        default="results", description="Field containing SearchResult entries."
    )
    output_key: str = Field(
        default="markdown", description="Key used to store formatted markdown."
    )
    header: str = Field(
        default="Search results:",
        description="Header text included before the formatted entries.",
    )
    empty_message: str = Field(
        default="No results found.",
        description="Message returned when no results are available.",
    )
    include_score: bool = Field(
        default=True, description="Include scores in the formatted output."
    )
    score_precision: int | str = Field(
        default=3,
        description="Decimal precision for score rounding.",
    )
    max_results: int | None = Field(
        default=None,
        gt=0,
        description="Optional maximum number of entries to include.",
    )
    title_fields: list[str] = Field(
        default_factory=lambda: ["title", "name", "platform_name"],
        description="Metadata fields to use for entry titles.",
    )
    snippet_fields: list[str] = Field(
        default_factory=lambda: [
            "snippet",
            "summary",
            "description",
            "recommendation_reason",
        ],
        description="Metadata fields to use for entry snippets.",
    )
    url_fields: list[str] = Field(
        default_factory=lambda: [
            "url",
            "link",
            "source_url",
            "permalink",
            "href",
            "source",
        ],
        description="Metadata fields to scan for source URLs.",
    )
    snippet_label: str = Field(
        default="Snippet",
        description="Label prefix applied to snippet lines.",
    )
    title_fallback: str = Field(
        default="Result {index}",
        description="Fallback format string when a title is missing.",
    )
    fallback_to_text: bool = Field(
        default=True,
        description="Use the SearchResult text when no snippet is found.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Render SearchResult entries into markdown."""
        entries = self._resolve_results(state)
        if not entries:
            return {self.output_key: self.empty_message}

        lines: list[str] = []
        if self.header:
            lines.append(self.header)

        max_results = self.max_results or len(entries)
        for index, entry in enumerate(entries[:max_results], start=1):
            metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
            title = self._pick_field(metadata, self.title_fields)
            if not title:
                title = self._format_title_fallback(entry, index)
            entry_line = f"{index}. {title}"
            if self.include_score:
                entry_line += f" (score: {self._format_score(entry.score)})"
            lines.append(entry_line)

            snippet = self._pick_field(metadata, self.snippet_fields)
            if not snippet and self.fallback_to_text:
                snippet = entry.text.strip()
            if snippet:
                lines.append(f"{self.snippet_label}: {snippet}")

            url = self._pick_field(metadata, self.url_fields)
            if url:
                lines.append(f"Source: {url}")
            lines.append("")

        if lines and not lines[-1]:  # pragma: no branch
            lines.pop()
        return {self.output_key: "\n".join(lines)}

    def _resolve_results(self, state: State) -> list[SearchResult]:
        results = state.get("results", {})
        payload = results.get(self.source_result_key, {})
        if isinstance(payload, dict) and self.results_field in payload:
            entries = payload[self.results_field]
        else:
            entries = payload
        if entries is None:
            return []
        if not isinstance(entries, list):
            msg = "SearchResultFormatterNode requires a list of retrieval results"
            raise ValueError(msg)
        return [
            SearchResult.model_validate(item) for item in entries if item is not None
        ]

    def _format_title_fallback(self, entry: SearchResult, index: int) -> str:
        try:
            return self.title_fallback.format(index=index, id=entry.id)
        except (KeyError, IndexError, ValueError):
            return f"Result {index}"

    @staticmethod
    def _pick_field(metadata: Mapping[str, Any], fields: list[str]) -> str | None:
        for field in fields:
            value = metadata.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _format_score(self, score: Any) -> str:
        if isinstance(score, int | float):
            precision = int(self.score_precision)
            return f"{score:.{precision}f}"
        return "n/a"
