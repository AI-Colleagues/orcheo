"""Result-channel field names and producer wiring for qualitative workflows."""

from __future__ import annotations
from pydantic import BaseModel


class QualitativeResultKeys(BaseModel):
    """Result-channel names used by a qualitative workflow.

    Each logical field has a ``*_field`` name (the key written under
    ``results[<node>]``) and a ``*_producers`` tuple listing the nodes that may
    emit it, most-recent-first. Accessors read the first producer that carries a
    value, so a workflow specialises the shared nodes purely by passing a
    customised instance of this model.
    """

    # --- field names --------------------------------------------------------
    research_objective_field: str = "research_objective"
    source_payload_field: str = "source_payload"
    pending_documents_field: str = "pending_documents"
    seed_codebook_field: str = "seed_codebook_from_file"
    approved_codebook_field: str = "approved_codebook"
    units_field: str = "units"
    assignments_field: str = "code_assignments_pass1"
    draft_codebook_field: str = "draft_codebook"
    quality_report_field: str = "quality_report"
    quantification_field: str = "quantification"
    cooccurrence_field: str = "cooccurrence"
    segment_breakdowns_field: str = "segment_breakdowns"
    segment_comparisons_field: str = "segment_comparisons"
    selected_quotes_field: str = "selected_quotes"
    candidate_insights_field: str = "candidate_insights"
    recommendations_field: str = "recommendations"
    approved_insight_ids_field: str = "approved_insight_ids"

    # --- producers ----------------------------------------------------------
    research_objective_producers: tuple[str, ...] = ("setup", "router_dispatch")
    source_payload_producers: tuple[str, ...] = ("ingest", "setup")
    pending_documents_producers: tuple[str, ...] = ()
    seed_codebook_producers: tuple[str, ...] = ()
    approved_codebook_producers: tuple[str, ...] = ("setup",)
    units_producers: tuple[str, ...] = ("ingest",)
    assignments_producers: tuple[str, ...] = ("open_coder_finalize",)
    draft_codebook_producers: tuple[str, ...] = (
        "codebook_consolidator_finalize",
        "export_codebook",
    )
    quality_report_producers: tuple[str, ...] = ("data_quality",)
    quantification_producers: tuple[str, ...] = ("ingest",)
    cooccurrence_producers: tuple[str, ...] = ("ingest",)
    segment_breakdowns_producers: tuple[str, ...] = ("ingest",)
    segment_comparisons_producers: tuple[str, ...] = ("ingest",)
    selected_quotes_producers: tuple[str, ...] = ("quote_selector_finalize",)
    candidate_insights_producers: tuple[str, ...] = (
        "recommendation_generator",
        "insight_critic",
        "insight_generator_finalize",
    )
    recommendations_producers: tuple[str, ...] = ("recommendation_generator",)
    approved_insight_ids_producers: tuple[str, ...] = ("recommendation_generator",)


__all__ = ["QualitativeResultKeys"]
