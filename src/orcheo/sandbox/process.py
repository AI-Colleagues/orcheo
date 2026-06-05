"""Managed subprocess execution helpers for sandboxed process launch."""

from __future__ import annotations
import asyncio
import os
import signal
import time
from collections.abc import Mapping
from pathlib import Path
from orcheo.sandbox.models import ProcessExecutionResult


async def execute_process(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int | float | None = None,
) -> ProcessExecutionResult:
    """Execute a command and capture partial output on failure or timeout."""
    started_at = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_task = asyncio.create_task(_read_stream(process.stdout, stdout_chunks))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, stderr_chunks))

    timed_out = False
    exit_code: int | None
    try:
        if timeout_seconds is None:
            exit_code = await process.wait()
        else:
            exit_code = await asyncio.wait_for(process.wait(), timeout_seconds)
    except TimeoutError:
        timed_out = True
        exit_code = await _terminate_process_group(process)

    await asyncio.gather(stdout_task, stderr_task)
    duration_seconds = time.monotonic() - started_at
    return ProcessExecutionResult(
        command=command,
        stdout=b"".join(stdout_chunks).decode("utf-8", errors="replace"),
        stderr=b"".join(stderr_chunks).decode("utf-8", errors="replace"),
        exit_code=exit_code,
        timed_out=timed_out,
        duration_seconds=duration_seconds,
    )


async def _read_stream(
    stream: asyncio.StreamReader | None,
    chunks: list[bytes],
) -> None:
    """Read one process stream until EOF."""
    if stream is None:
        return
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            return
        chunks.append(chunk)


async def _terminate_process_group(process: asyncio.subprocess.Process) -> int | None:
    """Terminate a subprocess process group and return the final exit code."""
    if process.returncode is not None:
        return process.returncode

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:  # pragma: no cover - race with exit
        return await process.wait()

    try:
        return await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:  # pragma: no cover - race with exit
            pass
        return await process.wait()
