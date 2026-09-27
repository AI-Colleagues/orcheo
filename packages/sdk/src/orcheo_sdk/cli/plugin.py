"""Plugin lifecycle CLI commands."""

from __future__ import annotations
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Any
import typer
from orcheo.plugins import PluginImpactSummary
from orcheo.plugins.manager import PluginError, hash_install_source
from orcheo_sdk.cli.output import (
    print_json,
    print_markdown_table,
    render_json,
    render_table,
)
from orcheo_sdk.cli.stack_project import (
    STACK_NOT_FOUND_MESSAGE,
    resolve_installed_stack,
)
from orcheo_sdk.cli.state import CLIState
from orcheo_sdk.services import (
    disable_plugin_data,
    doctor_plugins_data,
    enable_plugin_data,
    install_plugin_data,
    list_plugins_data,
    preview_disable_plugin_data,
    preview_enable_plugin_data,
    preview_uninstall_plugin_data,
    preview_update_all_plugins_data,
    preview_update_plugin_data,
    show_plugin_data,
    uninstall_plugin_data,
    update_all_plugins_data,
    update_plugin_data,
)


plugin_app = typer.Typer(help="Install, inspect, and manage Orcheo plugins.")

NameArgument = Annotated[
    str,
    typer.Argument(help="Plugin package name."),
]
RefArgument = Annotated[
    str,
    typer.Argument(help="Package, path, wheel, or git reference."),
]
RuntimeOption = Annotated[
    str,
    typer.Option(
        "--runtime",
        help="Plugin runtime target: auto, local, or stack.",
    ),
]

_STACK_RUNTIME_SERVICES = ("backend", "worker", "celery-beat")
_REMOTE_PLUGIN_REF_PATTERN = re.compile(r"^(?:git\+)?[A-Za-z][A-Za-z0-9+.-]*://")
_GIT_SCP_PLUGIN_REF_PATTERN = re.compile(r"^[^/\\\\]+@[^:]+:.+")


def _state(ctx: typer.Context) -> CLIState:
    return ctx.ensure_object(CLIState)


def _resolve_stack_project_dir() -> Path:
    configured = os.getenv("ORCHEO_STACK_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".orcheo" / "stack"


def _stack_compose_base_args() -> list[str]:
    project = resolve_installed_stack(_resolve_stack_project_dir())
    if project is None:
        raise typer.BadParameter(STACK_NOT_FOUND_MESSAGE)
    return [
        "docker",
        "compose",
        "-f",
        str(project.compose_file),
        "--project-directory",
        str(project.project_dir),
    ]


def _normalize_runtime(runtime: str) -> str:
    resolved = runtime.strip().lower()
    if resolved not in {"auto", "local", "stack"}:
        raise typer.BadParameter("Runtime must be one of: auto, local, stack.")
    return resolved


def _use_stack_runtime(runtime: str) -> bool:
    resolved = _normalize_runtime(runtime)
    if resolved == "local":
        return False
    if resolved == "stack":
        return True
    return resolve_installed_stack(_resolve_stack_project_dir()) is not None


def _run_stack_subprocess(
    command: list[str],
    *,
    expected_exit_codes: set[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    if shutil.which("docker") is None:
        raise typer.BadParameter(
            "Docker is not installed or not in PATH. Install Docker and retry."
        )
    if expected_exit_codes is None:  # pragma: no branch
        expected_exit_codes = {0}
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in expected_exit_codes:
        message = result.stderr.strip() or result.stdout.strip() or "Command failed."
        raise typer.BadParameter(message)
    return result


def _run_stack_subprocess_streaming(
    command: list[str],
    *,
    expected_exit_codes: set[int] | None = None,
) -> int:
    """Run a stack subprocess, streaming output to the terminal in real time."""
    if shutil.which("docker") is None:
        raise typer.BadParameter(
            "Docker is not installed or not in PATH. Install Docker and retry."
        )
    if expected_exit_codes is None:
        expected_exit_codes = {0}
    result = subprocess.run(command, check=False)
    if result.returncode not in expected_exit_codes:
        raise typer.BadParameter(
            f"Command failed with exit code {result.returncode}: {' '.join(command)}"
        )
    return result.returncode


def _running_stack_services() -> set[str]:
    compose_base_args = _stack_compose_base_args()
    result = _run_stack_subprocess(
        [*compose_base_args, "ps", "--services", "--status", "running"]
    )
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() in _STACK_RUNTIME_SERVICES
    }


def _stack_plugin_command(
    *,
    args: list[str],
    human: bool,
) -> list[str]:
    compose_base_args = _stack_compose_base_args()
    running = _running_stack_services()
    runtime_args = [*args, "--runtime", "local"]
    if "backend" in running:
        return [
            *compose_base_args,
            "exec",
            "-T",
            "backend",
            "orcheo",
            *(["--human"] if human else []),
            "plugin",
            *runtime_args,
        ]
    return [
        *compose_base_args,
        "run",
        "--rm",
        "-T",
        "--no-deps",
        "backend",
        "orcheo",
        *(["--human"] if human else []),
        "plugin",
        *runtime_args,
    ]


def _run_stack_plugin_passthrough(*, args: list[str], state: CLIState) -> None:
    expected_exit_codes = {0, 1} if args and args[0] == "doctor" else {0}
    command = _stack_plugin_command(args=args, human=state.human)
    if state.human:
        returncode = _run_stack_subprocess_streaming(
            command, expected_exit_codes=expected_exit_codes
        )
        if args and args[0] == "doctor" and returncode == 1:
            raise typer.Exit(code=1)
        return
    result = _run_stack_subprocess(command, expected_exit_codes=expected_exit_codes)
    output = result.stdout.rstrip()
    if output:
        typer.echo(output)
    if args and args[0] == "doctor" and result.returncode == 1:
        raise typer.Exit(code=1)


def _is_local_plugin_ref(ref: str) -> bool:
    stripped = ref.strip()
    if _REMOTE_PLUGIN_REF_PATTERN.match(stripped) and not stripped.startswith(
        "file://"
    ):
        return False
    if _GIT_SCP_PLUGIN_REF_PATTERN.match(stripped):
        return False
    candidate = Path(stripped).expanduser()
    if candidate.exists():
        return True
    if stripped.startswith(("./", "../", "~/")) or candidate.is_absolute():
        return True
    if candidate.suffix == ".whl":
        return True
    return "/" in stripped or "\\" in stripped


def _run_stack_plugin_json(args: list[str]) -> Any:
    result = _run_stack_subprocess(
        _stack_plugin_command(args=args, human=False),
    )
    payload = result.stdout.strip()
    if not payload:
        return None
    return json.loads(payload)


def _stack_service_container_id(service: str) -> str | None:
    compose_base_args = _stack_compose_base_args()
    result = _run_stack_subprocess([*compose_base_args, "ps", "-q", service])
    container_id = result.stdout.strip()
    return container_id or None


def _copy_local_plugin_ref_into_stack(ref: str) -> str:
    source_path = Path(ref).expanduser().resolve()
    if not source_path.exists():
        raise typer.BadParameter(f"Local plugin reference not found: {ref}")
    if "backend" not in _running_stack_services():
        raise typer.BadParameter(
            "Local stack plugin installs require the backend service to be "
            "running so the source can be staged into the shared stack volume."
        )
    container_id = _stack_service_container_id("backend")
    if container_id is None:
        raise typer.BadParameter(
            "Could not resolve the backend container id for the running stack."
        )

    destination_parent = Path("/data/plugin-sources") / hash_install_source(
        str(source_path)
    )
    destination_path = destination_parent / source_path.name
    compose_base_args = _stack_compose_base_args()
    _run_stack_subprocess(
        [
            *compose_base_args,
            "exec",
            "-T",
            "backend",
            "sh",
            "-lc",
            (
                f"mkdir -p {shlex.quote(str(destination_parent))} && "
                f"rm -rf {shlex.quote(str(destination_path))}"
            ),
        ]
    )
    _run_stack_subprocess(
        [
            "docker",
            "cp",
            str(source_path),
            f"{container_id}:{destination_parent}",
        ]
    )
    return str(destination_path)


def _restart_running_stack_services(console_state: CLIState) -> None:
    running = _running_stack_services()
    if not running:
        return
    compose_base_args = _stack_compose_base_args()
    _run_stack_subprocess([*compose_base_args, "restart", *sorted(running)])
    if console_state.human:
        console_state.console.print(
            "Restarted stack services: " + ", ".join(sorted(running))
        )


def _payload_requires_restart(payload: Any) -> bool:
    if isinstance(payload, list):
        return any(_payload_requires_restart(item) for item in payload)
    if not isinstance(payload, dict):
        return False
    impact = payload.get("impact")
    return isinstance(impact, dict) and bool(impact.get("restart_required"))


def _run_stack_mutation(
    *,
    args: list[str],
    state: CLIState,
    force: bool,
) -> Any:
    stack_args = [*args, *(["--force"] if force else [])]
    if state.human and not force:
        _run_stack_plugin_passthrough(args=stack_args, state=state)
        return None
    payload = _run_stack_plugin_json(stack_args)
    if _payload_requires_restart(payload):  # pragma: no branch
        _restart_running_stack_services(state)
    return payload


def _impact_to_dict(impact: PluginImpactSummary) -> dict[str, Any]:
    return {
        "change_type": impact.change_type,
        "affected_component_kinds": impact.affected_component_kinds,
        "affected_component_ids": impact.affected_component_ids,
        "activation_mode": impact.activation_mode,
        "prompt_required": impact.prompt_required,
        "restart_required": impact.restart_required,
    }


def _render_impact(console_state: CLIState, impact: PluginImpactSummary) -> None:
    if console_state.human:  # pragma: no branch
        render_json(console_state.console, _impact_to_dict(impact), title="Impact")


def _emit_plugin_progress(state: CLIState, message: str) -> None:
    if state.human:
        state.console.print(f"[dim]• {message}[/dim]")


def _maybe_confirm(
    *,
    impact: PluginImpactSummary,
    prompt_text: str,
    state: CLIState,
    force: bool,
) -> None:
    if force or not impact.prompt_required:
        return
    if not state.human:
        raise typer.BadParameter(
            "This operation requires confirmation. Re-run with --force or --human."
        )
    should_continue = typer.confirm(prompt_text, default=False)
    if not should_continue:
        raise typer.Exit(code=1)


def _render_update_all_payload(state: CLIState, payload: list[dict[str, Any]]) -> None:
    serialized = [
        {
            "plugin": item["plugin"],
            "impact": _impact_to_dict(item["impact"]),
        }
        for item in payload
    ]
    if not state.human:
        print_json(serialized)
        return
    render_json(state.console, serialized, title="Updated Plugins")


def _render_update_single_payload(state: CLIState, payload: dict[str, Any]) -> None:
    impact = payload["impact"]
    if isinstance(impact, PluginImpactSummary):
        serialized_impact = _impact_to_dict(impact)
    else:
        serialized_impact = impact
    if not state.human:
        print_json(
            {
                "plugin": payload["plugin"],
                "impact": serialized_impact,
            }
        )
        return
    render_json(state.console, payload["plugin"], title="Updated Plugin")
    if isinstance(impact, PluginImpactSummary):
        _render_impact(state, impact)
        return
    _render_impact(state, PluginImpactSummary(**impact))


def _update_plugins_in_stack(
    *,
    state: CLIState,
    name: str | None,
    all_plugins: bool,
    force: bool,
) -> None:
    if all_plugins:
        payload = _run_stack_mutation(
            args=["update", "--all"],
            state=state,
            force=force,
        )
        if payload is not None:  # pragma: no branch
            if not state.human:
                print_json(payload)
                return
            render_json(state.console, payload, title="Updated Plugins")
        return
    if not name:
        raise typer.BadParameter("Provide a plugin name or pass --all.")
    payload = _run_stack_mutation(
        args=["update", name],
        state=state,
        force=force,
    )
    if payload is not None:  # pragma: no branch
        _render_update_single_payload(state, payload)


def _update_all_plugins_locally(*, state: CLIState, force: bool) -> None:
    try:
        preview_all = preview_update_all_plugins_data()
    except PluginError as exc:
        raise typer.BadParameter(str(exc)) from exc
    for item in preview_all:
        _maybe_confirm(
            impact=item["impact"],
            prompt_text=(
                f"Apply update for {item['name']} with activation mode "
                f"{item['impact'].activation_mode}?"
            ),
            state=state,
            force=force,
        )
    try:
        _emit_plugin_progress(state, "Rebuilding plugin environment")
        payload = update_all_plugins_data(
            progress=lambda step: _emit_plugin_progress(state, step)
        )
    except PluginError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _render_update_all_payload(state, payload)


def _update_plugin_locally(*, state: CLIState, name: str, force: bool) -> None:
    try:
        preview_single = preview_update_plugin_data(name)
    except PluginError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _maybe_confirm(
        impact=preview_single["impact"],
        prompt_text=(
            "Update "
            f"{name} with activation mode "
            f"{preview_single['impact'].activation_mode}?"
        ),
        state=state,
        force=force,
    )
    try:
        _emit_plugin_progress(state, f"Updating plugin '{name}'")
        payload = update_plugin_data(
            name,
            progress=lambda step: _emit_plugin_progress(state, step),
        )
    except PluginError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _render_update_single_payload(state, payload)


@plugin_app.command("list")
def list_plugins(
    ctx: typer.Context,
    runtime: RuntimeOption = "auto",
) -> None:
    """List installed plugins and their status."""
    state = _state(ctx)
    if _use_stack_runtime(runtime):
        _run_stack_plugin_passthrough(args=["list"], state=state)
        return
    rows = list_plugins_data()
    if not state.human:
        print_markdown_table(rows)
        return
    render_table(
        state.console,
        title="Installed Plugins",
        columns=["Name", "Enabled", "Status", "Version", "Exports", "Source"],
        rows=[
            [
                row["name"],
                row["enabled"],
                row["status"],
                row["version"],
                ", ".join(row["exports"]),
                row["source"],
            ]
            for row in rows
        ],
        column_overflow={"Name": "fold", "Source": "fold"},
    )


@plugin_app.command("show")
def show_plugin(
    ctx: typer.Context,
    name: NameArgument,
    runtime: RuntimeOption = "auto",
) -> None:
    """Show plugin details."""
    state = _state(ctx)
    if _use_stack_runtime(runtime):
        _run_stack_plugin_passthrough(args=["show", name], state=state)
        return
    try:
        payload = show_plugin_data(name)
    except PluginError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not state.human:
        print_json(payload)
        return
    render_json(state.console, payload, title=name)


@plugin_app.command("install")
def install_plugin(
    ctx: typer.Context,
    ref: RefArgument,
    runtime: RuntimeOption = "auto",
) -> None:
    """Install a plugin from a package, path, wheel, or git ref."""
    state = _state(ctx)
    use_stack_runtime = _use_stack_runtime(runtime)
    if use_stack_runtime:
        stack_ref = (
            _copy_local_plugin_ref_into_stack(ref) if _is_local_plugin_ref(ref) else ref
        )
        if state.human:
            _emit_plugin_progress(state, "Installing plugin inside the stack backend")
            _run_stack_plugin_passthrough(args=["install", stack_ref], state=state)
            _restart_running_stack_services(state)
            return
        payload = _run_stack_plugin_json(["install", stack_ref])
        if _payload_requires_restart(payload):  # pragma: no branch
            _restart_running_stack_services(state)
        print_json(payload)
        return
    try:
        _emit_plugin_progress(state, f"Installing plugin from '{ref}'")
        payload = install_plugin_data(
            ref,
            progress=lambda step: _emit_plugin_progress(state, step),
        )
    except PluginError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not state.human:
        print_json(
            {
                "plugin": payload["plugin"],
                "impact": _impact_to_dict(payload["impact"]),
            }
        )
        return
    render_json(state.console, payload["plugin"], title="Installed Plugin")
    _render_impact(state, payload["impact"])


@plugin_app.command("update")
def update_plugin(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(help="Plugin package name."),
    ] = None,
    all_plugins: Annotated[
        bool,
        typer.Option("--all", help="Update all installed plugins."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Skip confirmation prompts."),
    ] = False,
    runtime: RuntimeOption = "auto",
) -> None:
    """Update a plugin using the stored install reference."""
    state = _state(ctx)
    if _use_stack_runtime(runtime):
        _update_plugins_in_stack(
            state=state,
            name=name,
            all_plugins=all_plugins,
            force=force,
        )
        return
    if all_plugins:
        _update_all_plugins_locally(state=state, force=force)
        return

    if not name:
        raise typer.BadParameter("Provide a plugin name or pass --all.")
    _update_plugin_locally(state=state, name=name, force=force)


@plugin_app.command("uninstall")
def uninstall_plugin(
    ctx: typer.Context,
    name: NameArgument,
    force: Annotated[
        bool,
        typer.Option("--force", help="Skip confirmation prompts."),
    ] = False,
    runtime: RuntimeOption = "auto",
) -> None:
    """Uninstall a plugin and rebuild the shared plugin environment."""
    state = _state(ctx)
    if _use_stack_runtime(runtime):
        payload = _run_stack_mutation(
            args=["uninstall", name],
            state=state,
            force=force,
        )
        if payload is None:
            return
        if not state.human:
            print_json(payload)
            return
        state.console.print(f"Uninstalled plugin {name}")
        _render_impact(state, PluginImpactSummary(**payload["impact"]))
        return
    try:
        payload = preview_uninstall_plugin_data(name)
    except PluginError as exc:
        raise typer.BadParameter(str(exc)) from exc
    impact = payload["impact"]
    _maybe_confirm(
        impact=impact,
        prompt_text=(f"Uninstall {name}? Activation mode: {impact.activation_mode}."),
        state=state,
        force=force,
    )
    try:
        payload = uninstall_plugin_data(name)
    except PluginError as exc:
        raise typer.BadParameter(str(exc)) from exc
    impact = payload["impact"]
    if not state.human:
        print_json({"name": name, "impact": _impact_to_dict(impact)})
        return
    state.console.print(f"Uninstalled plugin {name}")
    _render_impact(state, impact)


@plugin_app.command("enable")
def enable_plugin(
    ctx: typer.Context,
    name: NameArgument,
    force: Annotated[
        bool,
        typer.Option("--force", help="Skip confirmation prompts."),
    ] = False,
    runtime: RuntimeOption = "auto",
) -> None:
    """Enable a previously installed plugin."""
    state = _state(ctx)
    if _use_stack_runtime(runtime):
        payload = _run_stack_mutation(
            args=["enable", name],
            state=state,
            force=force,
        )
        if payload is None:
            return
        if not state.human:
            print_json(payload)
            return
        state.console.print(f"Enabled plugin {name}")
        _render_impact(state, PluginImpactSummary(**payload["impact"]))
        return
    try:
        payload = preview_enable_plugin_data(name)
    except PluginError as exc:
        raise typer.BadParameter(str(exc)) from exc
    impact = payload["impact"]
    _maybe_confirm(
        impact=impact,
        prompt_text=f"Enable {name}? Activation mode: {impact.activation_mode}.",
        state=state,
        force=force,
    )
    try:
        payload = enable_plugin_data(name)
    except PluginError as exc:
        raise typer.BadParameter(str(exc)) from exc
    impact = payload["impact"]
    if not state.human:
        print_json({"name": name, "impact": _impact_to_dict(impact)})
        return
    state.console.print(f"Enabled plugin {name}")
    _render_impact(state, impact)


@plugin_app.command("disable")
def disable_plugin(
    ctx: typer.Context,
    name: NameArgument,
    force: Annotated[
        bool,
        typer.Option("--force", help="Skip confirmation prompts."),
    ] = False,
    runtime: RuntimeOption = "auto",
) -> None:
    """Disable a plugin without removing its desired source reference."""
    state = _state(ctx)
    if _use_stack_runtime(runtime):
        payload = _run_stack_mutation(
            args=["disable", name],
            state=state,
            force=force,
        )
        if payload is None:
            return
        if not state.human:
            print_json(payload)
            return
        state.console.print(f"Disabled plugin {name}")
        _render_impact(state, PluginImpactSummary(**payload["impact"]))
        return
    try:
        payload = preview_disable_plugin_data(name)
    except PluginError as exc:
        raise typer.BadParameter(str(exc)) from exc
    impact = payload["impact"]
    _maybe_confirm(
        impact=impact,
        prompt_text=f"Disable {name}? Activation mode: {impact.activation_mode}.",
        state=state,
        force=force,
    )
    try:
        payload = disable_plugin_data(name)
    except PluginError as exc:
        raise typer.BadParameter(str(exc)) from exc
    impact = payload["impact"]
    if not state.human:
        print_json({"name": name, "impact": _impact_to_dict(impact)})
        return
    state.console.print(f"Disabled plugin {name}")
    _render_impact(state, impact)


@plugin_app.command("doctor")
def doctor_plugins(
    ctx: typer.Context,
    runtime: RuntimeOption = "auto",
) -> None:
    """Inspect plugin state and report errors and warnings."""
    state = _state(ctx)
    if _use_stack_runtime(runtime):
        _run_stack_plugin_passthrough(args=["doctor"], state=state)
        return
    payload = doctor_plugins_data()
    if not state.human:
        print_json(payload)
        if payload["has_errors"]:
            raise typer.Exit(code=1)
        return
    render_table(
        state.console,
        title="Plugin Doctor",
        columns=["Check", "Severity", "OK", "Message"],
        rows=[
            [check["name"], check["severity"], check["ok"], check["message"]]
            for check in payload["checks"]
        ],
        column_overflow={"Message": "fold"},
    )
    if payload["has_errors"]:
        raise typer.Exit(code=1)


@plugin_app.command("init")
def init_plugin(
    ctx: typer.Context,
    name: Annotated[
        str,
        typer.Argument(help="Plugin slug (e.g. 'my-plugin')."),
    ],
    author: Annotated[
        str,
        typer.Option("--author", help="Author name for pyproject.toml."),
    ] = "Your Name",
    description: Annotated[
        str,
        typer.Option("--description", help="Short plugin description."),
    ] = "",
    exports: Annotated[
        str,
        typer.Option(
            "--exports",
            help="Comma-separated export kinds.",
        ),
    ] = "nodes",
    output_dir: Annotated[
        str,
        typer.Option("--output-dir", "-o", help="Parent directory for the scaffold."),
    ] = "",
) -> None:
    """Generate a plugin package skeleton from the built-in template."""
    from orcheo_sdk.cli.scaffold import scaffold_plugin, validate_plugin_name

    state = _state(ctx)
    error = validate_plugin_name(name)
    if error:
        raise typer.BadParameter(error)

    target = Path(output_dir) if output_dir else None
    exports_list = [e.strip() for e in exports.split(",") if e.strip()]

    try:
        root = scaffold_plugin(
            name,
            target_dir=target,
            author=author,
            description=description,
            exports=exports_list,
        )
    except FileExistsError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if state.human:
        state.console.print(f"[green]✓[/green] Created plugin scaffold at {root}")
    else:
        print_json({"path": str(root), "name": f"orcheo-plugin-{name}"})


__all__ = ["plugin_app"]
