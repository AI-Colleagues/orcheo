"""Tests for AnalyticsExportNode."""

import json
import pytest
from orcheo.graph.state import State
from orcheo.nodes.evaluation.analytics import AnalyticsExportNode
from orcheo.nodes.evaluation.feedback import (
    FeedbackIngestionNode,
    UserFeedbackCollectionNode,
)


# --- Feedback mode (legacy) tests ---


@pytest.mark.asyncio
async def test_feedback_collection_ingestion_and_export() -> None:
    collector = UserFeedbackCollectionNode(name="collector")
    feedback = await collector.run(
        State(inputs={"rating": 4, "comment": "Nice", "session_id": "s1"}),
        {},
    )
    ingestor = FeedbackIngestionNode(name="ingestor")
    ingestion_result = await ingestor.run(State(inputs=feedback), {})
    assert ingestion_result["ingested"] == 1

    exporter = AnalyticsExportNode(name="exporter")
    exported = await exporter.run(
        State(
            inputs={"metrics": {"recall_at_k": 0.9}, "feedback": [feedback["feedback"]]}
        ),
        {},
    )
    assert exported["export"]["average_rating"] == 4.0
    assert exported["export"]["metrics"]["recall_at_k"] == 0.9


@pytest.mark.asyncio
async def test_analytics_export_validates_feedback_and_counts_categories() -> None:
    exporter = AnalyticsExportNode(name="exporter")
    with pytest.raises(ValueError, match="expects feedback to be a list"):
        await exporter.run(State(inputs={"feedback": {"rating": 5}}), {})

    payload = await exporter.run(
        State(
            inputs={
                "metrics": {"score": 1.0},
                "feedback": [
                    {"rating": 4, "category": "praise"},
                    {"rating": 3, "category": "praise"},
                    {"rating": 5, "category": "issue"},
                ],
            }
        ),
        {},
    )
    assert payload["export"]["feedback_categories"] == {"praise": 2, "issue": 1}
    assert payload["export"]["feedback_count"] == 3


# --- Evaluation mode tests ---


@pytest.mark.asyncio
async def test_analytics_evaluation_mode_merges_metrics() -> None:
    """AnalyticsExportNode merges metric results from named nodes."""
    node = AnalyticsExportNode(
        name="analytics",
        dataset_name="test_dataset",
        metric_node_names=["rouge", "bleu"],
        batch_eval_node_name="batch_eval",
    )
    state = State(
        node_results={
            "rouge": {
                "metric_name": "rougeL_fmeasure",
                "corpus_score": 0.75,
                "per_item": [0.8, 0.7],
            },
            "bleu": {
                "metric_name": "sacrebleu",
                "corpus_score": 30.0,
                "per_item": [40.0, 20.0],
            },
            "batch_eval": {
                "per_conversation": {
                    "conv1": {"num_turns": 1, "predictions": ["p1"]},
                    "conv2": {"num_turns": 1, "predictions": ["p2"]},
                },
            },
        },
    )

    result = await node.run(state, {})

    report = result["report"]
    assert report["dataset"] == "test_dataset"
    assert report["metrics"]["rougeL_fmeasure"] == 0.75
    assert report["metrics"]["sacrebleu"] == 30.0


@pytest.mark.asyncio
async def test_analytics_evaluation_mode_per_conversation_breakdown() -> None:
    """Per-conversation scores sliced correctly from per_item arrays."""
    node = AnalyticsExportNode(
        name="analytics",
        dataset_name="qrecc",
        metric_node_names=["token_f1"],
        batch_eval_node_name="batch_eval",
    )
    state = State(
        node_results={
            "token_f1": {
                "metric_name": "token_f1",
                "corpus_score": 0.5,
                "per_item": [0.8, 0.6, 0.2],
            },
            "batch_eval": {
                "per_conversation": {
                    "conv1": {"num_turns": 2, "predictions": ["p1", "p2"]},
                    "conv2": {"num_turns": 1, "predictions": ["p3"]},
                },
            },
        },
    )

    result = await node.run(state, {})

    per_conv = result["report"]["per_conversation"]
    assert per_conv["conv1"]["token_f1"] == pytest.approx(0.7)
    assert per_conv["conv2"]["token_f1"] == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_analytics_evaluation_mode_captures_config() -> None:
    """Pipeline config captured from state config.configurable."""
    node = AnalyticsExportNode(
        name="analytics",
        dataset_name="test",
        metric_node_names=["rouge"],
        batch_eval_node_name="batch_eval",
    )
    state = State(
        node_results={
            "rouge": {
                "metric_name": "rouge1_recall",
                "corpus_score": 0.9,
                "per_item": [],
            },
            "batch_eval": {"per_conversation": {}},
        },
        config={"configurable": {"model": "gpt-4", "top_k": 5}},
    )

    result = await node.run(state, {})

    assert result["report"]["config"]["model"] == "gpt-4"
    assert result["report"]["config"]["top_k"] == 5


@pytest.mark.asyncio
async def test_analytics_evaluation_mode_produces_json_and_table() -> None:
    """Output includes report_json and table strings."""
    node = AnalyticsExportNode(
        name="analytics",
        dataset_name="test",
        metric_node_names=["f1"],
        batch_eval_node_name="batch_eval",
    )
    state = State(
        node_results={
            "f1": {
                "metric_name": "token_f1",
                "corpus_score": 0.85,
                "per_item": [],
            },
            "batch_eval": {"per_conversation": {}},
        },
    )

    result = await node.run(state, {})

    assert "report_json" in result
    parsed = json.loads(result["report_json"])
    assert parsed["dataset"] == "test"
    assert "table" in result
    assert "token_f1" in result["table"]


@pytest.mark.asyncio
async def test_analytics_metric_node_names_as_json_string() -> None:
    """metric_node_names can be a JSON-encoded string list."""
    node = AnalyticsExportNode(
        name="analytics",
        dataset_name="test",
        metric_node_names='["rouge", "bleu"]',
        batch_eval_node_name="batch_eval",
    )
    state = State(
        node_results={
            "rouge": {
                "metric_name": "rougeL_fmeasure",
                "corpus_score": 0.5,
                "per_item": [],
            },
            "bleu": {
                "metric_name": "sacrebleu",
                "corpus_score": 25.0,
                "per_item": [],
            },
            "batch_eval": {"per_conversation": {}},
        },
    )

    result = await node.run(state, {})

    assert result["report"]["metrics"]["rougeL_fmeasure"] == 0.5
    assert result["report"]["metrics"]["sacrebleu"] == 25.0


@pytest.mark.asyncio
async def test_analytics_metric_node_names_as_plain_string() -> None:
    """A plain (non-JSON) string metric_node_names is treated as a single name."""
    node = AnalyticsExportNode(
        name="analytics",
        dataset_name="test",
        metric_node_names="rouge",
        batch_eval_node_name="batch_eval",
    )
    state = State(
        node_results={
            "rouge": {
                "metric_name": "rougeL_fmeasure",
                "corpus_score": 0.5,
                "per_item": [],
            },
            "batch_eval": {"per_conversation": {}},
        },
    )

    result = await node.run(state, {})

    assert result["report"]["metrics"]["rougeL_fmeasure"] == 0.5


@pytest.mark.asyncio
async def test_analytics_metric_node_names_json_non_list() -> None:
    """JSON-encoded non-list (e.g. a string) falls back to empty list."""
    node = AnalyticsExportNode(
        name="analytics",
        dataset_name="test",
        metric_node_names='"just_a_string"',
    )
    state = State(inputs={})
    result = await node.run(state, {})

    # Falls back to feedback mode because _resolve returns []
    assert "export" in result


@pytest.mark.asyncio
async def test_analytics_metric_node_names_empty_string() -> None:
    """Empty string metric_node_names falls back to feedback mode."""
    node = AnalyticsExportNode(
        name="analytics",
        metric_node_names="",
    )
    state = State(inputs={})
    result = await node.run(state, {})

    assert "export" in result


@pytest.mark.asyncio
async def test_analytics_skips_non_metric_results() -> None:
    """Entries without 'metric_name' are skipped during metric merging."""
    node = AnalyticsExportNode(
        name="analytics",
        dataset_name="test",
        metric_node_names=["rouge", "missing"],
        batch_eval_node_name="batch_eval",
    )
    state = State(
        node_results={
            "rouge": {
                "metric_name": "rougeL_fmeasure",
                "corpus_score": 0.75,
                "per_item": [],
            },
            "missing": {"some_other_key": "no metric_name"},
            "batch_eval": {"per_conversation": {}},
        },
    )

    result = await node.run(state, {})

    assert "rougeL_fmeasure" in result["report"]["metrics"]
    assert len(result["report"]["metrics"]) == 1


@pytest.mark.asyncio
async def test_analytics_config_not_dict() -> None:
    """When state config is not a dict, pipeline_config stays empty."""
    node = AnalyticsExportNode(
        name="analytics",
        dataset_name="test",
        metric_node_names=["rouge"],
        batch_eval_node_name="batch_eval",
    )
    state = State(
        node_results={
            "rouge": {
                "metric_name": "rouge1",
                "corpus_score": 0.5,
                "per_item": [],
            },
            "batch_eval": {"per_conversation": {}},
        },
        config="not_a_dict",
    )

    result = await node.run(state, {})

    assert result["report"]["config"] == {}


@pytest.mark.asyncio
async def test_analytics_configurable_not_dict() -> None:
    """When configurable is not a dict, pipeline_config stays empty."""
    node = AnalyticsExportNode(
        name="analytics",
        dataset_name="test",
        metric_node_names=["rouge"],
        batch_eval_node_name="batch_eval",
    )
    state = State(
        node_results={
            "rouge": {
                "metric_name": "rouge1",
                "corpus_score": 0.5,
                "per_item": [],
            },
            "batch_eval": {"per_conversation": {}},
        },
        config={"configurable": "not_a_dict"},
    )

    result = await node.run(state, {})

    assert result["report"]["config"] == {}


@pytest.mark.asyncio
async def test_analytics_batch_data_not_dict() -> None:
    """When batch_eval result is not a dict, per_conversation is empty."""
    node = AnalyticsExportNode(
        name="analytics",
        dataset_name="test",
        metric_node_names=["rouge"],
        batch_eval_node_name="batch_eval",
    )
    state = State(
        node_results={
            "rouge": {
                "metric_name": "rouge1",
                "corpus_score": 0.5,
                "per_item": [0.5],
            },
            "batch_eval": "not_a_dict",
        },
    )

    result = await node.run(state, {})

    assert result["report"]["per_conversation"] == {}


@pytest.mark.asyncio
async def test_analytics_per_conv_data_not_dict() -> None:
    """When per_conversation sub-key is not a dict, returns empty."""
    node = AnalyticsExportNode(
        name="analytics",
        dataset_name="test",
        metric_node_names=["rouge"],
        batch_eval_node_name="batch_eval",
    )
    state = State(
        node_results={
            "rouge": {
                "metric_name": "rouge1",
                "corpus_score": 0.5,
                "per_item": [0.5],
            },
            "batch_eval": {"per_conversation": "not_a_dict"},
        },
    )

    result = await node.run(state, {})

    assert result["report"]["per_conversation"] == {}


@pytest.mark.asyncio
async def test_analytics_per_conversation_empty_scores() -> None:
    """When per_item scores are exhausted, remaining convs get 0.0."""
    node = AnalyticsExportNode(
        name="analytics",
        dataset_name="test",
        metric_node_names=["f1"],
        batch_eval_node_name="batch_eval",
    )
    state = State(
        node_results={
            "f1": {
                "metric_name": "token_f1",
                "corpus_score": 0.5,
                "per_item": [0.8],  # only 1 score, but conv2 expects offset 1+
            },
            "batch_eval": {
                "per_conversation": {
                    "conv1": {"num_turns": 1},
                    "conv2": {"num_turns": 1},
                },
            },
        },
    )

    result = await node.run(state, {})

    per_conv = result["report"]["per_conversation"]
    assert per_conv["conv1"]["token_f1"] == pytest.approx(0.8)
    assert per_conv["conv2"]["token_f1"] == 0.0
