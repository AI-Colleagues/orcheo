"""Quantification helpers (theme frequencies, co-occurrence, segments) + node."""

# ruff: noqa: C901, PLR0912

from __future__ import annotations
from collections import Counter
from typing import Any
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.qualitative.accessors import (
    coerce_model,
    coerce_model_list,
    get_code_assignments,
    get_configurable,
    get_source_payload,
    get_units,
)
from orcheo.nodes.qualitative.codebook import code_to_theme_map, normalise_codebook_ids
from orcheo.nodes.qualitative.coded_data import parse_coded_data_csv
from orcheo.nodes.qualitative.models import (
    CodeAssignment,
    Codebook,
    CooccurrenceRow,
    QuantificationRow,
    SegmentBreakdownRow,
    SegmentComparison,
    SegmentVariable,
    Unit,
)
from orcheo.nodes.registry import NodeMetadata, registry


def parse_str_list(value: Any) -> list[str]:
    """Parse a configurable multi-value field (JSON list or comma-separated str)."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def compute_quantification(
    units: list[Unit],
    assignments: list[CodeAssignment],
    codebook: Codebook,
) -> tuple[list[QuantificationRow], list[CooccurrenceRow]]:
    """Compute per-theme frequencies and pairwise theme co-occurrence."""
    c2t = code_to_theme_map(codebook)
    units_by_id = {unit.unit_id: unit for unit in units}
    total_respondents = len({unit.record_id for unit in units_by_id.values()}) or 1
    mentions: Counter[str] = Counter()
    sentiment_by_theme: dict[str, Counter[str]] = {
        theme.theme_id: Counter() for theme in codebook.themes
    }
    respondents_by_theme: dict[str, set[str]] = {
        theme.theme_id: set() for theme in codebook.themes
    }
    titles = {theme.theme_id: theme.title for theme in codebook.themes}
    themes_by_record: dict[str, set[str]] = {}
    mentions_by_pair: Counter[tuple[str, str]] = Counter()
    for assignment in assignments:
        unit = units_by_id.get(assignment.unit_id)
        if unit is None:
            continue
        seen_theme_ids: set[str] = set()
        for entry in assignment.assignments:
            theme_info = c2t.get(entry.code_id)
            if theme_info is None:
                continue
            theme_id, _title = theme_info
            mentions.update([theme_id])
            sentiment_by_theme.setdefault(theme_id, Counter()).update([entry.sentiment])
            seen_theme_ids.add(theme_id)
        for theme_id in seen_theme_ids:
            respondents_by_theme.setdefault(theme_id, set()).add(unit.record_id)
        themes_by_record.setdefault(unit.record_id, set()).update(seen_theme_ids)
        for theme_a in seen_theme_ids:
            for theme_b in seen_theme_ids:
                if theme_a < theme_b:
                    mentions_by_pair.update([(theme_a, theme_b)])
    rows = [
        QuantificationRow(
            theme_id=theme.theme_id,
            title=titles[theme.theme_id],
            mentions=mentions[theme.theme_id],
            respondents=len(respondents_by_theme.get(theme.theme_id, set())),
            pct_respondents=round(
                100
                * len(respondents_by_theme.get(theme.theme_id, set()))
                / total_respondents,
                1,
            ),
            sentiment_counts=dict(sentiment_by_theme.get(theme.theme_id, Counter())),
        )
        for theme in codebook.themes
    ]
    pair_records: dict[tuple[str, str], set[str]] = {}
    for record_id, theme_ids in themes_by_record.items():
        ordered = sorted(theme_ids)
        for idx in range(len(ordered)):
            theme_a = ordered[idx]
            for theme_b in ordered[idx + 1 :]:
                pair_records.setdefault((theme_a, theme_b), set()).add(record_id)
    cooccurrence = [
        CooccurrenceRow(
            theme_id_a=theme_a,
            theme_id_b=theme_b,
            respondents=len(record_ids),
            mentions=mentions_by_pair[(theme_a, theme_b)],
        )
        for (theme_a, theme_b), record_ids in sorted(pair_records.items())
    ]
    return rows, cooccurrence


def plan_segments(
    units: list[Unit], overrides: list[str] | None = None, max_values: int = 8
) -> list[SegmentVariable]:
    """Pick useful metadata fields for segment analysis."""
    overrides = overrides or []
    metadata_values: dict[str, set[str]] = {}
    for unit in units:
        for key, value in unit.metadata.items():
            if value is None:
                continue
            text = str(value).strip()
            if text:
                metadata_values.setdefault(key, set()).add(text)
    planned: list[SegmentVariable] = []
    for key in overrides:
        values = sorted(metadata_values.get(key, set()))
        if values:
            planned.append(SegmentVariable(name=key, values=values, source="override"))
    for key, values_set in sorted(metadata_values.items()):
        if key in overrides:
            continue
        values = sorted(values_set)
        if 2 <= len(values) <= max_values:
            planned.append(SegmentVariable(name=key, values=values, source="auto"))
    return planned


def compute_segment_breakdowns(
    variables: list[SegmentVariable],
    units: list[Unit],
    assignments: list[CodeAssignment],
    codebook: Codebook,
    min_sample_size: int = 2,
) -> list[SegmentBreakdownRow]:
    """Compute per-segment theme respondent percentages."""
    if not variables:
        return []
    units_by_id = {unit.unit_id: unit for unit in units}
    c2t = code_to_theme_map(codebook)
    theme_ids_by_record: dict[str, set[str]] = {}
    metadata_by_record: dict[str, dict[str, Any]] = {}
    for unit in units:
        metadata_by_record.setdefault(unit.record_id, {}).update(unit.metadata)
    for assignment in assignments:
        assigned_unit = units_by_id.get(assignment.unit_id)
        if assigned_unit is None:
            continue
        for entry in assignment.assignments:
            theme_info = c2t.get(entry.code_id)
            if theme_info is not None:
                theme_ids_by_record.setdefault(assigned_unit.record_id, set()).add(
                    theme_info[0]
                )
    rows: list[SegmentBreakdownRow] = []
    for variable in variables:
        records_by_value: dict[str, set[str]] = {}
        for record_id, metadata in metadata_by_record.items():
            value = metadata.get(variable.name)
            if value is not None and str(value).strip():
                records_by_value.setdefault(str(value), set()).add(record_id)
        for value, record_ids in sorted(records_by_value.items()):
            for theme in codebook.themes:
                respondent_count = sum(
                    1
                    for r in record_ids
                    if theme.theme_id in theme_ids_by_record.get(r, set())
                )
                total = len(record_ids)
                rows.append(
                    SegmentBreakdownRow(
                        segment=variable.name,
                        value=value,
                        theme_id=theme.theme_id,
                        respondents=respondent_count,
                        total_respondents=total,
                        pct_respondents=round(100 * respondent_count / total, 1)
                        if total
                        else 0.0,
                        sample_size_guard="ok"
                        if total >= min_sample_size
                        else "small_n",
                    )
                )
    return rows


def compare_segments(
    rows: list[SegmentBreakdownRow], strong_delta_pct: float = 25.0
) -> list[SegmentComparison]:
    """Compare segment values for each theme."""
    grouped: dict[tuple[str, str], list[SegmentBreakdownRow]] = {}
    for row in rows:
        grouped.setdefault((row.segment, row.theme_id), []).append(row)
    comparisons: list[SegmentComparison] = []
    for (segment, theme_id), group in sorted(grouped.items()):
        eligible = [row for row in group if row.sample_size_guard == "ok"]
        if len(eligible) < 2:
            continue
        high = max(eligible, key=lambda row: row.pct_respondents)
        low = min(eligible, key=lambda row: row.pct_respondents)
        delta = round(high.pct_respondents - low.pct_respondents, 1)
        if delta <= 0:
            continue
        comparisons.append(
            SegmentComparison(
                segment=segment,
                theme_id=theme_id,
                high_value=high.value,
                low_value=low.value,
                high_pct=high.pct_respondents,
                low_pct=low.pct_respondents,
                delta_pct=delta,
                signal="strong" if delta >= strong_delta_pct else "weak",
                note=(
                    f"{theme_id} is {delta} percentage points"
                    f" higher for {high.value} than {low.value}."
                ),
            )
        )
    return comparisons


@registry.register(
    NodeMetadata(
        name="CodedDataIngestNode",
        description="Parse a coded-data CSV into units, assignments, quantification",
        category="workflow",
    )
)
class CodedDataIngestNode(TaskNode):
    """Reconstruct a coded dataset and compute theme frequencies and segments."""

    source_payload: Any | None = None
    units: Any | None = None
    code_assignments: Any | None = None
    approved_codebook: Any | None = None
    allow_chained_results: bool = False

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Parse the coded-data CSV and emit quantification artefacts."""
        source = (
            dict(self.source_payload)
            if isinstance(self.source_payload, dict)
            else get_source_payload(state) or {}
        )
        content = source.get("content") or ""
        parsed = parse_coded_data_csv(content) if content else None
        if parsed is None and self.allow_chained_results:
            units = coerce_model_list(self.units, Unit) or get_units(state)
            assignments = coerce_model_list(
                self.code_assignments, CodeAssignment
            ) or get_code_assignments(state)
            codebook = coerce_model(self.approved_codebook, Codebook)
            if units and assignments and codebook is not None:
                parsed = (units, assignments, codebook)
        if parsed is None:
            return {
                "assistant_message": (
                    "No coded data file was found. Please upload the "
                    "`coded_data.csv` exported by the Theme Coder."
                ),
                "halt": True,
            }
        units, assignments, reconstructed_codebook = parsed
        codebook = (
            coerce_model(self.approved_codebook, Codebook) or reconstructed_codebook
        )
        if codebook is None or not assignments:
            return {
                "assistant_message": (
                    "The coded data file did not contain any code assignments. "
                    "Please re-run the Theme Coder and upload its output."
                ),
                "halt": True,
            }
        codebook = normalise_codebook_ids(codebook)

        quantification, cooccurrence = compute_quantification(
            units, assignments, codebook
        )
        variables = plan_segments(
            units,
            parse_str_list(get_configurable(config).get("segment_variables")),
        )
        breakdowns = compute_segment_breakdowns(variables, units, assignments, codebook)
        comparisons = compare_segments(breakdowns)
        return {
            "halt": False,
            "unit_count": len(units),
            "assignment_count": sum(len(a.assignments) for a in assignments),
            "units": [u.model_dump(mode="json") for u in units],
            "code_assignments": [a.model_dump(mode="json") for a in assignments],
            "approved_codebook": codebook.model_dump(mode="json"),
            "quantification": [r.model_dump(mode="json") for r in quantification],
            "cooccurrence": [r.model_dump(mode="json") for r in cooccurrence],
            "segment_breakdowns": [r.model_dump(mode="json") for r in breakdowns],
            "segment_comparisons": [r.model_dump(mode="json") for r in comparisons],
        }


__all__ = [
    "CodedDataIngestNode",
    "compare_segments",
    "compute_quantification",
    "compute_segment_breakdowns",
    "parse_str_list",
    "plan_segments",
]
