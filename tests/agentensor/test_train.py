"""Test module for the Trainer class."""

from __future__ import annotations
import json
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic_evals import Dataset
from pydantic_graph import Graph
from agentensor.module import AgentModule
from agentensor.optim import Optimizer
from agentensor.tensor import TextTensor
from agentensor.train import GraphTrainer, Trainer


@pytest.fixture
def mock_graph():
    """Create a mock graph for testing."""
    mock_graph = MagicMock(spec=Graph)
    return mock_graph


@pytest.fixture
def mock_dataset():
    """Create a mock dataset for testing."""
    mock_dataset = MagicMock(spec=Dataset)
    return mock_dataset


@pytest.fixture
def mock_optimizer():
    """Create a mock optimizer for testing."""
    mock_optimizer = MagicMock(spec=Optimizer)
    mock_optimizer.params = []  # Add params attribute
    return mock_optimizer


@pytest.fixture
@patch("agentensor.module.init_chat_model")
@patch("agentensor.tensor.init_chat_model")
def mock_module_class(mock_tensor_init, mock_module_init):
    """Create a mock module class for testing."""
    # Mock the model initialization
    mock_tensor_init.return_value = MagicMock()
    mock_module_init.return_value = MagicMock()

    class MockModule(AgentModule):
        system_prompt: TextTensor = TextTensor("test", requires_grad=True)

        async def run(self, state: dict) -> dict:
            return state

        def get_agent(self):
            """Dummy get_agent method for testing."""
            pass

    return MockModule


def _build_graph_trainer(
    *,
    base_state: dict[str, object] | None = None,
    script_format: bool = False,
    runtime_prompts: dict[str, TextTensor] | None = None,
) -> GraphTrainer:
    """Construct a lightweight GraphTrainer for helper assertions."""
    dataset = MagicMock(spec=Dataset)
    dataset.cases = []
    dataset.evaluators = []
    optimizer = MagicMock(spec=Optimizer)
    optimizer.params = []
    return GraphTrainer(
        graph=MagicMock(),
        dataset=dataset,
        optimizer=optimizer,
        epochs=1,
        runtime_prompts=runtime_prompts
        or {"agent": TextTensor("prompt", requires_grad=True)},
        base_state=base_state or {},
        script_format=script_format,
    )


@pytest.mark.asyncio
async def test_trainer_initialization(
    mock_graph, mock_dataset, mock_optimizer, mock_module_class
):
    """Test Trainer initialization."""
    trainer = Trainer(
        graph=mock_graph,
        train_dataset=mock_dataset,
        optimizer=mock_optimizer,
        epochs=10,
        stop_threshold=0.95,
    )

    assert trainer.graph == mock_graph
    assert trainer.train_dataset == mock_dataset
    assert trainer.optimizer == mock_optimizer
    assert trainer.epochs == 10
    assert trainer.stop_threshold == 0.95


@pytest.mark.asyncio
async def test_trainer_step(
    mock_graph, mock_dataset, mock_optimizer, mock_module_class
):
    """Test the step method of Trainer."""
    # Setup
    trainer = Trainer(
        graph=mock_graph,
        train_dataset=mock_dataset,
        optimizer=mock_optimizer,
        epochs=10,
    )

    # Mock the graph's run method
    mock_graph.ainvoke = AsyncMock()

    # Create a proper mock TextTensor for the return value
    with patch("agentensor.tensor.init_chat_model") as mock_tensor_init:
        mock_tensor_init.return_value = MagicMock()
        expected_output = TextTensor("test output")
        mock_graph.ainvoke.return_value = {"output": expected_output}

        # Test step
        input_tensor = TextTensor("test input")
        result = await trainer.forward(input_tensor)

        # Verify
        assert isinstance(result, TextTensor)
        assert result.text == "test output"
        mock_graph.ainvoke.assert_called_once()


def test_trainer_train(mock_graph, mock_dataset, mock_optimizer, mock_module_class):
    """Test the train method of Trainer."""
    # Setup
    trainer = Trainer(
        graph=mock_graph,
        train_dataset=mock_dataset,
        optimizer=mock_optimizer,
        epochs=2,
    )

    # Mock dataset evaluation
    mock_report = MagicMock()
    mock_report.cases = []
    mock_report.averages.return_value.assertions = 0.96  # Above stop threshold
    mock_dataset.evaluate_sync.return_value = mock_report

    # Run training
    trainer.train()

    # Verify
    assert mock_dataset.evaluate_sync.call_count == 1
    assert mock_optimizer.step.call_count == 1
    assert mock_optimizer.zero_grad.call_count == 1


def test_trainer_train_requires_dataset() -> None:
    trainer = Trainer(graph=MagicMock(), optimizer=MagicMock(), epochs=1)

    with pytest.raises(ValueError, match="train dataset is required"):
        trainer.train()


def test_trainer_train_requires_optimizer() -> None:
    trainer = Trainer(graph=MagicMock(), train_dataset=MagicMock(), epochs=1)

    with pytest.raises(ValueError, match="Optimizer is required"):
        trainer.train()


def test_trainer_evaluate_requires_dataset() -> None:
    trainer = Trainer(graph=MagicMock(), train_dataset=MagicMock(), epochs=1)

    with pytest.raises(ValueError, match="eval dataset is required"):
        trainer.evaluate()


@patch("agentensor.tensor.init_chat_model")
def test_trainer_train_with_failed_cases(
    mock_tensor_init, mock_graph, mock_dataset, mock_optimizer, mock_module_class
):
    """Test the train method with failed cases that need backward pass."""
    # Mock the model initialization
    mock_tensor_init.return_value = MagicMock()

    # Setup
    trainer = Trainer(
        graph=mock_graph,
        train_dataset=mock_dataset,
        optimizer=mock_optimizer,
        epochs=2,
    )

    # Create a mock case with failed assertions
    mock_case = MagicMock()
    mock_case.output = TextTensor("test output", requires_grad=True)
    mock_case.assertions = {
        "test1": MagicMock(value=False, reason="error1"),
        "test2": MagicMock(value=True, reason=None),
    }

    # Mock dataset evaluation
    mock_report = MagicMock()
    mock_report.cases = [mock_case]
    mock_report.averages.return_value.assertions = 0.5  # Below stop threshold
    mock_dataset.evaluate_sync.return_value = mock_report

    # Run training
    trainer.train()

    # Verify
    assert mock_dataset.evaluate_sync.call_count == 2  # Called for each epoch
    assert mock_optimizer.step.call_count == 2
    assert mock_optimizer.zero_grad.call_count == 2


def test_trainer_train_default_failure_reason() -> None:
    trainer = Trainer(
        graph=MagicMock(),
        train_dataset=MagicMock(),
        optimizer=MagicMock(),
        epochs=1,
    )
    mock_case = MagicMock()
    mock_case.output = MagicMock()
    mock_case.assertions = {"test": MagicMock(value=False, reason=None)}
    mock_report = MagicMock()
    mock_report.cases = [mock_case]
    mock_report.averages.return_value.assertions = 0.0
    trainer.train_dataset.evaluate_sync.return_value = mock_report

    trainer.train()

    mock_case.output.backward.assert_called_once_with(
        "Evaluation failed without a reason."
    )


def test_trainer_early_stopping(
    mock_graph, mock_dataset, mock_optimizer, mock_module_class
):
    """Test early stopping when performance threshold is reached."""
    # Setup
    trainer = Trainer(
        graph=mock_graph,
        train_dataset=mock_dataset,
        optimizer=mock_optimizer,
        epochs=10,
        stop_threshold=0.95,
    )

    # Mock dataset evaluation with high performance
    mock_report = MagicMock()
    mock_report.cases = []
    mock_report.averages.return_value.assertions = 0.96  # Above stop threshold
    mock_dataset.evaluate_sync.return_value = mock_report

    # Run training
    trainer.train()

    # Verify early stopping
    assert mock_dataset.evaluate_sync.call_count == 1  # Only one epoch before stopping
    assert mock_optimizer.step.call_count == 1
    assert mock_optimizer.zero_grad.call_count == 1


@patch("agentensor.tensor.init_chat_model")
def test_trainer_train_with_no_losses(
    mock_tensor_init, mock_graph, mock_dataset, mock_optimizer, mock_module_class
):
    """Test the train method when all assertions pass and there are no losses."""
    # Mock the model initialization
    mock_tensor_init.return_value = MagicMock()

    # Setup
    trainer = Trainer(
        graph=mock_graph,
        train_dataset=mock_dataset,
        optimizer=mock_optimizer,
        epochs=2,
    )

    # Create a mock case with all passing assertions
    mock_case = MagicMock()
    mock_case.output = TextTensor("test output", requires_grad=True)
    mock_case.assertions = {
        "test1": MagicMock(value=True, reason=None),
        "test2": MagicMock(value=True, reason=None),
    }

    # Mock dataset evaluation
    mock_report = MagicMock()
    mock_report.cases = [mock_case]
    mock_report.averages.return_value.assertions = (
        0.5  # Below stop threshold to continue training
    )
    mock_dataset.evaluate_sync.return_value = mock_report

    # Run training
    trainer.train()

    # Verify
    assert mock_dataset.evaluate_sync.call_count == 2  # Called for each epoch
    assert mock_optimizer.step.call_count == 2
    assert mock_optimizer.zero_grad.call_count == 2


def test_graph_trainer_extracts_ai_message() -> None:
    output_state = {
        "node_results": {},
        "messages": [HumanMessage(content="hello"), AIMessage(content="done")],
    }

    assert GraphTrainer._extract_output(output_state) == "done"


def test_graph_trainer_falls_back_to_last_message() -> None:
    output_state = {
        "node_results": {},
        "messages": [{"role": "user", "content": "hello"}],
    }

    assert GraphTrainer._extract_output(output_state) == "hello"


def test_graph_trainer_merge_inputs_includes_base_state() -> None:
    trainer = _build_graph_trainer(base_state={"inputs": {"alpha": 1}})

    merged = trainer._merge_inputs({"beta": 2})

    assert merged == {"alpha": 1, "beta": 2}


def test_graph_trainer_build_case_state_script_format() -> None:
    prompts = {"agent": TextTensor("script", requires_grad=True)}
    trainer = _build_graph_trainer(
        base_state={"config": {"mode": "test"}},
        script_format=True,
        runtime_prompts=prompts,
    )

    state = trainer._build_case_state({"value": 1})

    assert state == {
        "value": 1,
        "config": {"mode": "test", "prompts": prompts},
    }


def test_graph_trainer_build_case_state_default_format() -> None:
    prompts = {"agent": TextTensor("default", requires_grad=True)}
    trainer = _build_graph_trainer(runtime_prompts=prompts)

    state = trainer._build_case_state({"value": 2})

    assert state["messages"] == []
    assert state["node_results"] == {}
    assert state["inputs"] == {"value": 2}
    assert state["config"]["prompts"] is prompts
    assert state["structured_response"] is None


def test_graph_trainer_merge_inputs_ignores_non_mapping_base_state() -> None:
    trainer = _build_graph_trainer(base_state="scalar")

    merged = trainer._merge_inputs({"beta": 4})

    assert merged == {"beta": 4}


def test_graph_trainer_stringify_output_handles_types() -> None:
    assert GraphTrainer._stringify_output("hello") == "hello"
    assert GraphTrainer._stringify_output({"key": "value"}) == json.dumps(
        {"key": "value"}
    )
    assert GraphTrainer._stringify_output({1}) == str({1})


def test_graph_trainer_extract_output_prefers_node_results_and_output() -> None:
    results_state = {"node_results": {"score": 0.99}, "messages": []}
    assert GraphTrainer._extract_output(results_state) == {"score": 0.99}

    output_state = {"output": "payload", "messages": []}
    assert GraphTrainer._extract_output(output_state) == "payload"


def test_graph_trainer_extract_output_returns_original_when_no_payload() -> None:
    original = {"messages": None}
    assert GraphTrainer._extract_output(original) == original


def test_graph_trainer_extract_message_output_handles_non_list_messages() -> None:
    assert GraphTrainer._extract_message_output(None) is None


def test_graph_trainer_extract_message_output_from_object_messages() -> None:
    class FakeMessage:
        def __init__(self, content: str, role: str) -> None:
            self.content = content
            self.role = role

    message = FakeMessage("hello", "assistant")
    assert GraphTrainer._extract_message_output([message]) == "hello"


def test_graph_trainer_extract_message_output_ignores_blank_messages() -> None:
    assert (
        GraphTrainer._extract_message_output([{"role": "user", "content": "   "}])
        is None
    )


def test_graph_trainer_after_epoch_records_prompts() -> None:
    prompts = {"agent": TextTensor("prompt", requires_grad=True)}
    trainer = _build_graph_trainer(runtime_prompts=prompts)

    trainer.after_epoch(0, MagicMock())

    assert trainer.prompt_history[-1] == {"agent": "prompt"}


def test_trainer_test(mock_graph, mock_dataset, mock_optimizer, mock_module_class):
    """Test the test method of Trainer."""
    # Create test dataset
    test_dataset = MagicMock(spec=Dataset)
    mock_report = MagicMock()
    test_dataset.evaluate_sync.return_value = mock_report

    # Setup trainer with test_dataset
    trainer = Trainer(
        graph=mock_graph,
        train_dataset=mock_dataset,
        test_dataset=test_dataset,
        optimizer=mock_optimizer,
        epochs=2,
    )

    # Run test (should not return anything)
    result = trainer.test()

    # Verify
    assert result is None  # test method doesn't return anything
    test_dataset.evaluate_sync.assert_called_once_with(
        trainer.forward, max_concurrency=None, progress=True
    )
    mock_report.print.assert_called_once_with(
        include_input=True, include_output=True, include_durations=True
    )


def test_trainer_evaluate_limits_cases() -> None:
    report = MagicMock()
    report.cases = []

    class FakeDataset:
        instances: list[FakeDataset] = []

        def __init__(self, cases: list[object], evaluators: list[object]) -> None:
            self.cases = list(cases)
            self.evaluators = evaluators
            self.__class__.instances.append(self)

        def evaluate_sync(
            self,
            forward: Callable[..., Any],
            max_concurrency: int | None,
            progress: bool,
        ) -> MagicMock:
            self.last_args = (forward, max_concurrency, progress)
            return report

    FakeDataset.instances.clear()
    dataset = FakeDataset(cases=["a", "b", "c"], evaluators=[])
    trainer = Trainer(
        graph=MagicMock(),
        train_dataset=MagicMock(),
        eval_dataset=dataset,
        optimizer=MagicMock(),
        epochs=1,
    )

    with patch("agentensor.train.Dataset", FakeDataset):
        result = trainer.evaluate(limit_cases=2, progress=False)

    assert result is report
    assert FakeDataset.instances[-1].cases == ["a", "b"]
