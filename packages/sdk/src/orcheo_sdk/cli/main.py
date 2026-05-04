"""Orcheo CLI entrypoint."""

from __future__ import annotations
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import timedelta
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated, Any, cast
import click
import typer
from rich.console import Console
from orcheo_sdk.cli.agent_tool import agent_tool_app
from orcheo_sdk.cli.auth import auth_app
from orcheo_sdk.cli.browser_context.commands import browser_aware_app, context_app
from orcheo_sdk.cli.cache import CacheManager
from orcheo_sdk.cli.codegen import code_app
from orcheo_sdk.cli.config import get_cache_dir, resolve_settings
from orcheo_sdk.cli.config_command import config_app
from orcheo_sdk.cli.credential import credential_app
from orcheo_sdk.cli.edge import edge_app
from orcheo_sdk.cli.errors import APICallError, CLIConfigurationError, CLIError
from orcheo_sdk.cli.http import ApiClient
from orcheo_sdk.cli.node import node_app
from orcheo_sdk.cli.plugin import plugin_app
from orcheo_sdk.cli.service_token import app as service_token_app
from orcheo_sdk.cli.setup import (
    AuthMode,
    SetupMode,
    build_generated_stack_env_defaults,
    ensure_stack_env_file,
    execute_setup,
    print_summary,
    run_setup,
)
from orcheo_sdk.cli.state import CLIState
from orcheo_sdk.cli.tenant import tenant_app
from orcheo_sdk.cli.update_check import maybe_print_update_notice
from orcheo_sdk.cli.workflow import workflow_app


PACKAGE_NAME = "orcheo-sdk"


def _is_completion_mode() -> bool:
    """Check if we're in shell completion mode."""
    return any(
        env_var.startswith("_TYPER_COMPLETE") or env_var.startswith("_ORCHEO_COMPLETE")
        for env_var in os.environ
    )


def _env_bool(name: str) -> bool:
    """Return True if env var is set to a truthy value."""
    value = os.getenv(name)
    if value is None:
        return False
    if value.strip().lower() in {"0", "false", "no", "off", ""}:
        return False
    return True


def _version_callback(value: bool) -> None:
    """Print CLI version and exit."""
    if not value:
        return
    try:
        version_value = package_version(PACKAGE_NAME)
    except PackageNotFoundError:
        version_value = "unknown"
    typer.echo(f"orcheo {version_value}")
    raise typer.Exit()


def _parse_setup_mode(value: str | None) -> str | None:
    """Normalize setup mode option values."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in {"install", "upgrade"}:
        msg = "--mode must be one of: install, upgrade."
        raise typer.BadParameter(msg)
    return normalized


def _parse_auth_mode(value: str | None) -> str | None:
    """Normalize auth mode option values."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in {"api-key", "oauth"}:
        msg = "--auth-mode must be one of: api-key, oauth."
        raise typer.BadParameter(msg)
    return normalized


app = typer.Typer(help="Command line interface for Orcheo workflows.")
app.add_typer(auth_app, name="auth")
app.add_typer(node_app, name="node")
app.add_typer(edge_app, name="edge")
app.add_typer(plugin_app, name="plugin")
app.add_typer(workflow_app, name="workflow")
app.add_typer(credential_app, name="credential")
app.add_typer(code_app, name="code")
app.add_typer(config_app, name="config")
app.add_typer(agent_tool_app, name="agent-tool")
app.add_typer(service_token_app, name="token")
app.add_typer(tenant_app, name="tenant")
app.add_typer(browser_aware_app, name="browser-aware")
app.add_typer(context_app, name="context")
install_app = typer.Typer(
    help="Install or upgrade the Orcheo stack components.",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(install_app, name="install")


def _create_token_provider(profile: str | None) -> Callable[[], str | None]:
    """Create a token provider callback for OAuth token resolution."""

    def provider() -> str | None:
        from orcheo_sdk.cli.auth.refresh import get_valid_access_token

        return get_valid_access_token(profile=profile)

    return provider


@app.callback()
def main(
    ctx: typer.Context,
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Profile name from the CLI config."),
    ] = None,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the Orcheo CLI version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Override the API URL."),
    ] = None,
    service_token: Annotated[
        str | None,
        typer.Option("--service-token", help="Override the service token."),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Use cached data when network calls fail."),
    ] = False,
    cache_ttl_hours: Annotated[
        int,
        typer.Option("--cache-ttl", help="Cache TTL in hours for offline data."),
    ] = 24,
    human: Annotated[
        bool,
        typer.Option(
            "--human",
            help="Use human-friendly Rich output instead of machine-readable JSON.",
        ),
    ] = False,
    no_update_check: Annotated[
        bool,
        typer.Option(
            "--no-update-check",
            help="Skip startup update checks for this invocation.",
        ),
    ] = False,
) -> None:
    """Configure shared CLI state and validate configuration."""
    # Skip expensive initialization during shell completion
    if _is_completion_mode():
        return  # pragma: no cover

    resolved_human = human or _env_bool("ORCHEO_HUMAN")
    console = Console() if resolved_human else Console(no_color=True, highlight=False)
    try:
        settings = resolve_settings(
            profile=profile,
            api_url=api_url,
            service_token=service_token,
            offline=offline,
        )
    except CLIConfigurationError as exc:  # pragma: no cover
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    cache = CacheManager(
        directory=get_cache_dir(),
        ttl=timedelta(hours=cache_ttl_hours),
    )
    client = ApiClient(
        base_url=settings.api_url,
        token=settings.service_token,
        public_base_url=settings.chatkit_public_base_url,
        token_provider=_create_token_provider(settings.profile),
    )
    ctx.obj = CLIState(
        settings=settings,
        client=client,
        cache=cache,
        console=console,
        human=resolved_human,
    )
    if not no_update_check and not _env_bool("ORCHEO_DISABLE_UPDATE_CHECK"):
        maybe_print_update_notice(
            cache=cache,
            client=client,
            profile=settings.profile,
            console=console,
        )


def _install_agent_skills(*, console: Console, should_install: bool) -> None:
    """Install the Orcheo skill into Claude Code and Codex via skill-mgr."""
    if not should_install:
        return
    skill_mgr_bin = shutil.which("skill-mgr")
    if skill_mgr_bin:
        cmd = [
            skill_mgr_bin,
            "install",
            "AI-Colleagues/agent-skills/orcheo",
        ]
    else:
        cmd = [
            "uv",
            "run",
            "skill-mgr",
            "install",
            "AI-Colleagues/agent-skills/orcheo",
        ]
    console.print(f"[cyan]$ {' '.join(cmd)}[/cyan]")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        console.print(
            "[yellow]Warning:[/yellow] skill-mgr exited with a non-zero status. "
            "You can install the skill manually with: "
            "skill-mgr install AI-Colleagues/agent-skills/orcheo"
        )


def _run_install_flow(
    *,
    console: Console,
    yes: bool,
    mode: str | None,
    stack_version: str | None,
    backend_url: str | None,
    auth_mode: str | None,
    api_key: str | None,
    chatkit_domain_key: str | None,
    public_ingress: bool | None,
    public_host: str | None,
    publish_local_ports: bool | None,
    start_stack: bool | None,
    install_docker: bool | None,
    manual_secrets: bool,
    forced_mode: SetupMode | None = None,
) -> None:
    """Run guided install/upgrade for Orcheo components."""
    mode_value = forced_mode or cast(SetupMode | None, _parse_setup_mode(mode))
    auth_value = cast(AuthMode | None, _parse_auth_mode(auth_mode))
    config = run_setup(
        mode=mode_value,
        backend_url=backend_url,
        auth_mode=auth_value,
        api_key=api_key,
        chatkit_domain_key=chatkit_domain_key,
        public_ingress=public_ingress,
        public_host=public_host,
        publish_local_ports=publish_local_ports,
        start_stack=start_stack,
        install_docker=install_docker,
        yes=yes,
        manual_secrets=manual_secrets,
        console=console,
    )
    execute_setup(config, console=console, stack_version=stack_version)
    print_summary(config, console=console)
    _install_agent_skills(console=console, should_install=config.install_agent_skills)


def _resolve_install_console(ctx: typer.Context) -> Console:
    state = ctx.obj if isinstance(ctx.obj, CLIState) else None
    return state.console if state is not None else Console()


def _resolve_stack_project_dir() -> Path:
    configured = os.getenv("ORCHEO_STACK_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".orcheo" / "stack"


def _compose_profile_args(stack_dir: Path) -> list[str]:
    env_file = stack_dir / ".env"
    if not env_file.exists():
        return []
    profiles: list[str] = []
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith("COMPOSE_PROFILES="):
            continue
        _, _, value = line.partition("=")
        for profile in value.split(","):
            normalized = profile.strip().strip('"').strip("'")
            if normalized:
                profiles.extend(["--profile", normalized])
        break
    return profiles


def _stack_compose_base_args() -> list[str]:
    stack_dir = _resolve_stack_project_dir()
    compose_file = stack_dir / "docker-compose.yml"
    if not compose_file.exists():
        raise typer.BadParameter(
            "Stack docker-compose file not found. Run 'orcheo install --yes' first."
        )
    return [
        "docker",
        "compose",
        *_compose_profile_args(stack_dir),
        "-f",
        str(compose_file),
        "--project-directory",
        str(stack_dir),
    ]


def _run_stack_command(
    command: list[str],
    *,
    console: Console,
    expected_exit_codes: set[int] | None = None,
) -> None:
    if expected_exit_codes is None:
        expected_exit_codes = {0}

    command_text = " ".join(command)
    console.print(f"[cyan]$ {command_text}[/cyan]")
    result = subprocess.run(command, check=False)
    if result.returncode not in expected_exit_codes:
        raise typer.BadParameter(
            f"Command failed with exit code {result.returncode}: {command_text}"
        )


@install_app.callback(invoke_without_command=True)
def install_command(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Accept defaults and run non-interactive install."),
    ] = False,
    mode: Annotated[
        str | None,
        typer.Option("--mode", help="Install mode: install or upgrade."),
    ] = None,
    stack_version: Annotated[
        str | None,
        typer.Option(
            "--stack-version",
            help=(
                "Pin stack assets to a specific release version (for example, 0.1.0)."
            ),
        ),
    ] = None,
    backend_url: Annotated[
        str | None,
        typer.Option("--backend-url", help="Backend URL for CLI config."),
    ] = None,
    auth_mode: Annotated[
        str | None,
        typer.Option("--auth-mode", help="Auth mode: api-key or oauth."),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="API key used when --auth-mode api-key."),
    ] = None,
    chatkit_domain_key: Annotated[
        str | None,
        typer.Option(
            "--chatkit-domain-key",
            help=(
                "Set VITE_ORCHEO_CHATKIT_DOMAIN_KEY for Canvas ChatKit. "
                "Leave unset to skip for now."
            ),
        ),
    ] = None,
    public_ingress: Annotated[
        bool | None,
        typer.Option(
            "--public-ingress/--local-only",
            help=(
                "Enable bundled Caddy ingress for a reachable public hostname, "
                "or keep the current local-only stack path."
            ),
        ),
    ] = None,
    public_host: Annotated[
        str | None,
        typer.Option(
            "--public-host",
            help="Public hostname served by bundled Caddy in public-ingress mode.",
        ),
    ] = None,
    publish_local_ports: Annotated[
        bool | None,
        typer.Option(
            "--publish-local-ports/--hide-local-ports",
            help=(
                "Keep localhost backend/canvas access ports published alongside the "
                "selected install mode."
            ),
        ),
    ] = None,
    start_stack: Annotated[
        bool | None,
        typer.Option(
            "--start-stack/--skip-stack",
            help="Start or skip docker compose stack startup after syncing assets.",
        ),
    ] = None,
    install_docker: Annotated[
        bool | None,
        typer.Option(
            "--install-docker/--skip-docker-install",
            help=(
                "Install Docker when missing. Uses Docker Desktop on macOS/Windows "
                "and distro packages on supported Linux systems."
            ),
        ),
    ] = None,
    manual_secrets: Annotated[
        bool,
        typer.Option(
            "--manual-secrets",
            help="Prompt for manual secret entry instead of auto-generating.",
        ),
    ] = False,
) -> None:
    """Run guided install/upgrade for Orcheo components."""
    if ctx.invoked_subcommand is not None:
        return
    _run_install_flow(  # pragma: no cover
        console=_resolve_install_console(ctx),
        yes=yes,
        mode=mode,
        stack_version=stack_version,
        backend_url=backend_url,
        auth_mode=auth_mode,
        api_key=api_key,
        chatkit_domain_key=chatkit_domain_key,
        public_ingress=public_ingress,
        public_host=public_host,
        publish_local_ports=publish_local_ports,
        start_stack=start_stack,
        install_docker=install_docker,
        manual_secrets=manual_secrets,
    )


@install_app.command("upgrade")
def install_upgrade_command(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Accept defaults and run non-interactive upgrade."),
    ] = False,
    stack_version: Annotated[
        str | None,
        typer.Option(
            "--stack-version",
            help=(
                "Pin stack assets to a specific release version (for example, 0.1.0)."
            ),
        ),
    ] = None,
    backend_url: Annotated[
        str | None,
        typer.Option("--backend-url", help="Backend URL for CLI config."),
    ] = None,
    auth_mode: Annotated[
        str | None,
        typer.Option("--auth-mode", help="Auth mode: api-key or oauth."),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="API key used when --auth-mode api-key."),
    ] = None,
    chatkit_domain_key: Annotated[
        str | None,
        typer.Option(
            "--chatkit-domain-key",
            help=(
                "Set VITE_ORCHEO_CHATKIT_DOMAIN_KEY for Canvas ChatKit. "
                "Leave unset to skip for now."
            ),
        ),
    ] = None,
    public_ingress: Annotated[
        bool | None,
        typer.Option(
            "--public-ingress/--local-only",
            help=(
                "Enable bundled Caddy ingress for a reachable public hostname, "
                "or keep the current local-only stack path."
            ),
        ),
    ] = None,
    public_host: Annotated[
        str | None,
        typer.Option(
            "--public-host",
            help="Public hostname served by bundled Caddy in public-ingress mode.",
        ),
    ] = None,
    publish_local_ports: Annotated[
        bool | None,
        typer.Option(
            "--publish-local-ports/--hide-local-ports",
            help=(
                "Keep localhost backend/canvas access ports published alongside the "
                "selected install mode."
            ),
        ),
    ] = None,
    start_stack: Annotated[
        bool | None,
        typer.Option(
            "--start-stack/--skip-stack",
            help="Start or skip docker compose stack startup after syncing assets.",
        ),
    ] = None,
    install_docker: Annotated[
        bool | None,
        typer.Option(
            "--install-docker/--skip-docker-install",
            help=(
                "Install Docker when missing. Uses Docker Desktop on macOS/Windows "
                "and distro packages on supported Linux systems."
            ),
        ),
    ] = None,
    manual_secrets: Annotated[
        bool,
        typer.Option(
            "--manual-secrets",
            help="Prompt for manual secret entry instead of auto-generating.",
        ),
    ] = False,
) -> None:
    """Run guided upgrade command with simplified syntax."""
    _run_install_flow(
        console=_resolve_install_console(ctx),
        yes=yes,
        mode=None,
        stack_version=stack_version,
        backend_url=backend_url,
        auth_mode=auth_mode,
        api_key=api_key,
        chatkit_domain_key=chatkit_domain_key,
        public_ingress=public_ingress,
        public_host=public_host,
        publish_local_ports=publish_local_ports,
        start_stack=start_stack,
        install_docker=install_docker,
        manual_secrets=manual_secrets,
        forced_mode="upgrade",
    )


@install_app.command("ensure-stack-env")
def ensure_stack_env_command(
    ctx: typer.Context,
    env_file: Annotated[
        str | None,
        typer.Option(
            "--env-file",
            help="Path to the stack .env file to create or update.",
        ),
    ] = None,
    env_template: Annotated[
        str | None,
        typer.Option(
            "--env-template",
            help="Path to the .env.example template used to seed missing values.",
        ),
    ] = None,
) -> None:
    """Create or backfill a stack env file without running full install setup."""
    stack_dir = _resolve_stack_project_dir()
    resolved_env_file = (
        Path(env_file).expanduser() if env_file is not None else stack_dir / ".env"
    )
    resolved_env_template = (
        Path(env_template).expanduser()
        if env_template is not None
        else stack_dir / ".env.example"
    )
    ensure_stack_env_file(
        env_file=resolved_env_file,
        env_template=resolved_env_template,
        console=_resolve_install_console(ctx),
        generated_defaults=build_generated_stack_env_defaults(),
    )


@app.command("stack")
def stack_command(
    ctx: typer.Context,
    logs: Annotated[
        bool,
        typer.Option("--logs", help="Stream stack logs (docker compose logs -f)."),
    ] = False,
    start: Annotated[
        bool,
        typer.Option("--start", help="Start stack services in detached mode."),
    ] = False,
    stop: Annotated[
        bool,
        typer.Option("--stop", help="Stop running stack services."),
    ] = False,
    restart: Annotated[
        bool,
        typer.Option("--restart", help="Restart stack services."),
    ] = False,
    ps: Annotated[
        bool,
        typer.Option("--ps", help="List stack service status."),
    ] = False,
    pull: Annotated[
        bool,
        typer.Option("--pull", help="Pull latest stack images."),
    ] = False,
    down: Annotated[
        bool,
        typer.Option("--down", help="Stop and remove stack resources."),
    ] = False,
) -> None:
    """Run common Docker Compose actions for the local Orcheo stack."""
    if shutil.which("docker") is None:
        raise typer.BadParameter(
            "Docker is not installed or not in PATH. Install Docker and retry."
        )

    selected_actions = [
        action
        for action, enabled in (
            ("logs", logs),
            ("start", start),
            ("stop", stop),
            ("restart", restart),
            ("ps", ps),
            ("pull", pull),
            ("down", down),
        )
        if enabled
    ]

    if not selected_actions:
        raise typer.BadParameter(
            "Choose one action: --logs, --start, --stop, --restart, "
            "--ps, --pull, or --down."
        )

    if len(selected_actions) > 1:
        raise typer.BadParameter("Choose only one stack action at a time.")

    action = selected_actions[0]
    compose_base_args = _stack_compose_base_args()
    command_by_action = {
        "logs": [*compose_base_args, "logs", "-f"],
        "start": [*compose_base_args, "up", "-d"],
        "stop": [*compose_base_args, "stop"],
        "restart": [*compose_base_args, "restart"],
        "ps": [*compose_base_args, "ps"],
        "pull": [*compose_base_args, "pull"],
        "down": [*compose_base_args, "down"],
    }
    _run_stack_command(
        command_by_action[action],
        console=_resolve_install_console(ctx),
        expected_exit_codes={0, 130} if action == "logs" else {0},
    )


def _print_cli_error(console: Console, exc: CLIError) -> None:
    """Print a concise, user-friendly error message for CLI errors."""
    error_msg = str(exc)

    # For authentication errors, provide helpful hints
    if isinstance(exc, APICallError):
        if exc.status_code == 401:
            console.print(f"[red]Error:[/red] {error_msg}")
            console.print(
                "\n[yellow]Hint:[/yellow] Authentication failed. "
                "Run 'orcheo auth login' to authenticate via OAuth, or "
                "set ORCHEO_SERVICE_TOKEN environment variable."
            )
            return
        elif exc.status_code == 403:
            console.print(f"[red]Error:[/red] {error_msg}")
            console.print(
                "\n[yellow]Hint:[/yellow] Your token lacks the required permissions."
            )
            return

    # Default: just print the error message without stack trace
    console.print(f"[red]Error:[/red] {error_msg}")


def _print_cli_error_machine(exc: CLIError) -> None:
    """Print a machine-readable error as JSON."""
    from orcheo_sdk.cli.output import print_json

    error_data: dict[str, Any] = {"error": str(exc)}
    if isinstance(exc, APICallError) and exc.status_code is not None:
        error_data["status_code"] = exc.status_code
    print_json(error_data)


def run() -> None:
    """Entry point used by console scripts."""
    human_mode = _env_bool("ORCHEO_HUMAN") or "--human" in sys.argv
    original_rich_markup = getattr(app, "rich_markup_mode", None)
    if not human_mode and hasattr(app, "rich_markup_mode"):
        app.rich_markup_mode = None
    console = Console()
    try:
        app(standalone_mode=False)
    except click.UsageError as exc:
        if human_mode:
            console.print(f"[red]Error:[/red] {exc.message}")
            if exc.ctx and exc.ctx.command_path:
                help_cmd = f"{exc.ctx.command_path} --help"
                console.print(f"\nRun '[cyan]{help_cmd}[/cyan]' for usage information.")
        else:
            from orcheo_sdk.cli.output import print_json

            error_data: dict[str, Any] = {"error": exc.message}
            if exc.ctx and exc.ctx.command_path:
                error_data["help"] = f"{exc.ctx.command_path} --help"
            print_json(error_data)
        sys.exit(1)
    except CLIError as exc:
        if human_mode:
            _print_cli_error(console, exc)
        else:
            _print_cli_error_machine(exc)
        sys.exit(1)
    finally:
        if hasattr(app, "rich_markup_mode"):
            app.rich_markup_mode = original_rich_markup


def run_human() -> None:
    """Entry point that defaults to human-readable output."""
    original_argv = sys.argv.copy()
    try:
        if "--human" not in sys.argv:
            sys.argv = [sys.argv[0], "--human", *sys.argv[1:]]
        run()
    finally:
        sys.argv = original_argv
