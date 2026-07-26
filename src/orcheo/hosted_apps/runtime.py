"""App-scoped workflow invocation acceptance and visitor-safe results."""

from __future__ import annotations
import hashlib
import json
import secrets
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from typing import Any
from uuid import UUID, uuid4
from orcheo.hosted_apps.models import AppBinding, AppRuntimeRun
from orcheo.models.base import _utcnow


__all__ = [
    "AppRuntimeConflictError",
    "AppRuntimeError",
    "AppRuntimeLimitError",
    "AppRuntimeResult",
    "AppRuntimeService",
    "validate_input_schema",
]


class AppRuntimeError(PermissionError):
    """Fail-closed visitor-safe runtime error."""


class AppRuntimeConflictError(AppRuntimeError):
    """Raised when an idempotency key is reused for a different request."""


class AppRuntimeLimitError(AppRuntimeError):
    """Raised when a declared invocation limit has been exhausted."""


@dataclass(frozen=True, slots=True)
class AppRuntimeResult:
    """Visitor-safe status with no internal workflow identifiers."""

    handle: str
    status: str
    output: Any | None = None
    error: str | None = None
    newly_accepted: bool = False


@dataclass(slots=True)
class _RunState:
    mapping: AppRuntimeRun
    status: str = "accepted"
    output: Any | None = None
    error: str | None = None
    runtime_generation: int = 0
    can_read_output: bool = False
    can_read_error: bool = False
    output_projection: dict[str, Any] | None = None
    max_output_bytes: int = 0
    timeout_at: datetime | None = None


class AppRuntimeService:
    """Thread-safe reference for durable runtime authorization semantics.

    Production persistence uses the same scope keys in the runtime-run,
    idempotency, lease, and dispatch-outbox tables. The service deliberately never
    returns the internal workflow run id to app visitors.
    """

    def __init__(
        self,
        *,
        handle_ttl_seconds: int = 3600,
        max_input_bytes: int = 256 * 1024,
        max_output_bytes: int = 256 * 1024,
    ) -> None:
        """Initialize bounded request, response, and handle lifetimes."""
        self._handle_ttl = handle_ttl_seconds
        self._max_input_bytes = max_input_bytes
        self._max_output_bytes = max_output_bytes
        self._runs: dict[str, _RunState] = {}
        self._idempotency: dict[str, tuple[str, str, datetime]] = {}
        self._invocation_windows: dict[str, deque[datetime]] = {}
        self._lock = RLock()

    def accept(
        self,
        binding: AppBinding,
        *,
        workspace_id: UUID,
        app_id: UUID,
        release_id: UUID,
        deployment_id: UUID,
        binding_snapshot_sha256: str,
        payload: Any,
        idempotency_key: str,
        runtime_generation: int,
        visitor_user_id: str | None,
        session_id: UUID | None,
        anonymous_visitor_id: str | None,
        workflow_run_id: UUID | None = None,
        client_ip: str | None = None,
    ) -> AppRuntimeResult:
        """Authorize and idempotently accept one immutable binding invocation."""
        self._ensure_binding_scope(binding, workspace_id, app_id)
        if binding.access_mode == "authenticated" and (
            visitor_user_id is None or session_id is None
        ):
            raise AppRuntimeError("This workflow binding requires authentication.")
        if not idempotency_key or len(idempotency_key.encode()) > 256:
            raise AppRuntimeError("A bounded Idempotency-Key is required.")
        encoded = _canonical_json(payload)
        input_limit = min(
            self._max_input_bytes,
            binding.limits.get("input_max_bytes", self._max_input_bytes),
        )
        if len(encoded) > input_limit:
            raise AppRuntimeError("Workflow input exceeds the configured byte limit.")
        _validate_schema(payload, binding.input_schema)
        if session_id is not None:
            owner = str(session_id)
        elif anonymous_visitor_id:
            owner = f"anonymous:{anonymous_visitor_id}"
        else:
            raise AppRuntimeError("Anonymous visitor identity is required.")
        scope_hash = _hash(
            "|".join(
                [
                    str(app_id),
                    str(release_id),
                    str(binding.id),
                    owner,
                    idempotency_key,
                ]
            )
        )
        request_hash = _hash_bytes(encoded)
        now = _utcnow()
        with self._lock:
            self._prune(now)
            existing = self._idempotency.get(scope_hash)
            if existing is not None:
                prior_hash, handle, _expires_at = existing
                if prior_hash != request_hash:
                    raise AppRuntimeConflictError(
                        "Idempotency-Key was already used for a different request."
                    )
                return self._visitor_result(self._runs[handle])
            self._enforce_invocation_limits(
                binding,
                app_id=app_id,
                session_id=session_id,
                client_ip=client_ip,
                now=now,
            )
            handle = secrets.token_urlsafe(32)
            expires_at = now + timedelta(seconds=self._handle_ttl)
            mapping = AppRuntimeRun(
                public_handle=handle,
                workspace_id=workspace_id,
                app_id=app_id,
                release_id=release_id,
                deployment_id=deployment_id,
                binding_id=binding.id,
                binding_snapshot_sha256=binding_snapshot_sha256,
                workflow_run_id=workflow_run_id or uuid4(),
                idempotency_key_hash=_hash(idempotency_key),
                expires_at=expires_at,
                visitor_user_id=visitor_user_id,
                originating_session_id=session_id,
            )
            self._runs[handle] = _RunState(
                mapping=mapping,
                runtime_generation=runtime_generation,
                can_read_output=binding.visitor_can_read_output,
                can_read_error=binding.visitor_can_read_sanitized_errors,
                output_projection=binding.output_projection,
                max_output_bytes=min(
                    self._max_output_bytes,
                    binding.limits.get("output_max_bytes", self._max_output_bytes),
                ),
                timeout_at=now
                + timedelta(
                    seconds=binding.limits.get("timeout_seconds", self._handle_ttl)
                ),
            )
            self._idempotency[scope_hash] = (request_hash, handle, expires_at)
            return AppRuntimeResult(
                handle=handle,
                status="accepted",
                newly_accepted=True,
            )

    def status(
        self,
        handle: str,
        *,
        workspace_id: UUID,
        app_id: UUID,
        runtime_generation: int,
        visitor_user_id: str | None,
        session_id: UUID | None,
    ) -> AppRuntimeResult:
        """Return a result only to its resolved app and originating visitor."""
        with self._lock:
            state = self._runs.get(handle)
            now = _utcnow()
            if (
                state is None
                or state.mapping.expires_at <= now
                or state.mapping.workspace_id != workspace_id
                or state.mapping.app_id != app_id
                or state.runtime_generation != runtime_generation
            ):
                raise AppRuntimeError("Workflow run is unavailable.")
            self._apply_timeout(state, now)
            if state.mapping.originating_session_id is not None:
                if (
                    session_id != state.mapping.originating_session_id
                    or visitor_user_id != state.mapping.visitor_user_id
                ):
                    raise AppRuntimeError("Workflow run is unavailable.")
            return self._visitor_result(state)

    def complete(
        self, handle: str, *, output: Any | None = None, error: str | None = None
    ) -> None:
        """Settle an internally claimed run while bounding visitor-readable data."""
        with self._lock:
            state = self._runs.get(handle)
            if state is None:
                raise KeyError("App runtime handle was not found.")
            self._apply_timeout(state, _utcnow())
            if state.status == "failed":
                return
            if error is not None:
                state.status = "failed"
                state.error = "Workflow execution failed."
                return
            projected = _project_output(output, state.output_projection or {})
            if len(_canonical_json(projected)) > state.max_output_bytes:
                state.status = "failed"
                state.error = "Workflow output exceeded the configured byte limit."
                state.output = None
                return
            state.status = "completed"
            state.output = projected

    def cancel(self, handle: str) -> None:
        """Mark an accepted run cancelled without leaking worker state."""
        with self._lock:
            state = self._runs.get(handle)
            if state is None:
                raise KeyError("App runtime handle was not found.")
            if state.status in {"accepted", "running"}:
                state.status = "cancelled"

    def _visitor_result(self, state: _RunState) -> AppRuntimeResult:
        return AppRuntimeResult(
            handle=state.mapping.public_handle,
            status=state.status,
            output=state.output if state.can_read_output else None,
            error=state.error if state.can_read_error else None,
        )

    def _enforce_invocation_limits(  # noqa: C901
        self,
        binding: AppBinding,
        *,
        app_id: UUID,
        session_id: UUID | None,
        client_ip: str | None,
        now: datetime,
    ) -> None:
        """Atomically consume minute windows and reserve concurrency."""
        concurrency_limit = binding.limits.get("max_concurrency")
        if concurrency_limit is not None:
            concurrent = sum(
                1
                for state in self._runs.values()
                if state.mapping.app_id == app_id
                and state.mapping.binding_id == binding.id
                and state.status in {"accepted", "running"}
                and state.mapping.expires_at > now
                and (state.timeout_at is None or state.timeout_at > now)
            )
            if concurrent >= concurrency_limit:
                raise AppRuntimeLimitError(
                    "Workflow binding concurrency limit exceeded."
                )

        windows: list[tuple[str, int]] = []
        app_limit = binding.limits.get("per_app_per_minute")
        if app_limit is not None:
            windows.append((f"app:{app_id}", app_limit))
        ip_limit = binding.limits.get("per_ip_per_minute")
        if ip_limit is not None:
            if not client_ip:
                raise AppRuntimeLimitError(
                    "Client identity required for invocation governance."
                )
            windows.append((f"ip:{app_id}:{client_ip}", ip_limit))
        session_limit = binding.limits.get("per_session_per_minute")
        if session_limit is not None and session_id is not None:
            windows.append((f"session:{app_id}:{session_id}", session_limit))

        cutoff = now - timedelta(minutes=1)
        for key, limit in windows:
            events = self._invocation_windows.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                raise AppRuntimeLimitError("Workflow invocation rate limit exceeded.")
        for key, _limit in windows:
            self._invocation_windows[key].append(now)

    @staticmethod
    def _apply_timeout(state: _RunState, now: datetime) -> None:
        if (
            state.timeout_at is not None
            and state.timeout_at <= now
            and state.status in {"accepted", "running"}
        ):
            state.status = "failed"
            state.error = "Workflow execution timed out."

    def _prune(self, now: datetime) -> None:
        expired_handles = {
            handle
            for handle, state in self._runs.items()
            if state.mapping.expires_at <= now
        }
        for handle in expired_handles:
            del self._runs[handle]
        for scope, (_request_hash, handle, expires_at) in list(
            self._idempotency.items()
        ):
            if expires_at <= now or handle not in self._runs:
                del self._idempotency[scope]
        cutoff = now - timedelta(minutes=1)
        for key, events in list(self._invocation_windows.items()):
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                del self._invocation_windows[key]

    @staticmethod
    def _ensure_binding_scope(
        binding: AppBinding, workspace_id: UUID, app_id: UUID
    ) -> None:
        if (
            binding.workspace_id != workspace_id
            or binding.app_id != app_id
            or binding.deleted_at is not None
        ):
            raise AppRuntimeError("Workflow binding is unavailable.")


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    except (TypeError, ValueError) as exc:
        raise AppRuntimeError("Workflow input must be valid JSON.") from exc


def _project_output(value: Any, projection: dict[str, Any]) -> Any:
    """Apply an explicit top-level allowlist; empty means no visitor output."""
    fields = projection.get("fields", [])
    if not isinstance(fields, list) or not fields:
        return None
    if not isinstance(value, dict):
        return None
    return {
        field: value[field]
        for field in fields
        if isinstance(field, str) and field in value
    }


def _validate_schema(value: Any, schema: dict[str, Any]) -> None:  # noqa: C901, PLR0912
    """Validate the documented bounded JSON Schema subset recursively."""
    if not schema:
        return
    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
    }
    unsupported = set(schema) - allowed
    if unsupported:
        raise AppRuntimeError("Workflow input schema uses unsupported keywords.")
    expected = schema.get("type")
    type_checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float))
        and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if expected is not None:
        check = type_checks.get(expected)
        if check is None or not check(value):
            raise AppRuntimeError("Workflow input does not match its schema.")
    if "enum" in schema and value not in schema["enum"]:
        raise AppRuntimeError("Workflow input does not match its schema.")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get(
            "maxLength", 2**31
        ):
            raise AppRuntimeError("Workflow input does not match its schema.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", float("-inf")) or value > schema.get(
            "maximum", float("inf")
        ):
            raise AppRuntimeError("Workflow input does not match its schema.")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get(
            "maxItems", 2**31
        ):
            raise AppRuntimeError("Workflow input does not match its schema.")
        item_schema = schema.get("items", {})
        for item in value:
            _validate_schema(item, item_schema)
    if isinstance(value, dict):
        required = schema.get("required", [])
        if any(name not in value for name in required):
            raise AppRuntimeError("Workflow input does not match its schema.")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and any(
            name not in properties for name in value
        ):
            raise AppRuntimeError("Workflow input does not match its schema.")
        for name, item in value.items():
            if name in properties:
                _validate_schema(item, properties[name])


def validate_input_schema(schema: dict[str, Any]) -> None:
    """Validate the supported schema-definition subset before binding storage."""
    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
    }
    if set(schema) - allowed:
        raise AppRuntimeError("Workflow input schema uses unsupported keywords.")
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in {
        "object",
        "array",
        "string",
        "integer",
        "number",
        "boolean",
        "null",
    }:
        raise AppRuntimeError("Workflow input schema type is unsupported.")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise AppRuntimeError("Workflow input schema properties must be an object.")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(
        not isinstance(name, str) or name not in properties for name in required
    ):
        raise AppRuntimeError("Workflow input schema required fields are invalid.")
    for child in properties.values():
        if not isinstance(child, dict):
            raise AppRuntimeError(
                "Workflow input schema property definitions must be objects."
            )
        validate_input_schema(child)
    if "items" in schema:
        items = schema["items"]
        if not isinstance(items, dict):
            raise AppRuntimeError("Workflow input schema items must be an object.")
        validate_input_schema(items)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
