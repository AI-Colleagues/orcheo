"""Conversation management and memory nodes for conversational search."""

from __future__ import annotations
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any, Literal
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field, model_validator
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


class MemoryTurn(BaseModel):
    """Representation of a single conversation turn."""

    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _trim_content(self) -> MemoryTurn:
        self.content = self.content.strip()
        if not self.content:
            msg = "MemoryTurn content cannot be empty"
            raise ValueError(msg)
        return self


class BaseMemoryStore(ABC, BaseModel):
    """Abstract contract for conversation memory backends."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @abstractmethod
    async def load_history(
        self, session_id: str, limit: int | None = None
    ) -> list[MemoryTurn]:
        """Return turns for ``session_id`` while honoring ``limit`` when provided."""

    @abstractmethod
    async def append_turn(self, session_id: str, turn: MemoryTurn) -> None:
        """Persist ``turn`` for the provided ``session_id``."""

    async def batch_append_turns(
        self, session_id: str, turns: Iterable[MemoryTurn]
    ) -> None:
        """Persist multiple ``turns`` for ``session_id`` using ``append_turn``.

        Stores can override for efficiency; the default implementation appends
        sequentially.
        """
        for turn in turns:
            await self.append_turn(session_id, turn)

    @abstractmethod
    async def prune(self, session_id: str, max_turns: int | None = None) -> None:
        """Remove oldest turns to enforce ``max_turns`` when specified."""

    @abstractmethod
    async def write_summary(
        self, session_id: str, summary: str, ttl_seconds: int | None = None
    ) -> None:
        """Persist ``summary`` with optional TTL."""

    @abstractmethod
    async def get_summary(self, session_id: str) -> str | None:
        """Return a persisted summary if present and not expired."""

    @abstractmethod
    async def clear(self, session_id: str) -> None:
        """Remove all state for ``session_id``."""


class InMemoryMemoryStore(BaseMemoryStore):
    """Simple in-memory store suited for local development and tests."""

    sessions: dict[str, list[MemoryTurn]] = Field(default_factory=dict)
    summaries: dict[str, tuple[str, float | None]] = Field(default_factory=dict)
    session_last_updated: dict[str, float] = Field(default_factory=dict)
    max_sessions: int | str | None = Field(
        default=None,
        description="Maximum concurrent sessions before evicting the stalest one.",
    )
    max_total_turns: int | str | None = Field(
        default=None,
        description="Global cap on stored turns across all sessions.",
    )

    async def load_history(
        self, session_id: str, limit: int | None = None
    ) -> list[MemoryTurn]:
        """Return stored turns for ``session_id`` with optional tail ``limit``."""
        history = self.sessions.get(session_id, [])
        if limit is None:
            return list(history)
        return list(history[-limit:])

    async def append_turn(self, session_id: str, turn: MemoryTurn) -> None:
        """Append ``turn`` to the session history."""
        await self._ensure_capacity(session_id, incoming_count=1)
        self.sessions.setdefault(session_id, []).append(turn)
        self.session_last_updated[session_id] = time.time()

    async def batch_append_turns(
        self, session_id: str, turns: Iterable[MemoryTurn]
    ) -> None:
        """Append multiple turns while enforcing store capacity."""
        turns_list = list(turns)
        if not turns_list:
            return
        await self._ensure_capacity(session_id, incoming_count=len(turns_list))
        self.sessions.setdefault(session_id, []).extend(turns_list)
        self.session_last_updated[session_id] = time.time()

    async def prune(self, session_id: str, max_turns: int | None = None) -> None:
        """Trim history to ``max_turns`` turns when provided."""
        if max_turns is None:
            return
        history = self.sessions.get(session_id)
        if history is None:
            return
        if len(history) > max_turns:
            self.sessions[session_id] = history[-max_turns:]

    async def write_summary(
        self, session_id: str, summary: str, ttl_seconds: int | None = None
    ) -> None:
        """Store ``summary`` with optional expiration."""
        expires_at = time.time() + ttl_seconds if ttl_seconds else None
        self.summaries[session_id] = (summary, expires_at)

    async def get_summary(self, session_id: str) -> str | None:
        """Return summary if available and not expired."""
        entry = self.summaries.get(session_id)
        if entry is None:
            return None
        summary, expires_at = entry
        if expires_at is not None and expires_at < time.time():
            self.summaries.pop(session_id, None)
            return None
        return summary

    async def clear(self, session_id: str) -> None:
        """Delete stored turns and summaries for ``session_id``."""
        self.sessions.pop(session_id, None)
        self.summaries.pop(session_id, None)
        self.session_last_updated.pop(session_id, None)

    def load_history_sync(
        self, session_id: str, limit: int | None = None
    ) -> list[MemoryTurn]:
        """Synchronous variant of :meth:`load_history` for local use."""
        history = self.sessions.get(session_id, [])
        if limit is None:
            return list(history)
        return list(history[-limit:])

    def append_turn_sync(self, session_id: str, turn: MemoryTurn) -> None:
        """Synchronous wrapper around :meth:`append_turn`."""
        self._ensure_capacity_sync(session_id, incoming_count=1)
        self.sessions.setdefault(session_id, []).append(turn)
        self.session_last_updated[session_id] = time.time()

    def clear_sync(self, session_id: str) -> None:
        """Synchronous wrapper around :meth:`clear`."""
        self.sessions.pop(session_id, None)
        self.summaries.pop(session_id, None)
        self.session_last_updated.pop(session_id, None)

    def _ensure_capacity_sync(self, session_id: str, incoming_count: int) -> None:
        now = time.time()
        self.session_last_updated.setdefault(session_id, now)

        if self.max_sessions is not None and session_id not in self.sessions:
            max_sessions_int = int(self.max_sessions)
            if len(self.sessions) >= max_sessions_int:
                self._evict_stalest_session_sync()

        if self.max_total_turns is not None:
            max_total_turns_int = int(self.max_total_turns)
            if max_total_turns_int > 0:
                current_turns = sum(len(turns) for turns in self.sessions.values())
                projected = current_turns + incoming_count
                while projected > max_total_turns_int and self.session_last_updated:
                    stalest_session = min(
                        self.session_last_updated,
                        key=lambda key: self.session_last_updated.get(key, 0.0),
                    )
                    turns_removed = len(self.sessions.get(stalest_session, []))
                    self.clear_sync(stalest_session)
                    projected -= turns_removed

    def _evict_stalest_session_sync(self) -> None:
        if not self.session_last_updated:
            return
        stalest_session = min(
            self.session_last_updated,
            key=lambda key: self.session_last_updated.get(key, 0.0),
        )
        self.clear_sync(stalest_session)

    async def _ensure_capacity(self, session_id: str, incoming_count: int) -> None:
        """Enforce session and global turn limits before appending."""
        now = time.time()
        self.session_last_updated.setdefault(session_id, now)

        if self.max_sessions is not None and session_id not in self.sessions:
            max_sessions_int = int(self.max_sessions)
            if len(self.sessions) >= max_sessions_int:
                await self._evict_stalest_session()

        if self.max_total_turns is not None:
            max_total_turns_int = int(self.max_total_turns)
            if max_total_turns_int > 0:
                current_turns = sum(len(turns) for turns in self.sessions.values())
                projected = current_turns + incoming_count
                while projected > max_total_turns_int and self.session_last_updated:
                    await self._evict_stalest_session()
                    projected = sum(len(turns) for turns in self.sessions.values())

    async def _evict_stalest_session(self) -> None:
        """Remove the least recently updated session to honor ``max_sessions``."""
        if not self.session_last_updated:
            return
        stalest_session = min(
            self.session_last_updated,
            key=lambda key: self.session_last_updated.get(key, 0.0),
        )
        await self.clear(stalest_session)


@registry.register(
    NodeMetadata(
        name="ConversationStateNode",
        description="Load and persist conversation history for a session.",
        category="conversational_search",
    )
)
class ConversationStateNode(TaskNode):
    """Manage per-session conversation turns with basic limits."""

    session_id_key: str = Field(
        default="session_id", description="Key in ``state.inputs`` with the session id."
    )
    user_message_key: str = Field(
        default="user_message",
        description="Key containing the latest user message to append.",
    )
    assistant_message_key: str = Field(
        default="assistant_message",
        description="Optional key containing an assistant response to persist.",
    )
    memory_store: BaseMemoryStore = Field(
        default_factory=InMemoryMemoryStore,
        description="Backing store used to load and persist conversation turns.",
    )
    max_turns: int | str = Field(
        default=50, description="Maximum number of turns retained per session."
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Load history, append new turns, and return updated session context."""
        session_id_raw = state.get("inputs", {}).get(self.session_id_key)
        if not isinstance(session_id_raw, str) or not session_id_raw.strip():
            msg = "ConversationStateNode requires a non-empty session id"
            raise ValueError(msg)
        session_id = session_id_raw.strip()

        max_turns = self._config_value(config, "max_turns", default=self.max_turns)
        history = await self.memory_store.load_history(session_id, limit=max_turns)

        append_candidates: list[tuple[str, Literal["user", "assistant"]]] = [
            (self.user_message_key, "user"),
            (self.assistant_message_key, "assistant"),
        ]
        turns_to_append = [
            MemoryTurn(role=role, content=message)
            for key, role in append_candidates
            if isinstance((message := state.get("inputs", {}).get(key)), str)
            and message.strip()
        ]
        if turns_to_append:
            await self.memory_store.batch_append_turns(session_id, turns_to_append)

        await self.memory_store.prune(session_id, max_turns=max_turns)
        history = await self.memory_store.load_history(session_id, limit=max_turns)
        summary = await self.memory_store.get_summary(session_id)

        return {
            "session_id": session_id,
            "conversation_history": [turn.model_dump() for turn in history],
            "turn_count": len(history),
            "summary": summary,
            "truncated": len(history) >= max_turns,
        }

    @staticmethod
    def _config_value(config: RunnableConfig, key: str, default: int | str) -> int:
        if isinstance(config, dict):
            configurable = config.get("configurable") or {}
            override = configurable.get(key)
            if isinstance(override, int) and override > 0:
                return override
        return int(default)


@registry.register(
    NodeMetadata(
        name="SessionManagementNode",
        description="Manage conversation sessions with capacity controls.",
        category="conversational_search",
    )
)
class SessionManagementNode(TaskNode):
    """Persist conversation turns for sessions while enforcing limits."""

    session_id_key: str = Field(
        default="session_id", description="Key under inputs containing session id"
    )
    turns_input_key: str = Field(
        default="turns", description="Optional new turns to append"
    )
    max_turns: int | str | None = Field(
        default=50,
        description="Maximum turns retained per session when provided",
    )
    memory_store: BaseMemoryStore = Field(
        default_factory=InMemoryMemoryStore,
        description="Backing store used for session persistence",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Persist new turns and return pruned session history."""
        session_id = state.get("inputs", {}).get(self.session_id_key)
        if not isinstance(session_id, str) or not session_id.strip():
            msg = "SessionManagementNode requires a non-empty session id"
            raise ValueError(msg)
        session_id = session_id.strip()

        new_turns = state.get("inputs", {}).get(self.turns_input_key) or []
        turns = [MemoryTurn.model_validate(turn) for turn in new_turns]
        if turns:
            await self.memory_store.batch_append_turns(session_id, turns)

        max_turns_int = int(self.max_turns) if self.max_turns is not None else None
        await self.memory_store.prune(session_id, max_turns_int)
        history = await self.memory_store.load_history(session_id, None)
        return {"history": history, "turn_count": len(history)}


@registry.register(
    NodeMetadata(
        name="AnswerCachingNode",
        description="Cache answers by query with TTL-based eviction.",
        category="conversational_search",
    )
)
class AnswerCachingNode(TaskNode):
    """Cache responses for repeated user queries using TTL-based eviction."""

    query_key: str = Field(
        default="message", description="Key within inputs containing the user message"
    )
    source_result_key: str = Field(
        default="grounded_generator",
        description="Result entry containing a new response to cache.",
    )
    response_field: str = Field(default="reply")
    ttl_seconds: int | str | None = Field(default=300)
    max_entries: int | str = Field(default=256)

    cache: OrderedDict[str, tuple[str, float | None]] = Field(  # pragma: no mutate
        default_factory=OrderedDict,
        description="In-memory cache mapping query -> (response, expires_at)",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return cached response for repeated queries when available."""
        inputs = state.get("inputs", {})
        query = (
            inputs.get(self.query_key)
            or inputs.get("message")
            or inputs.get("user_message")
            or inputs.get("query")
        )
        if not isinstance(query, str) or not query.strip():
            msg = "AnswerCachingNode requires a non-empty query"
            raise ValueError(msg)
        normalized_query = query.strip().lower()

        cached = self._get_cached(normalized_query)
        if cached is not None:
            return {"cached": True, "reply": cached}

        response = self._resolve_response(state)
        if response:
            self._store(normalized_query, response)
        return {"cached": False, "reply": response}

    def _get_cached(self, query: str) -> str | None:
        entry = self.cache.get(query)
        if entry is None:
            return None
        response, expires_at = entry
        if expires_at is not None and expires_at < time.time():
            self.cache.pop(query, None)
            return None
        self.cache.move_to_end(query)
        return response

    def _resolve_response(self, state: State) -> str | None:
        payload = state.get("results", {}).get(self.source_result_key, {})
        if isinstance(payload, dict):
            response = payload.get(self.response_field)
        else:
            response = None
        if response is None:
            return None
        if not isinstance(response, str) or not response.strip():
            msg = "Response field must be a non-empty string when provided"
            raise ValueError(msg)
        return response.strip()

    def _store(self, query: str, response: str) -> None:
        max_entries_int = int(self.max_entries)
        if len(self.cache) >= max_entries_int:
            self.cache.popitem(last=False)
        ttl_seconds_int = (
            int(self.ttl_seconds) if self.ttl_seconds is not None else None
        )
        expires_at = time.time() + ttl_seconds_int if ttl_seconds_int else None
        self.cache[query] = (response, expires_at)


@registry.register(
    NodeMetadata(
        name="ConversationCompressorNode",
        description="Summarize and budget a conversation history for downstream use.",
        category="conversational_search",
    )
)
class ConversationCompressorNode(TaskNode):
    """Reduce a conversation history to fit a token budget."""

    source_result_key: str = Field(
        default="conversation_state",
        description="Key within ``state.results`` containing conversation payloads.",
    )
    history_key: str = Field(
        default="conversation_history",
        description="Field holding turn dictionaries within ``source_result_key``.",
    )
    max_tokens: int | str = Field(
        default=120,
        description="Maximum whitespace token budget for the compressed history.",
    )
    preserve_recent: int | str = Field(
        default=2,
        description="Number of most recent turns that should always be retained.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return compressed conversation context within the configured token budget."""
        source = state.get("results", {}).get(self.source_result_key, {})
        history_payload = self._extract_history(source)
        turns = [MemoryTurn.model_validate(turn) for turn in history_payload]
        if not turns:
            msg = "ConversationCompressorNode requires at least one turn to compress"
            raise ValueError(msg)

        max_tokens = self._config_value(config, "max_tokens", int(self.max_tokens))
        preserve_recent = self._config_value(
            config, "preserve_recent", int(self.preserve_recent)
        )

        compressed: list[MemoryTurn] = []
        token_total = 0
        truncated = False

        for index, turn in enumerate(reversed(turns)):
            tokens = self._token_count(turn.content)
            should_keep = index < preserve_recent or token_total + tokens <= max_tokens
            if not should_keep:
                truncated = True
                continue
            compressed.append(turn)
            token_total += tokens

        compressed.reverse()
        summary_source = compressed or turns
        summary = self._summarize(summary_source, token_limit=max_tokens)

        return {
            "compressed_history": [turn.model_dump() for turn in compressed],
            "summary": summary,
            "total_tokens": token_total,
            "truncated": truncated,
        }

    @staticmethod
    def _config_value(config: RunnableConfig, key: str, default: int) -> int:
        if isinstance(config, dict):
            configurable = config.get("configurable") or {}
            candidate = configurable.get(key)
            if isinstance(candidate, int) and candidate > 0:
                return candidate
        return default

    def _extract_history(self, source: Any) -> list[dict[str, Any]]:
        if isinstance(source, dict) and self.history_key in source:
            history_payload = source[self.history_key]
        else:
            history_payload = source
        if not isinstance(history_payload, list):
            msg = "conversation_history must be a list of turn dictionaries"
            raise ValueError(msg)
        return history_payload

    @staticmethod
    def _token_count(text: str) -> int:
        return len(text.split())

    def _summarize(self, turns: Iterable[MemoryTurn], token_limit: int) -> str:
        buffer: list[str] = []
        token_total = 0
        for turn in turns:
            tokens = self._token_count(turn.content)
            entry = f"{turn.role}: {turn.content}"
            if token_total + tokens > token_limit:
                if not buffer:
                    snippet_tokens = turn.content.split()[: max(token_limit, 1)]
                    snippet = " ".join(snippet_tokens).strip()
                    entry = f"{turn.role}: {snippet}..." if snippet else "..."
                    buffer.append(entry)
                else:
                    buffer.append("...")
                break
            buffer.append(entry)
            token_total += tokens
        return " | ".join(buffer)


@registry.register(
    NodeMetadata(
        name="TopicShiftDetectorNode",
        description=(
            "Detect whether a new query diverges from recent conversation context."
        ),
        category="conversational_search",
    )
)
class TopicShiftDetectorNode(TaskNode):
    """Heuristic detector for topic shifts using token overlap."""

    query_key: str = Field(
        default="message", description="Key holding the active message string."
    )
    source_result_key: str = Field(
        default="conversation_state",
        description="Key within ``state.results`` providing conversation context.",
    )
    history_key: str = Field(
        default="conversation_history",
        description="Field containing turns used for topic comparison.",
    )
    similarity_threshold: float | str = Field(
        default=0.35,
        description="Minimum token overlap required to avoid a topic shift flag.",
    )
    recent_turns: int | str = Field(
        default=3,
        description="Number of turns to consider for similarity scoring.",
    )

    stopwords: set[str] = Field(
        default_factory=lambda: {
            "the",
            "a",
            "an",
            "and",
            "or",
            "of",
            "it",
            "this",
            "that",
            "to",
            "for",
        },
        description="Stopwords removed before similarity scoring.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Score the new query against recent turns and flag topic shifts."""
        inputs = state.get("inputs", {})
        query_raw = (
            inputs.get(self.query_key)
            or inputs.get("message")
            or inputs.get("user_message")
            or inputs.get("query")
        )
        if not isinstance(query_raw, str) or not query_raw.strip():
            msg = "TopicShiftDetectorNode requires a non-empty query"
            raise ValueError(msg)
        query = query_raw.strip()

        similarity_threshold = self._config_value(
            config, "similarity_threshold", self.similarity_threshold
        )
        recent_turns = self._config_int_value(config, "recent_turns", self.recent_turns)
        stopwords = self._config_stopwords(config)

        history_payload = state.get("results", {}).get(self.source_result_key, {})
        turns_raw = self._extract_turns(history_payload)
        if not turns_raw:
            return {
                "is_shift": False,
                "similarity": 1.0,
                "route": "continue",
                "reason": "no_history",
            }

        window = [MemoryTurn.model_validate(turn) for turn in turns_raw][-recent_turns:]
        similarity = self._jaccard_similarity(query, window, stopwords)
        is_shift = similarity < similarity_threshold
        route = "clarify" if is_shift else "continue"

        return {
            "is_shift": is_shift,
            "similarity": similarity,
            "route": route,
            "reason": "low_overlap" if is_shift else "aligned",
        }

    @staticmethod
    def _config_value(config: RunnableConfig, key: str, default: float | str) -> float:
        if isinstance(config, dict):
            configurable = config.get("configurable") or {}
            override = configurable.get(key)
            if isinstance(override, int | float):
                return float(override)
        return float(default)

    @staticmethod
    def _config_int_value(config: RunnableConfig, key: str, default: int | str) -> int:
        if isinstance(config, dict):
            configurable = config.get("configurable") or {}
            override = configurable.get(key)
            if isinstance(override, int) and override > 0:
                return override
        return int(default)

    def _config_stopwords(self, config: RunnableConfig) -> set[str]:
        if isinstance(config, dict):
            configurable = config.get("configurable") or {}
            override = configurable.get("stopwords")
            if isinstance(override, Iterable):
                return {token for token in override if isinstance(token, str)}
        return set(self.stopwords)

    def _extract_turns(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict) and self.history_key in payload:
            turns = payload[self.history_key]
        else:
            turns = payload
        if turns is None:
            return []
        if not isinstance(turns, list):
            msg = "conversation_history must be provided as a list"
            raise ValueError(msg)
        return turns

    def _tokenize(self, text: str, stopwords: set[str]) -> set[str]:
        tokens = {token.lower() for token in text.split()}
        return {token for token in tokens if token and token not in stopwords}

    def _jaccard_similarity(
        self, query: str, turns: Iterable[MemoryTurn], stopwords: set[str] | None = None
    ) -> float:
        active_stopwords = stopwords or set(self.stopwords)
        query_tokens = self._tokenize(query, active_stopwords)
        history_tokens: set[str] = set()
        for turn in turns:
            history_tokens |= self._tokenize(turn.content, active_stopwords)
        if not history_tokens or not query_tokens:
            return 0.0
        intersection = len(query_tokens & history_tokens)
        union = len(query_tokens | history_tokens)
        return intersection / union if union else 0.0


@registry.register(
    NodeMetadata(
        name="QueryClarificationNode",
        description="Generate clarifying prompts when ambiguity is detected.",
        category="conversational_search",
    )
)
class QueryClarificationNode(TaskNode):
    """Produce clarifying questions based on the active query and context."""

    query_key: str = Field(
        default="message",
        description="Key within ``state.inputs`` holding the user message.",
    )
    history_key: str = Field(
        default="conversation_history",
        description="Field used to retrieve conversation history if present.",
    )
    max_questions: int | str = Field(
        default=2, description="Maximum number of clarification prompts to emit."
    )

    ambiguous_markers: set[str] = Field(
        default_factory=lambda: {"it", "that", "those", "they", "this"},
        description="Tokens that often signal ambiguity requiring clarification.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Generate clarifying questions when the query appears ambiguous."""
        inputs = state.get("inputs", {})
        query_raw = self._resolve_query(inputs)
        if not query_raw:
            msg = "QueryClarificationNode requires a non-empty query"
            raise ValueError(msg)
        query = query_raw.strip()

        history = state.get("results", {}).get(self.history_key)
        context_hint = ""
        if isinstance(history, dict) and "summary" in history:
            context_hint = history.get("summary") or ""
        elif isinstance(history, list) and history:
            context_hint = history[-1] if isinstance(history[-1], str) else ""

        clarifications = self._build_questions(query, context_hint)

        return {
            "clarifications": clarifications[: int(self.max_questions)],
            "needs_clarification": bool(clarifications),
            "context_hint": context_hint or None,
        }

    def _resolve_query(self, inputs: dict[str, Any]) -> str | None:
        """Return the most meaningful query candidate available in inputs."""
        candidate = inputs.get(self.query_key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        for fallback in ("message", "user_message", "query"):
            candidate = inputs.get(fallback)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        return None

    def _build_questions(self, query: str, context_hint: str) -> list[str]:
        questions: list[str] = []
        tokens = {token.lower().strip(".,?!") for token in query.split()}
        if tokens & self.ambiguous_markers:
            questions.append("What specific item are you referring to?")
        if "or" in tokens:
            questions.append("Which option should I focus on first?")
        if not questions:
            focus = context_hint or "your last request"
            questions.append(f"Can you provide more detail about {focus}?")
        return questions


@registry.register(
    NodeMetadata(
        name="MemorySummarizerNode",
        description="Persist a compact conversation summary into the memory store.",
        category="conversational_search",
    )
)
class MemorySummarizerNode(TaskNode):
    """Write a conversation summary to the configured memory store."""

    session_id_key: str = Field(
        default="session_id", description="Key containing the active session id."
    )
    source_result_key: str = Field(
        default="conversation_state",
        description="Key within ``state.results`` providing conversation context.",
    )
    history_key: str = Field(
        default="conversation_history",
        description="Field inside ``source_result_key`` with turn payloads.",
    )
    summary_field: str = Field(
        default="summary",
        description="Optional existing summary to persist if present.",
    )
    memory_store: BaseMemoryStore = Field(
        default_factory=InMemoryMemoryStore,
        description="Backing store used to persist summaries.",
    )
    retention_seconds: int | None = Field(
        default=3600,
        description="TTL for persisted summaries; ``None`` disables expiration.",
    )
    max_summary_tokens: int | str = Field(
        default=180,
        description="Token budget when generating summaries from turns.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Persist a compact summary of the current conversation state."""
        session_id_raw = state.get("inputs", {}).get(self.session_id_key)
        if not isinstance(session_id_raw, str) or not session_id_raw.strip():
            msg = "MemorySummarizerNode requires a non-empty session id"
            raise ValueError(msg)
        session_id = session_id_raw.strip()

        retention_seconds = self._config_value(
            config, "retention_seconds", self.retention_seconds
        )
        max_summary_tokens_int = (
            int(self.max_summary_tokens)
            if isinstance(self.max_summary_tokens, int | str)
            else None
        )
        max_summary_tokens = self._config_value(
            config, "max_summary_tokens", max_summary_tokens_int
        )

        context = state.get("results", {}).get(self.source_result_key, {})
        summary = None
        if isinstance(context, dict):
            summary = context.get(self.summary_field)
            history_payload = context.get(self.history_key, [])
        else:
            history_payload = []

        turns = [MemoryTurn.model_validate(item) for item in history_payload]
        if summary is None:
            summary = self._summarize(turns, max_summary_tokens)

        if retention_seconds is not None and retention_seconds <= 0:
            msg = "retention_seconds must be positive when provided"
            raise ValueError(msg)

        await self.memory_store.write_summary(
            session_id, summary=summary, ttl_seconds=retention_seconds
        )

        return {
            "summary": summary,
            "turns_summarized": len(turns),
            "ttl_seconds": retention_seconds,
        }

    @staticmethod
    def _config_value(
        config: RunnableConfig, key: str, default: int | None
    ) -> int | None:
        if isinstance(config, dict):
            configurable = config.get("configurable") or {}
            if key in configurable:
                override = configurable.get(key)
                if isinstance(override, int) and override > 0:
                    return override
                if override is None:
                    return None
        return default

    def _summarize(self, turns: list[MemoryTurn], max_tokens: int | None) -> str:
        if not turns:
            return "No conversation history yet."
        buffer: list[str] = []
        token_total = 0
        for turn in turns:
            tokens = len(turn.content.split())
            if max_tokens is not None and token_total + tokens > max_tokens:
                buffer.append("...")
                break
            buffer.append(f"{turn.role}: {turn.content}")
            token_total += tokens
        return " | ".join(buffer)
