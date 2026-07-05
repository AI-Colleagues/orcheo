"""Conversational batch evaluation node."""

from __future__ import annotations
import asyncio
import logging
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.graph import StateGraph
from pydantic import ConfigDict, Field, field_validator
from pydantic.json_schema import SkipJsonSchema
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


logger = logging.getLogger(__name__)


@registry.register(
    NodeMetadata(
        name="ConversationalBatchEvalNode",
        description=(
            "Iterate conversations and turns through a pipeline, "
            "collecting predictions paired with gold labels"
        ),
        category="evaluation",
    )
)
class ConversationalBatchEvalNode(TaskNode):
    """Iterate conversations and turns, collecting predictions and references.

    When ``pipeline`` is provided, each turn is fed through the pipeline
    sub-graph (e.g. QueryRewriteNode, or a full retrieval-generation chain)
    and the prediction is extracted from the last node's output.  When
    ``pipeline`` is empty, the node operates in passthrough mode and
    extracts the prediction directly from the turn data.
    """

    conversations_key: str = Field(default="conversations")
    prediction_field: str = Field(
        default="query",
        description="Field name in pipeline output containing the prediction",
    )
    gold_field: str = Field(
        default="gold_rewrite",
        description="Field name in turn data containing the gold label",
    )
    max_conversations: int | str | None = Field(
        default=None,
        description="Limit number of conversations to evaluate",
    )
    max_concurrency: int | str | None = Field(
        default=1,
        description="Maximum number of conversations to evaluate concurrently.",
    )
    history_window_size: int | str | None = Field(
        default=None,
        description=(
            "Optional maximum number of prior user utterances to retain per "
            "conversation turn while building pipeline history."
        ),
    )
    include_per_conversation_details: bool = Field(
        default=True,
        description=(
            "When false, per_conversation only tracks turn counts, reducing "
            "memory usage for large runs."
        ),
    )
    pipeline: SkipJsonSchema[StateGraph | None] = Field(
        default=None,
        description=(
            "Optional pipeline sub-graph executed once per turn. "
            "When omitted, the node runs in passthrough mode."
        ),
    )
    _compiled_pipeline: SkipJsonSchema[Runnable | None] = None

    def get_compiled_pipeline(self) -> Runnable | None:
        """Return a cached compiled pipeline graph when configured."""
        if self.pipeline is None:
            return None
        if self._compiled_pipeline is None:
            self._compiled_pipeline = self.pipeline.compile()
        return self._compiled_pipeline

    @field_validator("max_conversations", mode="before")
    @classmethod
    def _validate_max_conversations(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            if cls.contains_template(value):
                return value
            try:
                value = int(value)
            except ValueError as exc:
                msg = "max_conversations must be an integer"
                raise ValueError(msg) from exc
        return value

    @field_validator("max_concurrency", mode="before")
    @classmethod
    def _validate_max_concurrency(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            if cls.contains_template(value):
                return value
            try:
                value = int(value)
            except ValueError as exc:
                msg = "max_concurrency must be an integer"
                raise ValueError(msg) from exc
        return value

    @field_validator("history_window_size", mode="before")
    @classmethod
    def _validate_history_window_size(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            if cls.contains_template(value):
                return value
            try:
                value = int(value)
            except ValueError as exc:
                msg = "history_window_size must be an integer"
                raise ValueError(msg) from exc
        return value

    def _resolve_max_conversations(self) -> int | None:
        value = self.max_conversations
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError as exc:
                msg = "max_conversations must resolve to an integer"
                raise ValueError(msg) from exc
        if value < 1:
            msg = "max_conversations must be >= 1"
            raise ValueError(msg)
        return value

    def _resolve_max_concurrency(self) -> int:
        value = self.max_concurrency
        if value is None:
            return 1
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError as exc:
                msg = "max_concurrency must resolve to an integer"
                raise ValueError(msg) from exc
        if value < 1:
            msg = "max_concurrency must be >= 1"
            raise ValueError(msg)
        return value

    def _resolve_history_window_size(self) -> int | None:
        value = self.history_window_size
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError as exc:
                msg = "history_window_size must resolve to an integer"
                raise ValueError(msg) from exc
        if value < 1:
            msg = "history_window_size must be >= 1"
            raise ValueError(msg)
        return value

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Iterate conversations, collect predictions and gold references."""
        inputs = state.get("inputs", {})
        conversations = self._resolve_conversations(state, inputs)
        if not isinstance(conversations, list):
            msg = "ConversationalBatchEvalNode expects conversations list"
            raise ValueError(msg)

        max_conversations = self._resolve_max_conversations()
        if max_conversations is not None:
            conversations = conversations[:max_conversations]

        history_window_size = self._resolve_history_window_size()
        max_concurrency = self._resolve_max_concurrency()
        conversation_results: list[dict[str, Any]]
        if max_concurrency == 1:
            conversation_results = []
            for conv in conversations:
                conversation_results.append(
                    await self._process_conversation(
                        conv,
                        history_window_size,
                        state,
                        config,
                    )
                )
        else:
            semaphore = asyncio.Semaphore(max_concurrency)

            async def _process_with_limit(conv: dict[str, Any]) -> dict[str, Any]:
                async with semaphore:
                    return await self._process_conversation(
                        conv,
                        history_window_size,
                        state,
                        config,
                    )

            tasks = [
                asyncio.create_task(_process_with_limit(conv)) for conv in conversations
            ]
            conversation_results = await asyncio.gather(*tasks)

        predictions: list[str] = []
        references: list[str] = []
        per_conversation: dict[str, dict[str, Any]] = {}

        for conv_result in conversation_results:
            conv_id = conv_result["conversation_id"]
            conv_predictions = conv_result["predictions"]
            conv_references = conv_result["references"]
            predictions.extend(conv_predictions)
            references.extend(conv_references)

            conv_summary: dict[str, Any] = {"num_turns": conv_result["num_turns"]}
            if self.include_per_conversation_details:
                conv_summary["predictions"] = conv_predictions
                conv_summary["references"] = conv_references
            per_conversation[conv_id] = conv_summary

        # Write to state["inputs"] so downstream metric nodes can read them.
        inputs["predictions"] = predictions
        inputs["references"] = references

        return {
            "predictions": predictions,
            "references": references,
            "per_conversation": per_conversation,
            "total_turns": len(predictions),
            "total_conversations": len(conversations),
        }

    async def _process_conversation(
        self,
        conversation: dict[str, Any],
        history_window_size: int | None,
        parent_state: State,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        conv_id = str(conversation.get("conversation_id", "unknown"))
        turns_raw = conversation.get("turns", [])
        turns = turns_raw if isinstance(turns_raw, list) else []

        conv_predictions: list[str] = []
        conv_references: list[str] = []

        history: deque[str] | list[str]
        if history_window_size is None:
            history = []
        else:
            history = deque(maxlen=history_window_size)

        for turn in turns:
            if not isinstance(turn, dict):
                continue
            gold = str(turn.get(self.gold_field, ""))
            prediction = await self._process_turn(turn, history, parent_state, config)

            conv_predictions.append(prediction)
            conv_references.append(gold)

            user_utterance = turn.get("raw_question", turn.get("user_utterance", ""))
            history.append(str(user_utterance))

        return {
            "conversation_id": conv_id,
            "predictions": conv_predictions,
            "references": conv_references,
            "num_turns": len(conv_predictions),
        }

    def _resolve_conversations(
        self,
        state: State,
        inputs: Mapping[str, Any],
    ) -> Any:
        conversations = inputs.get(self.conversations_key)
        if isinstance(conversations, list):
            return conversations

        results = state.get("node_results")
        if not isinstance(results, Mapping):
            return conversations

        direct = results.get(self.conversations_key)
        if isinstance(direct, list):
            return direct

        for node_result in results.values():
            if isinstance(node_result, Mapping):  # pragma: no branch
                nested = node_result.get(self.conversations_key)
                if isinstance(nested, list):
                    return nested

        return conversations

    async def _process_turn(
        self,
        turn: dict[str, Any],
        history: Sequence[str],
        parent_state: State,
        config: RunnableConfig,
    ) -> str:
        """Run a single turn through the pipeline or passthrough.

        In pipeline mode, builds a per-turn state and invokes each pipeline
        node sequentially, threading results through.  In passthrough mode,
        returns the raw field value from the turn data.
        """
        compiled_pipeline = self.get_compiled_pipeline()
        if compiled_pipeline is None:
            return self._passthrough(turn)

        user_query = turn.get("raw_question", turn.get("user_utterance", ""))

        turn_state = State(
            messages=[],
            inputs={
                "message": str(user_query),
                "query": str(user_query),
                "history": list(history),
            },
            node_results={},
            structured_response=None,
            config=parent_state.get("config"),
        )

        result_state = await compiled_pipeline.ainvoke(turn_state, config=config)
        return self._extract_prediction(result_state, str(user_query))

    def _extract_prediction(self, result_state: Any, fallback: str) -> str:
        """Extract the prediction field from compiled graph output."""
        if not isinstance(result_state, Mapping):
            return fallback

        prediction = self._extract_from_mapping(result_state)
        if prediction is not None:
            return prediction

        results = result_state.get("node_results")
        if isinstance(results, Mapping):  # pragma: no branch
            for value in reversed(list(results.values())):
                if isinstance(value, Mapping):  # pragma: no branch
                    prediction = self._extract_from_mapping(value)
                    if prediction is not None:
                        return prediction

        inputs = result_state.get("inputs")
        if isinstance(inputs, Mapping):  # pragma: no branch
            prediction = self._extract_from_mapping(inputs)
            if prediction is not None:
                return prediction

        return fallback

    def _extract_from_mapping(self, payload: Mapping[str, Any]) -> str | None:
        value = payload.get(self.prediction_field)
        if value is None:
            return None
        return str(value)

    def _passthrough(self, turn: dict[str, Any]) -> str:
        """Extract prediction directly from turn data (no pipeline)."""
        prediction = turn.get(self.prediction_field)
        if prediction is not None:
            return str(prediction)
        return str(turn.get("raw_question", turn.get("user_utterance", "")))

    model_config = ConfigDict(arbitrary_types_allowed=True)
