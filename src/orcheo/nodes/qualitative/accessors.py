"""Results-channel accessors for the qualitative-analysis workflows.

Each ``get_*`` reads a logical field from the first producer node that carries
it, coercing the serialized payload back into the right model. The colleague
workflows wire node inputs explicitly via templates, so these accessors only
cover the shared fallback wiring with fixed field names and producers.
"""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel
from orcheo.graph.state import State
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
from orcheo.runtime.results import first_result_field, node_result


def get_configurable(config: RunnableConfig | None) -> Mapping[str, Any]:
    """Return the ``configurable`` mapping from the runnable config."""
    block = config.get("configurable") if isinstance(config, Mapping) else None
    return block if isinstance(block, Mapping) else {}


def ingest_halt_message(
    state: State,
    ingest_node_name: str,
    *,
    default: str = "Ingest failed.",
) -> str | None:
    """Return the ingest node's early-halt message, or ``None`` if it did not halt.

    ``IngestNode`` sets ``assistant_message`` (a declared ``State`` field) on an
    early halt, which ``build_task_state_update`` hoists to the top level instead
    of nesting it under ``node_results``. Prefer the node-scoped value (older
    runs / other producers), then the hoisted top-level field, and only fall back
    to a generic message when neither is present.
    """
    early = node_result(state, ingest_node_name)
    if not early.get("halt"):
        return None
    message = early.get("assistant_message") or (
        state.get("assistant_message") if isinstance(state, Mapping) else None
    )
    return str(message) if message else default


def coerce_model_list[ModelT: BaseModel](
    value: Any, model: type[ModelT]
) -> list[ModelT]:
    """Coerce a raw or template-resolved value into a list of model instances."""
    if not isinstance(value, list):
        return []
    out: list[ModelT] = []
    for item in value:
        try:
            out.append(item if isinstance(item, model) else model.model_validate(item))
        except Exception:  # noqa: BLE001
            continue
    return out


def coerce_model[ModelT: BaseModel](value: Any, model: type[ModelT]) -> ModelT | None:
    """Coerce a raw or template-resolved value into a model instance."""
    if value is None:
        return None
    try:
        return value if isinstance(value, model) else model.model_validate(value)
    except Exception:  # noqa: BLE001
        return None


def coerce_pending_documents(value: Any) -> list[dict[str, Any]]:
    """Coerce a raw or template-resolved value into loaded document mappings."""
    return (
        [dict(doc) for doc in value if isinstance(doc, Mapping)]
        if isinstance(value, list)
        else []
    )


def get_source_payload(state: State) -> dict[str, Any] | None:
    """Return the resolved source payload carried on the node_results channel."""
    value = first_result_field(state, "source_payload", ("ingest",))
    return dict(value) if isinstance(value, Mapping) else None


def get_pending_documents(state: State) -> list[dict[str, Any]]:
    """Return the documents loaded by the attachments node (may be empty)."""
    value = first_result_field(state, "attachments", ("load_attachments",))
    return coerce_pending_documents(value)


def get_units(state: State) -> list[Unit]:
    """Return the ingested units, coerced from their serialized form."""
    value = first_result_field(state, "units", ("ingest",))
    return coerce_model_list(value, Unit)


def get_code_assignments(state: State) -> list[CodeAssignment]:
    """Return the code assignments reconstructed from the node_results channel."""
    value = first_result_field(
        state, "code_assignments_pass1", ("open_coder_finalize",)
    )
    return coerce_model_list(value, CodeAssignment)


def get_draft_codebook(state: State) -> Codebook | None:
    """Return the draft codebook produced by the consolidation stage."""
    value = first_result_field(
        state, "draft_codebook", ("codebook_consolidator_finalize",)
    )
    return coerce_model(value, Codebook)


def get_quality_report(state: State) -> QualityReport | None:
    """Return the data-quality report produced by the quality stage."""
    value = first_result_field(state, "quality_report", ("data_quality",))
    return coerce_model(value, QualityReport)


def get_quantification(state: State) -> list[QuantificationRow]:
    """Return the per-theme quantification rows."""
    value = first_result_field(state, "quantification", ("ingest",))
    return coerce_model_list(value, QuantificationRow)


def get_cooccurrence(state: State) -> list[CooccurrenceRow]:
    """Return the pairwise theme co-occurrence rows."""
    value = first_result_field(state, "cooccurrence", ("ingest",))
    return coerce_model_list(value, CooccurrenceRow)


def get_segment_breakdowns(state: State) -> list[SegmentBreakdownRow]:
    """Return the per-segment breakdown rows."""
    value = first_result_field(state, "segment_breakdowns", ("ingest",))
    return coerce_model_list(value, SegmentBreakdownRow)


def get_segment_comparisons(state: State) -> list[SegmentComparison]:
    """Return the segment comparison rows."""
    value = first_result_field(state, "segment_comparisons", ("ingest",))
    return coerce_model_list(value, SegmentComparison)


def get_selected_quotes(state: State) -> list[Quote]:
    """Return the quotes chosen by the quote selector."""
    value = first_result_field(state, "selected_quotes", ("quote_selector_finalize",))
    return coerce_model_list(value, Quote)


def get_candidate_insights(state: State) -> list[CandidateInsight]:
    """Return the latest candidate insights (generator -> critic -> recommender)."""
    value = first_result_field(
        state,
        "candidate_insights",
        ("recommendation_generator", "insight_critic", "insight_generator_finalize"),
    )
    return coerce_model_list(value, CandidateInsight)


def get_recommendations(state: State) -> list[Recommendation]:
    """Return the generated recommendations."""
    value = first_result_field(state, "recommendations", ("recommendation_generator",))
    return coerce_model_list(value, Recommendation)


def get_approved_insight_ids(state: State) -> list[str]:
    """Return the approved insight ids selected for the report."""
    value = first_result_field(
        state, "approved_insight_ids", ("recommendation_generator",)
    )
    return [str(item) for item in value] if isinstance(value, list) else []


def build_report_data(state: State) -> ReportData:
    """Assemble a :class:`ReportData` view from the ``node_results`` channel."""
    return ReportData(
        source_payload=get_source_payload(state),
        units=get_units(state),
        code_assignments_pass2=get_code_assignments(state),
        quantification=get_quantification(state),
        cooccurrence=get_cooccurrence(state),
        segment_breakdowns=get_segment_breakdowns(state),
        segment_comparisons=get_segment_comparisons(state),
        selected_quotes=get_selected_quotes(state),
        candidate_insights=get_candidate_insights(state),
        recommendations=get_recommendations(state),
        approved_insight_ids=get_approved_insight_ids(state),
    )


__all__ = [
    "build_report_data",
    "coerce_model",
    "coerce_model_list",
    "coerce_pending_documents",
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
    "get_segment_breakdowns",
    "get_segment_comparisons",
    "get_selected_quotes",
    "get_source_payload",
    "get_units",
    "ingest_halt_message",
]
