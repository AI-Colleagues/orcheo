"""Pydantic models shared across the qualitative-analysis workflows."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Literal
from pydantic import BaseModel, Field


Sentiment = Literal["positive", "neutral", "negative", "mixed"]


class Unit(BaseModel):
    """One single-idea unit of text after segmentation."""

    unit_id: str
    record_id: str
    source: str
    speaker: str | None = None
    text: str
    original_text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)


class Subtheme(BaseModel):
    """A code (subtheme) within a theme."""

    code_id: str
    title: str
    definition: str = ""
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    example_quotes: list[dict[str, str]] = Field(default_factory=list)


class Theme(BaseModel):
    """A top-level theme grouping one or more subtheme codes."""

    theme_id: str
    title: str
    subthemes: list[Subtheme] = Field(default_factory=list)


class Codebook(BaseModel):
    """The full themed codebook."""

    themes: list[Theme] = Field(default_factory=list)


class CodeAssignmentEntry(BaseModel):
    """A single (code_id, evidence, confidence) tuple on a unit."""

    code_id: str
    evidence: str = ""
    confidence: float = 0.0
    sentiment: Sentiment = "neutral"


class CodeAssignment(BaseModel):
    """All code assignments attached to a single unit."""

    unit_id: str
    assignments: list[CodeAssignmentEntry] = Field(default_factory=list)


@dataclass(frozen=True)
class ParsedRecord:
    """A pre-segmentation record extracted from a source payload."""

    record_id: str
    source: str
    speaker: str | None
    text: str
    metadata: dict[str, Any]


# ---------------------------------------------------------------------------
# Data-quality models
# ---------------------------------------------------------------------------


class QualityFlagSummary(BaseModel):
    """Aggregate count for one quality flag."""

    flag: str
    count: int
    severity: Literal["exclude", "warning"]


class QualityReport(BaseModel):
    """Data-quality artefact generated before coding."""

    total_units: int
    flagged_units: int
    excluded_units: int
    summaries: list[QualityFlagSummary] = Field(default_factory=list)
    unit_flags: dict[str, list[str]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Quantification models
# ---------------------------------------------------------------------------


class QuantificationRow(BaseModel):
    """Per-theme frequency row in the quantification table."""

    theme_id: str
    title: str
    mentions: int
    respondents: int
    pct_respondents: float
    sentiment_counts: dict[str, int] = Field(default_factory=dict)


class CooccurrenceRow(BaseModel):
    """Pairwise theme co-occurrence count."""

    theme_id_a: str
    theme_id_b: str
    respondents: int
    mentions: int


class SegmentBreakdownRow(BaseModel):
    """Theme frequency for one segment value."""

    segment: str
    value: str
    theme_id: str
    respondents: int
    total_respondents: int
    pct_respondents: float
    sample_size_guard: Literal["ok", "small_n"]


class SegmentComparison(BaseModel):
    """Strong or weak segment difference for one theme."""

    segment: str
    theme_id: str
    high_value: str
    low_value: str
    high_pct: float
    low_pct: float
    delta_pct: float
    signal: Literal["strong", "weak"]
    note: str


class SegmentVariable(BaseModel):
    """A metadata field selected for segment analysis."""

    name: str
    values: list[str] = Field(default_factory=list)
    source: Literal["auto", "override"] = "auto"


# ---------------------------------------------------------------------------
# Insight / report models
# ---------------------------------------------------------------------------


class Recommendation(BaseModel):
    """Action recommendation linked to an insight."""

    insight_id: str
    finding: str
    action: str
    expected_impact: str


class Quote(BaseModel):
    """A representative verbatim quote bound to a unit."""

    theme_id: str
    unit_id: str
    text: str
    speaker: str | None = None


class CandidateInsight(BaseModel):
    """A candidate insight emitted by the synthesiser."""

    insight_id: str
    observation: str
    interpretation: str = ""
    implication: str = ""
    supporting_codes: list[str] = Field(default_factory=list)
    supporting_units: list[str] = Field(default_factory=list)
    evidence_strength: Literal["low", "medium", "high"] = "medium"
    critic_notes: list[str] = Field(default_factory=list)
    counter_evidence_units: list[str] = Field(default_factory=list)
    recommendation: Recommendation | None = None


class Insight(CandidateInsight):
    """A reported insight — same shape as a candidate."""


# ---------------------------------------------------------------------------
# LLM structured-response schemas
# ---------------------------------------------------------------------------


class OpenCodingBatchResponse(BaseModel):
    """Structured LLM response for one open-coding batch."""

    assignments: list[CodeAssignment] = Field(default_factory=list)
    suggested_codes: list[dict[str, str]] = Field(default_factory=list)


class CodebookConsolidationResponse(BaseModel):
    """Structured LLM response for codebook consolidation."""

    codebook: Codebook


class RecodingBatchResponse(BaseModel):
    """Structured LLM response for one recoding batch."""

    assignments: list[CodeAssignment] = Field(default_factory=list)


class QuoteSelectionResponse(BaseModel):
    """Structured LLM response for quote selection."""

    quotes: list[Quote] = Field(default_factory=list)


class InsightGenerationResponse(BaseModel):
    """Structured LLM response for insight synthesis."""

    insights: list[CandidateInsight] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Transient report view
# ---------------------------------------------------------------------------


@dataclass
class ReportData:
    """A transient view of the report dataset assembled from ``node_results``.

    This is *not* persisted graph state — it is rebuilt on each call from the
    standard ``node_results`` channel to give the pure report helpers (rendering,
    validation, critique, fallbacks) a tidy attribute bundle to read from.
    """

    research_objective: str | None = None
    pending_documents: list[dict[str, Any]] | None = None
    source_payload: dict[str, Any] | None = None
    units: list[Unit] | None = None
    approved_codebook: Codebook | None = None
    code_assignments_pass2: list[CodeAssignment] | None = None
    quantification: list[QuantificationRow] | None = None
    cooccurrence: list[CooccurrenceRow] | None = None
    segment_breakdowns: list[SegmentBreakdownRow] | None = None
    segment_comparisons: list[SegmentComparison] | None = None
    selected_quotes: list[Quote] | None = None
    candidate_insights: list[CandidateInsight] | None = None
    recommendations: list[Recommendation] | None = None
    approved_insight_ids: list[str] | None = None


__all__ = [
    "CandidateInsight",
    "CodeAssignment",
    "CodeAssignmentEntry",
    "Codebook",
    "CodebookConsolidationResponse",
    "CooccurrenceRow",
    "Insight",
    "InsightGenerationResponse",
    "OpenCodingBatchResponse",
    "ParsedRecord",
    "QualityFlagSummary",
    "QualityReport",
    "QuantificationRow",
    "Quote",
    "QuoteSelectionResponse",
    "Recommendation",
    "RecodingBatchResponse",
    "ReportData",
    "SegmentBreakdownRow",
    "SegmentComparison",
    "SegmentVariable",
    "Sentiment",
    "Subtheme",
    "Theme",
    "Unit",
]
