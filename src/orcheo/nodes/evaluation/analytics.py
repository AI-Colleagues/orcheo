"""Analytics export node for evaluation workflows."""

from __future__ import annotations
import json
from collections import defaultdict
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


@registry.register(
    NodeMetadata(
        name="AnalyticsExportNode",
        description="Aggregate evaluation metrics and feedback for export.",
        category="conversational_search",
    )
)
class AnalyticsExportNode(TaskNode):
    """Summarize evaluation outputs into a transport-friendly bundle.

    Operates in two modes:

    **Evaluation mode** (when ``metric_node_names`` is non-empty):
    Collects metric results from parallel branches, builds per-conversation
    breakdowns, captures pipeline configuration, and produces the report
    schema specified in the design document.

    **Feedback mode** (default / when ``metric_node_names`` is empty):
    Aggregates metrics and user-feedback into an export payload.
    """

    # --- Shared fields ---
    metrics_key: str = Field(default="metrics")
    feedback_key: str = Field(default="feedback")

    # --- Evaluation-mode fields ---
    dataset_name: str = Field(
        default="",
        description="Dataset identifier for the evaluation report",
    )
    metric_node_names: list[str] | str = Field(
        default_factory=list,
        description="Names of upstream metric nodes whose results to merge",
    )
    batch_eval_node_name: str = Field(
        default="batch_eval",
        description=(
            "Name of the ConversationalBatchEvalNode for per-conversation data"
        ),
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Route to evaluation or feedback mode based on configuration."""
        node_names = self._resolve_metric_node_names()
        if node_names:
            return self._merge_evaluation_metrics(state, node_names)
        return self._aggregate_feedback(state)

    # ------------------------------------------------------------------
    # Evaluation mode
    # ------------------------------------------------------------------

    def _resolve_metric_node_names(self) -> list[str]:
        if isinstance(self.metric_node_names, str):
            try:
                parsed = json.loads(self.metric_node_names)
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                return [self.metric_node_names] if self.metric_node_names else []
        return list(self.metric_node_names)

    def _merge_evaluation_metrics(
        self,
        state: State,
        node_names: list[str],
    ) -> dict[str, Any]:
        """Collect metric-node outputs and build the design-specified report."""
        results = state.get("node_results", {})

        # Gather metric results from named nodes.
        metric_results: list[dict[str, Any]] = []
        for name in node_names:
            entry = results.get(name)
            if isinstance(entry, dict) and "metric_name" in entry:
                metric_results.append(entry)

        # Corpus-level metrics dict.
        metrics: dict[str, float] = {
            r["metric_name"]: r["corpus_score"] for r in metric_results
        }

        # Per-conversation breakdowns.
        per_conversation = self._build_per_conversation(results, metric_results)

        # Capture pipeline configuration snapshot.
        pipeline_config: dict[str, Any] = {}
        state_config = state.get("config")
        if isinstance(state_config, dict):
            configurable = state_config.get("configurable")
            if isinstance(configurable, dict):
                pipeline_config = dict(configurable)

        report: dict[str, Any] = {
            "dataset": self.dataset_name,
            "metrics": metrics,
            "per_conversation": per_conversation,
            "config": pipeline_config,
        }

        return {
            "report": report,
            "report_json": json.dumps(report, indent=2),
            "table": "\n".join(self._format_table(metrics)),
        }

    def _build_per_conversation(
        self,
        results: dict[str, Any],
        metric_results: list[dict[str, Any]],
    ) -> dict[str, dict[str, float]]:
        """Slice per-item scores by conversation boundaries."""
        batch_data = results.get(self.batch_eval_node_name)
        if not isinstance(batch_data, dict):
            return {}

        per_conv_data = batch_data.get("per_conversation")
        if not isinstance(per_conv_data, dict):
            return {}

        per_conversation: dict[str, dict[str, float]] = {}
        offset = 0

        for conv_id, conv_info in per_conv_data.items():
            num_turns = conv_info.get("num_turns", 0)
            conv_metrics: dict[str, float] = {}

            for mr in metric_results:
                per_item = mr.get("per_item", [])
                conv_scores = per_item[offset : offset + num_turns]
                if conv_scores:
                    conv_metrics[mr["metric_name"]] = sum(conv_scores) / len(
                        conv_scores
                    )
                else:
                    conv_metrics[mr["metric_name"]] = 0.0

            per_conversation[conv_id] = conv_metrics
            offset += num_turns

        return per_conversation

    @staticmethod
    def _format_table(metrics: dict[str, float]) -> list[str]:
        header = f"{'Metric':<30} {'Score':>10}"
        lines = [header, "-" * len(header)]
        for name, score in sorted(metrics.items()):
            lines.append(f"{name:<30} {score:>10.4f}")
        return lines

    # ------------------------------------------------------------------
    # Feedback mode
    # ------------------------------------------------------------------

    def _aggregate_feedback(self, state: State) -> dict[str, Any]:
        """Aggregate metrics and feedback into an export payload."""
        inputs = state.get("inputs", {})
        metrics = inputs.get(self.metrics_key, {}) or {}
        feedback = inputs.get(self.feedback_key, []) or []
        if not isinstance(feedback, list):
            msg = "AnalyticsExportNode expects feedback to be a list when provided"
            raise ValueError(msg)

        ratings = [
            entry.get("rating", 0) for entry in feedback if isinstance(entry, dict)
        ]
        average_rating = sum(ratings) / len(ratings) if ratings else 0.0
        counts: dict[str, int] = defaultdict(int)
        for entry in feedback:
            if category := entry.get("category"):
                counts[str(category)] += 1

        export_payload = {
            "metrics": metrics,
            "feedback_count": len(feedback),
            "average_rating": average_rating,
            "feedback_categories": dict(counts),
        }
        return {"export": export_payload}
