from __future__ import annotations
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner
from orcheo.plugins import PluginImpactSummary
from orcheo_sdk.cli import plugin as plugin_module


class DummyState:
    def __init__(self, human: bool) -> None:
        self.human = human
        self.console = Console(record=True)


def test_resolve_stack_project_dir_prefers_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack_dir = tmp_path / "stack"
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))
    assert plugin_module._resolve_stack_project_dir() == stack_dir


def test_resolve_stack_project_dir_defaults_to_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ORCHEO_STACK_DIR", raising=False)
    monkeypatch.setattr(plugin_module.Path, "home", staticmethod(lambda: tmp_path))
    assert plugin_module._resolve_stack_project_dir() == tmp_path / ".orcheo" / "stack"


def test_stack_compose_base_args_requires_compose_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))
    with pytest.raises(typer.BadParameter):
        plugin_module._stack_compose_base_args()


def test_stack_compose_base_args_returns_args(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    compose_file = stack_dir / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))
    assert plugin_module._stack_compose_base_args() == [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "--project-directory",
        str(stack_dir),
    ]


def test_normalize_runtime_invalid() -> None:
    with pytest.raises(typer.BadParameter):
        plugin_module._normalize_runtime("invalid")


def test_use_stack_runtime_variants(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    (stack_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(plugin_module, "_resolve_stack_project_dir", lambda: stack_dir)
    assert plugin_module._use_stack_runtime("local") is False
    assert plugin_module._use_stack_runtime("stack") is True
    assert plugin_module._use_stack_runtime("auto") is True


def test_stack_runtime_uses_lean_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lean_dir = tmp_path / "lean"
    lean_dir.mkdir()
    compose_file = lean_dir / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.delenv("ORCHEO_STACK_DIR", raising=False)
    monkeypatch.setenv("ORCHEO_LEAN_DIR", str(lean_dir))

    assert plugin_module._use_stack_runtime("auto") is True
    assert plugin_module._stack_compose_base_args() == [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "--project-directory",
        str(lean_dir),
    ]


def test_run_stack_subprocess_requires_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_module.shutil, "which", lambda _: None)
    with pytest.raises(typer.BadParameter):
        plugin_module._run_stack_subprocess(["echo"])


def test_run_stack_subprocess_handles_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_module.shutil, "which", lambda _: "/usr/bin/docker")

    def fake_run(
        command: list[str], *, check: bool, capture_output: bool, text: bool
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 2, stdout="output", stderr="oops")

    monkeypatch.setattr(plugin_module.subprocess, "run", fake_run)
    with pytest.raises(typer.BadParameter) as excinfo:
        plugin_module._run_stack_subprocess(["cmd"])
    assert "oops" in str(excinfo.value)


def test_stack_plugin_command_exec_and_run(monkeypatch: pytest.MonkeyPatch) -> None:
    base_args = ["docker", "compose", "-f", "compose", "--project-directory", "stack"]
    monkeypatch.setattr(plugin_module, "_stack_compose_base_args", lambda: base_args)
    monkeypatch.setattr(plugin_module, "_running_stack_services", lambda: {"backend"})
    exec_command = plugin_module._stack_plugin_command(args=["list"], human=True)
    assert "exec" in exec_command
    monkeypatch.setattr(plugin_module, "_running_stack_services", lambda: set())
    run_command = plugin_module._stack_plugin_command(args=["list"], human=False)
    assert "run" in run_command


def test_run_stack_plugin_passthrough_emits_output_and_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plugin_module, "_stack_plugin_command", lambda *, args, human: ["cmd"]
    )

    def fake_run(
        command: list[str], *, expected_exit_codes: set[int] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(plugin_module, "_run_stack_subprocess", fake_run)
    echoed: list[str] = []

    def fake_echo(message: str) -> None:
        echoed.append(message)

    monkeypatch.setattr(plugin_module.typer, "echo", fake_echo)
    state = DummyState(human=False)
    plugin_module._run_stack_plugin_passthrough(args=["list"], state=state)
    assert echoed == ["ok"]


def test_run_stack_plugin_passthrough_doctor_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plugin_module, "_stack_plugin_command", lambda *, args, human: ["cmd"]
    )

    def fake_run(
        command: list[str], *, expected_exit_codes: set[int] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(plugin_module, "_run_stack_subprocess", fake_run)
    with pytest.raises(typer.Exit):
        plugin_module._run_stack_plugin_passthrough(
            args=["doctor"], state=DummyState(human=False)
        )


def test_run_stack_subprocess_streaming_requires_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_module.shutil, "which", lambda _: None)
    with pytest.raises(typer.BadParameter):
        plugin_module._run_stack_subprocess_streaming(["cmd"])


def test_run_stack_subprocess_streaming_with_explicit_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_module.shutil, "which", lambda _: "/usr/bin/docker")

    def fake_run(
        command: list[str], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(plugin_module.subprocess, "run", fake_run)
    returncode = plugin_module._run_stack_subprocess_streaming(
        ["cmd"], expected_exit_codes={0, 1}
    )
    assert returncode == 0


def test_run_stack_plugin_passthrough_human_doctor_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plugin_module, "_stack_plugin_command", lambda *, args, human: ["cmd"]
    )

    def fake_streaming(
        command: list[str], *, expected_exit_codes: set[int] | None = None
    ) -> int:
        return 1

    monkeypatch.setattr(
        plugin_module, "_run_stack_subprocess_streaming", fake_streaming
    )
    state = DummyState(human=True)
    with pytest.raises(typer.Exit) as excinfo:
        plugin_module._run_stack_plugin_passthrough(args=["doctor"], state=state)
    assert excinfo.value.exit_code == 1


def test_is_local_plugin_ref_detects_variants(tmp_path: Path) -> None:
    file_path = tmp_path / "plugin.txt"
    file_path.write_text("x", encoding="utf-8")
    assert plugin_module._is_local_plugin_ref(str(file_path))
    assert plugin_module._is_local_plugin_ref("./cli")
    assert plugin_module._is_local_plugin_ref("plugin.whl")
    assert plugin_module._is_local_plugin_ref("group/plugin")
    assert not plugin_module._is_local_plugin_ref(
        "git+https://github.com/AI-Colleagues/orcheo-plugin-wecom-listener.git"
    )
    assert not plugin_module._is_local_plugin_ref(
        "git@github.com:AI-Colleagues/orcheo-plugin-wecom-listener.git"
    )


def test_run_stack_plugin_json_parses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plugin_module, "_stack_plugin_command", lambda *, args, human: ["cmd"]
    )

    def fake_run(
        command: list[str], *, expected_exit_codes: set[int] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 0, stdout='{"ok": true}\n', stderr=""
        )

    monkeypatch.setattr(plugin_module, "_run_stack_subprocess", fake_run)
    assert plugin_module._run_stack_plugin_json(["install"]) == {"ok": True}
    monkeypatch.setattr(
        plugin_module,
        "_run_stack_subprocess",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 0, stdout="", stderr=""
        ),
    )
    assert plugin_module._run_stack_plugin_json(["install"]) is None


def test_stack_service_container_id_reads_compose_ps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_module, "_stack_compose_base_args", lambda: ["docker"])
    monkeypatch.setattr(
        plugin_module,
        "_run_stack_subprocess",
        lambda command, expected_exit_codes=None: subprocess.CompletedProcess(
            command, 0, stdout="abc123\n", stderr=""
        ),
    )
    assert plugin_module._stack_service_container_id("backend") == "abc123"


def test_copy_local_plugin_ref_into_stack_stages_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "plugin"
    source_dir.mkdir()
    (source_dir / "pyproject.toml").write_text(
        "[project]\nname='demo'\n", encoding="utf-8"
    )

    monkeypatch.setattr(plugin_module, "_running_stack_services", lambda: {"backend"})
    monkeypatch.setattr(
        plugin_module, "_stack_service_container_id", lambda service: "cid-1"
    )
    monkeypatch.setattr(
        plugin_module, "_stack_compose_base_args", lambda: ["docker", "compose"]
    )
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], *, expected_exit_codes: set[int] | None = None
    ) -> subprocess.CompletedProcess[str]:
        del expected_exit_codes
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(plugin_module, "_run_stack_subprocess", fake_run)

    staged_ref = plugin_module._copy_local_plugin_ref_into_stack(str(source_dir))

    expected_parent = (
        f"/data/plugin-sources/"
        f"{plugin_module.hash_install_source(str(source_dir.resolve()))}"
    )
    assert staged_ref == f"{expected_parent}/plugin"
    assert calls[0][:6] == ["docker", "compose", "exec", "-T", "backend", "sh"]
    assert calls[1] == [
        "docker",
        "cp",
        str(source_dir.resolve()),
        f"cid-1:{expected_parent}",
    ]


def test_copy_local_plugin_ref_into_stack_requires_running_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "plugin"
    source_dir.mkdir()
    monkeypatch.setattr(plugin_module, "_running_stack_services", lambda: set())
    with pytest.raises(typer.BadParameter):
        plugin_module._copy_local_plugin_ref_into_stack(str(source_dir))


def test_copy_local_plugin_ref_into_stack_requires_source(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "plugin"
    with pytest.raises(typer.BadParameter, match="Local plugin reference not found"):
        plugin_module._copy_local_plugin_ref_into_stack(str(missing))


def test_copy_local_plugin_ref_into_stack_requires_container_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "plugin"
    source_dir.mkdir()

    monkeypatch.setattr(plugin_module, "_running_stack_services", lambda: {"backend"})
    monkeypatch.setattr(
        plugin_module, "_stack_service_container_id", lambda service: None
    )
    with pytest.raises(
        typer.BadParameter,
        match="Could not resolve the backend container id for the running stack",
    ):
        plugin_module._copy_local_plugin_ref_into_stack(str(source_dir))


def test_restart_running_stack_services(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_module, "_running_stack_services", lambda: set())
    monkeypatch.setattr(
        plugin_module, "_run_stack_subprocess", lambda *args, **kwargs: None
    )
    plugin_module._restart_running_stack_services(DummyState(human=False))
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], *, expected_exit_codes: set[int] | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    services = {"backend", "worker"}
    monkeypatch.setattr(plugin_module, "_running_stack_services", lambda: services)
    monkeypatch.setattr(plugin_module, "_stack_compose_base_args", lambda: ["docker"])
    monkeypatch.setattr(plugin_module, "_run_stack_subprocess", fake_run)
    state = DummyState(human=True)
    plugin_module._restart_running_stack_services(state)
    assert calls
    assert "Restarted stack services" in state.console.export_text()


def test_payload_requires_restart_checks_types() -> None:
    assert not plugin_module._payload_requires_restart("nope")
    assert plugin_module._payload_requires_restart(
        {"impact": {"restart_required": True}}
    )
    assert plugin_module._payload_requires_restart(
        [
            {"impact": {"restart_required": False}},
            {"impact": {"restart_required": True}},
        ]
    )


def test_run_stack_mutation_force_uses_json_and_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(human=False)
    calls: list[list[str]] = []
    restarts: list[DummyState] = []

    def fake_json(args: list[str]) -> dict[str, object]:
        calls.append(args)
        return {"impact": {"restart_required": True}}

    monkeypatch.setattr(plugin_module, "_run_stack_plugin_json", fake_json)
    monkeypatch.setattr(
        plugin_module,
        "_restart_running_stack_services",
        lambda console_state: restarts.append(console_state),
    )

    payload = plugin_module._run_stack_mutation(
        args=["update", "pkg"],
        state=state,
        force=True,
    )

    assert calls == [["update", "pkg", "--force"]]
    assert payload == {"impact": {"restart_required": True}}
    assert restarts == [state]


def test_run_stack_mutation_human_without_force_uses_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(human=True)
    calls: list[tuple[list[str], DummyState]] = []
    monkeypatch.setattr(
        plugin_module,
        "_run_stack_plugin_passthrough",
        lambda *, args, state: calls.append((args, state)),
    )
    monkeypatch.setattr(
        plugin_module,
        "_run_stack_plugin_json",
        lambda args: pytest.fail("json path should not run"),
    )

    payload = plugin_module._run_stack_mutation(
        args=["disable", "pkg"],
        state=state,
        force=False,
    )

    assert payload is None
    assert calls == [(["disable", "pkg"], state)]


def test_impact_to_dict_and_render(monkeypatch: pytest.MonkeyPatch) -> None:
    impact = PluginImpactSummary(
        change_type="install",
        affected_component_kinds=["nodes"],
        affected_component_ids=["node"],
        activation_mode="silent",
        prompt_required=False,
        restart_required=True,
    )
    summary = plugin_module._impact_to_dict(impact)
    assert summary["restart_required"] is True
    printed: list[dict[str, object]] = []

    def fake_render(console: Console, payload: dict[str, object], title: str) -> None:
        printed.append(payload)

    monkeypatch.setattr(plugin_module, "render_json", fake_render)
    state = DummyState(human=True)
    plugin_module._render_impact(state, impact)
    assert printed


def test_maybe_confirm_handles_human_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    impact = SimpleNamespace(prompt_required=True)
    state = DummyState(human=True)
    monkeypatch.setattr(
        plugin_module.typer, "confirm", lambda prompt, default=False: False
    )
    with pytest.raises(typer.Exit):
        plugin_module._maybe_confirm(
            impact=impact,
            prompt_text="ok?",
            state=state,
            force=False,
        )
    monkeypatch.setattr(
        plugin_module.typer, "confirm", lambda prompt, default=False: True
    )
    plugin_module._maybe_confirm(
        impact=impact,
        prompt_text="ok?",
        state=state,
        force=False,
    )
    plugin_module._maybe_confirm(
        impact=SimpleNamespace(prompt_required=True),
        prompt_text="ok?",
        state=state,
        force=True,
    )


def _impact_payload() -> dict[str, object]:
    return {
        "change_type": "update",
        "affected_component_kinds": [],
        "affected_component_ids": [],
        "activation_mode": "silent_hot_reload",
        "prompt_required": False,
        "restart_required": False,
    }


def test_render_update_single_payload_human_handles_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(human=True)
    rendered: list[tuple[str, dict[str, object], str]] = []
    monkeypatch.setattr(
        plugin_module,
        "render_json",
        lambda console, payload, title: rendered.append(("render", payload, title)),
    )
    impact_calls: list[object] = []
    monkeypatch.setattr(
        plugin_module,
        "_render_impact",
        lambda state_arg, impact: impact_calls.append(impact),
    )
    payload = {"plugin": {"name": "pkg"}, "impact": _impact_payload()}
    plugin_module._render_update_single_payload(state, payload)
    assert rendered and impact_calls


def test_render_update_single_payload_machine_mode_prints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(human=False)
    printed: list[dict[str, object]] = []
    monkeypatch.setattr(
        plugin_module,
        "print_json",
        lambda payload: printed.append(payload),
    )
    impact = PluginImpactSummary(
        change_type="install",
        affected_component_kinds=[],
        affected_component_ids=[],
        activation_mode="silent",
        prompt_required=False,
        restart_required=False,
    )
    plugin_module._render_update_single_payload(
        state, {"plugin": {"name": "pkg"}, "impact": impact}
    )
    assert printed
    assert printed[0]["impact"]["change_type"] == "install"


def test_update_plugins_in_stack_all_human_renders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(human=True)
    renders: list[tuple[list[dict[str, object]], str]] = []
    monkeypatch.setattr(
        plugin_module,
        "_run_stack_mutation",
        lambda *, args, state, force: [
            {"plugin": {"name": "pkg"}, "impact": _impact_payload()}
        ],
    )
    monkeypatch.setattr(
        plugin_module,
        "render_json",
        lambda console, payload, title: renders.append((payload, title)),
    )
    plugin_module._update_plugins_in_stack(
        state=state, name=None, all_plugins=True, force=False
    )
    assert renders


def test_update_plugins_in_stack_requires_name() -> None:
    with pytest.raises(typer.BadParameter):
        plugin_module._update_plugins_in_stack(
            state=DummyState(human=False), name=None, all_plugins=False, force=False
        )


def test_update_plugins_in_stack_single_plugin_triggers_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(human=False)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        plugin_module,
        "_run_stack_mutation",
        lambda *, args, state, force: {
            "plugin": {"name": "pkg"},
            "impact": _impact_payload(),
        },
    )
    monkeypatch.setattr(
        plugin_module,
        "_render_update_single_payload",
        lambda state_arg, payload: calls.append(payload),
    )
    plugin_module._update_plugins_in_stack(
        state=state, name="pkg", all_plugins=False, force=True
    )
    assert calls


def test_list_command_uses_stack_passthrough(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin_module, "_use_stack_runtime", lambda runtime: True)
    monkeypatch.setattr(plugin_module, "_state", lambda ctx: DummyState(human=True))
    recorded: list[tuple[list[str], DummyState]] = []

    def fake_passthrough(*, args: list[str], state: DummyState) -> None:
        recorded.append((args, state))

    monkeypatch.setattr(
        plugin_module, "_run_stack_plugin_passthrough", fake_passthrough
    )
    runner.invoke(plugin_module.plugin_app, ["list"], env=env)
    assert recorded and recorded[0][0] == ["list"]


def test_show_command_uses_stack_passthrough(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin_module, "_use_stack_runtime", lambda runtime: True)
    monkeypatch.setattr(plugin_module, "_state", lambda ctx: DummyState(human=True))
    recorded: list[tuple[list[str], DummyState]] = []

    def fake_passthrough(*, args: list[str], state: DummyState) -> None:
        recorded.append((args, state))

    monkeypatch.setattr(
        plugin_module, "_run_stack_plugin_passthrough", fake_passthrough
    )
    runner.invoke(plugin_module.plugin_app, ["show", "ore"], env=env)
    assert recorded and recorded[0][0] == ["show", "ore"]


def test_install_command_stack_path_renders_human_output(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin_module, "_use_stack_runtime", lambda runtime: True)
    monkeypatch.setattr(plugin_module, "_state", lambda ctx: DummyState(human=True))
    monkeypatch.setattr(plugin_module, "_is_local_plugin_ref", lambda ref: False)
    passthrough_calls: list[list[str]] = []

    def fake_passthrough(*, args: list[str], state: DummyState) -> None:
        passthrough_calls.append(args)

    monkeypatch.setattr(
        plugin_module, "_run_stack_plugin_passthrough", fake_passthrough
    )
    restarted: list[bool] = []
    monkeypatch.setattr(
        plugin_module,
        "_restart_running_stack_services",
        lambda state: restarted.append(True),
    )
    runner.invoke(plugin_module.plugin_app, ["install", "pkg"], env=env)
    assert passthrough_calls == [["install", "pkg"]]
    assert restarted


@pytest.mark.parametrize(
    ("command_args", "expected_args", "expected_force"),
    [
        (["update", "pkg", "--runtime", "stack", "--force"], ["update", "pkg"], True),
        (
            ["update", "--all", "--runtime", "stack", "--force"],
            ["update", "--all"],
            True,
        ),
        (
            ["uninstall", "pkg", "--runtime", "stack", "--force"],
            ["uninstall", "pkg"],
            True,
        ),
        (["enable", "pkg", "--runtime", "stack", "--force"], ["enable", "pkg"], True),
        (
            ["disable", "pkg", "--runtime", "stack", "--force"],
            ["disable", "pkg"],
            True,
        ),
    ],
)
def test_stack_runtime_mutation_commands_route_through_stack_runner(
    runner: CliRunner,
    env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    command_args: list[str],
    expected_args: list[str],
    expected_force: bool,
) -> None:
    monkeypatch.setattr(plugin_module, "_use_stack_runtime", lambda runtime: True)
    monkeypatch.setattr(plugin_module, "_state", lambda ctx: DummyState(human=False))
    calls: list[tuple[list[str], bool]] = []

    def fake_run_stack_mutation(
        *,
        args: list[str],
        state: DummyState,
        force: bool,
    ) -> dict[str, object]:
        del state
        calls.append((args, force))
        if args[0] == "update":
            if "--all" in args:
                return [
                    {"plugin": {"name": "pkg"}, "impact": {"restart_required": False}}
                ]
            return {
                "plugin": {"name": "pkg"},
                "impact": {
                    "change_type": "update",
                    "affected_component_kinds": [],
                    "affected_component_ids": [],
                    "activation_mode": "silent_hot_reload",
                    "prompt_required": False,
                    "restart_required": False,
                },
            }
        return {
            "name": "pkg",
            "impact": {
                "change_type": args[0],
                "affected_component_kinds": [],
                "affected_component_ids": [],
                "activation_mode": "silent_hot_reload",
                "prompt_required": False,
                "restart_required": False,
            },
        }

    monkeypatch.setattr(plugin_module, "_run_stack_mutation", fake_run_stack_mutation)

    result = runner.invoke(plugin_module.plugin_app, command_args, env=env)

    assert result.exit_code == 0
    assert calls == [(expected_args, expected_force)]


@pytest.mark.parametrize(
    "command_fn",
    [
        plugin_module.uninstall_plugin,
        plugin_module.enable_plugin,
        plugin_module.disable_plugin,
    ],
    ids=["uninstall", "enable", "disable"],
)
def test_stack_runtime_command_returns_without_payload(
    monkeypatch: pytest.MonkeyPatch, command_fn: Callable
) -> None:
    state = DummyState(human=False)
    monkeypatch.setattr(plugin_module, "_state", lambda ctx: state)
    monkeypatch.setattr(plugin_module, "_use_stack_runtime", lambda runtime: True)
    monkeypatch.setattr(
        plugin_module,
        "_run_stack_mutation",
        lambda *, args, state, force: None,
    )
    command_fn(None, name="pkg", runtime="stack")


@pytest.mark.parametrize(
    ("command_fn", "expected_message"),
    [
        (plugin_module.uninstall_plugin, "Uninstalled plugin pkg"),
        (plugin_module.enable_plugin, "Enabled plugin pkg"),
        (plugin_module.disable_plugin, "Disabled plugin pkg"),
    ],
)
def test_stack_runtime_command_human_prints_and_renders(
    monkeypatch: pytest.MonkeyPatch,
    command_fn: Callable,
    expected_message: str,
) -> None:
    state = DummyState(human=True)
    monkeypatch.setattr(plugin_module, "_state", lambda ctx: state)
    monkeypatch.setattr(plugin_module, "_use_stack_runtime", lambda runtime: True)
    payload = {"name": "pkg", "impact": _impact_payload()}
    monkeypatch.setattr(
        plugin_module,
        "_run_stack_mutation",
        lambda *, args, state, force: payload,
    )
    impact_calls: list[object] = []
    monkeypatch.setattr(
        plugin_module,
        "_render_impact",
        lambda state_arg, impact: impact_calls.append(impact),
    )
    command_fn(None, name="pkg", runtime="stack")
    assert expected_message in state.console.export_text()
    assert impact_calls
