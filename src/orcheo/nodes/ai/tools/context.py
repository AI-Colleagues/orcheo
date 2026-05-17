"""Context for agent tool execution."""

from __future__ import annotations
from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from langchain_core.runnables import RunnableConfig


ToolProgressCallback = Callable[[Mapping[str, Any]], Awaitable[None]]
NodeStatusEmitter = Callable[[Mapping[str, Any]], Awaitable[None]]

_ACTIVE_TOOL_CONFIG: ContextVar[RunnableConfig | None] = ContextVar(
    "orcheo_active_tool_config", default=None
)
_ACTIVE_TOOL_PROGRESS_CALLBACK: ContextVar[ToolProgressCallback | None] = ContextVar(
    "orcheo_active_tool_progress_callback", default=None
)
_ACTIVE_NODE_STATUS_EMITTER: ContextVar[NodeStatusEmitter | None] = ContextVar(
    "orcheo_active_node_status_emitter", default=None
)


@contextmanager
def tool_execution_context(config: RunnableConfig | None) -> Any:
    """Bind a RunnableConfig for tool executions within this context."""
    token = _ACTIVE_TOOL_CONFIG.set(config)
    try:
        yield config
    finally:
        _ACTIVE_TOOL_CONFIG.reset(token)


@contextmanager
def tool_progress_context(callback: ToolProgressCallback | None) -> Any:
    """Bind a progress callback for tool executions within this context."""
    token = _ACTIVE_TOOL_PROGRESS_CALLBACK.set(callback)
    try:
        yield callback
    finally:
        _ACTIVE_TOOL_PROGRESS_CALLBACK.reset(token)


@contextmanager
def node_status_context(emitter: NodeStatusEmitter | None) -> Any:
    """Bind an in-node status emitter for the current node execution.

    Node base classes call this around each ``run()`` invocation so developer
    code can publish optional intermediate status updates via
    :func:`emit_node_status` without depending on streaming wiring directly.
    """
    token = _ACTIVE_NODE_STATUS_EMITTER.set(emitter)
    try:
        yield emitter
    finally:
        _ACTIVE_NODE_STATUS_EMITTER.reset(token)


def get_active_tool_config() -> RunnableConfig | None:
    """Return the currently bound tool execution config, if any."""
    return _ACTIVE_TOOL_CONFIG.get()


def get_active_tool_progress_callback() -> ToolProgressCallback | None:
    """Return the currently bound tool progress callback, if any."""
    return _ACTIVE_TOOL_PROGRESS_CALLBACK.get()


def get_active_node_status_emitter() -> NodeStatusEmitter | None:
    """Return the in-node status emitter for the running node, if any."""
    return _ACTIVE_NODE_STATUS_EMITTER.get()


async def emit_node_status(
    status: Any = None,
    *,
    payload: Mapping[str, Any] | None = None,
    **details: Any,
) -> None:
    """Stream an optional in-node status update from inside ``run()``.

    The call is a no-op when no status emitter is bound (e.g. when the node
    runs outside a streaming workflow execution), so node implementations can
    call this unconditionally.

    Args:
        status: Optional short status label (e.g. ``"fetching"``). Stored under
            the ``"status"`` key when supplied and not already present.
        payload: Optional mapping of additional fields to merge into the
            emitted body.
        **details: Convenience keyword fields merged into the emitted body.
    """
    emitter = get_active_node_status_emitter()
    if emitter is None:
        return
    body: dict[str, Any] = {}
    if payload is not None:
        body.update(dict(payload))
    if details:
        body.update(details)
    if status is not None:
        body.setdefault("status", status)
    await emitter(body)


__all__ = [
    "NodeStatusEmitter",
    "ToolProgressCallback",
    "emit_node_status",
    "get_active_node_status_emitter",
    "get_active_tool_config",
    "get_active_tool_progress_callback",
    "node_status_context",
    "tool_execution_context",
    "tool_progress_context",
]
