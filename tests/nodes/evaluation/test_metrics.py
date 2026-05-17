"""Tests for evaluation metric nodes."""

from unittest.mock import MagicMock
import pytest
from orcheo.graph.state import State
from orcheo.nodes.evaluation.metrics import (
    AnswerQualityEvaluationNode,
    BleuMetricsNode,
    RetrievalEvaluationNode,
    RougeMetricsNode,
    SemanticSimilarityMetricsNode,
    TokenF1MetricsNode,
)


@pytest.mark.asyncio
async def test_retrieval_evaluation_computes_metrics() -> None:
    node = RetrievalEvaluationNode(name="retrieval_eval", k=3)
    dataset = [
        {"id": "q1", "relevant_ids": ["d1", "d2"]},
        {"id": "q2", "relevant_ids": ["d3"]},
    ]
    retrieval_results = [
        {"query_id": "q1", "results": [{"id": "d1"}, {"id": "d3"}]},
        {"query_id": "q2", "results": [{"id": "d4"}, {"id": "d3"}]},
    ]
    state = State(inputs={"dataset": dataset, "retrieval_results": retrieval_results})

    result = await node.run(state, {})

    metrics = result["metrics"]
    assert metrics["recall_at_k"] > 0.0
    assert metrics["map"] == pytest.approx(0.5)
    assert result["per_query"]["q2"]["mrr"] == 0.5
    assert result["per_query"]["q1"]["map"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_answer_quality_scores_answers() -> None:
    references = {"q1": "Paris is the capital of France"}
    answers = [{"id": "q1", "answer": "The capital of France is Paris."}]
    state = State(inputs={"references": references, "answers": answers})

    answer_eval = AnswerQualityEvaluationNode(name="answer_eval")
    judged = await answer_eval.run(state, {})
    assert judged["metrics"]["faithfulness"] > 0.0


@pytest.mark.asyncio
async def test_retrieval_node_requires_list_inputs() -> None:
    node = RetrievalEvaluationNode(name="retrieval")
    with pytest.raises(ValueError, match="retrieval_results lists"):
        await node.run(
            State(inputs={"dataset": [], "retrieval_results": "invalid"}), {}
        )
    with pytest.raises(ValueError, match="retrieval_results lists"):
        await node.run(
            State(inputs={"dataset": "invalid", "retrieval_results": []}), {}
        )


def test_retrieval_metrics_edge_cases() -> None:
    node = RetrievalEvaluationNode(name="retrieval_edge")
    assert node._recall_at_k([], set()) == 0.0
    assert node._mrr(["a"], {"b"}) == 0.0
    assert node._ndcg([], {"relevant"}) == 0.0
    assert node._average_precision(["a"], {"b"}) == 0.0
    assert node._ndcg([], set()) == 0.0
    assert node._average_precision([], set()) == 0.0


@pytest.mark.asyncio
async def test_answer_quality_node_validates_inputs() -> None:
    node = AnswerQualityEvaluationNode(name="answer_eval")
    with pytest.raises(ValueError, match="expects references dict and answers list"):
        await node.run(State(inputs={"references": [], "answers": []}), {})
    with pytest.raises(ValueError, match="expects references dict and answers list"):
        await node.run(
            State(inputs={"references": {"q": "a"}, "answers": "invalid"}), {}
        )


def test_answer_quality_scoring_edge_tokens() -> None:
    node = AnswerQualityEvaluationNode(name="answer_eval")
    assert node._overlap_score("text", "") == 0.0
    assert node._relevance_score("", "reference") == 0.0
    assert node._relevance_score("text", "") == 0.0


# --- RougeMetricsNode tests ---


@pytest.mark.asyncio
async def test_rouge_metrics_node_computes_scores() -> None:
    node = RougeMetricsNode(name="rouge", variant="rouge1", measure="fmeasure")
    predictions = ["The cat sat on the mat", "Hello world"]
    references = ["The cat sat on the mat", "Hello beautiful world"]
    state = State(inputs={"predictions": predictions, "references": references})

    result = await node.run(state, {})

    assert result["metric_name"] == "rouge1_fmeasure"
    assert result["corpus_score"] > 0.0
    assert len(result["per_item"]) == 2
    assert result["per_item"][0] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_rouge_metrics_node_recall_variant() -> None:
    node = RougeMetricsNode(name="rouge", variant="rougeL", measure="recall")
    predictions = ["The capital of France is Paris"]
    references = ["Paris is the capital of France"]
    state = State(inputs={"predictions": predictions, "references": references})

    result = await node.run(state, {})

    assert result["metric_name"] == "rougeL_recall"
    assert result["corpus_score"] > 0.0


@pytest.mark.asyncio
async def test_rouge_metrics_node_validates_inputs() -> None:
    node = RougeMetricsNode(name="rouge")
    with pytest.raises(ValueError, match="expects predictions and references lists"):
        await node.run(State(inputs={"predictions": "bad", "references": []}), {})
    with pytest.raises(ValueError, match="of same length"):
        await node.run(
            State(inputs={"predictions": ["a"], "references": ["b", "c"]}), {}
        )


@pytest.mark.asyncio
async def test_rouge_metrics_node_empty_inputs() -> None:
    node = RougeMetricsNode(name="rouge")
    result = await node.run(State(inputs={"predictions": [], "references": []}), {})
    assert result["corpus_score"] == 0.0
    assert result["per_item"] == []


# --- BleuMetricsNode tests ---


@pytest.mark.asyncio
async def test_bleu_metrics_node_computes_scores() -> None:
    node = BleuMetricsNode(name="bleu")
    predictions = ["The cat sat on the mat", "Hello world"]
    references = ["The cat sat on the mat", "Hello beautiful world"]
    state = State(inputs={"predictions": predictions, "references": references})

    result = await node.run(state, {})

    assert result["metric_name"] == "sacrebleu"
    assert result["corpus_score"] > 0.0
    assert len(result["per_item"]) == 2
    assert result["per_item"][0] > result["per_item"][1]


@pytest.mark.asyncio
async def test_bleu_metrics_node_validates_inputs() -> None:
    node = BleuMetricsNode(name="bleu")
    with pytest.raises(ValueError, match="expects predictions and references lists"):
        await node.run(State(inputs={"predictions": "bad", "references": []}), {})
    with pytest.raises(ValueError, match="of same length"):
        await node.run(
            State(inputs={"predictions": ["a"], "references": ["b", "c"]}), {}
        )


@pytest.mark.asyncio
async def test_bleu_metrics_node_empty_inputs() -> None:
    node = BleuMetricsNode(name="bleu")
    result = await node.run(State(inputs={"predictions": [], "references": []}), {})
    assert result["corpus_score"] == 0.0
    assert result["per_item"] == []


# --- SemanticSimilarityMetricsNode tests ---


@pytest.mark.asyncio
async def test_semantic_similarity_node_computes_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orcheo.nodes.rag.embeddings as emb_mod

    node = SemanticSimilarityMetricsNode(
        name="similarity", embed_model="test:fake", model_kwargs={}
    )
    predictions = ["The cat sat on the mat", "Hello world"]
    references = ["The cat sat on the mat", "Goodbye universe"]

    mock_embeddings = [
        [1.0, 0.0, 0.0],  # pred 0
        [0.0, 1.0, 0.0],  # pred 1
        [1.0, 0.0, 0.0],  # ref 0 (same as pred 0)
        [0.0, 0.0, 1.0],  # ref 1 (orthogonal to pred 1)
    ]

    class FakeEmbeddings:
        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return mock_embeddings

    monkeypatch.setattr(
        emb_mod, "init_dense_embeddings", lambda *a, **kw: FakeEmbeddings()
    )

    state = State(inputs={"predictions": predictions, "references": references})
    result = await node.run(state, {})

    assert result["metric_name"] == "semantic_similarity"
    assert len(result["per_item"]) == 2
    assert result["per_item"][0] == pytest.approx(1.0)
    assert result["per_item"][1] == pytest.approx(0.0)
    assert result["corpus_score"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_semantic_similarity_node_records_trace_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orcheo.nodes.rag.embeddings as emb_mod

    class FakeEmbeddings:
        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0] for _ in texts]

    monkeypatch.setattr(
        emb_mod, "init_dense_embeddings", lambda *args, **kwargs: FakeEmbeddings()
    )
    monkeypatch.setattr(
        "orcheo.nodes.evaluation.metrics.infer_model_name_from_instance",
        lambda instance: "inferred-model",
    )

    node = SemanticSimilarityMetricsNode(
        name="similarity-trace", embed_model="test:fake", model_kwargs={}
    )
    node._set_trace_metadata_for_run = MagicMock()

    state = State(
        inputs={"predictions": ["text"], "references": ["text"]},
    )
    await node.run(state, {})

    node._set_trace_metadata_for_run.assert_called_once()
    trace_payload = node._set_trace_metadata_for_run.call_args[0][0]
    assert trace_payload["ai"]["actual_model"] == "inferred-model"


@pytest.mark.asyncio
async def test_semantic_similarity_node_validates_inputs() -> None:
    node = SemanticSimilarityMetricsNode(
        name="similarity", embed_model="test:fake", model_kwargs={}
    )
    with pytest.raises(ValueError, match="expects predictions and references lists"):
        await node.run(State(inputs={"predictions": "bad", "references": []}), {})
    with pytest.raises(ValueError, match="of same length"):
        await node.run(
            State(inputs={"predictions": ["a"], "references": ["b", "c"]}), {}
        )


def test_semantic_similarity_cosine_edge_cases() -> None:
    node = SemanticSimilarityMetricsNode(
        name="similarity", embed_model="test:fake", model_kwargs={}
    )
    assert node._cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert node._cosine_similarity([1.0, 0.0], [0.0, 0.0]) == 0.0
    assert node._cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


# --- TokenF1MetricsNode tests ---


@pytest.mark.asyncio
async def test_token_f1_node_computes_scores() -> None:
    node = TokenF1MetricsNode(name="f1")
    predictions = ["The cat sat on the mat", "Hello world"]
    references = ["The cat sat on the mat", "Hello beautiful world"]
    state = State(inputs={"predictions": predictions, "references": references})

    result = await node.run(state, {})

    assert result["metric_name"] == "token_f1"
    assert len(result["per_item"]) == 2
    assert result["per_item"][0] == pytest.approx(1.0)
    assert result["per_item"][1] < 1.0
    assert result["per_item"][1] > 0.0


@pytest.mark.asyncio
async def test_token_f1_node_no_normalize() -> None:
    node = TokenF1MetricsNode(name="f1", normalize=False)
    predictions = ["Hello World"]
    references = ["Hello World"]
    state = State(inputs={"predictions": predictions, "references": references})

    result = await node.run(state, {})

    assert result["per_item"][0] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_token_f1_node_validates_inputs() -> None:
    node = TokenF1MetricsNode(name="f1")
    with pytest.raises(ValueError, match="expects predictions and references lists"):
        await node.run(State(inputs={"predictions": "bad", "references": []}), {})
    with pytest.raises(ValueError, match="of same length"):
        await node.run(
            State(inputs={"predictions": ["a"], "references": ["b", "c"]}), {}
        )


@pytest.mark.asyncio
async def test_token_f1_node_empty_inputs() -> None:
    node = TokenF1MetricsNode(name="f1")
    result = await node.run(State(inputs={"predictions": [], "references": []}), {})
    assert result["corpus_score"] == 0.0


def test_token_f1_compute_edge_cases() -> None:
    node = TokenF1MetricsNode(name="f1")
    assert node._compute_f1("", "reference") == 0.0
    assert node._compute_f1("prediction", "") == 0.0
    assert node._compute_f1("abc", "xyz") == 0.0


def test_token_f1_uses_token_frequencies() -> None:
    node = TokenF1MetricsNode(name="f1")

    score = node._compute_f1("a a a", "a")

    assert score == pytest.approx(0.5)
