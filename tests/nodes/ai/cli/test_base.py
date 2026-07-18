"""Tests covering CLIAgentNode subprocess execution behavior."""

from __future__ import annotations
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.ai.cli.base import CLIAgentNode, ProcessExecutionResult


class EchoAgentNode(CLIAgentNode):
    """Minimal concrete CLIAgentNode used for testing the base class."""

    executable_name = "echo-agent"

    def build_command(self, executable: str) -> list[str]:
        """Return a trivial invocation echoing the prompt."""
        return [executable, self.prompt]


def _config() -> RunnableConfig:
    return RunnableConfig()


@pytest.mark.asyncio
async def test_run_raises_when_executable_missing() -> None:
    """A missing CLI binary raises before any subprocess is spawned."""
    node = EchoAgentNode(name="agent", prompt="hello")
    state = State({"node_results": {}})

    with (
        patch("shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="not found on PATH"),
    ):
        await node.run(state, _config())


@pytest.mark.asyncio
async def test_run_raises_when_working_directory_missing() -> None:
    """A configured working_directory that doesn't exist raises a clear error."""
    node = EchoAgentNode(
        name="agent", prompt="hello", working_directory="/no/such/directory"
    )
    state = State({"node_results": {}})

    with (
        patch("shutil.which", return_value="/usr/bin/echo-agent"),
        pytest.raises(RuntimeError, match="does not exist on this host"),
    ):
        await node.run(state, _config())


@pytest.mark.asyncio
async def test_run_treats_empty_working_directory_as_unset(tmp_path: Path) -> None:
    """An empty working_directory string does not attempt to validate a cwd."""
    node = EchoAgentNode(name="agent", prompt="hello", working_directory="")
    state = State({"node_results": {}})
    result = ProcessExecutionResult(
        command=["echo-agent", "hello"],
        stdout="done",
        stderr="",
        exit_code=0,
        timed_out=False,
        duration_seconds=0.1,
    )

    with (
        patch("shutil.which", return_value="/usr/bin/echo-agent"),
        patch(
            "orcheo.nodes.ai.cli.base.execute_process",
            new=AsyncMock(return_value=result),
        ) as mock_execute,
    ):
        await node.run(state, _config())

    assert mock_execute.call_args.kwargs["cwd"] is None


@pytest.mark.asyncio
async def test_run_uses_existing_working_directory(tmp_path: Path) -> None:
    """An existing working_directory is passed through to execute_process."""
    node = EchoAgentNode(name="agent", prompt="hello", working_directory=str(tmp_path))
    state = State({"node_results": {}})
    result = ProcessExecutionResult(
        command=["echo-agent", "hello"],
        stdout="done",
        stderr="",
        exit_code=0,
        timed_out=False,
        duration_seconds=0.1,
    )

    with (
        patch("shutil.which", return_value="/usr/bin/echo-agent"),
        patch(
            "orcheo.nodes.ai.cli.base.execute_process",
            new=AsyncMock(return_value=result),
        ) as mock_execute,
    ):
        await node.run(state, _config())

    assert mock_execute.call_args.kwargs["cwd"] == tmp_path


@pytest.mark.asyncio
async def test_run_returns_output_on_success() -> None:
    """A zero-exit run returns captured stdout/stderr without raising."""
    node = EchoAgentNode(name="agent", prompt="hello")
    state = State({"node_results": {}})
    result = ProcessExecutionResult(
        command=["echo-agent", "hello"],
        stdout="done",
        stderr="",
        exit_code=0,
        timed_out=False,
        duration_seconds=0.1,
    )

    with (
        patch("shutil.which", return_value="/usr/bin/echo-agent"),
        patch(
            "orcheo.nodes.ai.cli.base.execute_process",
            new=AsyncMock(return_value=result),
        ),
    ):
        output = await node.run(state, _config())

    assert output == {
        "output": "done",
        "stderr": "",
        "exit_code": 0,
        "timed_out": False,
        "duration_seconds": 0.1,
    }


@pytest.mark.asyncio
async def test_run_raises_on_nonzero_exit_by_default() -> None:
    """A non-zero exit code raises with the captured stderr by default."""
    node = EchoAgentNode(name="agent", prompt="hello")
    state = State({"node_results": {}})
    result = ProcessExecutionResult(
        command=["echo-agent", "hello"],
        stdout="",
        stderr="boom",
        exit_code=1,
        timed_out=False,
        duration_seconds=0.1,
    )

    with (
        patch("shutil.which", return_value="/usr/bin/echo-agent"),
        patch(
            "orcheo.nodes.ai.cli.base.execute_process",
            new=AsyncMock(return_value=result),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await node.run(state, _config())


@pytest.mark.asyncio
async def test_run_does_not_raise_when_raise_on_error_disabled() -> None:
    """Disabling raise_on_error surfaces failure details in the output instead."""
    node = EchoAgentNode(name="agent", prompt="hello", raise_on_error=False)
    state = State({"node_results": {}})
    result = ProcessExecutionResult(
        command=["echo-agent", "hello"],
        stdout="",
        stderr="boom",
        exit_code=1,
        timed_out=False,
        duration_seconds=0.1,
    )

    with (
        patch("shutil.which", return_value="/usr/bin/echo-agent"),
        patch(
            "orcheo.nodes.ai.cli.base.execute_process",
            new=AsyncMock(return_value=result),
        ),
    ):
        output = await node.run(state, _config())

    assert output["exit_code"] == 1
    assert output["stderr"] == "boom"


@pytest.mark.asyncio
async def test_run_raises_on_timeout() -> None:
    """A timed-out run raises even when the captured exit code is None."""
    node = EchoAgentNode(name="agent", prompt="hello")
    state = State({"node_results": {}})
    result = ProcessExecutionResult(
        command=["echo-agent", "hello"],
        stdout="partial",
        stderr="",
        exit_code=None,
        timed_out=True,
        duration_seconds=600.0,
    )

    with (
        patch("shutil.which", return_value="/usr/bin/echo-agent"),
        patch(
            "orcheo.nodes.ai.cli.base.execute_process",
            new=AsyncMock(return_value=result),
        ),
        pytest.raises(RuntimeError, match="timed out"),
    ):
        await node.run(state, _config())


@pytest.mark.asyncio
async def test_execute_process_captures_stdout_and_exit_code() -> None:
    """execute_process runs a real subprocess and captures its output."""
    from orcheo.nodes.ai.cli.base import execute_process

    result = await execute_process(["python3", "-c", "print('hi')"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "hi"
    assert not result.timed_out


@pytest.mark.asyncio
async def test_execute_process_times_out() -> None:
    """execute_process terminates a process that exceeds the timeout."""
    from orcheo.nodes.ai.cli.base import execute_process

    result = await execute_process(
        ["python3", "-c", "import time; time.sleep(5)"],
        timeout_seconds=0.1,
    )

    assert result.timed_out is True
    assert result.exit_code is not None
