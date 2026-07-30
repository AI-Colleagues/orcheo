"""Sandbox helpers used while loading LangGraph scripts."""

from __future__ import annotations
import contextlib
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Generator
from types import FrameType
from typing import Any, cast
from orcheo.graph.ingestion.exceptions import ScriptIngestionError


logger = logging.getLogger(__name__)


TraceFunc = Callable[[FrameType | None, str, object], object]


def uploads_allowed() -> bool:
    """Return True when client-supplied workflow uploads are permitted.

    In managed deployments (the default) client-supplied workflow scripts are
    blocked at the API layer; only server-side onboarding of official candidates
    is permitted. Self-hosted operators who trust every workflow author can opt
    out by setting ORCHEO_WORKFLOW_TRUST_MODE=allow_client_uploads.

    Environment variables:
        ORCHEO_WORKFLOW_TRUST_MODE: Controls upload policy
            - managed (default): Only server-side candidate onboarding
            - allow_client_uploads: Permit client-supplied workflow uploads
    """
    trust_mode = os.environ.get("ORCHEO_WORKFLOW_TRUST_MODE", "managed").strip().lower()
    uploads_enabled = trust_mode == "allow_client_uploads"

    if not uploads_enabled:
        logger.info(
            "Client workflow uploads disabled (trust_mode=%s). "
            "Set ORCHEO_WORKFLOW_TRUST_MODE=allow_client_uploads to enable.",
            trust_mode,
        )
    else:
        logger.warning(
            "Client workflow uploads enabled (trust_mode=%s). "
            "Only use this in self-hosted environments where you trust all "
            "workflow authors.",
            trust_mode,
        )

    return uploads_enabled


def validate_script_size(source: str, max_script_bytes: int | None) -> None:
    """Raise ``ScriptIngestionError`` when the script exceeds the byte limit."""
    if max_script_bytes is None:
        return

    if max_script_bytes <= 0:
        msg = "LangGraph script size limit must be a positive integer"
        raise ScriptIngestionError(msg)

    encoded_length = len(source.encode("utf-8"))
    if encoded_length > max_script_bytes:
        msg = f"LangGraph script exceeds the permitted size of {max_script_bytes} bytes"
        raise ScriptIngestionError(msg)


_active_deadline = threading.local()


def remaining_execution_time(*, time_module: Any | None = None) -> float | None:
    """Return seconds left on this thread's active script timeout, if any.

    Ingestion sometimes has to finish executing a script on a helper thread (an
    ``orcheo_workflow`` coroutine awaited from synchronous code). The trace hook
    installed by :func:`execution_timeout` is deliberately confined to the thread
    that installed it, so callers that hand work to another thread must bound the
    wait themselves using this remaining budget.
    """
    deadline = getattr(_active_deadline, "value", None)
    if deadline is None:
        return None
    time_obj = time_module or time
    return max(deadline - time_obj.perf_counter(), 0.0)


@contextlib.contextmanager
def _tracked_deadline(deadline: float | None) -> Generator[None, None, None]:
    """Publish ``deadline`` as this thread's active timeout for the block."""
    previous = getattr(_active_deadline, "value", None)
    _active_deadline.value = deadline
    try:
        yield
    finally:
        _active_deadline.value = previous


@contextlib.contextmanager
def execution_timeout(
    timeout_seconds: float | None,
    *,
    sys_module: Any | None = None,
    threading_module: Any | None = None,
    time_module: Any | None = None,
) -> Generator[None, None, None]:
    """Enforce a wall-clock timeout around script execution.

    The timeout only ever applies to the calling thread. Both enforcement
    mechanisms are deliberately thread-scoped: ``SIGALRM`` is delivered to the
    main thread, and the ``sys.settrace`` fallback is per-thread state. Nothing
    here touches process-global hooks, because ingestion runs concurrently on
    shared worker pools and a leaked hook outlives the window it belongs to.
    """
    if timeout_seconds is None or timeout_seconds <= 0:
        yield
        return

    sys_obj = sys_module or sys
    threading_obj = threading_module or threading
    time_obj = time_module or time

    deadline = time_obj.perf_counter() + timeout_seconds

    use_signal = (
        hasattr(signal, "setitimer")
        and threading_obj.current_thread() is threading_obj.main_thread()
    )

    if use_signal:
        previous_handler = signal.getsignal(signal.SIGALRM)

        def _handle_timeout(_signum: int, _frame: FrameType | None) -> None:
            raise TimeoutError(
                "LangGraph script execution timed out"
            )  # pragma: no cover

        try:
            signal.signal(signal.SIGALRM, _handle_timeout)
            signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
            with _tracked_deadline(deadline):
                yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
        return

    owner_ident = threading_obj.get_ident()

    def _trace_timeout(_frame: FrameType | None, event: str, _arg: object) -> TraceFunc:
        # sys.settrace is per-thread, but a script can copy the active hook onto
        # other threads via threading.settrace. Refuse to fire anywhere except
        # the thread that armed the deadline, so an expired budget can never
        # kill an unrelated worker.
        if threading_obj.get_ident() != owner_ident:
            return _trace_timeout
        if event == "line" and time_obj.perf_counter() > deadline:
            raise TimeoutError("LangGraph script execution timed out")
        return _trace_timeout

    previous_trace = cast(TraceFunc | None, sys_obj.gettrace())

    sys_obj.settrace(cast(Any, _trace_timeout))
    try:
        with _tracked_deadline(deadline):
            yield
    finally:
        sys_obj.settrace(cast(Any, previous_trace))


__all__ = [
    "execution_timeout",
    "remaining_execution_time",
    "uploads_allowed",
    "validate_script_size",
]
