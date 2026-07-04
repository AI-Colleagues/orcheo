"""Quote/insight helpers and the critic + recommendation nodes."""

# ruff: noqa: C901

from __future__ import annotations
from collections import Counter
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.qualitative.accessors import (
    coerce_model,
    coerce_model_list,
    get_approved_codebook,
    get_candidate_insights,
    get_code_assignments,
    get_segment_comparisons,
    get_units,
)
from orcheo.nodes.qualitative.codebook import code_to_theme_map, make_insight_id
from orcheo.nodes.qualitative.keys import QualitativeResultKeys
from orcheo.nodes.qualitative.models import (
    CandidateInsight,
    CodeAssignment,
    Codebook,
    Quote,
    Recommendation,
    ReportData,
    SegmentComparison,
    Unit,
)
from orcheo.nodes.registry import NodeMetadata, registry


def fallback_quotes(
    codebook: Codebook,
    assignments: list[CodeAssignment],
    units: list[Unit],
    quotes_per_theme: int,
) -> list[Quote]:
    """Select deterministic first-seen quotes per theme."""
    c2t = code_to_theme_map(codebook)
    units_by_id = {unit.unit_id: unit for unit in units}
    quotes: list[Quote] = []
    used: set[tuple[str, str]] = set()
    counts: Counter[str] = Counter()
    for assignment in assignments:
        unit = units_by_id.get(assignment.unit_id)
        if unit is None:
            continue
        for entry in assignment.assignments:
            theme_info = c2t.get(entry.code_id)
            if theme_info is None:
                continue
            theme_id = theme_info[0]
            key = (theme_id, unit.unit_id)
            if key in used or counts[theme_id] >= quotes_per_theme:
                continue
            used.add(key)
            counts.update([theme_id])
            quotes.append(
                Quote(
                    theme_id=theme_id,
                    unit_id=unit.unit_id,
                    text=unit.text,
                    speaker=unit.speaker,
                )
            )
    return quotes


def filter_grounded_quotes(
    quotes: list[Quote], codebook: Codebook, units: list[Unit]
) -> list[Quote]:
    """Keep only quotes bound to known theme and unit IDs."""
    theme_ids = {theme.theme_id for theme in codebook.themes}
    unit_ids = {unit.unit_id for unit in units}
    return [
        q
        for q in quotes
        if q.theme_id in theme_ids and q.unit_id in unit_ids and q.text.strip()
    ]


def normalise_candidate_insights(
    insights: list[CandidateInsight],
) -> list[CandidateInsight]:
    """Ensure candidate insights have IDs."""
    normalised: list[CandidateInsight] = []
    index = 1
    for insight in insights:
        insight_id = (
            insight.insight_id.strip() if insight.insight_id else make_insight_id(index)
        )
        normalised.append(insight.model_copy(update={"insight_id": insight_id}))
        index += 1
    return normalised


def fallback_insights(thread_state: ReportData) -> list[CandidateInsight]:
    """Generate simple deterministic candidate insights from top themes."""
    codebook = thread_state.approved_codebook
    if codebook is None:
        return []
    theme_by_id = {theme.theme_id: theme for theme in codebook.themes}
    code_by_theme = {
        theme.theme_id: [s.code_id for s in theme.subthemes]
        for theme in codebook.themes
    }
    c2t = code_to_theme_map(codebook)
    units_by_theme: dict[str, list[str]] = {
        theme.theme_id: [] for theme in codebook.themes
    }
    for assignment in thread_state.code_assignments_pass2 or []:
        for entry in assignment.assignments:
            theme_info = c2t.get(entry.code_id)
            if theme_info is not None:
                units_by_theme.setdefault(theme_info[0], []).append(assignment.unit_id)
    rows = sorted(
        thread_state.quantification or [],
        key=lambda row: (row.respondents, row.mentions),
        reverse=True,
    )
    insights: list[CandidateInsight] = []
    index = 1
    for row in rows[:5]:
        if row.respondents <= 0:
            index += 1
            continue
        theme = theme_by_id.get(row.theme_id)
        if theme is None:
            index += 1
            continue
        insights.append(
            CandidateInsight(
                insight_id=make_insight_id(index),
                observation=(
                    f"{theme.title} appeared in {row.respondents}"
                    f" respondent(s) ({row.pct_respondents}%)."
                ),
                interpretation=f"{theme.title} is a notable pattern in the dataset.",
                implication="Review supporting quotes before acting on this pattern.",
                supporting_codes=code_by_theme.get(row.theme_id, [])[:3],
                supporting_units=list(
                    dict.fromkeys(units_by_theme.get(row.theme_id, []))
                )[:5],
                evidence_strength="medium" if row.respondents >= 2 else "low",
            )
        )
        index += 1
    return insights


def critique_insights(thread_state: ReportData) -> list[CandidateInsight]:
    """Annotate insights with simple deterministic counter-evidence."""
    insights = thread_state.candidate_insights or []
    codebook = thread_state.approved_codebook
    if codebook is None:
        return insights
    c2t = code_to_theme_map(codebook)
    assignment_by_unit = {
        a.unit_id: a for a in thread_state.code_assignments_pass2 or []
    }
    all_unit_ids = {unit.unit_id for unit in thread_state.units or []}
    units_by_id = {unit.unit_id: unit for unit in thread_state.units or []}
    updated: list[CandidateInsight] = []
    for insight in insights:
        supporting_theme_ids = {c2t[c][0] for c in insight.supporting_codes if c in c2t}
        supporting_units = set(insight.supporting_units)
        counter_units: list[str] = []
        for unit_id in sorted(all_unit_ids - supporting_units):
            unit = units_by_id.get(unit_id)
            assignment = assignment_by_unit.get(unit_id)
            if unit is None or assignment is None:
                continue
            assigned_theme_ids = {
                c2t[e.code_id][0] for e in assignment.assignments if e.code_id in c2t
            }
            text = unit.text.lower()
            has_negative = any(
                e.sentiment == "negative" for e in assignment.assignments
            )
            has_contrast = any(
                term in text
                for term in ("but", "however", "although", "not", "never", "hard")
            )
            if assigned_theme_ids & supporting_theme_ids and (
                has_negative or has_contrast
            ):
                counter_units.append(unit_id)
        notes = list(insight.critic_notes)
        if counter_units:
            notes.append(
                f"Counter-evidence found in {len(counter_units)} unit(s): "
                f"{', '.join(counter_units[:5])}."
            )
        for comparison in thread_state.segment_comparisons or []:
            if (
                comparison.theme_id in supporting_theme_ids
                and comparison.signal == "weak"
            ):
                notes.append(f"Weak segment difference: {comparison.note}")
        strength = insight.evidence_strength
        if counter_units and strength == "high":
            strength = "medium"
        elif counter_units and strength == "medium":
            strength = "low"
        updated.append(
            insight.model_copy(
                update={
                    "critic_notes": list(dict.fromkeys(notes)),
                    "counter_evidence_units": counter_units[:10],
                    "evidence_strength": strength,
                }
            )
        )
    return updated


def recommend_action(insight: CandidateInsight) -> str:
    """Create a concise action recommendation for an insight."""
    if insight.counter_evidence_units:
        return "Investigate the counter-evidence before prioritising a product change."
    if insight.evidence_strength == "high":
        return "Prioritise a targeted experiment or product change for this finding."
    return (
        "Validate this pattern with follow-up research before committing roadmap work."
    )


def recommend_impact(insight: CandidateInsight) -> str:
    """Create a concise expected-impact statement."""
    if insight.evidence_strength == "high":
        return "Likely to improve the most frequently cited user experience issue."
    if insight.evidence_strength == "medium":
        return "May reduce friction for a meaningful subset of respondents."
    return "Useful as a hypothesis, but impact is uncertain until validated."


@registry.register(
    NodeMetadata(
        name="InsightCriticNode",
        description="Find counter-evidence and annotate candidate insights",
        category="workflow",
    )
)
class InsightCriticNode(TaskNode):
    """Find counter-evidence and annotate candidate insights."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)
    units: Any | None = None
    approved_codebook: Any | None = None
    code_assignments: Any | None = None
    segment_comparisons: Any | None = None
    candidate_insights: Any | None = None
    candidate_insights_field: str | None = None

    def _report_data(self, state: State) -> ReportData:
        keys = self.result_keys
        return ReportData(
            units=coerce_model_list(self.units, Unit) or get_units(state, keys),
            approved_codebook=coerce_model(self.approved_codebook, Codebook)
            or get_approved_codebook(state, keys),
            code_assignments_pass2=coerce_model_list(
                self.code_assignments, CodeAssignment
            )
            or get_code_assignments(state, keys),
            segment_comparisons=coerce_model_list(
                self.segment_comparisons, SegmentComparison
            )
            or get_segment_comparisons(state, keys),
            candidate_insights=coerce_model_list(
                self.candidate_insights, CandidateInsight
            )
            or get_candidate_insights(state, keys),
        )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Persist critic notes and downgrade weakly supported claims."""
        insights = critique_insights(self._report_data(state))
        return {
            (
                self.candidate_insights_field
                or self.result_keys.candidate_insights_field
            ): [i.model_dump(mode="json") for i in insights],
            "critiqued": len(insights),
        }


@registry.register(
    NodeMetadata(
        name="RecommendationGeneratorNode",
        description="Attach Finding -> Action -> Expected impact recommendations",
        category="workflow",
    )
)
class RecommendationGeneratorNode(TaskNode):
    """Generate deterministic Finding -> Action -> Expected impact recommendations."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)
    candidate_insights: Any | None = None
    candidate_insights_field: str | None = None
    recommendations_field: str | None = None
    approved_insight_ids_field: str | None = None

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Attach recommendations and persist insights for the report renderer."""
        insights: list[CandidateInsight] = []
        recommendations: list[Recommendation] = []
        candidates = coerce_model_list(
            self.candidate_insights, CandidateInsight
        ) or get_candidate_insights(state, self.result_keys)
        for insight in candidates:
            rec = Recommendation(
                insight_id=insight.insight_id,
                finding=insight.observation,
                action=recommend_action(insight),
                expected_impact=recommend_impact(insight),
            )
            recommendations.append(rec)
            insights.append(insight.model_copy(update={"recommendation": rec}))
        keys = self.result_keys
        return {
            (self.candidate_insights_field or keys.candidate_insights_field): [
                i.model_dump(mode="json") for i in insights
            ],
            (self.recommendations_field or keys.recommendations_field): [
                r.model_dump(mode="json") for r in recommendations
            ],
            (self.approved_insight_ids_field or keys.approved_insight_ids_field): [
                i.insight_id for i in insights
            ],
            "insights": len(insights),
            "halt": False,
        }


__all__ = [
    "InsightCriticNode",
    "RecommendationGeneratorNode",
    "critique_insights",
    "fallback_insights",
    "fallback_quotes",
    "filter_grounded_quotes",
    "normalise_candidate_insights",
    "recommend_action",
    "recommend_impact",
]
