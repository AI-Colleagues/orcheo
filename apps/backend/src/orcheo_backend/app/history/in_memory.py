"""Async-safe in-memory history store implementation."""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from orcheo_backend.app.history.models import (
    RunHistoryError,
    RunHistoryNotFoundError,
    RunHistoryRecord,
    RunHistoryStep,
)
from orcheo_backend.app.history.serialization import (
    normalize_json_mapping,
    normalize_json_value,
)


class InMemoryRunHistoryStore:
    """Async-safe in-memory store for execution histories."""

    def __init__(self) -> None:
        """Initialize the in-memory store."""
        self._lock = asyncio.Lock()
        self._histories: dict[str, RunHistoryRecord] = {}

    async def start_run(
        self,
        *,
        workflow_id: str,
        execution_id: str,
        inputs: Mapping[str, Any] | None = None,
        runnable_config: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        callbacks: list[Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        run_name: str | None = None,
        trace_id: str | None = None,
        trace_started_at: datetime | None = None,
        workspace_id: str | None = None,
    ) -> RunHistoryRecord:
        """Initialise a history record for the provided execution."""
        async with self._lock:
            if execution_id in self._histories:
                msg = f"History already exists for execution_id={execution_id}"
                raise RunHistoryError(msg)

            effective_trace_started = trace_started_at
            config_payload = runnable_config
            if runnable_config and hasattr(runnable_config, "model_dump"):
                config_payload = runnable_config.model_dump(mode="json")  # type: ignore[arg-type]
            config_mapping = normalize_json_mapping(
                config_payload if isinstance(config_payload, Mapping) else None
            )
            tag_values = (
                [str(tag) for tag in (tags or config_mapping.get("tags", []))]
                if isinstance(config_mapping, Mapping)
                else [str(tag) for tag in (tags or [])]
            )
            callback_values = (
                normalize_json_value(
                    list(callbacks or config_mapping.get("callbacks", []))
                )
                if isinstance(config_mapping, Mapping)
                else normalize_json_value(list(callbacks or []))
            )
            if not isinstance(callback_values, list):
                callback_values = [callback_values]
            metadata_values = (
                normalize_json_mapping(
                    metadata
                    if metadata is not None
                    else config_mapping.get("metadata", {})
                    if isinstance(config_mapping.get("metadata", {}), Mapping)
                    else None
                )
                if isinstance(config_mapping, Mapping)
                else normalize_json_mapping(metadata)
            )
            run_identifier = run_name or (
                config_mapping.get("run_name")
                if isinstance(config_mapping, Mapping)
                else None
            )
            if run_identifier is not None:
                run_identifier = str(run_identifier)
            record = RunHistoryRecord(
                workflow_id=workflow_id,
                execution_id=execution_id,
                workspace_id=workspace_id,
                inputs=normalize_json_mapping(inputs),
                runnable_config=config_mapping,
                tags=tag_values,
                callbacks=callback_values,
                metadata=metadata_values,
                run_name=run_identifier,
                trace_id=trace_id,
                trace_started_at=effective_trace_started,
                trace_last_span_at=effective_trace_started,
            )
            if record.trace_started_at is None:
                record.trace_started_at = record.started_at
            if record.trace_last_span_at is None:
                record.trace_last_span_at = record.trace_started_at
            self._histories[execution_id] = record
            return record.model_copy(deep=True)

    async def append_step(
        self,
        execution_id: str,
        payload: Mapping[str, Any],
    ) -> RunHistoryStep:
        """Append a step for the execution."""
        async with self._lock:
            record = self._require_record(execution_id)
            return record.append_step(normalize_json_mapping(payload))

    async def mark_completed(self, execution_id: str) -> RunHistoryRecord:
        """Mark the execution as completed."""
        async with self._lock:
            record = self._require_record(execution_id)
            record.mark_completed()
            return record.model_copy(deep=True)

    async def mark_failed(self, execution_id: str, error: str) -> RunHistoryRecord:
        """Mark the execution as failed with the specified error message."""
        async with self._lock:
            record = self._require_record(execution_id)
            record.mark_failed(error)
            return record.model_copy(deep=True)

    async def mark_cancelled(
        self,
        execution_id: str,
        *,
        reason: str | None = None,
    ) -> RunHistoryRecord:
        """Mark the execution as cancelled."""
        async with self._lock:
            record = self._require_record(execution_id)
            record.mark_cancelled(reason=reason)
            return record.model_copy(deep=True)

    async def get_history(self, execution_id: str) -> RunHistoryRecord:
        """Return a deep copy of the execution history."""
        async with self._lock:
            record = self._require_record(execution_id)
            return record.model_copy(deep=True)

    async def clear(self) -> None:
        """Clear all stored histories. Intended for testing only."""
        async with self._lock:
            self._histories.clear()

    async def list_histories(
        self,
        workflow_id: str,
        *,
        limit: int | None = None,
        workspace_id: str | None = None,
    ) -> list[RunHistoryRecord]:
        """Return histories associated with the provided workflow."""
        async with self._lock:
            records = [
                record.model_copy(deep=True)
                for record in self._histories.values()
                if record.workflow_id == workflow_id
                and (
                    workspace_id is None
                    or record.workspace_id is None
                    or record.workspace_id == workspace_id
                )
            ]

        records.sort(key=lambda record: record.started_at, reverse=True)
        if limit is not None:
            return records[:limit]
        return records

    def _require_record(self, execution_id: str) -> RunHistoryRecord:
        """Return the record or raise an error if missing."""
        record = self._histories.get(execution_id)
        if record is None:
            msg = f"History not found for execution_id={execution_id}"
            raise RunHistoryNotFoundError(msg)
        return record


__all__ = ["InMemoryRunHistoryStore"]
