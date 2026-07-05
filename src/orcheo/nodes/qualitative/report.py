"""Report validation, Markdown rendering, and the report output/export nodes."""

# ruff: noqa: C901, PLR0912, PLR0915

from __future__ import annotations
import json
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.qualitative.accessors import (
    build_report_data,
    coerce_model,
    coerce_model_list,
    coerce_pending_documents,
)
from orcheo.nodes.qualitative.codebook import code_to_theme_map
from orcheo.nodes.qualitative.keys import QualitativeResultKeys
from orcheo.nodes.qualitative.models import (
    CandidateInsight,
    CodeAssignment,
    Codebook,
    CooccurrenceRow,
    QuantificationRow,
    Quote,
    Recommendation,
    ReportData,
    SegmentBreakdownRow,
    SegmentComparison,
    Unit,
)
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.nodes.storage import upload_attachment
from orcheo.runtime.results import node_result


def validate_final_state(thread_state: ReportData) -> list[str]:
    """Return grounding errors for the final structured state."""
    errors: list[str] = []
    units = thread_state.units or []
    unit_ids = {unit.unit_id for unit in units}
    codebook = thread_state.approved_codebook
    if codebook is None:
        return ["Missing codebook."]
    c2t = code_to_theme_map(codebook)
    code_ids = set(c2t)
    theme_ids = {theme.theme_id for theme in codebook.themes}
    assigned_code_ids = {
        entry.code_id
        for assignment in thread_state.code_assignments_pass2 or []
        for entry in assignment.assignments
    }
    for code_id in assigned_code_ids:
        if code_id not in code_ids:
            errors.append(f"Assignment references unknown code_id {code_id}.")
    reportable_theme_ids = {c2t[c][0] for c in assigned_code_ids if c in c2t}
    for row in thread_state.quantification or []:
        if row.theme_id not in theme_ids:
            errors.append(f"Quantification references unknown theme_id {row.theme_id}.")
    for quote in thread_state.selected_quotes or []:
        if quote.theme_id not in theme_ids:
            errors.append(f"Quote references unknown theme_id {quote.theme_id}.")
        if quote.unit_id not in unit_ids:
            errors.append(f"Quote references unknown unit_id {quote.unit_id}.")
    candidate_by_id = {i.insight_id: i for i in thread_state.candidate_insights or []}
    for insight_id in thread_state.approved_insight_ids or []:
        insight = candidate_by_id.get(insight_id)
        if insight is None:
            errors.append(f"Reported insight id {insight_id} is missing.")
            continue
        if not insight.supporting_units:
            errors.append(f"Insight {insight_id} has no supporting unit_id.")
        if not insight.supporting_codes:
            errors.append(f"Insight {insight_id} has no supporting code_id.")
        for unit_id in insight.supporting_units:
            if unit_id not in unit_ids:
                errors.append(
                    f"Insight {insight_id} references unknown unit_id {unit_id}."
                )
        for code_id in insight.supporting_codes:
            if code_id not in code_ids:
                errors.append(
                    f"Insight {insight_id} references unknown code_id {code_id}."
                )
            elif c2t[code_id][0] not in reportable_theme_ids:
                errors.append(
                    f"Insight {insight_id} references unreportable code_id {code_id}."
                )
    return errors


def render_markdown_report(thread_state: ReportData) -> str:
    """Render the final Markdown report without an LLM."""
    codebook = thread_state.approved_codebook or Codebook()
    candidate_by_id = {i.insight_id: i for i in thread_state.candidate_insights or []}
    approved = [
        candidate_by_id[iid]
        for iid in thread_state.approved_insight_ids or []
        if iid in candidate_by_id
    ]
    lines = [
        "# Insight Reporter — Final Report",
        "",
        "## Research objective",
        thread_state.research_objective or "(not provided)",
        "",
        "## Summary",
        f"- Units analysed: {len(thread_state.units or [])}",
        f"- Reported insights: {len(approved)}",
        "",
        "## Insights",
    ]
    for insight in approved:
        lines.extend(
            [
                f"### {insight.insight_id}: {insight.observation}",
                insight.interpretation,
                f"Implication: {insight.implication}",
                f"Evidence strength: {insight.evidence_strength}",
                f"Supporting codes: {', '.join(insight.supporting_codes)}",
                f"Supporting units: {', '.join(insight.supporting_units)}",
                "",
            ]
        )
        if insight.critic_notes:
            lines.append("Critic notes:")
            for note in insight.critic_notes:
                lines.append(f"- {note}")
            lines.append("")
    recommendations_by_id = {
        r.insight_id: r for r in thread_state.recommendations or []
    }
    if not approved:
        lines.append("(No insights met the evidence threshold.)")
    if recommendations_by_id:
        lines.append("## Recommendations")
        for insight in approved:
            rec = insight.recommendation or recommendations_by_id.get(
                insight.insight_id
            )
            if rec is None:
                continue
            lines.extend(
                [
                    f"### {rec.insight_id}",
                    f"- Finding: {rec.finding}",
                    f"- Action: {rec.action}",
                    f"- Expected impact: {rec.expected_impact}",
                    "",
                ]
            )
    lines.append("## Theme Quantification")
    for row in thread_state.quantification or []:
        summary = (
            f"- {row.theme_id} {row.title}: {row.respondents} respondent(s),"
            f" {row.mentions} mention(s), {row.pct_respondents}%"
        )
        if row.sentiment_counts:
            summary = f"{summary}, sentiment={row.sentiment_counts}"
        lines.append(summary)
    if thread_state.segment_comparisons:
        lines.extend(["", "## Segment Comparisons"])
        for comparison in thread_state.segment_comparisons:
            lines.append(
                f"- {comparison.signal.upper()} "
                f"{comparison.segment}/{comparison.theme_id}: {comparison.note}"
            )
    if thread_state.cooccurrence:
        lines.extend(["", "## Co-occurrence"])
        for co_row in thread_state.cooccurrence:
            lines.append(
                f"- {co_row.theme_id_a} + {co_row.theme_id_b}: "
                f"{co_row.respondents} respondent(s), {co_row.mentions} mention(s)"
            )
    lines.extend(["", "## Representative Quotes"])
    for quote in thread_state.selected_quotes or []:
        speaker = f"{quote.speaker}: " if quote.speaker else ""
        lines.append(f"- {quote.theme_id}/{quote.unit_id}: {speaker}{quote.text}")
    lines.extend(["", "## Codebook"])
    for theme in codebook.themes:
        lines.append(f"### {theme.theme_id}: {theme.title}")
        for subtheme in theme.subthemes:
            lines.append(
                f"- {subtheme.code_id} {subtheme.title}: {subtheme.definition}"
            )
        lines.append("")
    evidence_index: dict[str, Any] = {
        "units": [unit.model_dump(mode="json") for unit in thread_state.units or []],
        "assignments": [
            a.model_dump(mode="json") for a in thread_state.code_assignments_pass2 or []
        ],
        "quotes": [
            q.model_dump(mode="json") for q in thread_state.selected_quotes or []
        ],
        "approved_insight_ids": thread_state.approved_insight_ids or [],
    }
    lines.extend(
        [
            "",
            "## Evidence Index",
            "```json",
            json.dumps(evidence_index, indent=2, ensure_ascii=False),
            "```",
        ]
    )
    return "\n".join(line for line in lines if line is not None).strip()


def _report_data_from_values(
    *,
    state: State,
    keys: QualitativeResultKeys,
    research_objective: str | None = None,
    pending_documents: Any | None = None,
    source_payload: Any | None = None,
    units: Any | None = None,
    approved_codebook: Any | None = None,
    code_assignments: Any | None = None,
    quantification: Any | None = None,
    cooccurrence: Any | None = None,
    segment_breakdowns: Any | None = None,
    segment_comparisons: Any | None = None,
    selected_quotes: Any | None = None,
    candidate_insights: Any | None = None,
    recommendations: Any | None = None,
    approved_insight_ids: Any | None = None,
) -> ReportData:
    """Build report data from explicit node inputs, falling back to result keys."""
    fallback = build_report_data(state, keys)
    explicit_source = dict(source_payload) if isinstance(source_payload, dict) else None
    explicit_approved_ids = (
        [str(item) for item in approved_insight_ids]
        if isinstance(approved_insight_ids, list)
        else None
    )
    return ReportData(
        research_objective=research_objective or fallback.research_objective,
        pending_documents=coerce_pending_documents(pending_documents)
        or fallback.pending_documents,
        source_payload=explicit_source or fallback.source_payload,
        units=coerce_model_list(units, Unit) or fallback.units,
        approved_codebook=coerce_model(approved_codebook, Codebook)
        or fallback.approved_codebook,
        code_assignments_pass2=coerce_model_list(code_assignments, CodeAssignment)
        or fallback.code_assignments_pass2,
        quantification=coerce_model_list(quantification, QuantificationRow)
        or fallback.quantification,
        cooccurrence=coerce_model_list(cooccurrence, CooccurrenceRow)
        or fallback.cooccurrence,
        segment_breakdowns=coerce_model_list(segment_breakdowns, SegmentBreakdownRow)
        or fallback.segment_breakdowns,
        segment_comparisons=coerce_model_list(segment_comparisons, SegmentComparison)
        or fallback.segment_comparisons,
        selected_quotes=coerce_model_list(selected_quotes, Quote)
        or fallback.selected_quotes,
        candidate_insights=coerce_model_list(candidate_insights, CandidateInsight)
        or fallback.candidate_insights,
        recommendations=coerce_model_list(recommendations, Recommendation)
        or fallback.recommendations,
        approved_insight_ids=explicit_approved_ids or fallback.approved_insight_ids,
    )


@registry.register(
    NodeMetadata(
        name="ReportOutputNode",
        description="Render the final report and return it with a download link",
        category="workflow",
    )
)
class ReportOutputNode(TaskNode):
    """Render the final report and return it with a download link."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)
    research_objective: str | None = None
    pending_documents: Any | None = None
    source_payload: Any | None = None
    units: Any | None = None
    approved_codebook: Any | None = None
    code_assignments: Any | None = None
    quantification: Any | None = None
    cooccurrence: Any | None = None
    segment_breakdowns: Any | None = None
    segment_comparisons: Any | None = None
    selected_quotes: Any | None = None
    candidate_insights: Any | None = None
    recommendations: Any | None = None
    approved_insight_ids: Any | None = None
    ingest_node_name: str = "ingest"
    export_filename: str = "insight_report.md"
    export_mime_type: str = "text/markdown"
    failed_ingest_message: str = "Ingest failed."

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Validate grounding, render the report, and upload it for download."""
        early = node_result(state, self.ingest_node_name)
        if early.get("halt"):
            message = str(early.get("assistant_message", self.failed_ingest_message))
            return {"assistant_message": message}

        data = _report_data_from_values(
            state=state,
            keys=self.result_keys,
            research_objective=self.research_objective,
            pending_documents=self.pending_documents,
            source_payload=self.source_payload,
            units=self.units,
            approved_codebook=self.approved_codebook,
            code_assignments=self.code_assignments,
            quantification=self.quantification,
            cooccurrence=self.cooccurrence,
            segment_breakdowns=self.segment_breakdowns,
            segment_comparisons=self.segment_comparisons,
            selected_quotes=self.selected_quotes,
            candidate_insights=self.candidate_insights,
            recommendations=self.recommendations,
            approved_insight_ids=self.approved_insight_ids,
        )
        errors = validate_final_state(data)
        report = render_markdown_report(data)

        report_url: str | None = None
        export_error: str | None = None
        try:
            _, report_url = await upload_attachment(
                config, report, self.export_filename, self.export_mime_type
            )
        except RuntimeError as exc:
            export_error = str(exc)

        approved = len(data.approved_insight_ids or [])
        lines = ["# Insight Reporter — Report Complete\n"]
        lines.append(
            f"✅ Synthesised **{approved} insight(s)** from "
            f"**{len(data.units or [])} coded unit(s)**.\n"
        )
        if report_url:
            lines.append(f"**[⬇ Download {self.export_filename}]({report_url})**\n")
        elif export_error:
            lines.append(f"_Could not generate the download link: {export_error}_\n")
        if errors:
            lines.append("> ⚠️ Data caveats: " + "; ".join(errors) + "\n")
        lines.append("---\n")
        lines.append(report)

        assistant_message = "\n".join(lines).strip()
        return {
            "assistant_message": assistant_message,
            "research_objective": data.research_objective,
            "report_markdown": report,
            "report_url": report_url,
        }


@registry.register(
    NodeMetadata(
        name="ExportReportNode",
        description="Regenerate the downloadable Markdown report link",
        category="workflow",
    )
)
class ExportReportNode(TaskNode):
    """Regenerate the downloadable Markdown report link."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)
    research_objective: str | None = None
    pending_documents: Any | None = None
    source_payload: Any | None = None
    units: Any | None = None
    approved_codebook: Any | None = None
    code_assignments: Any | None = None
    quantification: Any | None = None
    cooccurrence: Any | None = None
    segment_breakdowns: Any | None = None
    segment_comparisons: Any | None = None
    selected_quotes: Any | None = None
    candidate_insights: Any | None = None
    recommendations: Any | None = None
    approved_insight_ids: Any | None = None
    export_filename: str = "insight_report.md"
    export_mime_type: str = "text/markdown"
    export_title: str = "Insight Report Export"
    missing_report_message: str = (
        "No report is available to export. Please run `generate_report` first."
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Re-render the report from the node_results channel and upload it."""
        data = _report_data_from_values(
            state=state,
            keys=self.result_keys,
            research_objective=self.research_objective,
            pending_documents=self.pending_documents,
            source_payload=self.source_payload,
            units=self.units,
            approved_codebook=self.approved_codebook,
            code_assignments=self.code_assignments,
            quantification=self.quantification,
            cooccurrence=self.cooccurrence,
            segment_breakdowns=self.segment_breakdowns,
            segment_comparisons=self.segment_comparisons,
            selected_quotes=self.selected_quotes,
            candidate_insights=self.candidate_insights,
            recommendations=self.recommendations,
            approved_insight_ids=self.approved_insight_ids,
        )
        if data.approved_codebook is None or not data.candidate_insights:
            return {"assistant_message": self.missing_report_message}
        report = render_markdown_report(data)
        try:
            _, report_url = await upload_attachment(
                config, report, self.export_filename, self.export_mime_type
            )
        except RuntimeError as exc:
            return {"assistant_message": f"Export failed: {exc}"}
        lines = [
            f"## {self.export_title}\n",
            f"[Download {self.export_filename}]({report_url})",
        ]
        return {
            "assistant_message": "\n".join(lines),
            "report_url": report_url,
        }


__all__ = [
    "ExportReportNode",
    "ReportOutputNode",
    "render_markdown_report",
    "validate_final_state",
]
