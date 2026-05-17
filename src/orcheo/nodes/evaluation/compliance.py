"""Policy compliance, memory privacy, and turn annotation nodes."""

from __future__ import annotations
import json
import re
import time
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.rag.conversation import MemoryTurn
from orcheo.nodes.registry import NodeMetadata, registry


@registry.register(
    NodeMetadata(
        name="PolicyComplianceNode",
        description="Apply policy checks and emit audit details.",
        category="conversational_search",
    )
)
class PolicyComplianceNode(TaskNode):
    """Detect basic policy violations and redact sensitive snippets."""

    text_key: str = Field(default="content")
    blocked_terms: list[str] | str = Field(default_factory=lambda: ["password", "ssn"])

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Apply policy checks and return sanitized content with audit metadata."""
        content = state.get("inputs", {}).get(self.text_key, "")
        if not isinstance(content, str):
            msg = "PolicyComplianceNode expects content string"
            raise ValueError(msg)

        if isinstance(self.blocked_terms, list):
            blocked_terms = self.blocked_terms
        elif isinstance(self.blocked_terms, str):
            try:
                parsed = json.loads(self.blocked_terms)
                blocked_terms = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                blocked_terms = [self.blocked_terms]
        else:
            blocked_terms = []

        violations = self._detect_violations(content, blocked_terms)
        sanitized = self._sanitize(content, blocked_terms)
        return {
            "compliant": not violations,
            "violations": violations,
            "sanitized": sanitized,
            "audit_log": [
                {
                    "timestamp": time.time(),
                    "violations": violations,
                    "original_length": len(content),
                    "sanitized_length": len(sanitized),
                }
            ],
        }

    def _detect_violations(self, content: str, blocked_terms: list[str]) -> list[str]:
        violations: list[str] = []
        for term in blocked_terms:
            if re.search(rf"\b{re.escape(term)}\b", content, re.IGNORECASE):
                violations.append(f"blocked_term:{term}")
        if re.search(r"\b\d{3}-\d{2}-\d{4}\b", content):
            violations.append("pii:ssn_pattern")
        if re.search(r"\b\S+@\S+\.[a-z]{2,}\b", content, re.IGNORECASE):
            violations.append("pii:email")
        return violations

    def _sanitize(self, content: str, blocked_terms: list[str]) -> str:
        sanitized = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED_SSN]", content)
        sanitized = re.sub(r"\b\S+@\S+\.[a-z]{2,}\b", "[REDACTED_EMAIL]", sanitized)
        for term in blocked_terms:
            sanitized = re.sub(
                rf"\b{re.escape(term)}\b",
                "[REDACTED_TERM]",
                sanitized,
                flags=re.IGNORECASE,
            )
        return sanitized


@registry.register(
    NodeMetadata(
        name="MemoryPrivacyNode",
        description="Enforce redaction and retention for conversation history.",
        category="conversational_search",
    )
)
class MemoryPrivacyNode(TaskNode):
    """Redact sensitive details from stored conversation turns."""

    history_key: str = Field(default="conversation_history")
    retention_count: int | str | None = Field(default=None)

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Redact sensitive details and enforce retention limits."""
        history_raw = state.get("inputs", {}).get(self.history_key, []) or []
        if not isinstance(history_raw, list):
            msg = "MemoryPrivacyNode expects a list for conversation_history"
            raise ValueError(msg)

        sanitized_history: list[dict[str, Any]] = []
        redactions = 0
        for turn_data in history_raw:
            turn = MemoryTurn.model_validate(turn_data)
            sanitized_content, turn_redactions = self._redact(turn.content)
            redactions += turn_redactions
            sanitized_history.append(
                {
                    "role": turn.role,
                    "content": sanitized_content,
                    "metadata": turn.metadata,
                }
            )

        retention_count_int = (
            int(self.retention_count) if self.retention_count is not None else None
        )
        if retention_count_int is not None:
            sanitized_history = sanitized_history[-retention_count_int:]

        return {
            "sanitized_history": sanitized_history,
            "redaction_count": redactions,
            "truncated": retention_count_int is not None
            and len(history_raw) > len(sanitized_history),
        }

    def _redact(self, content: str) -> tuple[str, int]:
        patterns = [
            (r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED_SSN]"),
            (r"\b\S+@\S+\.[a-z]{2,}\b", "[REDACTED_EMAIL]"),
            (r"\b\d{10}\b", "[REDACTED_PHONE]"),
        ]
        redactions = 0
        sanitized = content
        for pattern, replacement in patterns:
            sanitized, occurrences = re.subn(pattern, replacement, sanitized)
            redactions += occurrences
        return sanitized, redactions


@registry.register(
    NodeMetadata(
        name="TurnAnnotationNode",
        description="Annotate conversation turns with heuristics.",
        category="conversational_search",
    )
)
class TurnAnnotationNode(TaskNode):
    """Label conversation turns with semantic hints."""

    history_key: str = Field(default="conversation_history")

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Annotate conversation turns with basic heuristics."""
        history_raw = state.get("inputs", {}).get(self.history_key, []) or []
        if not isinstance(history_raw, list):
            msg = "TurnAnnotationNode expects conversation_history list"
            raise ValueError(msg)

        annotations: list[dict[str, Any]] = []
        for turn_data in history_raw:
            turn = MemoryTurn.model_validate(turn_data)
            annotations.append(
                {
                    "role": turn.role,
                    "content": turn.content,
                    "is_question": turn.content.strip().endswith("?"),
                    "sentiment": self._sentiment(turn.content),
                }
            )

        return {"annotations": annotations}

    def _sentiment(self, content: str) -> str:
        lowered = content.lower()
        if any(token in lowered for token in ["thank", "great", "awesome"]):
            return "positive"
        if any(token in lowered for token in ["error", "terrible", "fail"]):
            return "negative"
        return "neutral"
