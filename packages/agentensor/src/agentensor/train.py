"""Trainer."""

from __future__ import annotations
import asyncio
import json
from collections.abc import Mapping
from typing import Any, Literal
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from pydantic_evals import Dataset
from pydantic_evals.reporting import EvaluationReport
from agentensor.optim import Optimizer
from agentensor.tensor import TextTensor


CompiledGraph = CompiledStateGraph[Any, Any, Any, Any]


class Trainer:
    """Trainer."""

    def __init__(
        self,
        graph: CompiledGraph,
        graph_recursion_limit: int = 25,
        train_dataset: Dataset[TextTensor, TextTensor, Any] | None = None,
        eval_dataset: Dataset[TextTensor, TextTensor, Any] | None = None,
        test_dataset: Dataset[TextTensor, TextTensor, Any] | None = None,
        optimizer: Optimizer | None = None,
        epochs: int = 10,
        stop_threshold: float = 0.95,
    ):
        """Initialize the trainer."""
        self.graph = graph
        self.graph_recursion_limit = graph_recursion_limit
        self.optimizer = optimizer
        self.epochs = epochs
        self.stop_threshold = stop_threshold
        self.train_dataset = train_dataset
        self.eval_dataset = eval_dataset
        self.test_dataset = test_dataset

    async def forward(self, x: TextTensor) -> TextTensor:
        """Forward the graph."""
        result = await self.graph.ainvoke(
            {"output": x}, {"recursion_limit": self.graph_recursion_limit}
        )
        return result["output"]

    def train(self) -> None:
        """Train the graph."""
        self._require_dataset("train")
        optimizer = self._require_optimizer()
        for i in range(self.epochs):
            report = self.evaluate("train")
            report.print(
                include_input=True, include_output=True, include_durations=True
            )

            # Backward those failed cases
            for case in report.cases:
                losses: list[str] = []
                for evaluator in case.assertions.values():
                    if not evaluator.value:
                        reason = getattr(evaluator, "reason", None)
                        if reason is None or (
                            isinstance(reason, str) and not reason.strip()
                        ):
                            losses.append("Evaluation failed without a reason.")
                        else:
                            losses.append(str(reason))
                if losses:
                    case.output.backward(" ".join(losses))

            optimizer.step()
            optimizer.zero_grad()

            performance = report.averages()
            assertions = None if performance is None else performance.assertions
            self.after_epoch(i, report)
            if assertions is not None and assertions >= self.stop_threshold:
                print("Optimization complete.")
                break

    def evaluate(
        self,
        data_split: Literal["train", "eval", "test"] = "eval",
        limit_cases: int | None = None,
        max_concurrency: int | None = None,
        progress: bool = True,
    ) -> EvaluationReport:
        """Evaluate the graph."""
        dataset = self._require_dataset(data_split)
        if limit_cases:
            limited_cases = dataset.cases[:limit_cases]
            dataset = Dataset(cases=limited_cases, evaluators=dataset.evaluators)
        report = dataset.evaluate_sync(
            self.forward,
            max_concurrency=max_concurrency,
            progress=progress,
        )

        return report

    def test(self, limit_cases: int | None = None) -> None:
        """Test the graph."""
        report = self.evaluate("test", limit_cases=limit_cases)
        report.print(include_input=True, include_output=True, include_durations=True)

    def after_epoch(self, epoch_index: int, report: EvaluationReport) -> None:
        """Optional hook for subclasses to record state."""
        return None

    def _require_dataset(self, data_split: str) -> Dataset[Any, Any, Any]:
        """Return the dataset for a split or raise a descriptive error."""
        dataset = getattr(self, f"{data_split}_dataset", None)
        if dataset is None:
            raise ValueError(f"{data_split} dataset is required")
        return dataset

    def _require_optimizer(self) -> Optimizer:
        """Return the optimizer or raise a descriptive error."""
        if self.optimizer is None:
            raise ValueError("Optimizer is required")
        return self.optimizer


class GraphTrainer(Trainer):
    """Trainer that runs a compiled graph against mapping inputs."""

    def __init__(
        self,
        *,
        graph: CompiledGraph,
        dataset: Dataset[Any, Any, Any],
        optimizer: Optimizer | None = None,
        epochs: int,
        runtime_prompts: Mapping[str, TextTensor],
        base_state: Mapping[str, Any] | None = None,
        graph_config: RunnableConfig | None = None,
        max_concurrency: int = 1,
        case_timeout: int = 30,
        stop_threshold: float = 2.0,
        script_format: bool = False,
    ) -> None:
        """Initialize the graph trainer."""
        super().__init__(
            graph=graph,
            train_dataset=dataset,
            eval_dataset=dataset,
            optimizer=optimizer,
            epochs=epochs,
            stop_threshold=stop_threshold,
        )
        self.runtime_prompts = runtime_prompts
        self.base_state = base_state or {}
        self.graph_config = graph_config
        self.max_concurrency = max_concurrency
        self.case_timeout = case_timeout
        self.reports: list[EvaluationReport] = []
        self.script_format = script_format
        self.prompt_history: list[dict[str, str]] = []

    async def forward(self, case_inputs: Mapping[str, Any]) -> TextTensor:  # type: ignore[override]
        """Execute the compiled graph and return a tensor with the raw payload."""
        merged_inputs = self._merge_inputs(case_inputs)
        case_state = self._build_case_state(merged_inputs)
        output_state = await asyncio.wait_for(
            self.graph.ainvoke(case_state, config=self.graph_config),
            timeout=self.case_timeout,
        )
        output_payload = self._extract_output(output_state)
        parents = [
            prompt for prompt in self.runtime_prompts.values() if prompt.requires_grad
        ]
        tensor = TextTensor(
            text=self._stringify_output(output_payload),
            requires_grad=True,
            parents=parents,
            metadata={"payload": output_payload},
        )
        return tensor

    def evaluate(
        self,
        data_split: Literal["train", "eval", "test"] = "eval",
        limit_cases: int | None = None,
        max_concurrency: int | None = None,
        progress: bool = False,
    ) -> EvaluationReport:
        """Run evaluation without rendering progress to stdout."""
        dataset = self._require_dataset(data_split)
        cases = dataset.cases
        if limit_cases is not None:
            cases = cases[:limit_cases]
            dataset = Dataset(cases=cases, evaluators=dataset.evaluators)
        report = dataset.evaluate_sync(
            self.forward,
            max_concurrency=max_concurrency or self.max_concurrency,
            progress=progress,
        )
        self.reports.append(report)
        return report

    def _merge_inputs(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        if isinstance(self.base_state, Mapping):
            base_inputs = self.base_state.get("inputs", self.base_state)
            if isinstance(base_inputs, Mapping):  # pragma: no branch
                merged.update(base_inputs)
        merged.update(inputs)
        return merged

    def _build_case_state(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        runtime_config = (
            dict(self.base_state.get("config", {}))
            if isinstance(self.base_state, Mapping)
            else {}
        )
        if self.script_format:
            state = dict(inputs)
            state["config"] = runtime_config | {"prompts": self.runtime_prompts}
            return state
        return {
            "messages": [],
            "node_results": {},
            "inputs": dict(inputs),
            "structured_response": None,
            "config": runtime_config | {"prompts": self.runtime_prompts},
        }

    @staticmethod
    def _stringify_output(output: Any) -> str:
        if isinstance(output, str):
            return output
        try:
            return json.dumps(output)
        except TypeError:
            return str(output)

    @staticmethod
    def _extract_output(output_state: Any) -> Any:
        if isinstance(output_state, Mapping):  # pragma: no branch
            results = output_state.get("node_results")
            if isinstance(results, Mapping) and results:
                return results
            if "output" in output_state:
                return output_state["output"]
            message_output = GraphTrainer._extract_message_output(
                output_state.get("messages")
            )
            if message_output is not None:
                return message_output
        return output_state

    @staticmethod
    def _extract_message_output(messages: Any) -> Any | None:
        if not isinstance(messages, list):
            return None
        fallback: Any | None = None
        for message in reversed(messages):
            if isinstance(message, Mapping):
                content = message.get("content")
                role = message.get("role") or message.get("type")
            else:
                content = getattr(message, "content", None)
                role = getattr(message, "role", None) or getattr(message, "type", None)
            if role in {"assistant", "ai"}:
                return content
            if fallback is None and content is not None:  # pragma: no branch
                if not (isinstance(content, str) and not content.strip()):
                    fallback = content
        return fallback

    def after_epoch(self, epoch_index: int, report: EvaluationReport) -> None:
        """Record prompt snapshots after each optimizer step."""
        snapshot: dict[str, str] = {
            name: tensor.text for name, tensor in self.runtime_prompts.items()
        }
        self.prompt_history.append(snapshot)
