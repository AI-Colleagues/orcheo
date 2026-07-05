"""Agentensor evaluation/training node."""

from __future__ import annotations
import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast
from langchain_core.runnables import RunnableConfig
from pydantic import ConfigDict, Field
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext
from pydantic_evals.reporting import EvaluationReport
from agentensor.loss import LLMTensorJudge
from agentensor.optim import Optimizer
from agentensor.tensor import TextTensor
from agentensor.train import GraphTrainer
from orcheo.agentensor.checkpoints import (
    AgentensorCheckpoint,
    AgentensorCheckpointStore,
)
from orcheo.agentensor.evaluation import (
    EvaluationCase,
    EvaluationContext,
    EvaluationDataset,
    EvaluatorDefinition,
)
from orcheo.agentensor.prompts import TrainablePrompts, build_text_tensors
from orcheo.agentensor.training import OptimizerConfig
from orcheo.graph.ingestion import LANGGRAPH_SCRIPT_FORMAT
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


logger = logging.getLogger(__name__)


@dataclass
class _TextPayload:
    """Minimal text wrapper to satisfy LLMTensorJudge expectations."""

    text: str


@dataclass
class _TensorEvaluatorContext:
    """Shim context for LLM tensor evaluators with text-like inputs."""

    inputs: Any
    output: Any
    expected_output: Any
    metadata: Any
    duration: float


@dataclass
class _EvaluatorAdapter(
    Evaluator[dict[str, Any], Any, dict[str, Any]],
):
    """Adapter to reuse Agentensor evaluators with pydantic-evals."""

    definition: EvaluatorDefinition
    evaluator: Any

    evaluation_name: str = ""

    def __post_init__(self) -> None:
        self.evaluation_name = self.definition.id

    @staticmethod
    def _stringify_payload(payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        try:
            return json.dumps(payload)
        except TypeError:
            return str(payload)

    @classmethod
    def _coerce_text_payload(cls, payload: Any) -> Any:
        if isinstance(payload, TextTensor):
            return payload
        if hasattr(payload, "text") and isinstance(payload.text, str):
            return payload
        if (
            isinstance(payload, Mapping)
            and "text" in payload
            and isinstance(payload["text"], str)
        ):
            return _TextPayload(text=payload["text"])
        return _TextPayload(text=cls._stringify_payload(payload))

    async def evaluate(
        self, ctx: EvaluatorContext[dict[str, Any], Any, dict[str, Any]]
    ) -> EvaluationReason:
        """Run the wrapped evaluator and normalise to an EvaluationReason."""
        if isinstance(self.evaluator, LLMTensorJudge):
            try:
                tensor_ctx = cast(
                    EvaluatorContext[TextTensor, TextTensor, Any],
                    _TensorEvaluatorContext(
                        inputs=self._coerce_text_payload(ctx.inputs),
                        output=self._coerce_text_payload(ctx.output),
                        expected_output=ctx.expected_output,
                        metadata=ctx.metadata,
                        duration=ctx.duration,
                    ),
                )
                return await self.evaluator.evaluate(tensor_ctx)
            except Exception as exc:  # pragma: no cover - defensive
                return EvaluationReason(value=False, reason=str(exc))
        output = ctx.output
        if isinstance(output, TextTensor):  # pragma: no branch
            output = output.metadata.get("payload", output.text)
        context = EvaluationContext(
            inputs=ctx.inputs,
            output=output,
            expected_output=ctx.expected_output,
            metadata=ctx.metadata or {},
            duration_ms=ctx.duration * 1000.0,
        )
        try:
            candidate = self.evaluator(context)
            if inspect.iscoroutine(candidate):
                candidate = await candidate
        except Exception as exc:  # pragma: no cover - defensive
            return EvaluationReason(value=False, reason=str(exc))

        normalised = AgentensorNode._normalise_evaluation_result(candidate)
        return EvaluationReason(
            value=bool(normalised["passed"]),
            reason=normalised["reason"],
        )


@registry.register(
    NodeMetadata(
        name="AgentensorNode",
        description=(
            "Evaluate or train agent prompts using Agentensor datasets and evaluators."
        ),
        category="agentensor",
    )
)
class AgentensorNode(TaskNode):
    """Node shell for Agentensor evaluation and training flows."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    mode: Literal["evaluate", "train"] = "evaluate"
    prompts: TrainablePrompts = Field(
        default_factory=dict,
        description="Trainable prompt definitions resolved from runnable configs.",
    )
    dataset: EvaluationDataset | None = Field(
        default=None,
        description="Dataset of cases to evaluate (optional for prompt-only runs).",
    )
    evaluators: list[EvaluatorDefinition] = Field(
        default_factory=list,
        description="Evaluators applied to each case output.",
    )
    max_cases: int | None = Field(
        default=None,
        ge=1,
        description="Optional cap on the number of cases to run.",
    )
    compiled_graph: Any | None = Field(
        default=None,
        exclude=True,
        description="Compiled LangGraph used for evaluation.",
    )
    graph_config: Mapping[str, Any] | None = Field(
        default=None,
        exclude=True,
        description="Graph config to shape evaluation state.",
    )
    state_config: Mapping[str, Any] | None = Field(
        default=None,
        exclude=True,
        description="Runnable config injected into evaluation state.",
    )
    progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = Field(
        default=None,
        exclude=True,
        description="Optional hook for streaming evaluation progress.",
    )
    optimizer: OptimizerConfig = Field(
        default_factory=OptimizerConfig,
        description="Training optimizer configuration when mode='train'.",
    )
    checkpoint_store: AgentensorCheckpointStore | None = Field(
        default=None,
        exclude=True,
        description="Optional persistence layer for training checkpoints.",
    )
    workflow_id: str | None = Field(
        default=None,
        description="Workflow identifier used to persist checkpoints.",
    )

    _max_concurrency_cap = 8

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Execute the configured evaluation or return prompt metadata."""
        resolved_prompts = {
            name: prompt.model_dump(mode="json")
            for name, prompt in self.prompts.items()
        }
        if self.state_config is None and isinstance(state, Mapping):
            maybe_config = state.get("config")
            if isinstance(maybe_config, Mapping):  # pragma: no branch
                self.state_config = maybe_config
        tag_payload: list[str] | None = None
        if isinstance(config, Mapping):  # pragma: no branch
            tags = config.get("tags")
            if isinstance(tags, list):
                tag_payload = [str(tag) for tag in tags]
        result_base = {
            "mode": self.mode,
            "prompts": resolved_prompts,
            "tags": tag_payload or [],
        }
        if self.mode == "train":
            return await self._run_training(state, config, result_base)
        if self.mode != "evaluate":
            return result_base
        if self.dataset is None or not self.dataset.cases:
            return result_base | {"summary": {}, "results": []}

        return await self._run_evaluation(state, config, result_base)

    async def _run_evaluation(
        self,
        state: State,
        config: RunnableConfig,
        result_base: dict[str, Any],
    ) -> dict[str, Any]:
        assert self.dataset is not None
        compiled_graph = self._require_compiled_graph()
        dataset = self._build_pydantic_dataset()
        runtime_prompts = build_text_tensors(self.prompts)
        trainer = GraphTrainer(
            graph=compiled_graph,
            dataset=dataset,
            optimizer=None,
            epochs=1,
            runtime_prompts=runtime_prompts,
            base_state=state if isinstance(state, Mapping) else {},
            graph_config=config,
            max_concurrency=min(
                self.optimizer.max_concurrency, self._max_concurrency_cap
            ),
            case_timeout=self.optimizer.case_timeout_seconds,
            script_format=bool(
                self.graph_config
                and isinstance(self.graph_config, Mapping)
                and self.graph_config.get("format") == LANGGRAPH_SCRIPT_FORMAT
            ),
        )

        report = await asyncio.to_thread(trainer.evaluate, "train", self.max_cases)
        aggregated, case_results = await self._collect_evaluation_results(report)
        summary = self._summarize_metrics(aggregated)
        summary_payload = {
            "node": self.name,
            "event": "evaluation_summary",
            "payload": {
                "dataset_id": self.dataset.id,
                "summary": summary,
                "cases_ran": len(case_results),
            },
        }
        await self._emit_progress(summary_payload)

        return result_base | {
            "dataset_id": self.dataset.id,
            "summary": summary,
            "results": case_results,
        }

    async def _run_training(
        self,
        state: State,
        config: RunnableConfig,
        result_base: dict[str, Any],
    ) -> dict[str, Any]:
        if self.dataset is None or not self.dataset.cases:
            return result_base | {
                "summary": {},
                "results": [],
                "checkpoints": [],
            }

        trainable_prompts = [
            name
            for name, prompt in self.prompts.items()
            if getattr(prompt, "requires_grad", False)
        ]
        if not trainable_prompts:
            msg = (
                "AgentensorNode training requires at least one prompt with "
                "requires_grad=True."
            )
            raise ValueError(msg)

        compiled_graph = self._require_compiled_graph()
        runtime_prompts = build_text_tensors(self.prompts)
        dataset = self._build_pydantic_dataset()
        capped_config = self._enforce_training_limits(config)
        optimizer = Optimizer(
            graph=None,
            params=list(runtime_prompts.values()),
        )
        trainer = GraphTrainer(
            graph=compiled_graph,
            dataset=dataset,
            optimizer=optimizer,
            epochs=self.optimizer.epochs,
            runtime_prompts=runtime_prompts,
            base_state=state if isinstance(state, Mapping) else {},
            graph_config=capped_config,
            max_concurrency=min(
                self.optimizer.max_concurrency, self._max_concurrency_cap
            ),
            case_timeout=self.optimizer.case_timeout_seconds,
            script_format=bool(
                self.graph_config
                and isinstance(self.graph_config, Mapping)
                and self.graph_config.get("format") == LANGGRAPH_SCRIPT_FORMAT
            ),
            stop_threshold=2.0,
        )

        await asyncio.to_thread(trainer.train)

        checkpoints: list[dict[str, Any]] = []
        best_checkpoint: AgentensorCheckpoint | None = None
        best_score = -1.0
        all_results: list[dict[str, Any]] = []
        for epoch, report in enumerate(trainer.reports, start=1):
            if (
                trainer.prompt_history and len(trainer.prompt_history) >= epoch
            ):  # pragma: no branch
                prompt_snapshot = trainer.prompt_history[epoch - 1]
                for name, text in prompt_snapshot.items():
                    if name in runtime_prompts:
                        runtime_prompts[name].text = text
            self._sync_trained_prompts(runtime_prompts)
            aggregated, case_results = await self._collect_training_results(
                report, epoch
            )
            all_results.extend(case_results)
            summary = self._summarize_metrics(aggregated)
            score = self._score_summary(summary)
            should_checkpoint = (
                epoch % max(1, self.optimizer.checkpoint_interval) == 0
                or epoch == self.optimizer.epochs
            )
            checkpoint_obj: AgentensorCheckpoint | None = None
            if should_checkpoint:
                checkpoint_obj = await self._emit_checkpoint(
                    summary,
                    capped_config,
                    epoch=epoch,
                    is_best=score >= best_score,
                    prompts=runtime_prompts,
                )
                checkpoints.append(checkpoint_obj.model_dump(mode="json"))
                await self._emit_progress(
                    {
                        "node": self.name,
                        "event": "training_checkpoint",
                        "payload": checkpoint_obj.model_dump(mode="json"),
                    }
                )
            if score >= best_score:  # pragma: no branch
                best_score = score
                if checkpoint_obj is None:
                    checkpoint_obj = await self._emit_checkpoint(
                        summary,
                        capped_config,
                        epoch=epoch,
                        is_best=True,
                        prompts=runtime_prompts,
                    )
                    checkpoints.append(checkpoint_obj.model_dump(mode="json"))
                best_checkpoint = checkpoint_obj
            await self._emit_progress(
                {
                    "node": self.name,
                    "event": "training_epoch_complete",
                    "payload": {
                        "epoch": epoch,
                        "summary": summary,
                    },
                }
            )

        self._sync_trained_prompts(runtime_prompts)
        best_payload = (
            best_checkpoint.model_dump(mode="json") if best_checkpoint else None
        )
        trained_prompts = {
            name: prompt.model_dump(mode="json")
            for name, prompt in self.prompts.items()
        }
        return result_base | {
            "dataset_id": self.dataset.id,
            "summary": best_payload["metrics"] if best_payload else {},
            "results": all_results,
            "checkpoints": checkpoints,
            "best_checkpoint": best_payload,
            "prompts": trained_prompts,
        }

    def _build_pydantic_dataset(self) -> Dataset:
        assert self.dataset is not None
        cases = list(self.dataset.cases)
        if self.max_cases is not None:
            cases = cases[: self.max_cases]
        converted_cases = [
            Case(
                inputs=case.inputs,
                metadata=case.metadata,
                expected_output=case.expected_output,
            )
            for case in cases
        ]
        evaluators = [
            _EvaluatorAdapter(definition=definition, evaluator=definition.load())
            for definition in self.evaluators
        ]
        return Dataset(cases=converted_cases, evaluators=evaluators)

    async def _collect_evaluation_results(
        self, report: EvaluationReport
    ) -> tuple[dict[str, list[float]], list[dict[str, Any]]]:
        aggregated: dict[str, list[float]] = {
            definition.id: [] for definition in self.evaluators
        }
        case_results: list[dict[str, Any]] = []
        for index, case in enumerate(report.cases):
            evaluations: dict[str, dict[str, Any]] = {}
            for eval_name, evaluation in case.assertions.items():
                passed = bool(evaluation.value)
                evaluations[eval_name] = {
                    "score": 1.0 if passed else 0.0,
                    "passed": passed,
                    "reason": evaluation.reason,
                }
                aggregated.setdefault(eval_name, []).append(1.0 if passed else 0.0)
            output_payload = case.output
            if isinstance(output_payload, TextTensor):  # pragma: no branch
                output_payload = output_payload.metadata.get(
                    "payload", output_payload.text
                )
            duration_ms = case.task_duration * 1000.0
            case_result = {
                "case_index": index,
                "inputs": case.inputs,
                "output": output_payload,
                "evaluations": evaluations,
                "metadata": case.metadata or {},
                "duration_ms": duration_ms,
            }
            case_results.append(case_result)
            await self._emit_progress(
                {
                    "node": self.name,
                    "event": "evaluation_progress",
                    "payload": case_result,
                }
            )

        for failure in report.failures:
            case_result = {
                "case_index": len(case_results),
                "error": failure.error_message,
                "duration_ms": 0.0,
                "evaluations": {},
            }
            case_results.append(case_result)
            for evaluator_id in aggregated:
                aggregated[evaluator_id].append(0.0)
            await self._emit_progress(
                {
                    "node": self.name,
                    "event": "evaluation_progress",
                    "payload": case_result,
                }
            )

        return aggregated, case_results

    def _sync_trained_prompts(self, runtime_prompts: Mapping[str, TextTensor]) -> None:
        for name, tensor in runtime_prompts.items():
            prompt = self.prompts.get(name)
            if prompt is None:
                continue
            prompt.text = tensor.text

    async def _collect_training_results(
        self, report: EvaluationReport, epoch: int
    ) -> tuple[dict[str, list[float]], list[dict[str, Any]]]:
        aggregated: dict[str, list[float]] = {
            definition.id: [] for definition in self.evaluators
        }
        case_results: list[dict[str, Any]] = []
        for index, case in enumerate(report.cases):
            evaluations: dict[str, dict[str, Any]] = {}
            for eval_name, evaluation in case.assertions.items():
                passed = bool(evaluation.value)
                evaluations[eval_name] = {
                    "score": 1.0 if passed else 0.0,
                    "passed": passed,
                    "reason": evaluation.reason,
                }
                aggregated.setdefault(eval_name, []).append(1.0 if passed else 0.0)
            output_payload = case.output
            if isinstance(output_payload, TextTensor):  # pragma: no branch
                output_payload = output_payload.metadata.get(
                    "payload", output_payload.text
                )
            duration_ms = case.task_duration * 1000.0
            case_result = {
                "case_index": index,
                "epoch": epoch,
                "inputs": case.inputs,
                "output": output_payload,
                "evaluations": evaluations,
                "metadata": case.metadata or {},
                "duration_ms": duration_ms,
            }
            case_results.append(case_result)
            await self._emit_progress(
                {
                    "node": self.name,
                    "event": "training_progress",
                    "payload": case_result,
                }
            )

        for failure in report.failures:
            case_result = {
                "case_index": len(case_results),
                "epoch": epoch,
                "error": failure.error_message,
                "duration_ms": 0.0,
                "evaluations": {},
            }
            case_results.append(case_result)
            for evaluator_id in aggregated:
                aggregated[evaluator_id].append(0.0)
            await self._emit_progress(
                {
                    "node": self.name,
                    "event": "training_progress",
                    "payload": case_result,
                }
            )

        return aggregated, case_results

    async def _evaluate_case(
        self,
        evaluators: Sequence[tuple[EvaluatorDefinition, Any]],
        context: EvaluationContext,
        *,
        aggregated: dict[str, list[float]],
    ) -> dict[str, dict[str, Any]]:
        evaluations: dict[str, dict[str, Any]] = {}
        for definition, evaluator in evaluators:
            outcome = await self._run_evaluator(definition, evaluator, context)
            evaluations[definition.id] = outcome
            aggregated.setdefault(definition.id, []).append(outcome["score"])
        return evaluations

    async def _run_evaluator(
        self,
        definition: EvaluatorDefinition,
        evaluator: Any,
        context: EvaluationContext,
    ) -> dict[str, Any]:
        try:
            if hasattr(evaluator, "evaluate"):
                candidate = evaluator.evaluate(context)
            else:
                candidate = evaluator(context)
            result = await candidate if inspect.iscoroutine(candidate) else candidate
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                "Evaluator %s failed while scoring case.", definition.id, exc_info=exc
            )
            return {"score": 0.0, "passed": False, "reason": str(exc)}

        return self._normalise_evaluation_result(result)

    @staticmethod
    def _normalise_evaluation_result(result: Any) -> dict[str, Any]:
        score: float | None = None
        reason: str | None = None
        value: Any = result

        if hasattr(result, "value"):
            value = result.value  # type: ignore[attr-defined]
            reason = result.reason if hasattr(result, "reason") else None
        elif isinstance(result, Mapping):
            if "value" in result:  # pragma: no branch
                value = result["value"]
            reason = result.get("reason")

        if isinstance(value, bool):
            score = 1.0 if value else 0.0
            passed = value
        elif isinstance(value, int | float):
            score = float(value)
            passed = score >= 0.5
        else:
            passed = False if value is None else bool(value)

        if score is None:
            score = 1.0 if passed else 0.0
        return {"score": score, "passed": passed, "reason": reason}

    def _summarize_metrics(
        self, aggregated: Mapping[str, list[float]]
    ) -> dict[str, float]:
        summary: dict[str, float] = {}
        for evaluator_id, scores in aggregated.items():
            if scores:
                summary[evaluator_id] = sum(scores) / len(scores)
            else:
                summary[evaluator_id] = 0.0
        return summary

    async def _emit_progress(self, payload: dict[str, Any]) -> None:
        if self.progress_callback is None:
            return
        await self.progress_callback(payload)

    def _merge_inputs(self, state: State, case: EvaluationCase) -> dict[str, Any]:
        base_inputs: Mapping[str, Any] | None = None
        if isinstance(state, Mapping):
            if "inputs" in state and isinstance(state["inputs"], Mapping):
                base_inputs = state["inputs"]
            else:
                base_inputs = state
        merged: dict[str, Any] = {}
        if isinstance(base_inputs, Mapping):
            merged.update(base_inputs)
        merged.update(case.inputs)
        return merged

    def _build_case_state(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        runtime_config: Mapping[str, Any] = (
            dict(self.state_config) if isinstance(self.state_config, Mapping) else {}
        )
        if (
            self.graph_config
            and isinstance(self.graph_config, Mapping)
            and self.graph_config.get("format") == LANGGRAPH_SCRIPT_FORMAT
        ):
            state = dict(inputs)
            state["config"] = runtime_config
            return state
        return {
            "messages": [],
            "node_results": {},
            "inputs": dict(inputs),
            "structured_response": None,
            "config": runtime_config,
        }

    @staticmethod
    def _extract_output(output_state: Any) -> Any:
        if isinstance(output_state, Mapping):
            if "node_results" in output_state:
                return output_state["node_results"]
            if "output" in output_state:
                return output_state["output"]
        return output_state

    def _require_compiled_graph(self) -> Any:
        if self.compiled_graph is None:
            msg = "AgentensorNode evaluation requires a compiled graph."
            raise ValueError(msg)
        return self.compiled_graph

    def _enforce_training_limits(self, config: RunnableConfig) -> RunnableConfig:
        if not isinstance(config, Mapping):
            return config
        updated = dict(config)
        allowed_concurrency = min(
            self.optimizer.max_concurrency, self._max_concurrency_cap
        )
        max_concurrency = updated.get("max_concurrency")
        if not isinstance(max_concurrency, int) or (  # pragma: no branch
            max_concurrency > allowed_concurrency
        ):
            updated["max_concurrency"] = allowed_concurrency
        recursion_limit = updated.get("recursion_limit")
        if not isinstance(recursion_limit, int):
            updated["recursion_limit"] = 50
        return cast(RunnableConfig, updated)

    def _resolve_evaluators(self) -> list[tuple[EvaluatorDefinition, Any]]:
        resolved: list[tuple[EvaluatorDefinition, Any]] = []
        for definition in self.evaluators:
            evaluator = definition.load()
            resolved.append((definition, evaluator))
        return resolved

    def _refresh_state_prompts(self, prompts: Mapping[str, Any]) -> None:
        if not prompts:
            return
        base_config = (
            dict(self.state_config) if isinstance(self.state_config, Mapping) else {}
        )
        base_config["prompts"] = prompts
        self.state_config = base_config

    @staticmethod
    def _rewrite_prompt(text: str, grad: str) -> str:
        cleaned_grad = " ".join(str(grad).split())
        if not cleaned_grad:
            return text
        if cleaned_grad in text:
            return text
        return f"{text.strip()}\n\n[feedback] {cleaned_grad}".strip()

    def _score_summary(self, summary: Mapping[str, float]) -> float:
        if not summary:
            return 0.0
        return sum(summary.values()) / len(summary)

    async def _emit_checkpoint(
        self,
        summary: Mapping[str, float],
        config: RunnableConfig,
        *,
        epoch: int,
        is_best: bool,
        prompts: Mapping[str, TextTensor] | None = None,
    ) -> AgentensorCheckpoint:
        checkpoint_config = self._checkpoint_config(config, prompts=prompts)
        metadata = {"epoch": epoch, "summary": dict(summary)}
        if self.checkpoint_store is not None and self.workflow_id is not None:
            return await self.checkpoint_store.record_checkpoint(
                workflow_id=self.workflow_id,
                runnable_config=checkpoint_config,
                metrics=summary,
                metadata=metadata,
                is_best=is_best,
            )

        return AgentensorCheckpoint(
            workflow_id=self.workflow_id or "unknown",
            config_version=epoch,
            runnable_config=checkpoint_config,
            metrics=dict(summary),
            metadata=metadata,
            is_best=is_best,
        )

    def _checkpoint_config(
        self,
        config: RunnableConfig,
        prompts: Mapping[str, TextTensor] | None = None,
    ) -> dict[str, Any]:
        base_config = dict(config) if isinstance(config, Mapping) else {}
        prompt_source: Mapping[str, Any] | None = (
            prompts if prompts is not None else self.prompts
        )
        if prompt_source:
            prompt_payload: dict[str, Any] = {}
            for name, prompt in prompt_source.items():
                if hasattr(prompt, "model_dump"):
                    prompt_payload[name] = prompt.model_dump(mode="json")  # type: ignore[call-arg]
                elif isinstance(prompt, TextTensor):  # pragma: no branch
                    trainable_prompt = self.prompts.get(name)
                    if trainable_prompt is not None:
                        prompt_payload[name] = trainable_prompt.model_dump(mode="json")
                        continue
                    prompt_payload[name] = {
                        "text": prompt.text,
                        "type": "TextTensor",
                        "requires_grad": getattr(prompt, "requires_grad", False),
                        "metadata": getattr(prompt, "metadata", {}),
                    }
            if prompt_payload:  # pragma: no branch
                base_config["prompts"] = prompt_payload
        return base_config


__all__ = ["AgentensorNode"]
