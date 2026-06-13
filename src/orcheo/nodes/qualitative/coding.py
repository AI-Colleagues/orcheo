"""Helpers for batching units and shaping coding prompts/outputs."""

from __future__ import annotations
from collections import Counter
from collections.abc import Mapping
from orcheo.nodes.qualitative.codebook import code_to_theme_map
from orcheo.nodes.qualitative.models import (
    CodeAssignment,
    CodeAssignmentEntry,
    Codebook,
    Unit,
)


def batch_units(units: list[Unit], batch_size: int) -> list[list[Unit]]:
    """Chunk units into batches of the requested size."""
    return [units[i : i + batch_size] for i in range(0, len(units), batch_size)]


def format_open_coding_user_text(batch: list[Unit]) -> str:
    """Render a batch of units for open coding."""
    return "Units:\n" + "\n".join(f"- {u.unit_id}: {u.text}" for u in batch)


def format_recoding_user_text(batch: list[Unit]) -> str:
    """Render a batch of units for recoding."""
    return "Units:\n" + "\n".join(f"- {unit.unit_id}: {unit.text}" for unit in batch)


def existing_code_hints(
    assignments: list[CodeAssignment] | None, limit: int = 30
) -> list[str]:
    """Return the most-common existing codes to seed the next coding batch."""
    if not assignments:
        return []
    counter: Counter[str] = Counter()
    for assignment in assignments:
        for entry in assignment.assignments:
            counter.update([entry.code_id])
    return [code for code, _ in counter.most_common(limit)]


def format_assignments_with_units(
    assignments: list[CodeAssignment], units: list[Unit], limit: int | None = None
) -> str:
    """Render assignments alongside their unit text for consolidation prompts."""
    unit_by_id = {unit.unit_id: unit for unit in units}
    rows: list[str] = []
    for assignment in assignments[:limit]:
        unit = unit_by_id.get(assignment.unit_id)
        if unit is None:
            continue
        codes = ", ".join(f"{e.code_id} ({e.evidence})" for e in assignment.assignments)
        rows.append(
            f"- {assignment.unit_id}: {unit.text}\n  codes: {codes or '(none)'}"
        )
    return "\n".join(rows) or "(no assignments)"


def with_inferred_sentiment(
    entry: CodeAssignmentEntry, unit: Unit | None
) -> CodeAssignmentEntry:
    """Infer simple sentiment if an LLM leaves the default neutral label."""
    if entry.sentiment != "neutral" or unit is None:
        return entry
    text = f"{entry.evidence} {unit.text}".lower()
    negative_terms = {"confusing", "hard", "difficult", "slow", "bad", "missing"}
    positive_terms = {"easy", "quick", "helpful", "fast", "clear", "useful", "like"}
    has_negative = any(term in text for term in negative_terms)
    has_positive = any(term in text for term in positive_terms)
    if has_negative and has_positive:
        sentiment = "mixed"
    elif has_negative:
        sentiment = "negative"
    elif has_positive:
        sentiment = "positive"
    else:
        sentiment = "neutral"
    return entry.model_copy(update={"sentiment": sentiment})


def filter_assignments_to_codebook(
    assignments: list[CodeAssignment],
    codebook: Codebook,
    units_by_id: Mapping[str, Unit] | None = None,
    *,
    infer_sentiment: bool = False,
) -> list[CodeAssignment]:
    """Drop invented code IDs from LLM recoding output."""
    valid_codes = code_to_theme_map(codebook)
    filtered: list[CodeAssignment] = []
    for assignment in assignments:
        unit = units_by_id.get(assignment.unit_id) if units_by_id else None
        entries = [
            with_inferred_sentiment(e, unit) if infer_sentiment else e
            for e in assignment.assignments
            if e.code_id in valid_codes and e.confidence >= 0
        ]
        filtered.append(assignment.model_copy(update={"assignments": entries}))
    return filtered


__all__ = [
    "batch_units",
    "existing_code_hints",
    "filter_assignments_to_codebook",
    "format_assignments_with_units",
    "format_open_coding_user_text",
    "format_recoding_user_text",
    "with_inferred_sentiment",
]
