"""Tests covering ingestion size limits and timeouts."""

from __future__ import annotations
import asyncio
import contextlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from orcheo.graph.ingestion import (
    DEFAULT_SCRIPT_SIZE_LIMIT,
    ScriptIngestionError,
    ingest_langgraph_script,
)
from orcheo.graph.ingestion.sandbox import validate_script_size as _validate_script_size
from orcheo.graph.ingestion.loader import load_graph_from_script


def test_default_script_size_limit_is_512_kib() -> None:
    assert DEFAULT_SCRIPT_SIZE_LIMIT == 512 * 1024


def test_ingest_script_exceeding_size_limit() -> None:
    oversized = "a" * (DEFAULT_SCRIPT_SIZE_LIMIT + 1)

    with pytest.raises(ScriptIngestionError, match="exceeds the permitted size"):
        ingest_langgraph_script(oversized)


def test_validate_script_size_without_limit() -> None:
    assert _validate_script_size("payload", None) is None


def test_validate_script_size_rejects_non_positive_limits() -> None:
    with pytest.raises(ScriptIngestionError, match="must be a positive integer"):
        _validate_script_size("payload", 0)


def test_validate_script_size_accepts_equal_limit() -> None:
    source = "hello"
    _validate_script_size(source, max_script_bytes=len(source.encode("utf-8")))


def test_validate_script_size_rejects_payload_above_limit() -> None:
    with pytest.raises(
        ScriptIngestionError,
        match="LangGraph script exceeds the permitted size of 1 bytes",
    ):
        _validate_script_size("ab", max_script_bytes=1)


def test_ingest_script_enforces_execution_timeout() -> None:
    script = "while True:\n    pass\n"

    with pytest.raises(
        ScriptIngestionError, match="execution exceeded the configured timeout"
    ):
        load_graph_from_script(script, execution_timeout_seconds=0.1)


def test_ingest_script_enforces_timeout_on_helper_thread() -> None:
    """A top-level await ingested from inside an event loop runs on a helper
    thread, which the thread-scoped timeout cannot interrupt; the wait itself
    must be bounded instead, or the caller hangs forever."""
    script = "import asyncio\nawait asyncio.sleep(30)\n"

    async def ingest_from_running_loop() -> None:
        load_graph_from_script(script, execution_timeout_seconds=0.5)

    started = time.monotonic()
    with pytest.raises(
        ScriptIngestionError, match="execution exceeded the configured timeout"
    ):
        asyncio.run(ingest_from_running_loop())
    # Must give up on its own budget, not once the script happens to finish.
    assert time.monotonic() - started < 10


def test_ingestion_does_not_poison_shared_worker_threads() -> None:
    """Regression: execution_timeout used to install a process-global trace hook
    via threading.settrace, which leaked into every thread spawned during the
    window. Those threads kept a stale deadline for life and then raised
    TimeoutError on their next line of unrelated work."""
    script = "import time\ntime.sleep(0.2)\n"
    at_barrier = threading.Barrier(2, timeout=20)

    def unrelated_work() -> str:
        return "ok"

    def blocking_work() -> str:
        at_barrier.wait()
        return "ok"

    with ThreadPoolExecutor(max_workers=2) as pool:
        # Worker 1 runs an ingestion that finishes well inside its deadline.
        ingesting = pool.submit(
            load_graph_from_script, script, execution_timeout_seconds=0.5
        )
        time.sleep(0.05)
        # Worker 2 is spawned while worker 1 holds the ingestion window open.
        spawned = pool.submit(unrelated_work)

        with contextlib.suppress(ScriptIngestionError):
            ingesting.result(timeout=30)
        assert spawned.result(timeout=30) == "ok"

        # Let the ingestion's deadline fall into the past, then reuse the pool.
        time.sleep(0.6)
        # Two blocking tasks force *both* pooled workers to run, including the
        # one born mid-window. A poisoned worker dies before it picks up its
        # work item, so its future never resolves.
        futures = [pool.submit(blocking_work) for _ in range(2)]
        for future in futures:
            assert future.result(timeout=30) == "ok"
