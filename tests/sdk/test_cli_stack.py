"""Tests for ``orcheo stack`` shortcuts."""

from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Any
from types import SimpleNamespace
import pytest
import typer
from rich.console import Console
from orcheo_sdk.cli.main import (
    _resolve_stack_project_dir,
    _run_stack_command,
    app,
    ensure_stack_env_command,
)


def _stack_env(base_env: dict[str, str], stack_dir: Path) -> dict[str, str]:
    env = dict(base_env)
    env["ORCHEO_STACK_DIR"] = str(stack_dir)
    return env


def test_stack_logs_shortcut_runs_compose_logs_follow(
    runner: Any,
    env: dict[str, str],
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(exist_ok=True)
    compose_file = stack_dir / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    executed: list[str] = []

    def _run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        del check
        executed[:] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("orcheo_sdk.cli.main.subprocess.run", _run)
    monkeypatch.setattr("orcheo_sdk.cli.main.shutil.which", lambda _: "/usr/bin/docker")

    result = runner.invoke(
        app,
        ["--no-update-check", "stack", "--logs"],
        env=_stack_env(env, stack_dir),
    )

    assert result.exit_code == 0
    assert executed == [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "--project-directory",
        str(stack_dir),
        "logs",
        "-f",
    ]


def test_stack_start_shortcut_runs_compose_up_detached(
    runner: Any,
    env: dict[str, str],
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(exist_ok=True)
    compose_file = stack_dir / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    executed: list[str] = []

    def _run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        del check
        executed[:] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("orcheo_sdk.cli.main.subprocess.run", _run)
    monkeypatch.setattr("orcheo_sdk.cli.main.shutil.which", lambda _: "/usr/bin/docker")

    result = runner.invoke(
        app,
        ["--no-update-check", "stack", "--start"],
        env=_stack_env(env, stack_dir),
    )

    assert result.exit_code == 0
    assert executed == [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "--project-directory",
        str(stack_dir),
        "up",
        "-d",
    ]


def test_install_ensure_stack_env_command_creates_env_file(
    runner: Any,
    env: dict[str, str],
    tmp_path: Path,
) -> None:
    env_template = tmp_path / ".env.example"
    env_template.write_text(
        "ORCHEO_POSTGRES_PASSWORD=change-me\n"
        "ORCHEO_VAULT_ENCRYPTION_KEY=replace-with-64-hex-chars\n"
        "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY=strong-random-secret\n"
        "VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_replace_me\n",
        encoding="utf-8",
    )
    env_file = tmp_path / "stack" / ".env"

    result = runner.invoke(
        app,
        [
            "--no-update-check",
            "install",
            "ensure-stack-env",
            "--env-file",
            str(env_file),
            "--env-template",
            str(env_template),
        ],
        env=env,
    )

    assert result.exit_code == 0
    env_content = env_file.read_text(encoding="utf-8")
    assert "ORCHEO_POSTGRES_PASSWORD=change-me" not in env_content
    assert "ORCHEO_VAULT_ENCRYPTION_KEY=replace-with-64-hex-chars" not in env_content
    assert "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY=strong-random-secret" not in env_content
    assert "VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_replace_me" in env_content


def test_install_ensure_stack_env_command_renders_egress_config_when_compose_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The install helper also writes the egress proxy config when applicable."""

    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    (stack_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    env_file = stack_dir / ".env"
    env_template = tmp_path / ".env.example"
    env_template.write_text("ORCHEO_API_URL=http://template\n", encoding="utf-8")

    calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        "orcheo_sdk.cli.main._resolve_stack_project_dir", lambda: stack_dir
    )
    monkeypatch.setattr(
        "orcheo_sdk.cli.main.ensure_stack_env_file",
        lambda **kwargs: calls.append(("env", kwargs)),
    )
    monkeypatch.setattr(
        "orcheo_sdk.cli.main.ensure_egress_proxy_config",
        lambda **kwargs: calls.append(("egress", kwargs)),
    )

    ensure_stack_env_command(
        SimpleNamespace(obj=None),
        env_file=str(env_file),
        env_template=str(env_template),
    )

    assert [call[0] for call in calls] == ["env", "egress"]


def test_stack_logs_treats_sigint_exit_as_success(
    runner: Any,
    env: dict[str, str],
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(exist_ok=True)
    compose_file = stack_dir / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    executed: list[str] = []

    def _run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        del check
        executed[:] = command
        return subprocess.CompletedProcess(command, 130)

    monkeypatch.setattr("orcheo_sdk.cli.main.subprocess.run", _run)
    monkeypatch.setattr("orcheo_sdk.cli.main.shutil.which", lambda _: "/usr/bin/docker")

    result = runner.invoke(
        app,
        ["--no-update-check", "stack", "--logs"],
        env=_stack_env(env, stack_dir),
    )

    assert result.exit_code == 0
    assert executed == [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "--project-directory",
        str(stack_dir),
        "logs",
        "-f",
    ]


def test_stack_rejects_multiple_actions(
    runner: Any,
    env: dict[str, str],
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(exist_ok=True)
    (stack_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr("orcheo_sdk.cli.main.shutil.which", lambda _: "/usr/bin/docker")
    called = {"value": False}
    monkeypatch.setattr(
        "orcheo_sdk.cli.main.subprocess.run",
        lambda *args, **kwargs: (
            called.__setitem__("value", True),
            subprocess.CompletedProcess(args, 0),
        )[1],
    )

    result = runner.invoke(
        app,
        ["--no-update-check", "stack", "--logs", "--ps"],
        env=_stack_env(env, stack_dir),
    )

    assert result.exit_code == 2
    assert called["value"] is False


def test_stack_requires_existing_compose_file(
    runner: Any,
    env: dict[str, str],
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("orcheo_sdk.cli.main.shutil.which", lambda _: "/usr/bin/docker")
    called = {"value": False}
    monkeypatch.setattr(
        "orcheo_sdk.cli.main.subprocess.run",
        lambda *args, **kwargs: (
            called.__setitem__("value", True),
            subprocess.CompletedProcess(args, 0),
        )[1],
    )

    result = runner.invoke(
        app,
        ["--no-update-check", "stack", "--ps"],
        env=_stack_env(env, stack_dir),
    )

    assert result.exit_code == 2
    assert called["value"] is False


def test_stack_requires_docker_binary(
    runner: Any,
    env: dict[str, str],
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(exist_ok=True)
    (stack_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr("orcheo_sdk.cli.main.shutil.which", lambda _: None)
    called = {"value": False}
    monkeypatch.setattr(
        "orcheo_sdk.cli.main.subprocess.run",
        lambda *args, **kwargs: (
            called.__setitem__("value", True),
            subprocess.CompletedProcess(args, 0),
        )[1],
    )

    result = runner.invoke(
        app,
        ["--no-update-check", "stack", "--ps"],
        env=_stack_env(env, stack_dir),
    )

    assert result.exit_code == 2
    assert called["value"] is False


# ------------------------------------------------------------------
# Coverage for previously-uncovered lines
# ------------------------------------------------------------------


def test_resolve_stack_project_dir_defaults_to_home(
    monkeypatch: Any,
) -> None:
    """When ORCHEO_STACK_DIR is unset, fall back to ~/.orcheo/stack."""
    monkeypatch.delenv("ORCHEO_STACK_DIR", raising=False)
    result = _resolve_stack_project_dir()
    assert result == Path.home() / ".orcheo" / "stack"


def test_run_stack_command_defaults_expected_exit_codes(
    monkeypatch: Any,
) -> None:
    """expected_exit_codes defaults to {0} when not provided."""
    executed: list[str] = []

    def _run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        del check
        executed[:] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("orcheo_sdk.cli.main.subprocess.run", _run)
    _run_stack_command(["echo", "hello"], console=Console())
    assert executed == ["echo", "hello"]


def test_run_stack_command_raises_on_unexpected_exit_code(
    monkeypatch: Any,
) -> None:
    """Non-zero exit code not in expected set raises BadParameter."""

    def _run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        del check
        return subprocess.CompletedProcess(command, 42)

    monkeypatch.setattr("orcheo_sdk.cli.main.subprocess.run", _run)
    with pytest.raises(typer.BadParameter, match="exit code 42"):
        _run_stack_command(["false"], console=Console())


def test_stack_no_action_raises_error(
    runner: Any,
    env: dict[str, str],
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Invoking stack without any action flag shows an error."""
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(exist_ok=True)
    (stack_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr("orcheo_sdk.cli.main.shutil.which", lambda _: "/usr/bin/docker")

    result = runner.invoke(
        app,
        ["--no-update-check", "stack"],
        env=_stack_env(env, stack_dir),
    )

    assert result.exit_code == 2
