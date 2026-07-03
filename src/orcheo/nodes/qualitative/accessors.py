"""Results-channel accessors for the qualitative-analysis workflows.

Each ``get_*`` reads a logical field from the first listed producer node that
carries it, coercing the serialized payload back into the right model. A
:class:`QualitativeResultKeys` instance supplies the field names and producer
order, so the same accessors serve every workflow.
"""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel
from orcheo.graph.state import State
from orcheo.nodes.qualitative.keys import QualitativeResultKeys
from orcheo.nodes.qualitative.models import (
    CandidateInsight,
    CodeAssignment,
    Codebook,
    CooccurrenceRow,
    QualityReport,
    QuantificationRow,
    Quote,
    Recommendation,
    ReportData,
    SegmentBreakdownRow,
    SegmentComparison,
    Unit,
)
from orcheo.runtime.results import first_result_field


def get_configurable(config: RunnableConfig | None) -> Mapping[str, Any]:
    """Return the ``configurable`` mapping from the runnable config."""
    block = config.get("configurable") if isinstance(config, Mapping) else None
    return block if isinstance(block, Mapping) else {}


def is_vacuous(text: str) -> bool:
    """Return True when a string is missing or trivially short."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    return len(stripped.split()) < 3


def _coerce_models[ModelT: BaseModel](value: Any, model: type[ModelT]) -> list[ModelT]:
    """Coerce a list of serialized payloads back into model instances."""
    if not isinstance(value, list):
        return []
    out: list[ModelT] = []
    for item in value:
        try:
            out.append(item if isinstance(item, model) else model.model_validate(item))
        except Exception:  # noqa: BLE001
            continue
    return out


def _coerce_model[ModelT: BaseModel](value: Any, model: type[ModelT]) -> ModelT | None:
    """Coerce a single serialized payload back into a model instance."""
    if value is None:
        return None
    try:
        return value if isinstance(value, model) else model.model_validate(value)
    except Exception:  # noqa: BLE001
        return None


def coerce_model_list[ModelT: BaseModel](
    value: Any, model: type[ModelT]
) -> list[ModelT]:
    """Coerce a raw or template-resolved value into a list of model instances."""
    return _coerce_models(value, model)


def coerce_model[ModelT: BaseModel](value: Any, model: type[ModelT]) -> ModelT | None:
    """Coerce a raw or template-resolved value into a model instance."""
    return _coerce_model(value, model)


def coerce_pending_documents(value: Any) -> list[dict[str, Any]]:
    """Coerce a raw or template-resolved value into loaded document mappings."""
    return (
        [dict(doc) for doc in value if isinstance(doc, Mapping)]
        if isinstance(value, list)
        else []
    )


def _keys(keys: QualitativeResultKeys | None) -> QualitativeResultKeys:
    return keys or QualitativeResultKeys()


def get_research_objective(
    state: State, keys: QualitativeResultKeys | None = None
) -> str | None:
    """Return the research objective carried on the results channel."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.research_objective_field, keys.research_objective_producers
    )
    return value if isinstance(value, str) and value.strip() else None


def get_source_payload(
    state: State, keys: QualitativeResultKeys | None = None
) -> dict[str, Any] | None:
    """Return the resolved source payload carried on the results channel."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.source_payload_field, keys.source_payload_producers
    )
    return dict(value) if isinstance(value, Mapping) else None


def get_pending_documents(
    state: State, keys: QualitativeResultKeys | None = None
) -> list[dict[str, Any]]:
    """Return the documents loaded by the context node (may be empty)."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.pending_documents_field, keys.pending_documents_producers
    )
    return coerce_pending_documents(value)


def get_seed_codebook_from_file(
    state: State, keys: QualitativeResultKeys | None = None
) -> dict[str, Any] | None:
    """Return a seed codebook resolved from an uploaded file, if any."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.seed_codebook_field, keys.seed_codebook_producers
    )
    return dict(value) if isinstance(value, Mapping) else None


def get_approved_codebook(
    state: State, keys: QualitativeResultKeys | None = None
) -> Codebook | None:
    """Return the approved (or reconstructed) codebook."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.approved_codebook_field, keys.approved_codebook_producers
    )
    return _coerce_model(value, Codebook)


def get_units(state: State, keys: QualitativeResultKeys | None = None) -> list[Unit]:
    """Return the ingested units, coerced from their serialized form."""
    keys = _keys(keys)
    value = first_result_field(state, keys.units_field, keys.units_producers)
    return _coerce_models(value, Unit)


def get_code_assignments(
    state: State, keys: QualitativeResultKeys | None = None
) -> list[CodeAssignment]:
    """Return the code assignments reconstructed from the results channel."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.assignments_field, keys.assignments_producers
    )
    return _coerce_models(value, CodeAssignment)


def get_draft_codebook(
    state: State, keys: QualitativeResultKeys | None = None
) -> Codebook | None:
    """Return the draft codebook produced by the consolidation stage."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.draft_codebook_field, keys.draft_codebook_producers
    )
    return _coerce_model(value, Codebook)


def get_quality_report(
    state: State, keys: QualitativeResultKeys | None = None
) -> QualityReport | None:
    """Return the data-quality report produced by the quality stage."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.quality_report_field, keys.quality_report_producers
    )
    return _coerce_model(value, QualityReport)


def get_quantification(
    state: State, keys: QualitativeResultKeys | None = None
) -> list[QuantificationRow]:
    """Return the per-theme quantification rows."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.quantification_field, keys.quantification_producers
    )
    return _coerce_models(value, QuantificationRow)


def get_cooccurrence(
    state: State, keys: QualitativeResultKeys | None = None
) -> list[CooccurrenceRow]:
    """Return the pairwise theme co-occurrence rows."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.cooccurrence_field, keys.cooccurrence_producers
    )
    return _coerce_models(value, CooccurrenceRow)


def get_segment_breakdowns(
    state: State, keys: QualitativeResultKeys | None = None
) -> list[SegmentBreakdownRow]:
    """Return the per-segment breakdown rows."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.segment_breakdowns_field, keys.segment_breakdowns_producers
    )
    return _coerce_models(value, SegmentBreakdownRow)


def get_segment_comparisons(
    state: State, keys: QualitativeResultKeys | None = None
) -> list[SegmentComparison]:
    """Return the segment comparison rows."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.segment_comparisons_field, keys.segment_comparisons_producers
    )
    return _coerce_models(value, SegmentComparison)


def get_selected_quotes(
    state: State, keys: QualitativeResultKeys | None = None
) -> list[Quote]:
    """Return the quotes chosen by the quote selector."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.selected_quotes_field, keys.selected_quotes_producers
    )
    return _coerce_models(value, Quote)


def get_candidate_insights(
    state: State, keys: QualitativeResultKeys | None = None
) -> list[CandidateInsight]:
    """Return the latest candidate insights (generator -> critic -> recommender)."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.candidate_insights_field, keys.candidate_insights_producers
    )
    return _coerce_models(value, CandidateInsight)


def get_recommendations(
    state: State, keys: QualitativeResultKeys | None = None
) -> list[Recommendation]:
    """Return the generated recommendations."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.recommendations_field, keys.recommendations_producers
    )
    return _coerce_models(value, Recommendation)


def get_approved_insight_ids(
    state: State, keys: QualitativeResultKeys | None = None
) -> list[str]:
    """Return the approved insight ids selected for the report."""
    keys = _keys(keys)
    value = first_result_field(
        state, keys.approved_insight_ids_field, keys.approved_insight_ids_producers
    )
    return [str(item) for item in value] if isinstance(value, list) else []


def build_report_data(
    state: State, keys: QualitativeResultKeys | None = None
) -> ReportData:
    """Assemble a :class:`ReportData` view from the ``results`` channel."""
    keys = _keys(keys)
    return ReportData(
        research_objective=get_research_objective(state, keys),
        pending_documents=get_pending_documents(state, keys),
        source_payload=get_source_payload(state, keys),
        units=get_units(state, keys),
        approved_codebook=get_approved_codebook(state, keys),
        code_assignments_pass2=get_code_assignments(state, keys),
        quantification=get_quantification(state, keys),
        cooccurrence=get_cooccurrence(state, keys),
        segment_breakdowns=get_segment_breakdowns(state, keys),
        segment_comparisons=get_segment_comparisons(state, keys),
        selected_quotes=get_selected_quotes(state, keys),
        candidate_insights=get_candidate_insights(state, keys),
        recommendations=get_recommendations(state, keys),
        approved_insight_ids=get_approved_insight_ids(state, keys),
    )


__all__ = [
    "build_report_data",
    "coerce_model",
    "coerce_model_list",
    "coerce_pending_documents",
    "get_approved_codebook",
    "get_approved_insight_ids",
    "get_candidate_insights",
    "get_code_assignments",
    "get_configurable",
    "get_cooccurrence",
    "get_draft_codebook",
    "get_pending_documents",
    "get_quality_report",
    "get_quantification",
    "get_recommendations",
    "get_research_objective",
    "get_seed_codebook_from_file",
    "get_segment_breakdowns",
    "get_segment_comparisons",
    "get_selected_quotes",
    "get_source_payload",
    "get_units",
    "is_vacuous",
]
