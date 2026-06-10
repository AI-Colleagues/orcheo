from collections.abc import Sequence
from subprocess import CompletedProcess
import pytest
from orcheo.tooling import commands


@pytest.fixture()
def successful_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def _run(command: Sequence[str], check: bool = False) -> CompletedProcess[str]:
        return CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(commands.subprocess, "run", _run)


def test_dev_server_invokes_uvicorn(successful_run: None) -> None:
    commands.dev_server()


def test_lint_invokes_all_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Sequence[str]] = []

    def _run(command: Sequence[str], check: bool = False) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(commands.subprocess, "run", _run)

    commands.lint()

    assert calls == [
        ["ruff", "check", "src/orcheo", "packages/sdk/src", "apps/backend/src"],
        ["mypy", "src/orcheo", "packages/sdk/src", "apps/backend/src"],
        ["ruff", "format", ".", "--check"],
    ]


def test_format_invokes_formatters(successful_run: None) -> None:
    commands.format_code()


def test_test_invokes_pytest(successful_run: None) -> None:
    commands.test()


def test_studio_lint_invokes_npm(successful_run: None) -> None:
    commands.studio_lint()


def test_studio_dev_invokes_npm(successful_run: None) -> None:
    commands.studio_dev()


def test_run_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _run(command: Sequence[str], check: bool = False) -> CompletedProcess[str]:
        return CompletedProcess(args=command, returncode=1)

    monkeypatch.setattr(commands.subprocess, "run", _run)

    with pytest.raises(SystemExit):
        commands.dev_server()
