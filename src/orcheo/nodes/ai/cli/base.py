"""Base class for nodes that run a host-installed CLI coding agent.

These nodes assume the provider CLI (Codex, Claude Code, Antigravity, ...) is
already installed and authenticated on the host running the workflow worker
by an operator (e.g. via ``claude setup-token`` or ``codex login``). No
credentials are materialized, probed, or injected by the node itself — it
only resolves the executable on ``PATH`` and runs it non-interactively.

Because the node executes an arbitrary host subprocess with the worker's own
privileges, every concrete subclass registers with ``NodeMetadata(restricted=
True)`` so restricted-mode (untrusted-author) workflow ingestion rejects it;
see ``orcheo.graph.ir.node_policy``. It is only available to trusted,
first-party workflow sources.
"""

from __future__ import annotations
import asyncio
import os
import shutil
import signal
import time
from abc import abstractmethod
from pathlib import Path
from typing import Any, ClassVar
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode


class ProcessExecutionResult(BaseModel):
    """Captured result of a managed subprocess invocation."""

    command: list[str]
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    duration_seconds: float


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


async def execute_process(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: int | float | None = None,
) -> ProcessExecutionResult:
    """Execute ``command``, capturing output and enforcing an optional timeout."""
    started_at = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd) if cwd is not None else None,
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


class CLIAgentNode(TaskNode):
    """Run a pre-authenticated, host-installed CLI coding agent non-interactively."""

    executable_name: ClassVar[str]
    """Binary name resolved via ``PATH``; set by each provider subclass."""

    prompt: str = Field(description="Task instructions sent to the CLI agent.")
    system_prompt: str | None = Field(
        default=None,
        description="Optional system instructions prepended to the task prompt.",
    )
    working_directory: str | None = Field(
        default=None,
        description="Directory the CLI process runs in. Defaults to the worker's cwd.",
    )
    timeout_seconds: int = Field(
        default=600,
        gt=0,
        description="Maximum time in seconds to wait for the CLI process to finish.",
    )
    raise_on_error: bool = Field(
        default=True,
        description="Raise if the CLI exits non-zero or the run times out.",
    )

    @abstractmethod
    def build_command(self, executable: str) -> list[str]:
        """Return the non-interactive CLI invocation for this provider."""

    def _resolve_executable(self) -> str:
        """Locate the provider CLI on ``PATH``."""
        resolved = shutil.which(self.executable_name)
        if resolved is None:
            msg = (
                f"'{self.executable_name}' was not found on PATH. Install and "
                f"authenticate the {self.executable_name} CLI on this host "
                f"before running node '{self.name}'."
            )
            raise RuntimeError(msg)
        return resolved

    def _resolve_working_directory(self) -> Path | None:
        """Validate and return the configured working directory, if any."""
        if not self.working_directory:
            return None
        cwd = Path(self.working_directory)
        if not cwd.is_dir():
            msg = (
                f"working_directory '{cwd}' does not exist on this host. Set "
                f"it to a real directory before running node '{self.name}'."
            )
            raise RuntimeError(msg)
        return cwd

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Run the CLI agent as a subprocess and return its captured output."""
        executable = self._resolve_executable()
        command = self.build_command(executable)
        cwd = self._resolve_working_directory()
        result = await execute_process(
            command, cwd=cwd, timeout_seconds=self.timeout_seconds
        )

        if self.raise_on_error and (result.timed_out or result.exit_code != 0):
            reason = "timed out" if result.timed_out else f"exited {result.exit_code}"
            detail = result.stderr.strip() or result.stdout.strip()
            msg = f"'{self.executable_name}' {reason}: {detail}"
            raise RuntimeError(msg)

        return {
            "output": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_seconds": result.duration_seconds,
        }


__all__ = ["CLIAgentNode", "ProcessExecutionResult", "execute_process"]
