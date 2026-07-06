"""Tests for AgentensorNode registration and prompt interpolation."""

from __future__ import annotations
import asyncio
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
import pytest
from langchain_core.runnables import RunnableConfig
from pydantic_evals.evaluators import EvaluationReason
from agentensor.loss import LLMTensorJudge
from agentensor.tensor import TextTensor
from orcheo.agentensor.checkpoints import AgentensorCheckpoint
from orcheo.agentensor.evaluation import (
    EvaluationCase,
    EvaluationContext,
    EvaluationDataset,
    EvaluatorDefinition,
)
from orcheo.agentensor.prompts import TrainablePrompt
from orcheo.agentensor.training import OptimizerConfig
from orcheo.graph.ingestion.config import LANGGRAPH_SCRIPT_FORMAT
from orcheo.graph.state import State
from orcheo.nodes.ai.agentensor import AgentensorNode, _EvaluatorAdapter, _TextPayload
from orcheo.nodes.registry import registry
from orcheo.runtime.runnable_config import RunnableConfigModel
from tests.agentensor.helpers import EvaluateCallable, simple_result


@pytest.fixture(autouse=True)
def _patch_agentensor_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub agentensor LLM utilities to avoid real network calls during tests."""

    class DummyAgent:
        def invoke(self, *_: object, **__: object) -> dict[str, list[dict[str, str]]]:
            return {"messages": [SimpleNamespace(content="stub-feedback")]}

    monkeypatch.setattr("agentensor.tensor.init_chat_model", lambda *_, **__: object())
    monkeypatch.setattr("agentensor.tensor.create_agent", lambda *_, **__: DummyAgent())
    monkeypatch.setattr("agentensor.optim.init_chat_model", lambda *_, **__: object())
    monkeypatch.setattr("agentensor.optim.create_agent", lambda *_, **__: DummyAgent())


def _build_state_and_config() -> tuple[State, RunnableConfig]:
    base_config = RunnableConfigModel(
        prompts={"seed": TrainablePrompt(text="Hello world")},
        tags=["experiment"],
        run_name="agentensor-eval",
    )
    runtime_config = base_config.to_runnable_config("exec-agentensor")
    state_config = base_config.to_state_config("exec-agentensor")
    state = cast(
        State,
        {
            "inputs": {"lang": "en"},
            "node_results": {},
            "structured_response": {},
            "config": state_config,
        },
    )
    return state, runtime_config


def evaluation_echo(ctx: EvaluationContext) -> dict[str, Any]:
    """Simple evaluator that checks output mirroring."""
    output = ctx.output if isinstance(ctx.output, dict) else {}
    value = output.get("echo") == ctx.inputs.get("prompt")
    return {"value": value, "reason": "echo match" if value else "mismatch"}


def training_evaluator(ctx: EvaluationContext) -> dict[str, Any]:
    """Evaluator used to drive prompt updates in training mode."""
    output = ctx.output if isinstance(ctx.output, dict) else {}
    candidate = output.get("echo", "")
    passed = "feedback" in str(candidate)
    return {"value": passed, "reason": "needs feedback"}


@pytest.mark.asyncio
async def test_agentensor_node_resolves_prompts_from_config() -> None:
    state, runtime_config = _build_state_and_config()
    node = AgentensorNode(
        name="agentensor",
        prompts={
            "candidate": TrainablePrompt(
                text="{{config.prompts.seed.text}}",
                metadata={"lang": "{{inputs.lang}}"},
                requires_grad=True,
            )
        },
    )

    result = await node(state, runtime_config)

    payload: dict[str, Any] = result["node_results"]["agentensor"]
    candidate = payload["prompts"]["candidate"]
    assert candidate["text"] == "Hello world"
    assert candidate["metadata"]["lang"] == "en"
    assert payload["tags"] == ["experiment"]
    assert registry.get_node("AgentensorNode") is AgentensorNode


@pytest.mark.asyncio
async def test_agentensor_node_runs_evaluation_with_progress() -> None:
    class DummyGraph:
        async def ainvoke(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            return {"node_results": {"echo": state["inputs"]["prompt"]}}

    progress_events: list[dict[str, Any]] = []

    async def progress(payload: dict[str, Any]) -> None:
        progress_events.append(payload)

    state, runtime_config = _build_state_and_config()
    node = AgentensorNode(
        name="agentensor",
        mode="evaluate",
        dataset=EvaluationDataset(
            cases=[EvaluationCase(inputs={"prompt": "ping"}, metadata={"idx": 0})]
        ),
        evaluators=[
            EvaluatorDefinition(
                id="echo-check",
                entrypoint="tests.agentensor.test_agentensor_node:evaluation_echo",
            )
        ],
        compiled_graph=DummyGraph(),
        graph_config={},
        state_config=state["config"],
        progress_callback=progress,
    )

    result = await node(state, runtime_config)

    payload: dict[str, Any] = result["node_results"]["agentensor"]
    assert payload["summary"] == {"echo-check": 1.0}
    assert payload["results"][0]["evaluations"]["echo-check"]["passed"] is True
    assert progress_events[0]["event"] == "evaluation_progress"
    assert progress_events[-1]["event"] == "evaluation_summary"


@pytest.mark.asyncio
async def test_agentensor_node_adopts_state_config_and_tag_payloads() -> None:
    node = AgentensorNode(name="state-node")
    state = {
        "config": {"configurable": {"thread_id": "tid"}},
        "inputs": {"lang": "en"},
    }

    result = await node(state, {"tags": [1, 2]})
    payload = result["node_results"][node.name]

    assert node.state_config == state["config"]
    assert payload["tags"] == ["1", "2"]


@pytest.mark.asyncio
async def test_agentensor_node_returns_base_for_non_evaluate_modes() -> None:
    node = AgentensorNode(name="mode-node")
    node.mode = "preview"

    result = await node({}, {"tags": ["preview"]})
    payload = result["node_results"][node.name]

    assert payload["mode"] == "preview"
    assert "dataset_id" not in payload
    assert payload["tags"] == ["preview"]


@pytest.mark.asyncio
async def test_agentensor_node_run_applies_state_config_and_tags() -> None:
    node = AgentensorNode(name="config-node")
    state = {"config": {"value": "bar"}}

    result = await node(state, {"tags": ["alpha", 2]})

    assert node.state_config == state["config"]
    payload = result["node_results"][node.name]
    assert payload["tags"] == ["alpha", "2"]
    assert payload["summary"] == {}


@pytest.mark.asyncio
async def test_agentensor_training_emits_checkpoints_and_best_config() -> None:
    class TrainingGraph:
        async def ainvoke(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            prompt_obj = state["config"]["prompts"]["candidate"]
            prompt_text = (
                prompt_obj.text if hasattr(prompt_obj, "text") else prompt_obj["text"]
            )
            return {"node_results": {"echo": prompt_text}}

    progress_events: list[dict[str, Any]] = []

    async def progress(payload: dict[str, Any]) -> None:
        progress_events.append(payload)

    state, runtime_config = _build_state_and_config()
    node = AgentensorNode(
        name="agentensor_trainer",
        mode="train",
        dataset=EvaluationDataset(
            cases=[EvaluationCase(inputs={"prompt": "ping"}, metadata={"idx": 0})]
        ),
        evaluators=[
            EvaluatorDefinition(
                id="echo-pass",
                entrypoint="tests.agentensor.test_agentensor_node:training_evaluator",
            )
        ],
        compiled_graph=TrainingGraph(),
        graph_config={},
        state_config=state["config"],
        progress_callback=progress,
        optimizer=OptimizerConfig(epochs=2, checkpoint_interval=1, max_concurrency=2),
        workflow_id="wf-training",
        prompts={
            "candidate": TrainablePrompt(
                text="{{config.prompts.seed.text}}",
                requires_grad=True,
            )
        },
    )

    result = await node(state, runtime_config)

    payload: dict[str, Any] = result["node_results"]["agentensor_trainer"]
    assert payload["summary"]["echo-pass"] == 1.0
    assert payload["best_checkpoint"]["workflow_id"] == "wf-training"
    assert len(payload["checkpoints"]) == 2
    assert "feedback" in payload["prompts"]["candidate"]["text"]
    assert any(event["event"] == "training_checkpoint" for event in progress_events)


@pytest.mark.asyncio
async def test_agentensor_training_requires_trainable_prompts() -> None:
    class TrainingGraph:
        async def ainvoke(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            prompt_obj = state["config"]["prompts"]["candidate"]
            prompt_text = (
                prompt_obj.text if hasattr(prompt_obj, "text") else prompt_obj["text"]
            )
            return {"node_results": {"echo": prompt_text}}

    state, runtime_config = _build_state_and_config()
    node = AgentensorNode(
        name="agentensor_trainer",
        mode="train",
        dataset=EvaluationDataset(
            cases=[EvaluationCase(inputs={"prompt": "ping"}, metadata={"idx": 0})]
        ),
        evaluators=[
            EvaluatorDefinition(
                id="echo-pass",
                entrypoint="tests.agentensor.test_agentensor_node:training_evaluator",
            )
        ],
        compiled_graph=TrainingGraph(),
        graph_config={},
        state_config=state["config"],
        optimizer=OptimizerConfig(epochs=1),
        workflow_id="wf-training",
        prompts={
            "candidate": TrainablePrompt(
                text="{{config.prompts.seed.text}}",
                requires_grad=False,
            )
        },
    )

    with pytest.raises(ValueError, match="requires_grad=True"):
        await node(state, runtime_config)


@pytest.mark.asyncio
async def test_run_training_returns_empty_when_dataset_missing() -> None:
    node = AgentensorNode(name="trainer")

    result = await node._run_training(
        {"inputs": {}}, {}, {"mode": "train", "prompts": {}, "tags": []}
    )

    assert result["summary"] == {}
    assert result["results"] == []
    assert result["checkpoints"] == []


@pytest.mark.asyncio
async def test_agentensor_training_adds_best_checkpoint_when_unscheduled() -> None:
    class TrainingGraph:
        async def ainvoke(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            prompt_obj = state["config"]["prompts"]["candidate"]
            prompt_text = (
                prompt_obj.text if hasattr(prompt_obj, "text") else prompt_obj["text"]
            )
            return {"node_results": {"echo": prompt_text}}

    state, runtime_config = _build_state_and_config()
    node = AgentensorNode(
        name="agentensor_trainer",
        mode="train",
        dataset=EvaluationDataset(
            cases=[EvaluationCase(inputs={"prompt": "ping"}, metadata={"idx": 0})]
        ),
        evaluators=[
            EvaluatorDefinition(
                id="echo-pass",
                entrypoint="tests.agentensor.test_agentensor_node:training_evaluator",
            )
        ],
        compiled_graph=TrainingGraph(),
        graph_config={},
        state_config=state["config"],
        optimizer=OptimizerConfig(epochs=3, checkpoint_interval=5),
        workflow_id="wf-training",
        prompts={
            "candidate": TrainablePrompt(
                text="{{config.prompts.seed.text}}",
                requires_grad=True,
            )
        },
    )

    result = await node(state, runtime_config)

    payload: dict[str, Any] = result["node_results"]["agentensor_trainer"]
    epochs_recorded = {cp["metadata"]["epoch"] for cp in payload["checkpoints"]}
    assert 1 in epochs_recorded
    assert 2 in epochs_recorded
    assert 3 in epochs_recorded
    assert payload["best_checkpoint"]["metadata"]["epoch"] in {2, 3}


@pytest.mark.asyncio
async def test_run_training_enforces_case_limit_and_best_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LimitedGraph:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def ainvoke(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            self.calls.append(state)
            return {"node_results": {"echo": state["inputs"]["prompt"]}}

    state, runtime_config = _build_state_and_config()
    graph = LimitedGraph()
    node = AgentensorNode(
        name="trainer",
        mode="train",
        dataset=EvaluationDataset(
            cases=[
                EvaluationCase(inputs={"prompt": "first"}),
                EvaluationCase(inputs={"prompt": "second"}),
            ]
        ),
        evaluators=[
            EvaluatorDefinition(
                id="limit-eval",
                entrypoint="tests.agentensor.helpers:simple_result",
            )
        ],
        prompts={"seed": TrainablePrompt(text="seed", requires_grad=True)},
        compiled_graph=graph,
        optimizer=OptimizerConfig(epochs=2, max_concurrency=1),
    )
    node.max_cases = 1

    result = await node(state, runtime_config)

    payload: dict[str, Any] = result["node_results"]["trainer"]
    assert len(graph.calls) == node.optimizer.epochs
    assert payload["summary"]["limit-eval"] == 1.0
    assert all(case["case_index"] == 0 for case in payload["results"])


@pytest.mark.asyncio
async def test_run_training_updates_prompts_and_checkpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = AgentensorNode(
        name="spoof-trainer",
        mode="train",
        dataset=EvaluationDataset(
            id="dataset",
            cases=[EvaluationCase(inputs={"prompt": "value"})],
        ),
        evaluators=[eval_definition],
        compiled_graph=object(),
        optimizer=OptimizerConfig(
            epochs=1,
            max_concurrency=1,
            case_timeout_seconds=5,
            checkpoint_interval=1,
        ),
        workflow_id="workflow",
        prompts={"seed": TrainablePrompt(text="seed", requires_grad=True)},
    )

    runtime_tensor = TextTensor(
        text="seed",
        requires_grad=True,
        model=SimpleNamespace(),
    )

    monkeypatch.setattr(
        "orcheo.nodes.agentensor.build_text_tensors",
        lambda prompts: {"seed": runtime_tensor},
    )

    class DummyTrainer:
        def __init__(
            self,
            *args: object,
            runtime_prompts: dict[str, TextTensor],
            **kwargs: object,
        ) -> None:
            self.runtime_prompts = runtime_prompts
            self.reports = [SimpleNamespace(cases=[], failures=[])]
            self.prompt_history = [{"seed": "updated", "extra": "value"}]

        def train(self) -> None:
            return None

    monkeypatch.setattr("orcheo.nodes.agentensor.GraphTrainer", DummyTrainer)

    async def fake_to_thread(
        fn: Callable[..., Any], *args: object, **kwargs: object
    ) -> Any:
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    async def fake_collect_training_results(
        self, report: Any, epoch: int
    ) -> tuple[dict[str, list[float]], list[dict[str, Any]]]:
        return {"runner": [1.0]}, [{"case_index": 0, "epoch": epoch, "evaluations": {}}]

    monkeypatch.setattr(
        AgentensorNode,
        "_collect_training_results",
        fake_collect_training_results,
    )

    async def fake_emit_checkpoint(
        self,
        summary: Mapping[str, float],
        config: RunnableConfig,
        *,
        epoch: int,
        is_best: bool,
        prompts: Mapping[str, TextTensor] | None = None,
    ) -> AgentensorCheckpoint:
        return AgentensorCheckpoint(
            workflow_id=self.workflow_id or "unknown",
            config_version=epoch,
            runnable_config=config,
            metrics=dict(summary),
            metadata={"epoch": epoch},
            is_best=is_best,
        )

    monkeypatch.setattr(AgentensorNode, "_emit_checkpoint", fake_emit_checkpoint)

    progress = AsyncMock()
    monkeypatch.setattr(AgentensorNode, "_emit_progress", progress)

    result = await node._run_training(
        {"inputs": {}},
        {"recursion_limit": 5},
        {"mode": "train", "prompts": {}, "tags": []},
    )

    assert runtime_tensor.text == "updated"
    assert node.prompts["seed"].text == "updated"
    assert result["summary"] == {"runner": 1.0}
    assert result["results"][0]["epoch"] == 1
    assert result["checkpoints"]
    assert result["best_checkpoint"]["metrics"]["runner"] == 1.0
    progress.assert_called()


class _StubGraph:
    def __init__(self, result: object | Exception) -> None:
        self.result = result

    async def ainvoke(self, *_: object, **__: object) -> object:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _DummyCheckpointStore:
    def __init__(self) -> None:
        self.called = False

    async def record_checkpoint(self, **kwargs: object) -> AgentensorCheckpoint:  # type: ignore[override]
        self.called = True
        return AgentensorCheckpoint(
            workflow_id=str(kwargs.get("workflow_id")),
            config_version=kwargs.get("metadata", {}).get("epoch", 1),
            runnable_config={},
            metrics=kwargs.get("metrics", {}),
            metadata=kwargs.get("metadata", {}),
            is_best=kwargs.get("is_best", False),
        )


def test_merge_inputs_prefers_inputs_key() -> None:
    node = AgentensorNode(name="merge-node")
    state = {"inputs": {"shared": "state"}, "other": "value"}
    case = EvaluationCase(inputs={"shared": "case", "extra": 1})

    assert node._merge_inputs(state, case)["shared"] == "case"


def test_merge_inputs_uses_state_when_inputs_missing() -> None:
    node = AgentensorNode(name="merge-node")
    state = {"a": 1}
    case = EvaluationCase(inputs={"b": 2})

    merged = node._merge_inputs(state, case)

    assert merged == {"a": 1, "b": 2}


def test_merge_inputs_handles_non_mapping_state() -> None:
    node = AgentensorNode(name="merge-node")
    state = SimpleNamespace(value=1)
    case = EvaluationCase(inputs={"b": 2})

    merged = node._merge_inputs(state, case)

    assert merged == {"b": 2}


def test_build_case_state_includes_langgraph_config() -> None:
    node = AgentensorNode(name="graph-node")
    node.graph_config = {"format": LANGGRAPH_SCRIPT_FORMAT}
    node.state_config = {"configurable": {"thread_id": "tid"}}

    state = node._build_case_state({"prompt": "hi"})

    assert state["config"]["configurable"]["thread_id"] == "tid"
    assert "messages" not in state


def test_build_case_state_defaults_structure() -> None:
    node = AgentensorNode(name="graph-node")

    default = node._build_case_state({"prompt": "hi"})

    assert default["messages"] == []
    assert default["node_results"] == {}
    assert default["inputs"]["prompt"] == "hi"


def test_extract_output_picks_best_key() -> None:
    node = AgentensorNode(name="extract-node")

    assert node._extract_output({"node_results": {"foo": 1}}) == {"foo": 1}
    assert node._extract_output({"output": 2}) == 2
    assert node._extract_output("raw") == "raw"


def test_extract_output_returns_original_for_unknown_mapping() -> None:
    node = AgentensorNode(name="extract-node")
    data = {"metadata": "value"}

    assert node._extract_output(data) == data


def test_require_compiled_graph_raises_without_graph() -> None:
    node = AgentensorNode(name="graph-node")

    with pytest.raises(ValueError):
        node._require_compiled_graph()


def test_enforce_training_limits_updates_fields() -> None:
    node = AgentensorNode(name="limit-node")
    node.optimizer = OptimizerConfig(max_concurrency=3)

    updated = node._enforce_training_limits(
        {"max_concurrency": 10, "recursion_limit": "bad"}
    )

    assert updated["max_concurrency"] == 3
    assert updated["recursion_limit"] == 50


def test_enforce_training_limits_returns_non_mapping() -> None:
    node = AgentensorNode(name="limit-node")

    sentinel = "non-mapping"

    assert node._enforce_training_limits(sentinel) is sentinel


def test_resolve_evaluators_loads_helpers() -> None:
    definition = EvaluatorDefinition(
        id="resolver",
        entrypoint="tests.agentensor.helpers:simple_result",
    )
    node = AgentensorNode(name="resolve-node", evaluators=[definition])

    resolved = node._resolve_evaluators()

    assert resolved[0][0] is definition
    assert resolved[0][1] is simple_result


def test_refresh_state_prompts_merges_prompts() -> None:
    node = AgentensorNode(name="refresh-node")
    node.state_config = {"configurable": {}}

    node._refresh_state_prompts({"prompt": "value"})

    assert node.state_config["prompts"]["prompt"] == "value"


def test_refresh_state_prompts_noops_for_empty_input() -> None:
    node = AgentensorNode(name="refresh-node")
    node.state_config = {"configurable": {}}

    node._refresh_state_prompts({})

    assert node.state_config == {"configurable": {}}


def test_rewrite_prompt_behaviour() -> None:
    node = AgentensorNode(name="rewrite-node")

    assert node._rewrite_prompt("text", "") == "text"
    assert node._rewrite_prompt("text [feedback] old", "old") == "text [feedback] old"
    assert "[feedback]" in node._rewrite_prompt("text", "new")


def test_score_summary_handles_empty_and_values() -> None:
    node = AgentensorNode(name="summary-node")

    assert node._score_summary({}) == 0.0
    assert node._score_summary({"a": 1.0, "b": 3.0}) == 2.0


def test_checkpoint_config_serializes_prompts() -> None:
    prompt = TrainablePrompt(text="hi", requires_grad=True)
    node = AgentensorNode(name="checkpoint-node", prompts={"seed": prompt})

    payload = node._checkpoint_config({"foo": "bar"})

    assert payload["prompts"]["seed"]["text"] == "hi"


def test_checkpoint_config_preserves_model_kwargs_for_tensors() -> None:
    prompt = TrainablePrompt(
        text="seed",
        requires_grad=True,
        model_kwargs={"api_key": "test-key"},
    )
    node = AgentensorNode(name="checkpoint-node", prompts={"seed": prompt})
    runtime_prompts = {
        "seed": TextTensor(
            text="updated",
            requires_grad=True,
            model=SimpleNamespace(),
        )
    }
    node._sync_trained_prompts(runtime_prompts)

    payload = node._checkpoint_config({"foo": "bar"}, prompts=runtime_prompts)

    assert payload["prompts"]["seed"]["text"] == "updated"
    assert payload["prompts"]["seed"]["model_kwargs"] == {"api_key": "test-key"}


def test_sync_trained_prompts_skips_missing_runtime_prompts() -> None:
    prompt = TrainablePrompt(text="seed", requires_grad=True)
    node = AgentensorNode(name="sync-node", prompts={"seed": prompt})
    unknown_runtime = {
        "shadow": TextTensor(
            text="hidden",
            requires_grad=True,
            model=SimpleNamespace(),
        )
    }

    node._sync_trained_prompts(unknown_runtime)

    assert node.prompts["seed"].text == "seed"


def test_normalise_evaluation_result_variants() -> None:
    node = AgentensorNode(name="normalise-node")

    class Result:
        value = 1
        reason = "done"

    assert node._normalise_evaluation_result(Result())["score"] == 1
    assert (
        node._normalise_evaluation_result({"value": 0.1, "reason": "r"})["reason"]
        == "r"
    )
    assert node._normalise_evaluation_result(True)["score"] == 1.0
    assert node._normalise_evaluation_result(None)["score"] == 0.0


def test_summarize_metrics_reports_zero() -> None:
    node = AgentensorNode(name="summary-node")

    summary = node._summarize_metrics({"a": [], "b": [2.0]})

    assert summary["a"] == 0.0
    assert summary["b"] == 2.0


@pytest.mark.asyncio
async def test_emit_progress_invokes_callback() -> None:
    node = AgentensorNode(name="progress-node")
    seen: list[dict[str, object]] = []

    async def callback(payload: dict[str, object]) -> None:
        seen.append(payload)

    node.progress_callback = callback

    await node._emit_progress({"status": "ok"})

    assert seen == [{"status": "ok"}]


eval_definition = EvaluatorDefinition(
    id="runner",
    entrypoint="tests.agentensor.helpers:simple_result",
)


@pytest.mark.asyncio
async def test_run_evaluator_captures_evaluate_and_failure() -> None:
    node = AgentensorNode(name="evaluator-node")
    context = EvaluationContext(
        inputs={}, output=1, expected_output=None, metadata={}, duration_ms=0.0
    )

    result = await node._run_evaluator(eval_definition, EvaluateCallable(), context)
    assert result["score"] == 0.2

    def failing(_: EvaluationContext) -> None:
        raise RuntimeError("boom")

    error_result = await node._run_evaluator(eval_definition, failing, context)
    assert error_result["score"] == 0.0
    assert error_result["passed"] is False


@pytest.mark.asyncio
async def test_run_evaluates_cases_with_max_limit() -> None:
    cases = [EvaluationCase(inputs={"value": i}) for i in (1, 2)]
    node = AgentensorNode(
        name="run-node",
        dataset=EvaluationDataset(id="dataset", cases=cases),
        evaluators=[eval_definition],
    )
    node.compiled_graph = _StubGraph({"node_results": {"reply": "ok"}})
    node.max_cases = 1

    result = await node.run({"config": {"foo": "bar"}}, {"tags": ["tag"]})

    assert result["dataset_id"] == "dataset"
    assert result["summary"] == {eval_definition.id: 1.0}
    assert len(result["results"]) == 1
    assert result["tags"] == ["tag"]
    assert node.state_config == {"foo": "bar"}


@pytest.mark.asyncio
async def test_emit_checkpoint_handles_store_and_defaults() -> None:
    node = AgentensorNode(name="checkpoint-node")
    node.workflow_id = "workflow"

    auto_checkpoint = await node._emit_checkpoint(
        {"metric": 1.0}, {}, epoch=1, is_best=True
    )
    assert auto_checkpoint.metrics["metric"] == 1.0

    store = _DummyCheckpointStore()
    node.checkpoint_store = store

    checkpoint = await node._emit_checkpoint(
        {"metric": 2.0}, {}, epoch=2, is_best=False
    )
    assert store.called
    assert checkpoint.config_version == 2


@pytest.mark.asyncio
async def test_emit_checkpoint_sets_defaults() -> None:
    node = AgentensorNode(name="trainer")
    node.workflow_id = "workflow"
    node.optimizer = OptimizerConfig(epochs=1)
    node.prompts = {}
    node._emit_progress = AsyncMock()

    summary = {"metric": 1.0}
    checkpoint = await node._emit_checkpoint(summary, {}, epoch=1, is_best=True)

    assert checkpoint.workflow_id == "workflow"
    assert checkpoint.is_best is True


def test_evaluator_adapter_stringify_and_coerce_payloads() -> None:
    definition = EvaluatorDefinition(
        id="adapter",
        entrypoint="tests.agentensor.helpers:simple_result",
    )
    adapter = _EvaluatorAdapter(definition=definition, evaluator=simple_result)
    assert adapter.definition.id == "adapter"

    class Payload:
        def __str__(self) -> str:
            return "coerced"

    assert _EvaluatorAdapter._stringify_payload("text") == "text"
    assert _EvaluatorAdapter._stringify_payload(Payload()) == "coerced"

    tensor = TextTensor(text="foo", requires_grad=False, model=SimpleNamespace())
    assert _EvaluatorAdapter._coerce_text_payload(tensor) is tensor

    class TextHolder:
        text = "value"

    holder = TextHolder()
    assert _EvaluatorAdapter._coerce_text_payload(holder) is holder

    mapped = _EvaluatorAdapter._coerce_text_payload({"text": "mapped"})
    assert isinstance(mapped, _TextPayload)
    assert mapped.text == "mapped"

    fallback = _EvaluatorAdapter._coerce_text_payload({"value": 1})
    assert isinstance(fallback, _TextPayload)
    assert "1" in fallback.text


@pytest.mark.asyncio
async def test_evaluator_adapter_respects_text_tensor_outputs() -> None:
    definition = EvaluatorDefinition(
        id="adapter-text",
        entrypoint="tests.agentensor.helpers:simple_result",
    )

    captured: list[Any] = []

    def sync_eval(context: EvaluationContext) -> dict[str, Any]:
        captured.append(context.output)
        return {"value": True, "reason": "ok"}

    adapter = _EvaluatorAdapter(definition=definition, evaluator=sync_eval)

    tensor = TextTensor(
        text="payload",
        requires_grad=False,
        model=SimpleNamespace(),
        metadata={"payload": {"result": "ok"}},
    )
    context = SimpleNamespace(
        inputs={},
        output=tensor,
        expected_output=None,
        metadata={},
        duration=0.0,
    )

    result = await adapter.evaluate(context)

    assert captured[0] == {"result": "ok"}
    assert result.value is True


@pytest.mark.asyncio
async def test_evaluator_adapter_handles_llm_judge_branch() -> None:
    class DummyJudge(LLMTensorJudge):
        async def evaluate(
            self, ctx: EvaluationReason | SimpleNamespace
        ) -> EvaluationReason:
            return EvaluationReason(value=True, reason="llm")

    definition = EvaluatorDefinition(
        id="adapter-llm",
        entrypoint="tests.agentensor.helpers:simple_result",
    )
    adapter = _EvaluatorAdapter(
        definition=definition, evaluator=DummyJudge(rubric="test")
    )

    ctx = SimpleNamespace(
        inputs={"text": "input"},
        output={"text": "output"},
        expected_output=None,
        metadata={},
        duration=0.1,
    )
    result = await adapter.evaluate(ctx)
    assert result.value is True


@pytest.mark.asyncio
async def test_evaluator_adapter_handles_coroutine_and_text_tensor_outputs() -> None:
    async def async_eval(_: EvaluationContext) -> dict[str, Any]:
        return {"value": 0.3, "reason": "async"}

    definition = EvaluatorDefinition(
        id="async-adapter",
        entrypoint="tests.agentensor.helpers:simple_result",
    )
    adapter = _EvaluatorAdapter(definition=definition, evaluator=async_eval)
    tensor = TextTensor(
        text="sample",
        requires_grad=False,
        model=SimpleNamespace(),
        metadata={"payload": {"result": "ok"}},
    )
    ctx = SimpleNamespace(
        inputs={"prompt": "x"},
        output=tensor,
        expected_output=None,
        metadata={},
        duration=0.2,
    )

    result = await adapter.evaluate(ctx)
    assert result.reason == "async"
    assert result.value is False


@pytest.mark.asyncio
async def test_collect_evaluation_results_handles_tensor_and_failures() -> None:
    node = AgentensorNode(name="collector")
    node.prompts = {}
    node.evaluators = [eval_definition]
    node.progress_callback = AsyncMock()

    case = SimpleNamespace(
        assertions={eval_definition.id: SimpleNamespace(value=True, reason="ok")},
        output=TextTensor(
            text="tensor",
            requires_grad=False,
            model=SimpleNamespace(),
            metadata={"payload": {"echo": "ok"}},
        ),
        task_duration=0.1,
        inputs={"value": 1},
        metadata={"case": 1},
    )
    report = SimpleNamespace(
        cases=[case],
        failures=[SimpleNamespace(error_message="boom")],
    )

    aggregated, results = await node._collect_evaluation_results(report)

    assert aggregated[eval_definition.id] == [1.0, 0.0]
    assert results[0]["output"] == {"echo": "ok"}
    assert results[-1]["error"] == "boom"


@pytest.mark.asyncio
async def test_collect_training_results_records_failures() -> None:
    node = AgentensorNode(name="trainer")
    node.prompts = {}
    node.evaluators = [eval_definition]
    node.progress_callback = AsyncMock()

    case = SimpleNamespace(
        assertions={eval_definition.id: SimpleNamespace(value=False, reason="nok")},
        output=TextTensor(
            text="tensor",
            requires_grad=False,
            model=SimpleNamespace(),
            metadata={"payload": {"echo": "no"}},
        ),
        task_duration=0.2,
        inputs={"value": 2},
        metadata={},
    )
    report = SimpleNamespace(
        cases=[case],
        failures=[SimpleNamespace(error_message="train-fail")],
    )

    aggregated, results = await node._collect_training_results(report, epoch=2)

    assert aggregated[eval_definition.id] == [0.0, 0.0]
    assert results[0]["epoch"] == 2
    assert results[-1]["error"] == "train-fail"


@pytest.mark.asyncio
async def test_evaluate_case_aggregates_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    node = AgentensorNode(name="evaluate-case")
    context = EvaluationContext(
        inputs={"prompt": "ping"},
        output={},
        expected_output=None,
        metadata={},
        duration_ms=0.0,
    )

    async def fake_run(
        definition: EvaluatorDefinition, evaluator: Any, ctx: EvaluationContext
    ) -> dict[str, Any]:
        return {"score": 0.4, "passed": True, "reason": "ok"}

    monkeypatch.setattr(
        AgentensorNode,
        "_run_evaluator",
        AsyncMock(side_effect=fake_run),
    )

    aggregated: dict[str, list[float]] = {}
    result = await node._evaluate_case(
        [(eval_definition, object())], context, aggregated=aggregated
    )

    assert aggregated[eval_definition.id] == [0.4]
    assert result[eval_definition.id]["reason"] == "ok"


@pytest.mark.asyncio
async def test_run_evaluator_awaits_coroutine_result() -> None:
    async def async_eval(_: EvaluationContext) -> dict[str, Any]:
        return {"value": 0.45, "reason": "awaited"}

    node = AgentensorNode(name="async-evaluator")
    context = EvaluationContext(
        inputs={},
        output={},
        expected_output=None,
        metadata={},
        duration_ms=0.0,
    )

    result = await node._run_evaluator(eval_definition, async_eval, context)

    assert result["score"] == 0.45


def test_checkpoint_config_serializes_dynamic_text_tensor() -> None:
    node = AgentensorNode(name="checkpoint-node")
    tensor = TextTensor(
        text="dynamic",
        requires_grad=True,
        model=SimpleNamespace(),
        metadata={"note": "fresh"},
    )

    payload = node._checkpoint_config({}, prompts={"dynamic": tensor})

    assert payload["prompts"]["dynamic"]["type"] == "TextTensor"
    assert payload["prompts"]["dynamic"]["metadata"] == {"note": "fresh"}
