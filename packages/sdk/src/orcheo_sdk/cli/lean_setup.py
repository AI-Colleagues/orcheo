"""`orcheo install --lean`: run the single-image lean stack with Docker Compose."""

from __future__ import annotations
import json
import os
import secrets
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen
import typer
from rich.console import Console
from orcheo_sdk.cli.setup import (
    _discover_latest_stack_version,
    _docker_command,
    _is_prerelease_stack_version,
    _normalize_optional_value,
    _poll_backend_health,
    _prepare_docker_for_start,
    _read_env_value,
    _read_health_poll_timeout_seconds,
    _resolve_setup_toggles,
    _run_command,
    _run_privileged_command,
    _upsert_env_values,
    _write_synced_asset,
    build_generated_stack_env_defaults,
    ensure_stack_env_file,
)


_LEAN_RELEASE_TAG_PREFIX = "lean-v"
_LEAN_IMAGE_REPOSITORY = "ghcr.io/ai-colleagues/orcheo-lean"
_LEAN_ASSET_BASE_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/AI-Colleagues/orcheo/{ref}"
)
_GITHUB_CONTENTS_API_URL = "https://api.github.com/repos/AI-Colleagues/orcheo/contents"
# Repository-relative paths; the compose file mounts the widgets relative to
# itself, so assets keep their repository layout inside the lean directory.
_LEAN_COMPOSE_FILE = "docker-compose-lean.yml"
_LEAN_ENV_TEMPLATE = "deploy/lean/.env.example"
_LEAN_WIDGETS_DIR = "deploy/stack/chatkit_widgets"
_DEFAULT_LEAN_PORT = "2025"


def _resolve_lean_project_dir() -> Path:
    configured = os.getenv("ORCHEO_LEAN_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".orcheo" / "lean"


def _normalize_lean_version(version: str | None) -> str | None:
    resolved = _normalize_optional_value(version)
    if resolved is None:
        return None
    return _normalize_optional_value(resolved.removeprefix(_LEAN_RELEASE_TAG_PREFIX))


def _resolve_lean_version(
    lean_version: str | None,
    *,
    staging: bool,
    console: Console,
) -> str | None:
    """Resolve the lean release to install; ``None`` means the main branch."""
    resolved = _normalize_lean_version(lean_version) or _normalize_lean_version(
        os.getenv("ORCHEO_LEAN_VERSION")
    )
    if resolved is not None:
        if staging and not _is_prerelease_stack_version(resolved):
            raise typer.BadParameter(
                "--staging requires a prerelease ORCHEO_LEAN_VERSION or no "
                "configured lean version."
            )
        return resolved
    resolved = _discover_latest_stack_version(
        console,
        prerelease=staging,
        tag_prefix=_LEAN_RELEASE_TAG_PREFIX,
    )
    if staging and resolved is None:
        raise typer.BadParameter("No published prerelease lean version was found.")
    return resolved


def _lean_git_ref(lean_version: str | None) -> str:
    if lean_version is None:
        return "main"
    return f"{_LEAN_RELEASE_TAG_PREFIX}{lean_version}"


def _resolve_lean_asset_base_url(lean_version: str | None) -> str:
    configured = os.getenv("ORCHEO_LEAN_ASSET_BASE_URL")
    if configured:
        return configured.rstrip("/")
    return _LEAN_ASSET_BASE_URL_TEMPLATE.format(ref=_lean_git_ref(lean_version))


def _lean_image(lean_version: str | None) -> str:
    return f"{_LEAN_IMAGE_REPOSITORY}:{lean_version or 'latest'}"


def _list_lean_widget_paths(
    lean_version: str | None,
    console: Console,
) -> tuple[str, ...]:
    """List the ChatKit widget files via the GitHub Contents API.

    Returns an empty tuple when the API is unreachable; the widgets mount is
    optional, so installation continues without them.
    """
    url = (
        f"{_GITHUB_CONTENTS_API_URL}/{_LEAN_WIDGETS_DIR}"
        f"?ref={_lean_git_ref(lean_version)}"
    )
    try:
        with urlopen(url, timeout=10) as response:  # noqa: S310
            entries = json.loads(response.read().decode("utf-8"))
        if not isinstance(entries, list):
            raise ValueError("expected a JSON array")
        return tuple(
            f"{_LEAN_WIDGETS_DIR}/{entry['name']}"
            for entry in entries
            if isinstance(entry, dict) and entry.get("type") == "file"
        )
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        KeyError,
    ) as exc:
        console.print(
            f"[yellow]Could not list {_LEAN_WIDGETS_DIR}/ from GitHub; "
            f"skipping widget sync: {exc}[/yellow]"
        )
        return ()


def _sync_lean_asset(
    relative_path: str,
    lean_dir: Path,
    *,
    lean_version: str | None,
    console: Console,
) -> None:
    asset_url = (
        f"{_resolve_lean_asset_base_url(lean_version)}/{quote(relative_path, safe='/')}"
    )
    console.print(f"[cyan]Fetching lean asset: {relative_path}[/cyan]")
    try:
        with urlopen(asset_url, timeout=30) as response:  # noqa: S310
            payload = response.read()
    except OSError as exc:
        raise typer.BadParameter(
            f"Failed to download lean asset '{relative_path}' from {asset_url}: {exc}"
        ) from exc
    _write_synced_asset(
        lean_dir / relative_path,
        payload,
        label=f"lean asset: {relative_path}",
        console=console,
    )


def _sync_lean_assets(
    lean_dir: Path,
    *,
    lean_version: str | None,
    console: Console,
) -> None:
    for relative_path in (
        _LEAN_COMPOSE_FILE,
        _LEAN_ENV_TEMPLATE,
        *_list_lean_widget_paths(lean_version, console),
    ):
        _sync_lean_asset(
            relative_path,
            lean_dir,
            lean_version=lean_version,
            console=console,
        )


def _build_lean_generated_defaults() -> dict[str, str]:
    """Return secrets generated only for a freshly created lean .env file."""
    stack_defaults = build_generated_stack_env_defaults()
    return {
        "ORCHEO_POSTGRES_PASSWORD": stack_defaults["ORCHEO_POSTGRES_PASSWORD"],
        "ORCHEO_VAULT_ENCRYPTION_KEY": stack_defaults["ORCHEO_VAULT_ENCRYPTION_KEY"],
        "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY": stack_defaults[
            "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY"
        ],
        "ORCHEO_AUTH_JWT_SECRET": secrets.token_hex(32),
        "ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN": secrets.token_urlsafe(32),
    }


def _configure_lean_env(
    lean_dir: Path,
    *,
    lean_version: str | None,
    console: Console,
) -> Path:
    """Create or backfill ``.env`` from the template and pin the lean image."""
    env_file = lean_dir / ".env"
    ensure_stack_env_file(
        env_file=env_file,
        env_template=lean_dir / _LEAN_ENV_TEMPLATE,
        console=console,
        generated_defaults=_build_lean_generated_defaults(),
    )
    _upsert_env_values(
        env_file,
        {"ORCHEO_LEAN_IMAGE": _lean_image(lean_version)},
        console=console,
    )
    return env_file


def _lean_compose_args(lean_dir: Path) -> list[str]:
    docker_command = _docker_command()
    if docker_command is None:
        raise typer.BadParameter(
            "Docker appears to be installed, but the docker CLI could not be "
            "resolved in PATH."
        )
    return [
        *docker_command,
        "compose",
        "-f",
        str(lean_dir / _LEAN_COMPOSE_FILE),
        "--project-directory",
        str(lean_dir),
    ]


def _lean_backend_url(env_file: Path) -> str:
    port = _read_env_value(env_file, "ORCHEO_LEAN_PORT") or _DEFAULT_LEAN_PORT
    return f"http://localhost:{port}"


def _start_lean_stack(
    lean_dir: Path,
    *,
    env_file: Path,
    use_privileged_docker: bool,
    console: Console,
) -> None:
    compose_args = _lean_compose_args(lean_dir)
    command_runner = _run_privileged_command if use_privileged_docker else _run_command
    command_runner([*compose_args, "pull"], console=console)
    # The published image replaces the source build declared in the compose
    # file, which needs a full checkout.
    command_runner([*compose_args, "up", "-d", "--no-build"], console=console)
    if _poll_backend_health(_lean_backend_url(env_file), console=console):
        return
    console.print(
        "[yellow]Backend did not become healthy within "
        f"{_read_health_poll_timeout_seconds()} seconds.\n"
        "Check service logs with:[/yellow]\n"
        f"  docker compose -f {lean_dir / _LEAN_COMPOSE_FILE} logs"
    )


def _print_lean_summary(
    *,
    lean_dir: Path,
    env_file: Path,
    lean_version: str | None,
    started: bool,
    console: Console,
) -> None:
    public_url = _read_env_value(
        env_file, "ORCHEO_LEAN_PUBLIC_URL"
    ) or _lean_backend_url(env_file)
    console.print("\n[bold green]Lean setup complete[/bold green]")
    console.print_json(
        json.dumps(
            {
                "lean_version": lean_version,
                "lean_image": _lean_image(lean_version),
                "stack_started": started,
                "stack_project_dir": str(lean_dir),
                "stack_env_file": str(env_file),
            }
        )
    )
    if started:
        console.print(f"\n[bold cyan]Studio:[/bold cyan] {public_url}")
    else:
        console.print(
            "\nStart the stack with:\n"
            f"  docker compose -f {lean_dir / _LEAN_COMPOSE_FILE} "
            f"--project-directory {lean_dir} up -d --no-build"
        )
    console.print(
        f"\nThe CLI service token is ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN in {env_file}."
    )


def run_lean_install(
    *,
    lean_version: str | None,
    staging: bool,
    start_stack: bool | None,
    install_docker: bool | None,
    yes: bool,
    console: Console,
) -> None:
    """Fetch the lean compose assets, configure ``.env``, and start the stack."""
    if staging and lean_version is not None:
        raise typer.BadParameter("Use either --staging or --stack-version, not both.")
    resolved_start_stack, resolved_install_docker = _resolve_setup_toggles(
        start_stack=start_stack,
        install_docker=install_docker,
        yes=yes,
    )
    lean_dir = _resolve_lean_project_dir()
    lean_dir.mkdir(parents=True, exist_ok=True)
    resolved_version = _resolve_lean_version(
        lean_version,
        staging=staging,
        console=console,
    )
    _sync_lean_assets(lean_dir, lean_version=resolved_version, console=console)
    env_file = _configure_lean_env(
        lean_dir,
        lean_version=resolved_version,
        console=console,
    )

    started = False
    if resolved_start_stack:
        can_start, _, use_privileged_docker = _prepare_docker_for_start(
            install_docker_if_missing=resolved_install_docker,
            console=console,
        )
        if can_start:
            _start_lean_stack(
                lean_dir,
                env_file=env_file,
                use_privileged_docker=use_privileged_docker,
                console=console,
            )
            started = True

    _print_lean_summary(
        lean_dir=lean_dir,
        env_file=env_file,
        lean_version=resolved_version,
        started=started,
        console=console,
    )


__all__ = ["run_lean_install"]
