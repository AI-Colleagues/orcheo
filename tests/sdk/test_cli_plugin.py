"""Tests for plugin lifecycle CLI commands."""

from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner
from orcheo.plugins.models import PluginImpactSummary
from orcheo_sdk.cli import plugin as plugin_module
from orcheo_sdk.cli.main import app
from orcheo_sdk.cli.state import CLIState


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "plugin_fixtures"


def _copy_fixture(tmp_path: Path, fixture_name: str) -> Path:
    source = FIXTURE_ROOT / fixture_name
    destination = tmp_path / fixture_name
    shutil.copytree(source, destination)
    return destination


def _plugin_dir(env: dict[str, str]) -> Path:
    return Path(env["ORCHEO_PLUGIN_DIR"])


def _stack_env(base_env: dict[str, str], stack_dir: Path) -> dict[str, str]:
    env = dict(base_env)
    env["ORCHEO_STACK_DIR"] = str(stack_dir)
    return env


def _make_cli_state(*, human: bool) -> CLIState:
    return CLIState(
        settings=MagicMock(),
        client=MagicMock(),
        cache=MagicMock(),
        console=Console(record=True),
        human=human,
    )


def test_run_stack_subprocess_defaults_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "orcheo_sdk.cli.plugin.shutil.which", lambda _: "/usr/bin/docker"
    )

    def fake_run(
        command: list[str], *, check: bool, capture_output: bool, text: bool
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text
        return subprocess.CompletedProcess(command, 0, stdout="ok\n")

    monkeypatch.setattr("orcheo_sdk.cli.plugin.subprocess.run", fake_run)
    result = plugin_module._run_stack_subprocess(["docker", "ps"])
    assert result.returncode == 0


def test_run_stack_plugin_passthrough_streams_for_human_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _make_cli_state(human=True)
    monkeypatch.setattr(
        plugin_module,
        "_stack_plugin_command",
        lambda *, args, human: ["dummy"],
    )

    called_with: list[list[str]] = []

    def fake_streaming(
        command: list[str], *, expected_exit_codes: set[int] | None = None
    ) -> int:
        called_with.append(command)
        return 0

    monkeypatch.setattr(
        plugin_module, "_run_stack_subprocess_streaming", fake_streaming
    )
    plugin_module._run_stack_plugin_passthrough(args=["list"], state=state)
    assert called_with == [["dummy"]]


def test_run_stack_plugin_passthrough_uses_typer_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _make_cli_state(human=False)
    monkeypatch.setattr(
        plugin_module,
        "_stack_plugin_command",
        lambda *, args, human: ["dummy"],
    )

    def fake_subprocess(
        command: list[str], *, expected_exit_codes: set[int] | None = None
    ) -> subprocess.CompletedProcess[str]:
        del expected_exit_codes
        return subprocess.CompletedProcess(command, 0, stdout="machine\n")

    monkeypatch.setattr(plugin_module, "_run_stack_subprocess", fake_subprocess)
    echoed: list[str] = []

    def capture_echo(message: str) -> None:
        echoed.append(message)

    monkeypatch.setattr("typer.echo", capture_echo)
    plugin_module._run_stack_plugin_passthrough(args=["list"], state=state)
    assert echoed == ["machine"]


def test_run_stack_plugin_passthrough_doctor_raises_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _make_cli_state(human=False)
    monkeypatch.setattr(
        plugin_module,
        "_stack_plugin_command",
        lambda *, args, human: ["dummy"],
    )

    def fake_subprocess(
        command: list[str], *, expected_exit_codes: set[int] | None = None
    ) -> subprocess.CompletedProcess[str]:
        del expected_exit_codes
        return subprocess.CompletedProcess(command, 1, stdout="")

    monkeypatch.setattr(plugin_module, "_run_stack_subprocess", fake_subprocess)
    with pytest.raises(typer.Exit) as excinfo:
        plugin_module._run_stack_plugin_passthrough(args=["doctor"], state=state)
    assert excinfo.value.exit_code == 1


def test_run_stack_subprocess_streaming_calls_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "orcheo_sdk.cli.plugin.shutil.which", lambda _: "/usr/bin/docker"
    )

    def fake_run(
        command: list[str], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("orcheo_sdk.cli.plugin.subprocess.run", fake_run)
    returncode = plugin_module._run_stack_subprocess_streaming(["docker", "ps"])
    assert returncode == 0


def test_run_stack_subprocess_streaming_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "orcheo_sdk.cli.plugin.shutil.which", lambda _: "/usr/bin/docker"
    )

    def fake_run(
        command: list[str], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr("orcheo_sdk.cli.plugin.subprocess.run", fake_run)
    with pytest.raises(typer.BadParameter):
        plugin_module._run_stack_subprocess_streaming(["docker", "fail"])


def test_install_plugin_stack_runtime_human_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = MagicMock()
    state.human = True
    passthrough_calls: list[tuple[list[str], Any]] = []

    def capture_passthrough(*, args: list[str], state: Any) -> None:
        passthrough_calls.append((args, state))

    restarts: list[Any] = []

    monkeypatch.setattr(plugin_module, "_state", lambda ctx: state)
    monkeypatch.setattr(plugin_module, "_use_stack_runtime", lambda runtime: True)
    monkeypatch.setattr(plugin_module, "_is_local_plugin_ref", lambda ref: False)
    monkeypatch.setattr(
        plugin_module, "_run_stack_plugin_passthrough", capture_passthrough
    )
    monkeypatch.setattr(
        plugin_module,
        "_restart_running_stack_services",
        lambda s: restarts.append(s),
    )
    plugin_module.install_plugin(None, "example-ref", runtime="auto")
    assert len(passthrough_calls) == 1
    assert passthrough_calls[0][0] == ["install", "example-ref"]
    assert restarts == [state]


def test_install_plugin_stack_runtime_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    state = MagicMock()
    state.human = False
    payload = {
        "plugin": {"name": "example"},
        "impact": {
            "change_type": "install",
            "affected_component_kinds": [],
            "affected_component_ids": [],
            "activation_mode": "silent_hot_reload",
            "prompt_required": False,
            "restart_required": True,
        },
    }
    monkeypatch.setattr(plugin_module, "_state", lambda ctx: state)
    monkeypatch.setattr(plugin_module, "_use_stack_runtime", lambda runtime: True)
    monkeypatch.setattr(plugin_module, "_is_local_plugin_ref", lambda ref: False)
    monkeypatch.setattr(plugin_module, "_run_stack_plugin_json", lambda args: payload)
    restarts: list[CLIState] = []

    def capture_restart(console_state: CLIState) -> None:
        restarts.append(console_state)

    monkeypatch.setattr(
        plugin_module, "_restart_running_stack_services", capture_restart
    )
    printed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        plugin_module, "print_json", lambda value: printed.append(value)
    )
    plugin_module.install_plugin(None, "example-ref", runtime="auto")
    assert restarts == [state]
    assert printed == [payload]


def test_doctor_plugins_stack_runtime_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = MagicMock()
    monkeypatch.setattr(plugin_module, "_state", lambda ctx: state)
    monkeypatch.setattr(plugin_module, "_use_stack_runtime", lambda runtime: True)
    calls: list[tuple[list[str], CLIState]] = []

    def capture_passthrough(*, args: list[str], state: CLIState) -> None:
        calls.append((args, state))

    monkeypatch.setattr(
        plugin_module,
        "_run_stack_plugin_passthrough",
        capture_passthrough,
    )
    plugin_module.doctor_plugins(None, runtime="auto")
    assert calls == [(["doctor"], state)]


def test_plugin_list_empty(runner: CliRunner, machine_env: dict[str, str]) -> None:
    """Machine mode renders empty output when no plugins are installed."""
    result = runner.invoke(app, ["plugin", "list"], env=machine_env)
    assert result.exit_code == 0
    assert result.stdout.strip() == "(empty)"


def test_plugin_install_list_and_show(
    runner: CliRunner, machine_env: dict[str, str], tmp_path: Path
) -> None:
    """Install a local plugin fixture and expose it through list/show."""
    fixture_path = _copy_fixture(tmp_path, "node_plugin")

    install_result = runner.invoke(
        app, ["plugin", "install", str(fixture_path)], env=machine_env
    )
    assert install_result.exit_code == 0
    install_payload = json.loads(install_result.stdout)
    assert install_payload["plugin"]["name"] == "orcheo-plugin-fixture-node"

    list_result = runner.invoke(app, ["plugin", "list"], env=machine_env)
    assert list_result.exit_code == 0
    assert "orcheo-plugin-fixture-node" in list_result.stdout

    show_result = runner.invoke(
        app,
        ["plugin", "show", "orcheo-plugin-fixture-node"],
        env=machine_env,
    )
    assert show_result.exit_code == 0
    show_payload = json.loads(show_result.stdout)
    assert show_payload["plugin_api_version"] == 1
    assert show_payload["exports"] == ["nodes"]

    plugin_dir = _plugin_dir(machine_env)
    assert (plugin_dir / "plugins.toml").exists()
    assert (plugin_dir / "plugin-lock.toml").exists()
    assert (plugin_dir / "venv").exists()

    node_list_result = runner.invoke(app, ["node", "list"], env=machine_env)
    assert node_list_result.exit_code == 0
    assert "FixturePluginNode" in node_list_result.stdout


def test_plugin_install_machine_mode_returns_json(
    runner: CliRunner, machine_env: dict[str, str], tmp_path: Path
) -> None:
    """Machine mode returns structured install output."""
    fixture_path = _copy_fixture(tmp_path, "edge_plugin")

    result = runner.invoke(
        app, ["plugin", "install", str(fixture_path)], env=machine_env
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["plugin"]["name"] == "orcheo-plugin-fixture-edge"
    assert payload["impact"]["activation_mode"] == "silent_hot_reload"

    edge_list_result = runner.invoke(app, ["edge", "list"], env=machine_env)
    assert edge_list_result.exit_code == 0
    assert "FixturePluginEdge" in edge_list_result.stdout


def test_plugin_install_human_mode_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _make_cli_state(human=True)
    monkeypatch.setattr(plugin_module, "_state", lambda ctx: state)
    monkeypatch.setattr(plugin_module, "_use_stack_runtime", lambda runtime: False)
    monkeypatch.setattr(
        plugin_module,
        "install_plugin_data",
        lambda ref, *, progress=None: (
            (
                progress("Creating isolated plugin environment")
                if progress is not None
                else None
            )
            or {
                "plugin": {"name": "example"},
                "impact": PluginImpactSummary(
                    change_type="install",
                    affected_component_kinds=[],
                    affected_component_ids=[],
                    activation_mode="silent_hot_reload",
                    prompt_required=False,
                    restart_required=False,
                ),
            }
        ),
    )

    plugin_module.install_plugin(None, "example-ref", runtime="auto")

    output = state.console.export_text()
    assert "Installing plugin from 'example-ref'" in output
    assert "Creating isolated plugin environment" in output


def test_plugin_install_targets_stack_and_restarts_running_services(
    runner: CliRunner,
    machine_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Stack-aware install shells into the backend container and restarts services."""
    stack_dir = tmp_path / "stack"
    compose_file = stack_dir / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    executed: list[list[str]] = []
    responses = iter(
        [
            subprocess.CompletedProcess(["docker"], 0, stdout="backend\nworker\n"),
            subprocess.CompletedProcess(
                ["docker"],
                0,
                stdout=json.dumps(
                    {
                        "plugin": {"name": "orcheo-plugin-lark-listener"},
                        "impact": {
                            "change_type": "install",
                            "affected_component_kinds": ["listeners"],
                            "affected_component_ids": ["lark"],
                            "activation_mode": "restart_required",
                            "prompt_required": False,
                            "restart_required": True,
                        },
                    }
                ),
            ),
            subprocess.CompletedProcess(["docker"], 0, stdout="backend\nworker\n"),
            subprocess.CompletedProcess(["docker"], 0, stdout=""),
        ]
    )

    def _run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text
        executed.append(command)
        return next(responses)

    monkeypatch.setattr("orcheo_sdk.cli.plugin.subprocess.run", _run)
    monkeypatch.setattr(
        "orcheo_sdk.cli.plugin.shutil.which", lambda _: "/usr/bin/docker"
    )

    result = runner.invoke(
        app,
        ["plugin", "install", "orcheo-plugin-lark-listener"],
        env=_stack_env(machine_env, stack_dir),
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["plugin"]["name"] == "orcheo-plugin-lark-listener"
    assert executed[1] == [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "--project-directory",
        str(stack_dir),
        "exec",
        "-T",
        "backend",
        "orcheo",
        "plugin",
        "install",
        "orcheo-plugin-lark-listener",
        "--runtime",
        "local",
    ]
    assert executed[3] == [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "--project-directory",
        str(stack_dir),
        "restart",
        "backend",
        "worker",
    ]


def test_plugin_install_git_ref_targets_stack_runtime(
    runner: CliRunner,
    machine_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Git refs should install into the stack when stack runtime is requested."""
    stack_dir = tmp_path / "stack"
    compose_file = stack_dir / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    git_ref = "git+https://github.com/AI-Colleagues/orcheo-plugin-wecom-listener.git"
    executed: list[list[str]] = []
    responses = iter(
        [
            subprocess.CompletedProcess(["docker"], 0, stdout="backend\n"),
            subprocess.CompletedProcess(
                ["docker"],
                0,
                stdout=json.dumps(
                    {
                        "plugin": {"name": "orcheo-plugin-wecom-listener"},
                        "impact": {
                            "change_type": "install",
                            "affected_component_kinds": ["listeners"],
                            "affected_component_ids": ["wecom"],
                            "activation_mode": "restart_required",
                            "prompt_required": False,
                            "restart_required": True,
                        },
                    }
                ),
            ),
            subprocess.CompletedProcess(["docker"], 0, stdout="backend\n"),
            subprocess.CompletedProcess(["docker"], 0, stdout=""),
        ]
    )

    def _run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text
        executed.append(command)
        return next(responses)

    monkeypatch.setattr("orcheo_sdk.cli.plugin.subprocess.run", _run)
    monkeypatch.setattr(
        "orcheo_sdk.cli.plugin.shutil.which", lambda _: "/usr/bin/docker"
    )

    result = runner.invoke(
        app,
        ["plugin", "install", "--runtime", "stack", git_ref],
        env=_stack_env(machine_env, stack_dir),
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["plugin"]["name"] == "orcheo-plugin-wecom-listener"
    assert executed[1] == [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "--project-directory",
        str(stack_dir),
        "exec",
        "-T",
        "backend",
        "orcheo",
        "plugin",
        "install",
        git_ref,
        "--runtime",
        "local",
    ]


def test_plugin_install_local_path_uses_stack_runtime(
    runner: CliRunner,
    machine_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Local path installs should stage into the stack runtime when selected."""
    fixture_path = _copy_fixture(tmp_path, "node_plugin")
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(exist_ok=True)
    compose_file = stack_dir / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    monkeypatch.setattr(
        "orcheo_sdk.cli.plugin._copy_local_plugin_ref_into_stack",
        lambda ref: "/data/plugin-sources/abc/node_plugin",
    )
    stack_install = MagicMock(
        return_value={
            "plugin": {"name": "orcheo-plugin-fixture-node"},
            "impact": {
                "change_type": "install",
                "affected_component_kinds": [],
                "affected_component_ids": [],
                "activation_mode": "restart_required",
                "prompt_required": False,
                "restart_required": False,
            },
        }
    )
    monkeypatch.setattr("orcheo_sdk.cli.plugin._run_stack_plugin_json", stack_install)

    result = runner.invoke(
        app,
        ["plugin", "install", str(fixture_path), "--runtime", "stack"],
        env=_stack_env(machine_env, stack_dir),
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["plugin"]["name"] == "orcheo-plugin-fixture-node"
    stack_install.assert_called_once_with(
        ["install", "/data/plugin-sources/abc/node_plugin"]
    )


def test_plugin_update_prompts_for_existing_hot_reloadable_plugin(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Updating an existing node plugin prompts for confirmation."""
    fixture_path = _copy_fixture(tmp_path, "node_plugin")
    result = runner.invoke(app, ["plugin", "install", str(fixture_path)], env=env)
    assert result.exit_code == 0

    pyproject_path = fixture_path / "pyproject.toml"
    pyproject_path.write_text(
        pyproject_path.read_text(encoding="utf-8").replace(
            'version = "0.1.0"', 'version = "0.2.0"'
        ),
        encoding="utf-8",
    )

    update_result = runner.invoke(
        app,
        ["plugin", "update", "orcheo-plugin-fixture-node"],
        env=env,
        input="y\n",
    )
    assert update_result.exit_code == 0
    assert "Update orcheo-plugin-fixture-node" in update_result.stdout

    show_result = runner.invoke(
        app,
        ["plugin", "show", "orcheo-plugin-fixture-node"],
        env=env,
    )
    assert show_result.exit_code == 0
    assert "0.2.0" in show_result.stdout


def test_plugin_disable_and_enable(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Disable and re-enable a listener plugin."""
    fixture_path = _copy_fixture(tmp_path, "listener_plugin")
    result = runner.invoke(app, ["plugin", "install", str(fixture_path)], env=env)
    assert result.exit_code == 0

    disable_result = runner.invoke(
        app,
        ["plugin", "disable", "orcheo-plugin-fixture-listener"],
        env=env,
        input="y\n",
    )
    assert disable_result.exit_code == 0
    assert "Disable orcheo-plugin-fixture-listener" in disable_result.stdout

    show_disabled = runner.invoke(
        app,
        ["plugin", "show", "orcheo-plugin-fixture-listener"],
        env=env,
    )
    assert show_disabled.exit_code == 0
    assert "'enabled': False" in show_disabled.stdout

    enable_result = runner.invoke(
        app,
        ["plugin", "enable", "orcheo-plugin-fixture-listener", "--force"],
        env=env,
    )
    assert enable_result.exit_code == 0

    show_enabled = runner.invoke(
        app,
        ["plugin", "show", "orcheo-plugin-fixture-listener"],
        env=env,
    )
    assert show_enabled.exit_code == 0
    assert "'enabled': True" in show_enabled.stdout


def test_plugin_listener_registration_is_available_to_compiler(
    runner: CliRunner,
    machine_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Installing a listener plugin makes its platform available to compilation."""
    from uuid import uuid4
    from orcheo.listeners.compiler import compile_listener_subscriptions
    from orcheo.plugins import reset_plugin_loader_for_tests

    fixture_path = _copy_fixture(tmp_path, "listener_plugin")
    result = runner.invoke(
        app, ["plugin", "install", str(fixture_path)], env=machine_env
    )
    assert result.exit_code == 0

    for key, value in machine_env.items():
        monkeypatch.setenv(key, value)

    reset_plugin_loader_for_tests()
    subscriptions = compile_listener_subscriptions(
        uuid4(),
        uuid4(),
        {
            "index": {
                "listeners": [
                    {
                        "node_name": "fixture",
                        "platform": "fixture-listener",
                        "credential_ref": "fixture-credential",
                    }
                ]
            }
        },
    )
    assert len(subscriptions) == 1
    assert subscriptions[0].platform == "fixture-listener"
    assert subscriptions[0].bot_identity_key == "fixture-listener:fixture-credential"


def test_plugin_uninstall_rebuilds_environment(
    runner: CliRunner, machine_env: dict[str, str], tmp_path: Path
) -> None:
    """Uninstall removes the plugin from desired and resolved state."""
    fixture_path = _copy_fixture(tmp_path, "node_plugin")
    result = runner.invoke(
        app, ["plugin", "install", str(fixture_path)], env=machine_env
    )
    assert result.exit_code == 0

    uninstall_result = runner.invoke(
        app,
        ["plugin", "uninstall", "orcheo-plugin-fixture-node", "--force"],
        env=machine_env,
    )
    assert uninstall_result.exit_code == 0
    payload = json.loads(uninstall_result.stdout)
    assert payload["name"] == "orcheo-plugin-fixture-node"

    list_result = runner.invoke(app, ["plugin", "list"], env=machine_env)
    assert list_result.exit_code == 0
    assert list_result.stdout.strip() == "(empty)"


def test_plugin_uninstall_prompt_happens_before_mutation(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Declining uninstall confirmation should leave plugin state unchanged."""
    fixture_path = _copy_fixture(tmp_path, "node_plugin")
    install_result = runner.invoke(
        app, ["plugin", "install", str(fixture_path)], env=env
    )
    assert install_result.exit_code == 0

    uninstall_result = runner.invoke(
        app,
        ["plugin", "uninstall", "orcheo-plugin-fixture-node"],
        env=env,
        input="n\n",
    )
    assert uninstall_result.exit_code == 1

    show_result = runner.invoke(
        app,
        ["plugin", "show", "orcheo-plugin-fixture-node"],
        env=env,
    )
    assert show_result.exit_code == 0


def test_plugin_update_prompt_happens_before_mutation(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Declining update confirmation should not rebuild to newer plugin version."""
    fixture_path = _copy_fixture(tmp_path, "node_plugin")
    install_result = runner.invoke(
        app, ["plugin", "install", str(fixture_path)], env=env
    )
    assert install_result.exit_code == 0

    pyproject_path = fixture_path / "pyproject.toml"
    pyproject_path.write_text(
        pyproject_path.read_text(encoding="utf-8").replace(
            'version = "0.1.0"', 'version = "0.2.0"'
        ),
        encoding="utf-8",
    )

    update_result = runner.invoke(
        app,
        ["plugin", "update", "orcheo-plugin-fixture-node"],
        env=env,
        input="n\n",
    )
    assert update_result.exit_code == 1

    show_result = runner.invoke(
        app,
        ["plugin", "show", "orcheo-plugin-fixture-node"],
        env=env,
    )
    assert show_result.exit_code == 0
    assert "0.1.0" in show_result.stdout


def test_plugin_doctor_reports_broken_plugin(
    runner: CliRunner, machine_env: dict[str, str], tmp_path: Path
) -> None:
    """Doctor surfaces import failures as errors."""
    fixture_path = _copy_fixture(tmp_path, "broken_plugin")
    install_result = runner.invoke(
        app,
        ["plugin", "install", str(fixture_path)],
        env=machine_env,
    )
    assert install_result.exit_code == 0

    doctor_result = runner.invoke(app, ["plugin", "doctor"], env=machine_env)
    assert doctor_result.exit_code == 1
    payload = json.loads(doctor_result.stdout)
    assert payload["has_errors"] is True
    assert any("failed to import" in check["message"] for check in payload["checks"])


def test_plugin_install_incompatible_manifest_is_transactional(
    runner: CliRunner, machine_env: dict[str, str], tmp_path: Path
) -> None:
    """Failed installs do not leave desired state or an activated plugin venv behind."""
    fixture_path = _copy_fixture(tmp_path, "incompatible_plugin")
    result = runner.invoke(
        app, ["plugin", "install", str(fixture_path)], env=machine_env
    )
    assert result.exit_code != 0

    plugin_dir = _plugin_dir(machine_env)
    state_file = plugin_dir / "plugins.toml"
    lock_file = plugin_dir / "plugin-lock.toml"

    assert not state_file.exists() or not state_file.read_text(encoding="utf-8").strip()
    assert not lock_file.exists() or not lock_file.read_text(encoding="utf-8").strip()

    list_result = runner.invoke(app, ["plugin", "list"], env=machine_env)
    assert list_result.exit_code == 0
    assert list_result.stdout.strip() == "(empty)"


def test_plugin_list_human_mode_render_table(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Human mode renders a table instead of machine output (line 91)."""
    mock_rows = [
        {
            "name": "orcheo-plugin-fixture-node",
            "enabled": True,
            "status": "installed",
            "version": "0.1.0",
            "exports": ["nodes"],
            "source": "/path/to/plugin",
        }
    ]
    with patch("orcheo_sdk.cli.plugin.list_plugins_data", return_value=mock_rows):
        result = runner.invoke(app, ["plugin", "list"], env=env)
    assert result.exit_code == 0
    # Human mode shows a rich table (machine mode shows markdown)
    assert "Installed Plugins" in result.stdout
    assert "orcheo-plugin-f" in result.stdout


def test_plugin_show_error_nonexistent(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """show_plugin raises BadParameter when plugin not found (lines 116-117)."""
    result = runner.invoke(
        app, ["plugin", "show", "nonexistent-plugin"], env=machine_env
    )
    assert result.exit_code != 0


def test_plugin_update_no_name_no_all_raises(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """update with no name and no --all raises BadParameter (line 206)."""
    result = runner.invoke(app, ["plugin", "update"], env=machine_env)
    assert result.exit_code != 0
    assert "Provide a plugin name" in result.output


def test_plugin_update_single_preview_error(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """update single raises BadParameter on preview PluginError (lines 209-210)."""
    result = runner.invoke(
        app, ["plugin", "update", "nonexistent-plugin"], env=machine_env
    )
    assert result.exit_code != 0


def test_plugin_update_single_machine_mode_output(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """update single in machine mode prints JSON and returns (lines 226-232)."""
    mock_impact = MagicMock()
    mock_impact.prompt_required = False
    mock_impact.activation_mode = "silent_hot_reload"
    mock_preview = {"name": "my-plugin", "impact": mock_impact}
    mock_plugin = {"name": "my-plugin", "version": "0.2.0"}
    mock_payload = {"plugin": mock_plugin, "impact": mock_impact}

    with patch(
        "orcheo_sdk.cli.plugin.preview_update_plugin_data",
        return_value=mock_preview,
    ):
        with patch(
            "orcheo_sdk.cli.plugin.update_plugin_data",
            return_value=mock_payload,
        ):
            result = runner.invoke(
                app,
                ["plugin", "update", "my-plugin", "--force"],
                env=machine_env,
            )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["plugin"]["name"] == "my-plugin"
    assert "impact" in payload


def test_plugin_update_single_error_in_update_data(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """update_plugin_data raises BadParameter (lines 223-224)."""
    from orcheo.plugins.manager import PluginError

    mock_impact = MagicMock()
    mock_impact.prompt_required = False
    mock_impact.activation_mode = "silent_hot_reload"
    mock_preview = {"name": "my-plugin", "impact": mock_impact}

    with patch(
        "orcheo_sdk.cli.plugin.preview_update_plugin_data",
        return_value=mock_preview,
    ):
        with patch(
            "orcheo_sdk.cli.plugin.update_plugin_data",
            side_effect=PluginError("update failed"),
        ):
            result = runner.invoke(
                app,
                ["plugin", "update", "my-plugin", "--force"],
                env=machine_env,
            )
    assert result.exit_code != 0


def test_plugin_update_all_no_plugins_machine_mode(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """update --all with no installed plugins outputs empty JSON (lines 181-191)."""
    with patch(
        "orcheo_sdk.cli.plugin.preview_update_all_plugins_data",
        return_value=[],
    ):
        with patch(
            "orcheo_sdk.cli.plugin.update_all_plugins_data",
            return_value=[],
        ):
            result = runner.invoke(
                app, ["plugin", "update", "--all", "--force"], env=machine_env
            )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == []


def test_plugin_update_all_human_mode(runner: CliRunner, env: dict[str, str]) -> None:
    """update --all in human mode uses render_json (lines 192-202)."""
    mock_impact = MagicMock()
    mock_impact.prompt_required = False
    mock_impact.activation_mode = "silent_hot_reload"
    mock_preview = [{"name": "my-plugin", "impact": mock_impact}]
    mock_plugin = {"name": "my-plugin", "version": "0.2.0"}
    mock_payload = [{"plugin": mock_plugin, "impact": mock_impact}]

    with patch(
        "orcheo_sdk.cli.plugin.preview_update_all_plugins_data",
        return_value=mock_preview,
    ):
        with patch(
            "orcheo_sdk.cli.plugin.update_all_plugins_data",
            return_value=mock_payload,
        ):
            result = runner.invoke(
                app, ["plugin", "update", "--all", "--force"], env=env
            )
    assert result.exit_code == 0


def test_plugin_update_all_preview_error(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """update --all raises BadParameter on preview PluginError (lines 165-166)."""
    from orcheo.plugins.manager import PluginError

    with patch(
        "orcheo_sdk.cli.plugin.preview_update_all_plugins_data",
        side_effect=PluginError("preview failed"),
    ):
        result = runner.invoke(
            app, ["plugin", "update", "--all", "--force"], env=machine_env
        )
    assert result.exit_code != 0


def test_plugin_update_all_update_error(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """update --all raises BadParameter on update_all_plugins_data PluginError."""
    from orcheo.plugins.manager import PluginError

    mock_impact = MagicMock()
    mock_impact.prompt_required = False
    mock_impact.activation_mode = "silent_hot_reload"
    mock_preview = [{"name": "some-plugin", "impact": mock_impact}]

    with patch(
        "orcheo_sdk.cli.plugin.preview_update_all_plugins_data",
        return_value=mock_preview,
    ):
        with patch(
            "orcheo_sdk.cli.plugin.update_all_plugins_data",
            side_effect=PluginError("update all failed"),
        ):
            result = runner.invoke(
                app, ["plugin", "update", "--all", "--force"], env=machine_env
            )
    assert result.exit_code != 0


def test_plugin_uninstall_preview_error(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """uninstall raises BadParameter on preview PluginError (lines 250-251)."""
    result = runner.invoke(
        app, ["plugin", "uninstall", "nonexistent-plugin", "--force"], env=machine_env
    )
    assert result.exit_code != 0


def test_plugin_uninstall_data_error(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """uninstall_plugin_data raises BadParameter (lines 261-262)."""
    from orcheo.plugins.manager import PluginError

    mock_impact = MagicMock()
    mock_impact.prompt_required = False
    mock_impact.activation_mode = "silent_hot_reload"
    mock_preview = {"name": "my-plugin", "impact": mock_impact}

    with patch(
        "orcheo_sdk.cli.plugin.preview_uninstall_plugin_data",
        return_value=mock_preview,
    ):
        with patch(
            "orcheo_sdk.cli.plugin.uninstall_plugin_data",
            side_effect=PluginError("uninstall failed"),
        ):
            result = runner.invoke(
                app,
                ["plugin", "uninstall", "my-plugin", "--force"],
                env=machine_env,
            )
    assert result.exit_code != 0


def test_plugin_uninstall_human_mode_output(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Uninstall in human mode prints confirmation message (lines 267-268)."""
    mock_impact = MagicMock()
    mock_impact.prompt_required = False
    mock_impact.activation_mode = "silent_hot_reload"
    mock_preview = {"name": "my-plugin", "impact": mock_impact}
    mock_payload = {"name": "my-plugin", "impact": mock_impact}

    with patch(
        "orcheo_sdk.cli.plugin.preview_uninstall_plugin_data",
        return_value=mock_preview,
    ):
        with patch(
            "orcheo_sdk.cli.plugin.uninstall_plugin_data",
            return_value=mock_payload,
        ):
            result = runner.invoke(
                app,
                ["plugin", "uninstall", "my-plugin", "--force"],
                env=env,
            )
    assert result.exit_code == 0
    assert "my-plugin" in result.output


def test_plugin_enable_preview_error(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """enable raises BadParameter on preview PluginError (lines 284-285)."""
    result = runner.invoke(
        app, ["plugin", "enable", "nonexistent-plugin", "--force"], env=machine_env
    )
    assert result.exit_code != 0


def test_plugin_enable_data_error(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """enable_plugin_data raises BadParameter (lines 295-296)."""
    from orcheo.plugins.manager import PluginError

    mock_impact = MagicMock()
    mock_impact.prompt_required = False
    mock_impact.activation_mode = "silent_hot_reload"
    mock_preview = {"name": "my-plugin", "impact": mock_impact}

    with patch(
        "orcheo_sdk.cli.plugin.preview_enable_plugin_data",
        return_value=mock_preview,
    ):
        with patch(
            "orcheo_sdk.cli.plugin.enable_plugin_data",
            side_effect=PluginError("enable failed"),
        ):
            result = runner.invoke(
                app,
                ["plugin", "enable", "my-plugin", "--force"],
                env=machine_env,
            )
    assert result.exit_code != 0


def test_plugin_enable_machine_mode_output(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """Enable in machine mode prints JSON (lines 299-300)."""
    mock_impact = MagicMock()
    mock_impact.prompt_required = False
    mock_impact.activation_mode = "silent_hot_reload"
    mock_preview = {"name": "my-plugin", "impact": mock_impact}
    mock_payload = {"name": "my-plugin", "impact": mock_impact}

    with patch(
        "orcheo_sdk.cli.plugin.preview_enable_plugin_data",
        return_value=mock_preview,
    ):
        with patch(
            "orcheo_sdk.cli.plugin.enable_plugin_data",
            return_value=mock_payload,
        ):
            result = runner.invoke(
                app,
                ["plugin", "enable", "my-plugin", "--force"],
                env=machine_env,
            )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["name"] == "my-plugin"


def test_plugin_disable_preview_error(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """disable raises BadParameter on preview PluginError (lines 318-319)."""
    result = runner.invoke(
        app, ["plugin", "disable", "nonexistent-plugin", "--force"], env=machine_env
    )
    assert result.exit_code != 0


def test_plugin_disable_data_error(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """disable_plugin_data raises BadParameter (lines 329-330)."""
    from orcheo.plugins.manager import PluginError

    mock_impact = MagicMock()
    mock_impact.prompt_required = False
    mock_impact.activation_mode = "silent_hot_reload"
    mock_preview = {"name": "my-plugin", "impact": mock_impact}

    with patch(
        "orcheo_sdk.cli.plugin.preview_disable_plugin_data",
        return_value=mock_preview,
    ):
        with patch(
            "orcheo_sdk.cli.plugin.disable_plugin_data",
            side_effect=PluginError("disable failed"),
        ):
            result = runner.invoke(
                app,
                ["plugin", "disable", "my-plugin", "--force"],
                env=machine_env,
            )
    assert result.exit_code != 0


def test_plugin_disable_machine_mode_output(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """Disable in machine mode prints JSON (lines 333-334)."""
    mock_impact = MagicMock()
    mock_impact.prompt_required = False
    mock_impact.activation_mode = "silent_hot_reload"
    mock_preview = {"name": "my-plugin", "impact": mock_impact}
    mock_payload = {"name": "my-plugin", "impact": mock_impact}

    with patch(
        "orcheo_sdk.cli.plugin.preview_disable_plugin_data",
        return_value=mock_preview,
    ):
        with patch(
            "orcheo_sdk.cli.plugin.disable_plugin_data",
            return_value=mock_payload,
        ):
            result = runner.invoke(
                app,
                ["plugin", "disable", "my-plugin", "--force"],
                env=machine_env,
            )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["name"] == "my-plugin"


def test_plugin_doctor_human_mode_with_errors(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Doctor in human mode renders a table and exits 1 on errors (lines 348-360)."""
    mock_payload = {
        "has_errors": True,
        "checks": [
            {
                "name": "some-check",
                "severity": "ERROR",
                "ok": False,
                "message": "Something failed",
            }
        ],
    }
    with patch("orcheo_sdk.cli.plugin.doctor_plugins_data", return_value=mock_payload):
        result = runner.invoke(app, ["plugin", "doctor"], env=env)
    assert result.exit_code == 1
    assert "Plugin Doctor" in result.output


def test_plugin_doctor_human_mode_no_errors(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Doctor in human mode exits 0 when no errors (line 358 False branch)."""
    mock_payload = {
        "has_errors": False,
        "checks": [
            {
                "name": "some-check",
                "severity": "INFO",
                "ok": True,
                "message": "All good",
            }
        ],
    }
    with patch("orcheo_sdk.cli.plugin.doctor_plugins_data", return_value=mock_payload):
        result = runner.invoke(app, ["plugin", "doctor"], env=env)
    assert result.exit_code == 0
    assert "Plugin Doctor" in result.output


def test_plugin_doctor_machine_mode_no_errors(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """Doctor in machine mode exits 0 and returns when no errors (line 348)."""
    mock_payload = {
        "has_errors": False,
        "checks": [
            {
                "name": "some-check",
                "severity": "INFO",
                "ok": True,
                "message": "All good",
            }
        ],
    }
    with patch("orcheo_sdk.cli.plugin.doctor_plugins_data", return_value=mock_payload):
        result = runner.invoke(app, ["plugin", "doctor"], env=machine_env)
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["has_errors"] is False


def test_maybe_confirm_machine_mode_prompt_required_raises(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """_maybe_confirm raises BadParameter in machine mode when prompt is needed."""
    mock_impact = MagicMock()
    mock_impact.prompt_required = True
    mock_impact.activation_mode = "requires_restart"
    mock_preview = {"name": "my-plugin", "impact": mock_impact}

    with patch(
        "orcheo_sdk.cli.plugin.preview_update_plugin_data",
        return_value=mock_preview,
    ):
        # No --force in machine mode with prompt_required=True → BadParameter
        result = runner.invoke(
            app,
            ["plugin", "update", "my-plugin"],
            env=machine_env,
        )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# plugin init
# ---------------------------------------------------------------------------


def test_plugin_init_machine_mode(
    runner: CliRunner, machine_env: dict[str, str], tmp_path: Path
) -> None:
    """init in machine mode outputs JSON with path and name."""
    result = runner.invoke(
        app,
        ["plugin", "init", "hello", "--output-dir", str(tmp_path)],
        env=machine_env,
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["name"] == "orcheo-plugin-hello"
    assert Path(payload["path"]).is_dir()


def test_plugin_init_human_mode(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """init in human mode prints a success message."""
    result = runner.invoke(
        app,
        ["plugin", "init", "hello", "--output-dir", str(tmp_path)],
        env=env,
    )
    assert result.exit_code == 0
    assert "orcheo-plugin-hello" in result.output.replace("\n", "")


def test_plugin_init_creates_files(
    runner: CliRunner, machine_env: dict[str, str], tmp_path: Path
) -> None:
    """init creates a complete plugin skeleton."""
    result = runner.invoke(
        app,
        ["plugin", "init", "acme", "--output-dir", str(tmp_path)],
        env=machine_env,
    )
    assert result.exit_code == 0
    root = tmp_path / "orcheo-plugin-acme"
    assert (root / "pyproject.toml").is_file()
    assert (root / "src" / "orcheo_plugin_acme" / "__init__.py").is_file()
    assert (root / "tests" / "test_plugin.py").is_file()


def test_plugin_init_with_author_and_description(
    runner: CliRunner, machine_env: dict[str, str], tmp_path: Path
) -> None:
    """init passes author and description through to the scaffold."""
    result = runner.invoke(
        app,
        [
            "plugin",
            "init",
            "acme",
            "--author",
            "Jane Doe",
            "--description",
            "My cool plugin",
            "--output-dir",
            str(tmp_path),
        ],
        env=machine_env,
    )
    assert result.exit_code == 0
    pyproject = (tmp_path / "orcheo-plugin-acme" / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "Jane Doe" in pyproject
    assert "My cool plugin" in pyproject


def test_plugin_init_with_custom_exports(
    runner: CliRunner, machine_env: dict[str, str], tmp_path: Path
) -> None:
    """init passes comma-separated exports to the manifest."""
    result = runner.invoke(
        app,
        [
            "plugin",
            "init",
            "acme",
            "--exports",
            "nodes,edges",
            "--output-dir",
            str(tmp_path),
        ],
        env=machine_env,
    )
    assert result.exit_code == 0
    manifest = (
        tmp_path
        / "orcheo-plugin-acme"
        / "src"
        / "orcheo_plugin_acme"
        / "orcheo_plugin.toml"
    ).read_text(encoding="utf-8")
    assert '"nodes"' in manifest
    assert '"edges"' in manifest


def test_plugin_init_invalid_name(
    runner: CliRunner, machine_env: dict[str, str], tmp_path: Path
) -> None:
    """init rejects invalid plugin names."""
    result = runner.invoke(
        app,
        ["plugin", "init", "Bad-Name", "--output-dir", str(tmp_path)],
        env=machine_env,
    )
    assert result.exit_code != 0


def test_plugin_init_duplicate_directory(
    runner: CliRunner, machine_env: dict[str, str], tmp_path: Path
) -> None:
    """init rejects when target directory already exists."""
    result1 = runner.invoke(
        app,
        ["plugin", "init", "hello", "--output-dir", str(tmp_path)],
        env=machine_env,
    )
    assert result1.exit_code == 0

    result2 = runner.invoke(
        app,
        ["plugin", "init", "hello", "--output-dir", str(tmp_path)],
        env=machine_env,
    )
    assert result2.exit_code != 0
