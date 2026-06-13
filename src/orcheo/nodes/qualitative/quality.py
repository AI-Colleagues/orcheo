"""Data-quality assessment helper and node."""

# ruff: noqa: C901

from __future__ import annotations
import re
from collections import Counter
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.qualitative.accessors import get_units
from orcheo.nodes.qualitative.keys import QualitativeResultKeys
from orcheo.nodes.qualitative.models import (
    QualityFlagSummary,
    QualityReport,
    Unit,
)
from orcheo.nodes.registry import NodeMetadata, registry


LOW_EFFORT_VALUES = {"n/a", "na", "none", "nothing", "no", "nope", "asdf", "test", "-"}
EXCLUDE_QUALITY_FLAGS = {"empty", "too_short", "duplicate", "low_effort"}
PII_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|\+?\d[\d\s().-]{7,}\d", re.IGNORECASE)
AI_LIKE_RE = re.compile(
    r"\b(?:as an ai|large language model|in conclusion|moreover|furthermore)\b",
    re.IGNORECASE,
)


def assess_quality(units: list[Unit]) -> tuple[list[Unit], QualityReport]:
    """Flag quality issues without deleting units from the audit trail."""
    seen_texts: set[str] = set()
    updated: list[Unit] = []
    unit_flags: dict[str, list[str]] = {}
    counter: Counter[str] = Counter()
    for unit in units:
        flags = list(unit.quality_flags)
        text = unit.text.strip()
        normalized = re.sub(r"\s+", " ", text.lower())
        tokens = re.findall(r"[a-z0-9]+", normalized)
        if not text:
            flags.append("empty")
        if 0 < len(tokens) <= 2:
            flags.append("too_short")
        if normalized in LOW_EFFORT_VALUES:
            flags.append("low_effort")
        if normalized in seen_texts:
            flags.append("duplicate")
        seen_texts.add(normalized)
        if PII_RE.search(text):
            flags.append("pii")
        if AI_LIKE_RE.search(text) or len(tokens) > 80:
            flags.append("ai_like")
        deduped = list(dict.fromkeys(flags))
        counter.update(deduped)
        if deduped:
            unit_flags[unit.unit_id] = deduped
        updated.append(unit.model_copy(update={"quality_flags": deduped}))
    summaries = [
        QualityFlagSummary(
            flag=flag,
            count=count,
            severity="exclude" if flag in EXCLUDE_QUALITY_FLAGS else "warning",
        )
        for flag, count in sorted(counter.items())
    ]
    excluded = 0
    for flags in unit_flags.values():
        for flag in flags:
            if flag in EXCLUDE_QUALITY_FLAGS:
                excluded += 1
                break
    return updated, QualityReport(
        total_units=len(units),
        flagged_units=len(unit_flags),
        excluded_units=excluded,
        summaries=summaries,
        unit_flags=unit_flags,
    )


@registry.register(
    NodeMetadata(
        name="DataQualityNode",
        description="Flag low-effort, duplicate, PII, and AI-like responses",
        category="workflow",
    )
)
class DataQualityNode(TaskNode):
    """Assess data quality and persist unit flags plus a QualityReport."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Persist unit-level quality flags and a QualityReport artefact."""
        units, report = assess_quality(get_units(state, self.result_keys))
        return {
            self.result_keys.units_field: [u.model_dump(mode="json") for u in units],
            self.result_keys.quality_report_field: report.model_dump(mode="json"),
            "flagged_units": report.flagged_units,
            "excluded_units": report.excluded_units,
        }


__all__ = [
    "AI_LIKE_RE",
    "EXCLUDE_QUALITY_FLAGS",
    "LOW_EFFORT_VALUES",
    "PII_RE",
    "DataQualityNode",
    "assess_quality",
]
