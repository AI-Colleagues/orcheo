"""`orcheo install --lean`: run the single-image lean stack with Docker Compose."""

from __future__ import annotations
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import urlopen
import click
import typer
from rich.console import Console
from orcheo.identity.email_domains import parse_email_domains
from orcheo_sdk.cli.setup import (
    SmtpEmailConfig,
    _backend_url_requires_https_auth,
    _discover_latest_stack_version,
    _docker_command,
    _is_prerelease_stack_version,
    _mask_secret,
    _normalize_optional_value,
    _poll_backend_health,
    _prepare_docker_for_start,
    _read_env_value,
    _read_health_poll_timeout_seconds,
    _resolve_backend_url,
    _resolve_chatkit_domain_key,
    _resolve_required_auth_config,
    _resolve_setup_toggles,
    _resolve_smtp_email_config,
    _run_command,
    _run_privileged_command,
    _upsert_env_values,
    _write_synced_asset,
    build_generated_stack_env_defaults,
    build_required_auth_env_updates,
    build_smtp_env_updates,
    ensure_stack_env_file,
    report_docker_readiness,
    report_env_preview,
)


_LEAN_RELEASE_TAG_PREFIX = "lean-v"
_LEAN_IMAGE_REPOSITORY = "ghcr.io/ai-colleagues/orcheo-lean"
_LEAN_ASSET_BASE_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/AI-Colleagues/orcheo/{ref}/deploy/lean"
)
# Paths inside deploy/lean/, synced flat into the lean directory. The ChatKit
# widgets ship inside the image, so no other assets are needed.
_LEAN_COMPOSE_FILE = "docker-compose.yml"
_LEAN_ENV_TEMPLATE = ".env.example"
_DEFAULT_LEAN_PORT = "2025"
_DEFAULT_LEAN_PUBLIC_URL = "http://localhost:2025"


@dataclass(slots=True)
class LeanSettings:
    """Prompted settings written into the lean ``.env``."""

    public_url: str
    chatkit_domain_key: str | None
    smtp: SmtpEmailConfig
    postgres_dsn: str = ""
    login_email_domains: str = ""
    auth_required: bool = False
    auth_jwt_secret: str | None = None
    auth_issuer: str | None = None
    auth_audience: str | None = None


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


def _sync_lean_asset(
    relative_path: str,
    lean_dir: Path,
    *,
    lean_version: str | None,
    console: Console,
    dry_run: bool = False,
) -> bytes:
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
        dry_run=dry_run,
    )
    return payload


def _sync_lean_assets(
    lean_dir: Path,
    *,
    lean_version: str | None,
    console: Console,
    dry_run: bool = False,
) -> dict[str, bytes]:
    """Sync the lean assets and return their downloaded payloads by path."""
    return {
        relative_path: _sync_lean_asset(
            relative_path,
            lean_dir,
            lean_version=lean_version,
            console=console,
            dry_run=dry_run,
        )
        for relative_path in (_LEAN_COMPOSE_FILE, _LEAN_ENV_TEMPLATE)
    }


def _validate_supabase_dsn(value: str) -> str:
    """Return a stripped PostgreSQL connection string or raise ``UsageError``."""
    dsn = value.strip()
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise click.UsageError(
            "Enter a postgresql:// connection string from Supabase "
            "(Project Settings > Database)."
        )
    if any(char.isspace() or not char.isprintable() for char in dsn):
        raise click.UsageError(
            "The connection string must be a single line; URL-encode spaces."
        )
    if "'" in dsn:
        raise click.UsageError("URL-encode single quotes in the connection string.")
    return dsn


def _resolve_supabase_dsn(
    supabase_connection_string: str | None,
    *,
    yes: bool,
    env_file: Path,
    env_exists: bool,
) -> str:
    """Resolve the required Supabase connection string (hidden prompt)."""
    existing = _read_env_value(env_file, "ORCHEO_POSTGRES_DSN") if env_exists else None
    selected = _normalize_optional_value(supabase_connection_string)
    if selected is None and not yes:
        masked_existing = _mask_secret(existing) if existing else None
        entered = typer.prompt(
            "Supabase connection string",
            default=masked_existing,
            hide_input=True,
            value_proc=lambda value: (
                value if value == masked_existing else _validate_supabase_dsn(value)
            ),
        )
        selected = existing if entered == masked_existing else entered
    selected = selected or existing
    if selected is None:
        raise typer.BadParameter(
            "A Supabase connection string is required for the lean stack. Pass "
            f"--supabase-connection-string or set ORCHEO_POSTGRES_DSN in {env_file}."
        )
    try:
        return _validate_supabase_dsn(selected)
    except click.UsageError as exc:
        raise typer.BadParameter(exc.message) from exc


def _normalize_login_email_domains(value: str) -> str:
    """Return a comma-joined allowlist; ``*`` or blank allows every domain."""
    if value.strip() == "*":
        return ""
    try:
        return ",".join(parse_email_domains(value))
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


def _resolve_login_email_domains(
    login_email_domains: str | None,
    *,
    yes: bool,
    env_file: Path,
    env_exists: bool,
) -> str:
    """Resolve the email domains allowed to sign in; blank allows any."""
    existing = (
        _read_env_value(env_file, "ORCHEO_AUTH_ALLOWED_EMAIL_DOMAINS")
        if env_exists
        else None
    )
    if login_email_domains is None and not yes:
        label = (
            "Login email domains (comma-separated; * allows any)"
            if existing
            else "Login email domains (comma-separated) - press Enter to allow any"
        )
        return typer.prompt(
            label,
            default=existing or "",
            show_default=bool(existing),
            value_proc=_normalize_login_email_domains,
        )
    try:
        return _normalize_login_email_domains(
            login_email_domains if login_email_domains is not None else existing or ""
        )
    except click.UsageError as exc:
        raise typer.BadParameter(exc.message) from exc


def _resolve_lean_settings(
    *,
    backend_url: str | None,
    supabase_connection_string: str | None,
    login_email_domains: str | None,
    chatkit_domain_key: str | None,
    smtp_host: str | None,
    smtp_port: int | None,
    smtp_username: str | None,
    smtp_password: str | None,
    smtp_from_email: str | None,
    smtp_use_tls: bool | None,
    yes: bool,
    env_file: Path,
) -> LeanSettings:
    """Resolve the lean prompts with the same resolvers as the full install.

    The backend URL is the browser-facing origin because the lean backend also
    serves Studio. SMTP port and STARTTLS are not prompted; they keep existing
    values or default to 587 with STARTTLS. As in the full install, an HTTPS
    URL (or an existing ``ORCHEO_AUTH_MODE=required``) requires sign-in; the
    issuer and audience keep their defaults instead of being prompted.
    """
    env_exists = env_file.exists()
    existing_public_url = (
        _read_env_value(env_file, "ORCHEO_LEAN_PUBLIC_URL") if env_exists else None
    )
    public_url, _ = _resolve_backend_url(
        backend_url,
        mode="install",
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
        default_backend_url=existing_public_url or _DEFAULT_LEAN_PUBLIC_URL,
        preserve_existing_default=False,
    )
    postgres_dsn = _resolve_supabase_dsn(
        supabase_connection_string,
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
    )
    resolved_login_email_domains = _resolve_login_email_domains(
        login_email_domains,
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
    )
    resolved_chatkit_domain_key = _resolve_chatkit_domain_key(
        chatkit_domain_key,
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
    )
    smtp = _resolve_smtp_email_config(
        smtp_host,
        smtp_port,
        smtp_username,
        smtp_password,
        smtp_from_email,
        smtp_use_tls,
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
        prompt_transport=False,
    )
    existing_auth_mode = (
        _read_env_value(env_file, "ORCHEO_AUTH_MODE") if env_exists else None
    )
    auth_required = (
        _backend_url_requires_https_auth(public_url) or existing_auth_mode == "required"
    )
    auth_jwt_secret, auth_issuer, auth_audience = _resolve_required_auth_config(
        auth_mode_required=auth_required,
        backend_url=public_url,
        yes=True,
        env_file=env_file,
        env_exists=env_exists,
    )
    if auth_jwt_secret is None:
        # The compose file requires the secret even while sign-in is off, so
        # keep the existing one or generate it (a copied template leaves it
        # blank).
        auth_jwt_secret = (
            _read_env_value(env_file, "ORCHEO_AUTH_JWT_SECRET") if env_exists else None
        ) or secrets.token_hex(32)
    return LeanSettings(
        public_url=public_url,
        chatkit_domain_key=resolved_chatkit_domain_key,
        smtp=smtp,
        auth_required=auth_required,
        auth_jwt_secret=auth_jwt_secret,
        auth_issuer=auth_issuer,
        auth_audience=auth_audience,
        postgres_dsn=postgres_dsn,
        login_email_domains=resolved_login_email_domains,
    )


def _build_lean_env_updates(
    settings: LeanSettings,
    *,
    lean_version: str | None,
) -> dict[str, str]:
    updates = {
        "ORCHEO_LEAN_IMAGE": _lean_image(lean_version),
        "ORCHEO_LEAN_PUBLIC_URL": settings.public_url,
        "ORCHEO_AUTH_ALLOWED_EMAIL_DOMAINS": settings.login_email_domains,
    }
    if settings.postgres_dsn:
        # Single quotes keep Compose from interpolating `$` in the password.
        updates["ORCHEO_POSTGRES_DSN"] = f"'{settings.postgres_dsn}'"
    if settings.auth_jwt_secret:
        updates["ORCHEO_AUTH_JWT_SECRET"] = settings.auth_jwt_secret
    if settings.chatkit_domain_key:
        updates["VITE_ORCHEO_CHATKIT_DOMAIN_KEY"] = settings.chatkit_domain_key
    updates.update(build_smtp_env_updates(settings.smtp))
    if settings.auth_required:
        updates.update(
            build_required_auth_env_updates(
                jwt_secret=settings.auth_jwt_secret,
                issuer=settings.auth_issuer,
                audience=settings.auth_audience,
            )
        )
    return updates


def _build_lean_generated_defaults() -> dict[str, str]:
    """Return secrets generated only for a freshly created lean .env file."""
    stack_defaults = build_generated_stack_env_defaults()
    return {
        "ORCHEO_VAULT_ENCRYPTION_KEY": stack_defaults["ORCHEO_VAULT_ENCRYPTION_KEY"],
        "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY": stack_defaults[
            "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY"
        ],
        "ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN": secrets.token_urlsafe(32),
    }


def _configure_lean_env(
    lean_dir: Path,
    *,
    lean_version: str | None,
    settings: LeanSettings,
    console: Console,
) -> Path:
    """Create or backfill ``.env`` from the template and apply the settings."""
    env_file = lean_dir / ".env"
    ensure_stack_env_file(
        env_file=env_file,
        env_template=lean_dir / _LEAN_ENV_TEMPLATE,
        console=console,
        generated_defaults=_build_lean_generated_defaults(),
    )
    _upsert_env_values(
        env_file,
        _build_lean_env_updates(settings, lean_version=lean_version),
        console=console,
    )
    return env_file


def _lean_compose_base_args(lean_dir: Path) -> list[str]:
    return [
        "compose",
        "-f",
        str(lean_dir / _LEAN_COMPOSE_FILE),
        "--project-directory",
        str(lean_dir),
    ]


def _lean_compose_args(lean_dir: Path) -> list[str]:
    docker_command = _docker_command()
    if docker_command is None:
        raise typer.BadParameter(
            "Docker appears to be installed, but the docker CLI could not be "
            "resolved in PATH."
        )
    return [*docker_command, *_lean_compose_base_args(lean_dir)]


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


def _plan_lean_start(
    lean_dir: Path,
    *,
    backend_url: str,
    install_docker_if_missing: bool,
    console: Console,
) -> None:
    """Report the Docker steps that starting the stack would take."""
    docker_command = report_docker_readiness(
        install_docker_if_missing=install_docker_if_missing,
        console=console,
    )
    compose_command = " ".join(
        [*(docker_command or ["docker"]), *_lean_compose_base_args(lean_dir)]
    )
    console.print(f"[yellow]Would run: {compose_command} pull[/yellow]")
    console.print(f"[yellow]Would run: {compose_command} up -d --no-build[/yellow]")
    console.print(
        "[yellow]Would wait for backend health at "
        f"{backend_url}/api/system/health[/yellow]"
    )


def _run_lean_dry_run(
    lean_dir: Path,
    *,
    lean_version: str | None,
    settings: LeanSettings,
    payloads: dict[str, bytes],
    start_stack: bool,
    install_docker_if_missing: bool,
    console: Console,
) -> None:
    """Preview the ``.env`` and Docker steps without changing anything.

    ``.env`` configuration runs against a temporary copy so the report matches
    a real run.
    """
    env_file = lean_dir / ".env"
    with tempfile.TemporaryDirectory(prefix="orcheo-lean-dry-run-") as temp_dir:
        preview_dir = Path(temp_dir)
        template_file = preview_dir / _LEAN_ENV_TEMPLATE
        template_file.write_bytes(payloads[_LEAN_ENV_TEMPLATE])
        if env_file.is_file():
            (preview_dir / ".env").write_bytes(env_file.read_bytes())
        preview_env_file = _configure_lean_env(
            preview_dir,
            lean_version=lean_version,
            settings=settings,
            console=Console(quiet=True),
        )
        report_env_preview(
            env_file,
            preview_env_file=preview_env_file,
            template_file=template_file,
            console=console,
        )
        backend_url = _lean_backend_url(preview_env_file)
    if start_stack:
        _plan_lean_start(
            lean_dir,
            backend_url=backend_url,
            install_docker_if_missing=install_docker_if_missing,
            console=console,
        )
    else:
        console.print("[cyan]Would skip starting the stack (--skip-stack).[/cyan]")
    console.print("\n[bold green]Dry run complete; nothing was changed.[/bold green]")


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
    auth_required = _read_env_value(env_file, "ORCHEO_AUTH_MODE") == "required"
    if auth_required and not _read_env_value(env_file, "ORCHEO_SMTP_HOST"):
        console.print(
            "[yellow]Sign-in is required but SMTP is not configured, so sign-in "
            "links and codes are written to the backend logs.[/yellow]"
        )
    if not auth_required and _read_env_value(
        env_file, "ORCHEO_AUTH_ALLOWED_EMAIL_DOMAINS"
    ):
        console.print(
            "[yellow]Login email domains only apply once sign-in is required "
            "(an https:// backend URL or ORCHEO_AUTH_MODE=required).[/yellow]"
        )


def run_lean_install(
    *,
    lean_version: str | None,
    staging: bool,
    start_stack: bool | None,
    install_docker: bool | None,
    yes: bool,
    console: Console,
    dry_run: bool = False,
    backend_url: str | None = None,
    supabase_connection_string: str | None = None,
    login_email_domains: str | None = None,
    chatkit_domain_key: str | None = None,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    smtp_username: str | None = None,
    smtp_password: str | None = None,
    smtp_from_email: str | None = None,
    smtp_use_tls: bool | None = None,
) -> None:
    """Fetch the lean compose assets, configure ``.env``, and start the stack.

    Only the backend URL, Supabase connection string, login email domains,
    ChatKit domain key, and SMTP sender are prompted;
    the stack starts and Docker is installed when missing unless overridden
    by flags. With ``dry_run`` the prompts, release lookup, and downloads
    still run, but nothing is written, Docker is not installed, and no
    compose command runs.
    """
    if staging and lean_version is not None:
        raise typer.BadParameter("Use either --staging or --stack-version, not both.")
    lean_dir = _resolve_lean_project_dir()
    settings = _resolve_lean_settings(
        backend_url=backend_url,
        supabase_connection_string=supabase_connection_string,
        login_email_domains=login_email_domains,
        chatkit_domain_key=chatkit_domain_key,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        smtp_from_email=smtp_from_email,
        smtp_use_tls=smtp_use_tls,
        yes=yes,
        env_file=lean_dir / ".env",
    )
    resolved_start_stack, resolved_install_docker = _resolve_setup_toggles(
        start_stack=start_stack,
        install_docker=install_docker,
        yes=True,
    )
    if dry_run:
        console.print(
            "[bold cyan]Dry run: no files, containers, or Docker installs will "
            "be changed.[/bold cyan]"
        )
    resolved_version = _resolve_lean_version(
        lean_version,
        staging=staging,
        console=console,
    )
    payloads = _sync_lean_assets(
        lean_dir,
        lean_version=resolved_version,
        console=console,
        dry_run=dry_run,
    )
    if dry_run:
        _run_lean_dry_run(
            lean_dir,
            lean_version=resolved_version,
            settings=settings,
            payloads=payloads,
            start_stack=resolved_start_stack,
            install_docker_if_missing=resolved_install_docker,
            console=console,
        )
        return
    env_file = _configure_lean_env(
        lean_dir,
        lean_version=resolved_version,
        settings=settings,
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
