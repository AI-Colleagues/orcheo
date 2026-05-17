"""Retrieval and answer quality evaluation metric nodes."""

from __future__ import annotations
import logging
import math
import re
from collections import Counter
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.tracing.model_metadata import (
    build_ai_trace_metadata,
    infer_model_name_from_instance,
)


logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"\W+", text.lower()) if token]


@registry.register(
    NodeMetadata(
        name="RetrievalEvaluationNode",
        description="Compute retrieval quality metrics for search results.",
        category="conversational_search",
    )
)
class RetrievalEvaluationNode(TaskNode):
    """Evaluate retrieval outputs against golden relevance labels."""

    dataset_key: str = Field(default="dataset")
    results_key: str = Field(default="retrieval_results")
    k: int | str = Field(default=5)

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Compute retrieval metrics across the provided dataset."""
        inputs = state.get("inputs", {})
        dataset = inputs.get(self.dataset_key)
        results = inputs.get(self.results_key)
        if not isinstance(dataset, list) or not isinstance(results, list):
            msg = "RetrievalEvaluationNode requires dataset and retrieval_results lists"
            raise ValueError(msg)

        per_query: dict[str, dict[str, float]] = {}
        recalls: list[float] = []
        mrrs: list[float] = []
        ndcgs: list[float] = []
        maps: list[float] = []

        result_map = {row.get("query_id"): row.get("results", []) for row in results}
        k = int(self.k)
        for example in dataset:
            query_id = example.get("id")
            relevant_ids: set[str] = set(example.get("relevant_ids", []))
            returned = result_map.get(query_id, [])[:k]
            ranked_ids = [
                item["id"]
                for item in returned
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ]
            recall = self._recall_at_k(ranked_ids, relevant_ids)
            mrr = self._mrr(ranked_ids, relevant_ids)
            ndcg = self._ndcg(ranked_ids, relevant_ids)
            average_precision = self._average_precision(ranked_ids, relevant_ids)

            per_query[str(query_id)] = {
                "recall_at_k": recall,
                "mrr": mrr,
                "ndcg": ndcg,
                "map": average_precision,
            }
            recalls.append(recall)
            mrrs.append(mrr)
            ndcgs.append(ndcg)
            maps.append(average_precision)

        return {
            "metrics": {
                "recall_at_k": sum(recalls) / len(recalls) if recalls else 0.0,
                "mrr": sum(mrrs) / len(mrrs) if mrrs else 0.0,
                "ndcg": sum(ndcgs) / len(ndcgs) if ndcgs else 0.0,
                "map": sum(maps) / len(maps) if maps else 0.0,
            },
            "per_query": per_query,
        }

    def _recall_at_k(self, ranked_ids: list[str], relevant_ids: set[str]) -> float:
        if not relevant_ids:
            return 0.0
        hits = sum(1 for item in ranked_ids if item in relevant_ids)
        return hits / len(relevant_ids)

    def _mrr(self, ranked_ids: list[str], relevant_ids: set[str]) -> float:
        for index, item_id in enumerate(ranked_ids):
            if item_id in relevant_ids:
                return 1.0 / (index + 1)
        return 0.0

    def _ndcg(self, ranked_ids: list[str], relevant_ids: set[str]) -> float:
        if not relevant_ids:
            return 0.0
        dcg = 0.0
        for index, item_id in enumerate(ranked_ids):
            if item_id in relevant_ids:
                dcg += 1.0 / math.log2(index + 2)
        ideal_hits = min(len(relevant_ids), len(ranked_ids))
        idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
        if idcg == 0:
            return 0.0
        return dcg / idcg

    def _average_precision(
        self, ranked_ids: list[str], relevant_ids: set[str]
    ) -> float:
        if not relevant_ids:
            return 0.0
        hits = 0
        precision_sum = 0.0
        for index, item_id in enumerate(ranked_ids, start=1):
            if item_id in relevant_ids:
                hits += 1
                precision_sum += hits / index
        if hits == 0:
            return 0.0
        return precision_sum / len(relevant_ids)


@registry.register(
    NodeMetadata(
        name="AnswerQualityEvaluationNode",
        description="Score generated answers against reference answers.",
        category="conversational_search",
    )
)
class AnswerQualityEvaluationNode(TaskNode):
    """Compute heuristic faithfulness and relevance scores."""

    references_key: str = Field(default="references")
    answers_key: str = Field(default="answers")

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Score answers against references using overlap heuristics."""
        inputs = state.get("inputs", {})
        references = inputs.get(self.references_key)
        answers = inputs.get(self.answers_key)
        if not isinstance(references, dict) or not isinstance(answers, list):
            msg = "AnswerQualityEvaluationNode expects references dict and answers list"
            raise ValueError(msg)

        per_answer: dict[str, dict[str, float]] = {}
        faithfulness_scores: list[float] = []
        relevance_scores: list[float] = []

        for entry in answers:
            answer_id = entry.get("id")
            answer_text = entry.get("answer", "")
            reference = references.get(answer_id, "")
            faithfulness = self._overlap_score(answer_text, reference)
            relevance = self._relevance_score(answer_text, reference)
            per_answer[str(answer_id)] = {
                "faithfulness": faithfulness,
                "relevance": relevance,
            }
            faithfulness_scores.append(faithfulness)
            relevance_scores.append(relevance)

        return {
            "metrics": {
                "faithfulness": sum(faithfulness_scores) / len(faithfulness_scores)
                if faithfulness_scores
                else 0.0,
                "relevance": sum(relevance_scores) / len(relevance_scores)
                if relevance_scores
                else 0.0,
            },
            "per_answer": per_answer,
        }

    def _overlap_score(self, answer: str, reference: str) -> float:
        answer_tokens = set(_tokenize(answer))
        reference_tokens = set(_tokenize(reference))
        if not reference_tokens:
            return 0.0
        overlap = len(answer_tokens & reference_tokens)
        return overlap / len(reference_tokens)

    def _relevance_score(self, answer: str, reference: str) -> float:
        if not answer.strip() or not reference.strip():
            return 0.0
        answer_tokens = _tokenize(answer)
        reference_tokens = _tokenize(reference)
        shared = sum(1 for token in answer_tokens if token in reference_tokens)
        return shared / max(len(answer_tokens), 1)


@registry.register(
    NodeMetadata(
        name="RougeMetricsNode",
        description="Compute ROUGE scores between predicted and reference texts",
        category="evaluation",
    )
)
class RougeMetricsNode(TaskNode):
    """Configurable ROUGE scoring. Task-agnostic."""

    variant: str = Field(
        default="rouge1",
        description="rouge1, rouge2, rougeL, or rougeLsum",
    )
    measure: str = Field(
        default="fmeasure",
        description="precision, recall, or fmeasure",
    )
    predictions_key: str = Field(default="predictions")
    references_key: str = Field(default="references")

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return per-item scores and corpus-level aggregate."""
        from rouge_score import rouge_scorer

        inputs = state.get("inputs", {})
        predictions = inputs.get(self.predictions_key)
        references = inputs.get(self.references_key)
        if not isinstance(predictions, list) or not isinstance(references, list):
            msg = "RougeMetricsNode expects predictions and references lists"
            raise ValueError(msg)
        if len(predictions) != len(references):
            msg = "RougeMetricsNode expects predictions and references of same length"
            raise ValueError(msg)

        scorer = rouge_scorer.RougeScorer([self.variant], use_stemmer=True)
        per_item: list[float] = []
        for pred, ref in zip(predictions, references, strict=True):
            scores = scorer.score(str(ref), str(pred))
            score_obj = scores[self.variant]
            value = getattr(score_obj, self.measure)
            per_item.append(float(value))

        corpus_score = sum(per_item) / len(per_item) if per_item else 0.0
        metric_name = f"{self.variant}_{self.measure}"

        return {
            "metric_name": metric_name,
            "corpus_score": corpus_score,
            "per_item": per_item,
        }


@registry.register(
    NodeMetadata(
        name="BleuMetricsNode",
        description="Compute SacreBLEU between predicted and reference texts",
        category="evaluation",
    )
)
class BleuMetricsNode(TaskNode):
    """Corpus-level and per-item BLEU scoring. Task-agnostic."""

    predictions_key: str = Field(default="predictions")
    references_key: str = Field(default="references")

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return per-item scores and corpus-level aggregate."""
        import sacrebleu

        inputs = state.get("inputs", {})
        predictions = inputs.get(self.predictions_key)
        references = inputs.get(self.references_key)
        if not isinstance(predictions, list) or not isinstance(references, list):
            msg = "BleuMetricsNode expects predictions and references lists"
            raise ValueError(msg)
        if len(predictions) != len(references):
            msg = "BleuMetricsNode expects predictions and references of same length"
            raise ValueError(msg)

        if not predictions:
            return {
                "metric_name": "sacrebleu",
                "corpus_score": 0.0,
                "per_item": [],
            }

        str_predictions = [str(p) for p in predictions]
        str_references = [str(r) for r in references]

        corpus_result = sacrebleu.corpus_bleu(str_predictions, [str_references])
        corpus_score = corpus_result.score

        per_item: list[float] = []
        for pred, ref in zip(str_predictions, str_references, strict=True):
            item_result = sacrebleu.sentence_bleu(pred, [ref])
            per_item.append(item_result.score)

        return {
            "metric_name": "sacrebleu",
            "corpus_score": corpus_score,
            "per_item": per_item,
        }


@registry.register(
    NodeMetadata(
        name="SemanticSimilarityMetricsNode",
        description=(
            "Compute embedding cosine similarity between predicted and reference texts"
        ),
        category="evaluation",
    )
)
class SemanticSimilarityMetricsNode(TaskNode):
    """Embedding-based similarity scoring. Task-agnostic."""

    embed_model: str = Field(
        default="openai:text-embedding-3-small",
        description=(
            "Dense embedding model identifier, e.g. openai:text-embedding-3-small"
        ),
    )
    model_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional keyword arguments forwarded to init_embeddings.",
    )
    predictions_key: str = Field(default="predictions")
    references_key: str = Field(default="references")

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return per-item scores and corpus-level aggregate."""
        from orcheo.nodes.rag.embeddings import (
            init_dense_embeddings,
        )

        inputs = state.get("inputs", {})
        predictions = inputs.get(self.predictions_key)
        references = inputs.get(self.references_key)
        if not isinstance(predictions, list) or not isinstance(references, list):
            msg = (
                "SemanticSimilarityMetricsNode expects predictions and references lists"
            )
            raise ValueError(msg)
        if len(predictions) != len(references):
            msg = (
                "SemanticSimilarityMetricsNode expects predictions and "
                "references of same length"
            )
            raise ValueError(msg)

        str_predictions = [str(p) for p in predictions]
        str_references = [str(r) for r in references]

        all_texts = str_predictions + str_references
        model = init_dense_embeddings(self.embed_model, self.model_kwargs)
        all_embeddings = await model.aembed_documents(all_texts)
        if all_embeddings:  # pragma: no branch
            self._set_trace_metadata_for_run(
                {
                    "ai": build_ai_trace_metadata(
                        kind="embedding",
                        requested_model=self.embed_model,
                        actual_model=(
                            infer_model_name_from_instance(model) or self.embed_model
                        ),
                        operation="documents",
                        vector_size=len(all_embeddings[0]),
                    )
                }
            )

        n = len(str_predictions)
        pred_embeddings = all_embeddings[:n]
        ref_embeddings = all_embeddings[n:]

        per_item: list[float] = []
        for pred_emb, ref_emb in zip(pred_embeddings, ref_embeddings, strict=True):
            similarity = self._cosine_similarity(pred_emb, ref_emb)
            per_item.append(similarity)

        corpus_score = sum(per_item) / len(per_item) if per_item else 0.0

        return {
            "metric_name": "semantic_similarity",
            "corpus_score": corpus_score,
            "per_item": per_item,
        }

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        dot_product = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot_product / (norm_a * norm_b)


@registry.register(
    NodeMetadata(
        name="TokenF1MetricsNode",
        description="Compute token-level F1 between predicted and reference texts",
        category="evaluation",
    )
)
class TokenF1MetricsNode(TaskNode):
    """Token-overlap precision, recall, and F1. Task-agnostic."""

    normalize: bool = Field(
        default=True,
        description="Lowercase and strip punctuation before scoring",
    )
    predictions_key: str = Field(default="predictions")
    references_key: str = Field(default="references")

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return per-item scores and corpus-level aggregate."""
        inputs = state.get("inputs", {})
        predictions = inputs.get(self.predictions_key)
        references = inputs.get(self.references_key)
        if not isinstance(predictions, list) or not isinstance(references, list):
            msg = "TokenF1MetricsNode expects predictions and references lists"
            raise ValueError(msg)
        if len(predictions) != len(references):
            msg = "TokenF1MetricsNode expects predictions and references of same length"
            raise ValueError(msg)

        per_item: list[float] = []
        for pred, ref in zip(predictions, references, strict=True):
            f1 = self._compute_f1(str(pred), str(ref))
            per_item.append(f1)

        corpus_score = sum(per_item) / len(per_item) if per_item else 0.0

        return {
            "metric_name": "token_f1",
            "corpus_score": corpus_score,
            "per_item": per_item,
        }

    def _compute_f1(self, prediction: str, reference: str) -> float:
        if self.normalize:
            pred_tokens = _tokenize(prediction)
            ref_tokens = _tokenize(reference)
        else:
            pred_tokens = prediction.split()
            ref_tokens = reference.split()

        if not pred_tokens or not ref_tokens:
            return 0.0

        pred_counter = Counter(pred_tokens)
        ref_counter = Counter(ref_tokens)
        overlap = pred_counter & ref_counter
        overlap_count = sum(overlap.values())

        if overlap_count == 0:
            return 0.0

        precision = overlap_count / len(pred_tokens)
        recall = overlap_count / len(ref_tokens)
        return 2 * precision * recall / (precision + recall)
