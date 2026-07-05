"""Integration tests for QReCC and MultiDoc2Dial evaluation pipelines.

Uses micro-datasets (5 conversations each) to verify the full pipeline:
dataset loading → batch evaluation → metric computation → analytics export.
"""

from typing import Any
import pytest
from orcheo.graph.state import State
from orcheo.nodes.evaluation.analytics import AnalyticsExportNode
from orcheo.nodes.evaluation.batch import ConversationalBatchEvalNode
from orcheo.nodes.evaluation.datasets import (
    MultiDoc2DialDatasetNode,
    QReCCDatasetNode,
)
from orcheo.nodes.evaluation.metrics import (
    BleuMetricsNode,
    RougeMetricsNode,
    TokenF1MetricsNode,
)


# --- Micro-datasets ---

QRECC_MICRO = [
    {
        "Conversation_no": 1,
        "Turn_no": 1,
        "Question": "What is machine learning?",
        "Rewrite": "What is machine learning?",
        "Context": [],
        "Answer": "Machine learning is a subset of AI.",
    },
    {
        "Conversation_no": 1,
        "Turn_no": 2,
        "Question": "How does it work?",
        "Rewrite": "How does machine learning work?",
        "Context": [
            "What is machine learning?",
            "Machine learning is a subset of AI.",
        ],
        "Answer": "It uses statistical models to learn from data.",
    },
    {
        "Conversation_no": 2,
        "Turn_no": 1,
        "Question": "What is deep learning?",
        "Rewrite": "What is deep learning?",
        "Context": [],
        "Answer": "Deep learning uses neural networks.",
    },
    {
        "Conversation_no": 3,
        "Turn_no": 1,
        "Question": "Explain NLP",
        "Rewrite": "Explain natural language processing",
        "Context": [],
        "Answer": "NLP deals with language understanding.",
    },
    {
        "Conversation_no": 3,
        "Turn_no": 2,
        "Question": "What are its applications?",
        "Rewrite": "What are the applications of natural language processing?",
        "Context": [
            "Explain NLP",
            "NLP deals with language understanding.",
        ],
        "Answer": "Translation, summarization, chatbots.",
    },
    {
        "Conversation_no": 4,
        "Turn_no": 1,
        "Question": "What is reinforcement learning?",
        "Rewrite": "What is reinforcement learning?",
        "Context": [],
        "Answer": "RL learns from rewards and penalties.",
    },
    {
        "Conversation_no": 5,
        "Turn_no": 1,
        "Question": "Define computer vision",
        "Rewrite": "Define computer vision",
        "Context": [],
        "Answer": "CV enables machines to interpret images.",
    },
    {
        "Conversation_no": 5,
        "Turn_no": 2,
        "Question": "What tasks can it do?",
        "Rewrite": "What tasks can computer vision do?",
        "Context": [
            "Define computer vision",
            "CV enables machines to interpret images.",
        ],
        "Answer": "Object detection, segmentation, classification.",
    },
]


MD2D_MICRO = [
    {
        "dial_id": "d1",
        "domain": "ssa",
        "turns": [
            {
                "turn_id": "0",
                "user_utterance": "How do I apply for benefits?",
                "response": "You can apply online at ssa.gov.",
                "grounding_spans": [
                    {
                        "doc_id": "doc_ssa_1",
                        "span_text": "apply online at ssa.gov",
                        "start": 8,
                        "end": 30,
                    }
                ],
            },
            {
                "turn_id": "1",
                "user_utterance": "What documents do I need?",
                "response": "You need proof of identity and age.",
                "grounding_spans": [],
            },
        ],
    },
    {
        "dial_id": "d2",
        "domain": "va",
        "turns": [
            {
                "turn_id": "0",
                "user_utterance": "Am I eligible for VA benefits?",
                "response": "Eligibility depends on your service record.",
                "grounding_spans": [],
            },
        ],
    },
    {
        "dial_id": "d3",
        "domain": "studentaid",
        "turns": [
            {
                "turn_id": "0",
                "user_utterance": "How do I fill out the FAFSA?",
                "response": "Visit studentaid.gov and complete the form.",
                "grounding_spans": [],
            },
            {
                "turn_id": "1",
                "user_utterance": "When is the deadline?",
                "response": "The federal deadline is June 30.",
                "grounding_spans": [],
            },
        ],
    },
    {
        "dial_id": "d4",
        "domain": "dmv",
        "turns": [
            {
                "turn_id": "0",
                "user_utterance": "How do I renew my license?",
                "response": "You can renew online or at a DMV office.",
                "grounding_spans": [],
            },
        ],
    },
    {
        "dial_id": "d5",
        "domain": "ssa",
        "turns": [
            {
                "turn_id": "0",
                "user_utterance": "What is the retirement age?",
                "response": "Full retirement age is 67 for people born after 1960.",
                "grounding_spans": [],
            },
        ],
    },
]


# --- QReCC Integration Tests ---


@pytest.mark.asyncio
async def test_qrecc_end_to_end_pipeline() -> None:
    """Full QReCC pipeline: dataset → batch eval → ROUGE-1 recall → report."""
    # Step 1: Load dataset
    dataset_node = QReCCDatasetNode(name="dataset")
    state: State[str, Any] = State(inputs={"qrecc_data": QRECC_MICRO})
    dataset_result = await dataset_node.run(state, {})

    assert dataset_result["total_conversations"] == 5
    assert dataset_result["total_turns"] == 8

    # Step 2: Batch evaluation
    batch_node = ConversationalBatchEvalNode(
        name="batch_eval",
        conversations_key="conversations",
        prediction_field="raw_question",
        gold_field="gold_rewrite",
    )
    batch_state: State[str, Any] = State(
        inputs={"conversations": dataset_result["conversations"]}
    )
    batch_result = await batch_node.run(batch_state, {})

    assert batch_result["total_turns"] == 8
    assert batch_result["total_conversations"] == 5
    assert len(batch_result["predictions"]) == 8
    assert len(batch_result["references"]) == 8

    # Step 3: Compute ROUGE-1 Recall
    rouge_node = RougeMetricsNode(
        name="rouge",
        variant="rouge1",
        measure="recall",
    )
    rouge_state: State[str, Any] = State(
        inputs={
            "predictions": batch_result["predictions"],
            "references": batch_result["references"],
        }
    )
    rouge_result = await rouge_node.run(rouge_state, {})

    assert rouge_result["metric_name"] == "rouge1_recall"
    assert 0.0 <= rouge_result["corpus_score"] <= 1.0
    assert len(rouge_result["per_item"]) == 8
    # Identical turns (e.g., "What is machine learning?" == "What is machine learning?")
    # should score 1.0
    assert rouge_result["per_item"][0] == 1.0

    # Step 4: Generate report via AnalyticsExportNode
    analytics_node = AnalyticsExportNode(
        name="analytics",
        dataset_name="qrecc",
        metric_node_names=["rouge"],
        batch_eval_node_name="batch_eval",
    )
    analytics_state: State[str, Any] = State(
        node_results={
            "rouge": rouge_result,
            "batch_eval": batch_result,
        },
    )
    analytics_result = await analytics_node.run(analytics_state, {})

    assert analytics_result["report"]["dataset"] == "qrecc"
    assert "rouge1_recall" in analytics_result["report"]["metrics"]
    assert "table" in analytics_result
    assert "rouge1_recall" in analytics_result["table"]


@pytest.mark.asyncio
async def test_qrecc_identical_rewrites_perfect_scores() -> None:
    """Verify identical rewrites produce perfect ROUGE scores."""
    # Use only turns where raw_question == gold_rewrite
    identical_data = [
        record for record in QRECC_MICRO if record["Question"] == record["Rewrite"]
    ]
    assert len(identical_data) > 0

    dataset_node = QReCCDatasetNode(name="dataset")
    state: State[str, Any] = State(inputs={"qrecc_data": identical_data})
    dataset_result = await dataset_node.run(state, {})

    batch_node = ConversationalBatchEvalNode(
        name="batch_eval",
        conversations_key="conversations",
        prediction_field="raw_question",
        gold_field="gold_rewrite",
    )
    batch_state: State[str, Any] = State(
        inputs={"conversations": dataset_result["conversations"]}
    )
    batch_result = await batch_node.run(batch_state, {})

    rouge_node = RougeMetricsNode(
        name="rouge",
        variant="rouge1",
        measure="recall",
    )
    rouge_state: State[str, Any] = State(
        inputs={
            "predictions": batch_result["predictions"],
            "references": batch_result["references"],
        }
    )
    rouge_result = await rouge_node.run(rouge_state, {})

    assert rouge_result["corpus_score"] == 1.0
    assert all(score == 1.0 for score in rouge_result["per_item"])


# --- MultiDoc2Dial Integration Tests ---


@pytest.mark.asyncio
async def test_md2d_end_to_end_pipeline() -> None:
    """Full MD2D pipeline: dataset → batch eval → F1, BLEU, ROUGE-L → report."""
    # Step 1: Load dataset
    dataset_node = MultiDoc2DialDatasetNode(name="dataset")
    state: State[str, Any] = State(inputs={"md2d_data": MD2D_MICRO})
    dataset_result = await dataset_node.run(state, {})

    assert dataset_result["total_conversations"] == 5
    assert dataset_result["total_turns"] == 7

    # Step 2: Batch evaluation
    batch_node = ConversationalBatchEvalNode(
        name="batch_eval",
        conversations_key="conversations",
        prediction_field="user_utterance",
        gold_field="gold_response",
    )
    batch_state: State[str, Any] = State(
        inputs={"conversations": dataset_result["conversations"]}
    )
    batch_result = await batch_node.run(batch_state, {})

    assert batch_result["total_turns"] == 7
    assert batch_result["total_conversations"] == 5

    predictions = batch_result["predictions"]
    references = batch_result["references"]

    # Step 3a: Token F1
    f1_node = TokenF1MetricsNode(name="token_f1")
    f1_state: State[str, Any] = State(
        inputs={"predictions": predictions, "references": references}
    )
    f1_result = await f1_node.run(f1_state, {})

    assert f1_result["metric_name"] == "token_f1"
    assert 0.0 <= f1_result["corpus_score"] <= 1.0
    assert len(f1_result["per_item"]) == 7

    # Step 3b: SacreBLEU
    bleu_node = BleuMetricsNode(name="bleu")
    bleu_state: State[str, Any] = State(
        inputs={"predictions": predictions, "references": references}
    )
    bleu_result = await bleu_node.run(bleu_state, {})

    assert bleu_result["metric_name"] == "sacrebleu"
    assert bleu_result["corpus_score"] >= 0.0
    assert len(bleu_result["per_item"]) == 7

    # Step 3c: ROUGE-L
    rouge_node = RougeMetricsNode(
        name="rouge",
        variant="rougeL",
        measure="fmeasure",
    )
    rouge_state: State[str, Any] = State(
        inputs={"predictions": predictions, "references": references}
    )
    rouge_result = await rouge_node.run(rouge_state, {})

    assert rouge_result["metric_name"] == "rougeL_fmeasure"
    assert 0.0 <= rouge_result["corpus_score"] <= 1.0
    assert len(rouge_result["per_item"]) == 7

    # Step 4: Generate combined report via AnalyticsExportNode
    analytics_node = AnalyticsExportNode(
        name="analytics",
        dataset_name="multidoc2dial",
        metric_node_names=["token_f1", "bleu", "rouge"],
        batch_eval_node_name="batch_eval",
    )
    analytics_state: State[str, Any] = State(
        node_results={
            "token_f1": f1_result,
            "bleu": bleu_result,
            "rouge": rouge_result,
            "batch_eval": batch_result,
        },
    )
    analytics_result = await analytics_node.run(analytics_state, {})

    report = analytics_result["report"]
    assert report["dataset"] == "multidoc2dial"
    assert "token_f1" in report["metrics"]
    assert "sacrebleu" in report["metrics"]
    assert "rougeL_fmeasure" in report["metrics"]
    assert len(report["per_conversation"]) == 5


@pytest.mark.asyncio
async def test_md2d_per_conversation_tracking() -> None:
    """Verify per-conversation breakdowns are correctly tracked."""
    dataset_node = MultiDoc2DialDatasetNode(name="dataset")
    state: State[str, Any] = State(inputs={"md2d_data": MD2D_MICRO})
    dataset_result = await dataset_node.run(state, {})

    batch_node = ConversationalBatchEvalNode(
        name="batch_eval",
        conversations_key="conversations",
        prediction_field="user_utterance",
        gold_field="gold_response",
    )
    batch_state: State[str, Any] = State(
        inputs={"conversations": dataset_result["conversations"]}
    )
    batch_result = await batch_node.run(batch_state, {})

    per_conv = batch_result["per_conversation"]
    assert len(per_conv) == 5
    assert per_conv["d1"]["num_turns"] == 2
    assert per_conv["d2"]["num_turns"] == 1
    assert per_conv["d3"]["num_turns"] == 2
    assert per_conv["d4"]["num_turns"] == 1
    assert per_conv["d5"]["num_turns"] == 1


@pytest.mark.asyncio
async def test_combined_qrecc_and_md2d_report() -> None:
    """Verify both datasets produce valid reports that can be compared."""
    # QReCC pipeline
    qrecc_dataset = QReCCDatasetNode(name="dataset")
    qrecc_state: State[str, Any] = State(inputs={"qrecc_data": QRECC_MICRO})
    qrecc_data = await qrecc_dataset.run(qrecc_state, {})

    qrecc_batch = ConversationalBatchEvalNode(
        name="batch",
        conversations_key="conversations",
        prediction_field="raw_question",
        gold_field="gold_rewrite",
    )
    qrecc_batch_state: State[str, Any] = State(
        inputs={"conversations": qrecc_data["conversations"]}
    )
    qrecc_batch_result = await qrecc_batch.run(qrecc_batch_state, {})

    rouge_node = RougeMetricsNode(name="rouge", variant="rouge1", measure="recall")
    rouge_state: State[str, Any] = State(
        inputs={
            "predictions": qrecc_batch_result["predictions"],
            "references": qrecc_batch_result["references"],
        }
    )
    qrecc_rouge = await rouge_node.run(rouge_state, {})

    qrecc_analytics_node = AnalyticsExportNode(
        name="analytics",
        dataset_name="qrecc",
        metric_node_names=["rouge"],
        batch_eval_node_name="batch",
    )
    qrecc_analytics_state: State[str, Any] = State(
        node_results={"rouge": qrecc_rouge, "batch": qrecc_batch_result},
    )
    qrecc_report = await qrecc_analytics_node.run(qrecc_analytics_state, {})

    # MD2D pipeline
    md2d_dataset = MultiDoc2DialDatasetNode(name="dataset")
    md2d_state: State[str, Any] = State(inputs={"md2d_data": MD2D_MICRO})
    md2d_data = await md2d_dataset.run(md2d_state, {})

    md2d_batch = ConversationalBatchEvalNode(
        name="batch",
        conversations_key="conversations",
        prediction_field="user_utterance",
        gold_field="gold_response",
    )
    md2d_batch_state: State[str, Any] = State(
        inputs={"conversations": md2d_data["conversations"]}
    )
    md2d_batch_result = await md2d_batch.run(md2d_batch_state, {})

    f1_node = TokenF1MetricsNode(name="f1")
    f1_state: State[str, Any] = State(
        inputs={
            "predictions": md2d_batch_result["predictions"],
            "references": md2d_batch_result["references"],
        }
    )
    md2d_f1 = await f1_node.run(f1_state, {})

    md2d_analytics_node = AnalyticsExportNode(
        name="analytics",
        dataset_name="multidoc2dial",
        metric_node_names=["f1"],
        batch_eval_node_name="batch",
    )
    md2d_analytics_state: State[str, Any] = State(
        node_results={"f1": md2d_f1, "batch": md2d_batch_result},
    )
    md2d_report = await md2d_analytics_node.run(md2d_analytics_state, {})

    # Both reports valid
    assert qrecc_report["report"]["dataset"] == "qrecc"
    assert md2d_report["report"]["dataset"] == "multidoc2dial"
    assert "rouge1_recall" in qrecc_report["report"]["metrics"]
    assert "token_f1" in md2d_report["report"]["metrics"]
