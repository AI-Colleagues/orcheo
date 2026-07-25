"""Guided setup and upgrade command for the Orcheo stack."""

from __future__ import annotations
import getpass
import ipaddress
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit
from urllib.request import urlopen
import typer
from rich.console import Console
from orcheo.hosted_apps.setup_validation import validate_hosted_apps_setup


AuthMode = Literal["api-key", "oauth"]
SetupMode = Literal["install", "upgrade"]
_STACK_ASSET_BASE_URL = (
    "https://raw.githubusercontent.com/AI-Colleagues/orcheo/main/deploy/stack"
)
_STACK_ASSET_BASE_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/AI-Colleagues/orcheo/{ref}/deploy/stack"
)
_STACK_RELEASE_TAG_PREFIX = "stack-v"
_GITHUB_TAGS_API_URL = "https://api.github.com/repos/AI-Colleagues/orcheo/tags"
_GITHUB_CONTENTS_API_URL = (
    "https://api.github.com/repos/AI-Colleagues/orcheo/contents/deploy/stack"
)
_STACK_IMAGE_REPOSITORY = "ghcr.io/ai-colleagues/orcheo-stack"
_CHATKIT_WIDGETS_DIR = "chatkit_widgets"
_STACK_ASSET_FILES = (
    "docker-compose.yml",
    "Caddyfile",
    "Dockerfile.orcheo",
    ".env.example",
)
_CHATKIT_DOMAIN_KEY_PLACEHOLDER = "domain_pk_replace_me"
_OS_RELEASE_KEY_PATTERN = re.compile(r"^[A-Z0-9_]+$")
_MACOS_DOCKER_DESKTOP_DOWNLOADS = {
    "arm64": "https://desktop.docker.com/mac/main/arm64/Docker.dmg",
    "x86_64": "https://desktop.docker.com/mac/main/amd64/Docker.dmg",
}
_WINDOWS_DOCKER_DESKTOP_DOWNLOADS = {
    "arm64": "https://desktop.docker.com/win/main/arm64/Docker%20Desktop%20Installer.exe",
    "x86_64": "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe",
}
_DOCKER_READY_POLL_INTERVAL_SECONDS = 5
_DEFAULT_DOCKER_READY_TIMEOUT_SECONDS = 180


@dataclass(slots=True)
class SetupConfig:
    """Resolved setup options before execution."""

    mode: SetupMode
    backend_url: str
    studio_url: str
    auth_mode: AuthMode
    api_key: str | None
    chatkit_domain_key: str | None
    public_ingress_enabled: bool
    public_host: str | None
    publish_local_ports: bool
    backend_upstreams: str
    studio_upstream: str
    start_stack: bool
    install_docker_if_missing: bool
    install_agent_skills: bool = False
    preserve_existing_backend_url: bool = False
    stack_project_dir: str | None = None
    stack_env_file: str | None = None
    auth_mode_required: bool = False
    auth_jwt_secret: str | None = None
    auth_issuer: str | None = None
    auth_audience: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_use_tls: bool = True
    hosted_apps_enabled: bool = False
    apps_base_domain: str = "apps.localhost"
    hosted_apps_workspace_allowlist: str = ""
    app_gateway_secret: str | None = None
    app_trusted_proxy_cidrs: str = ""
    app_trusted_proxy_hops: int = 0
    app_tls_method: str = "local"
    app_tls_cert_file: str | None = None
    app_tls_key_file: str | None = None


def _run_command(command: list[str], *, console: Console) -> None:
    command_text = " ".join(command)
    console.print(f"[cyan]$ {command_text}[/cyan]")
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise typer.BadParameter(
            f"Command failed with exit code {result.returncode}: {command_text}"
        )


def _has_binary(name: str) -> bool:
    if name == "docker":
        _refresh_docker_cli_path_for_current_process()
    return shutil.which(name) is not None


def _normalized_machine() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine


def _docker_cli_path_candidates() -> list[Path]:
    system = platform.system()
    if system == "Darwin":
        return [
            Path("/usr/local/bin/docker"),
            Path("/opt/homebrew/bin/docker"),
            Path.home() / ".docker" / "bin" / "docker",
            Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
        ]
    if system == "Windows":
        program_files = Path(os.getenv("ProgramFiles", r"C:\Program Files"))
        return [
            program_files / "Docker" / "Docker" / "resources" / "bin" / "docker.exe"
        ]
    return []


def _refresh_docker_cli_path_for_current_process() -> None:
    current_path = os.environ.get("PATH", "")
    known_entries = set(filter(None, current_path.split(os.pathsep)))
    updated_entries = list(filter(None, current_path.split(os.pathsep)))

    for candidate in _docker_cli_path_candidates():
        if not candidate.exists():
            continue
        candidate_dir = str(candidate.parent)
        if candidate_dir in known_entries:
            continue  # pragma: no cover
        updated_entries.insert(0, candidate_dir)
        known_entries.add(candidate_dir)

    if updated_entries:
        os.environ["PATH"] = os.pathsep.join(updated_entries)


def _docker_command() -> list[str] | None:
    _refresh_docker_cli_path_for_current_process()
    docker_path = shutil.which("docker")
    if docker_path is None:
        return None
    return [docker_path]


def _read_os_release() -> dict[str, str]:
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return {}

    try:
        lines = os_release.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}

    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, raw_value = stripped.partition("=")
        if separator != "=":
            continue
        normalized_key = key.strip()
        if not _OS_RELEASE_KEY_PATTERN.fullmatch(normalized_key):
            continue
        values[normalized_key] = _normalize_dotenv_value(raw_value) or ""
    return values


def _is_supported_docker_autoinstall_linux() -> bool:
    if platform.system() != "Linux":
        return False

    os_release = _read_os_release()
    distro_id = os_release.get("ID", "").lower()
    distro_like = os_release.get("ID_LIKE", "").lower().split()
    if distro_id in {"ubuntu", "debian"}:
        return True
    return any(token in {"ubuntu", "debian"} for token in distro_like)


def _run_privileged_command(command: list[str], *, console: Console) -> None:
    if os.geteuid() == 0:
        _run_command(command, console=console)
        return
    if not _has_binary("sudo"):
        raise typer.BadParameter(
            "Automatic Docker installation requires root privileges or sudo."
        )
    _run_command(["sudo", *command], console=console)


def _current_username() -> str | None:
    username = _normalize_optional_value(os.getenv("SUDO_USER"))
    if username:
        return username
    try:
        return _normalize_optional_value(getpass.getuser())
    except (KeyError, OSError, ImportError):
        return None


def _current_shell_has_docker_access() -> bool:
    docker_command = _docker_command()
    if docker_command is None:
        return False
    result = subprocess.run(
        [*docker_command, "info"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_windows_elevated_command(command: list[str], *, console: Console) -> None:
    argument_list = ", ".join(_powershell_literal(arg) for arg in command[1:])
    powershell_command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        (
            "$process = Start-Process "
            f"-FilePath {_powershell_literal(command[0])} "
            f"-ArgumentList @({argument_list}) "
            "-Verb RunAs -Wait -PassThru; "
            "exit $process.ExitCode"
        ),
    ]
    _run_command(powershell_command, console=console)


def _read_docker_ready_timeout_seconds() -> int:
    raw = os.getenv("ORCHEO_SETUP_DOCKER_READY_TIMEOUT_SECONDS")
    if not raw:
        return _DEFAULT_DOCKER_READY_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_DOCKER_READY_TIMEOUT_SECONDS
    if value < 0:
        return _DEFAULT_DOCKER_READY_TIMEOUT_SECONDS
    return value


def _wait_for_docker_access(*, console: Console) -> bool:
    timeout_seconds = _read_docker_ready_timeout_seconds()
    console.print(
        "[cyan]Waiting for Docker to become available "
        f"(up to {timeout_seconds}s)...[/cyan]"
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _current_shell_has_docker_access():
            console.print("[green]Docker is ready.[/green]")
            return True
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(_DOCKER_READY_POLL_INTERVAL_SECONDS, remaining))
    return False


def _download_binary_asset(
    download_url: str,
    destination: Path,
    *,
    console: Console,
) -> None:
    console.print(f"[cyan]Downloading installer from {download_url}[/cyan]")
    try:
        with urlopen(download_url, timeout=60) as response:  # noqa: S310
            with destination.open("wb") as file_handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    file_handle.write(chunk)
    except OSError as exc:
        raise typer.BadParameter(
            f"Failed to download Docker installer from {download_url}: {exc}"
        ) from exc


def _start_docker_desktop(*, console: Console) -> None:
    system = platform.system()
    if system == "Darwin":
        _run_command(["open", "-a", "Docker"], console=console)
        return
    if system == "Windows":
        program_files = Path(os.getenv("ProgramFiles", r"C:\Program Files"))
        docker_desktop = program_files / "Docker" / "Docker" / "Docker Desktop.exe"
        if not docker_desktop.exists():
            raise typer.BadParameter(
                "Docker Desktop was installed but could not be found in Program Files."
            )
        _run_command(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"Start-Process -FilePath {_powershell_literal(str(docker_desktop))}",
            ],
            console=console,
        )
        return
    raise typer.BadParameter(
        f"Automatic Docker installation is not supported on {system}."
    )


def _current_windows_wsl_ready() -> bool:
    if platform.system() != "Windows":
        return True
    try:
        result = subprocess.run(
            ["wsl.exe", "--status"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _resolve_macos_docker_volume_path() -> Path | None:
    candidates = sorted(
        (
            path
            for path in Path("/Volumes").glob("Docker*")
            if (path / "Docker.app" / "Contents" / "MacOS" / "install").exists()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    return candidates[0]


def _ensure_windows_wsl(*, console: Console) -> bool:
    if platform.system() != "Windows":
        return True
    if _current_windows_wsl_ready():
        return True

    console.print(
        "[cyan]WSL 2 is not ready. Attempting automatic installation before "
        "Docker Desktop setup...[/cyan]"
    )
    try:
        _run_windows_elevated_command(
            ["wsl.exe", "--install", "--no-distribution", "--web-download"],
            console=console,
        )
    except (typer.BadParameter, FileNotFoundError) as exc:
        console.print(
            "[yellow]Automatic WSL installation failed: "
            f"{exc}. Docker Desktop may still require manual setup.[/yellow]"
        )
        return False

    if _current_windows_wsl_ready():
        return True

    console.print(
        "[yellow]WSL installation completed but is not ready yet. A Windows reboot "
        "may be required before Docker Desktop can start.[/yellow]"
    )
    return False


def _attempt_macos_docker_desktop_install(*, console: Console) -> bool:
    machine = _normalized_machine()
    download_url = _MACOS_DOCKER_DESKTOP_DOWNLOADS.get(machine)
    if download_url is None:
        console.print(
            "[yellow]Automatic Docker installation is not supported on this macOS "
            f"architecture ({machine}).[/yellow]"
        )
        return False

    username = _current_username()
    if username is None:
        console.print(
            "[yellow]Could not determine the current macOS username needed for "
            "Docker Desktop setup.[/yellow]"
        )
        return False

    with tempfile.TemporaryDirectory(prefix="orcheo-docker-") as temp_dir:
        dmg_path = Path(temp_dir) / "Docker.dmg"
        _download_binary_asset(download_url, dmg_path, console=console)
        attached = False
        mounted_volume: Path | None = None
        try:
            _run_privileged_command(
                ["hdiutil", "attach", str(dmg_path), "-nobrowse"],
                console=console,
            )
            attached = True
            mounted_volume = _resolve_macos_docker_volume_path()
            if mounted_volume is None:
                raise typer.BadParameter(
                    "Docker Desktop installer volume was mounted but could not be "
                    "located under /Volumes."
                )
            _run_privileged_command(
                [
                    str(
                        mounted_volume / "Docker.app" / "Contents" / "MacOS" / "install"
                    ),
                    "--accept-license",
                    f"--user={username}",
                ],
                console=console,
            )
        except (typer.BadParameter, FileNotFoundError) as exc:
            console.print(
                "[yellow]Automatic Docker Desktop installation failed on macOS: "
                f"{exc}[/yellow]"
            )
            return False
        finally:
            if attached and mounted_volume is not None:
                try:
                    _run_privileged_command(
                        ["hdiutil", "detach", str(mounted_volume)], console=console
                    )
                except typer.BadParameter:
                    console.print(
                        "[yellow]Docker installer volume is still mounted at "
                        f"{mounted_volume}. You may need to detach it "
                        "manually.[/yellow]"
                    )

    _refresh_docker_cli_path_for_current_process()
    _start_docker_desktop(console=console)
    return _wait_for_docker_access(console=console)


def _attempt_windows_docker_desktop_install(*, console: Console) -> bool:
    machine = _normalized_machine()
    download_url = _WINDOWS_DOCKER_DESKTOP_DOWNLOADS.get(machine)
    if download_url is None:
        console.print(
            "[yellow]Automatic Docker installation is not supported on this Windows "
            f"architecture ({machine}).[/yellow]"
        )
        return False

    if not _ensure_windows_wsl(console=console):
        return False

    with tempfile.TemporaryDirectory(prefix="orcheo-docker-") as temp_dir:
        installer_path = Path(temp_dir) / "Docker Desktop Installer.exe"
        _download_binary_asset(download_url, installer_path, console=console)
        try:
            _run_windows_elevated_command(
                [
                    str(installer_path),
                    "install",
                    "--accept-license",
                    "--backend=wsl-2",
                    "--quiet",
                ],
                console=console,
            )
        except (typer.BadParameter, FileNotFoundError) as exc:
            console.print(
                "[yellow]Automatic Docker Desktop installation failed on Windows: "
                f"{exc}[/yellow]"
            )
            return False

    _refresh_docker_cli_path_for_current_process()
    _start_docker_desktop(console=console)
    return _wait_for_docker_access(console=console)


def _attempt_linux_docker_autoinstall(*, console: Console) -> bool:
    if not _is_supported_docker_autoinstall_linux():
        return False
    if not _has_binary("apt-get"):
        console.print(
            "[yellow]Automatic Docker installation currently supports "
            "apt-based Ubuntu/Debian systems on Linux.[/yellow]"
        )
        return False

    try:
        _run_privileged_command(["apt-get", "update"], console=console)
        _run_privileged_command(
            ["apt-get", "install", "-y", "docker.io", "docker-compose-v2"],
            console=console,
        )
        _run_privileged_command(
            ["systemctl", "enable", "--now", "docker"], console=console
        )

        username = _current_username()
        if username:
            _run_privileged_command(
                ["usermod", "-aG", "docker", username], console=console
            )
    except (typer.BadParameter, FileNotFoundError) as exc:
        console.print(
            "[yellow]Automatic Docker installation failed: "
            f"{exc}. Continuing without starting the stack.[/yellow]"
        )
        return False

    if not _has_binary("docker"):
        console.print(
            "[yellow]Docker installation completed but the docker binary is still "
            "not available in PATH.[/yellow]"
        )
        return False
    return True


def _attempt_docker_autoinstall(*, console: Console) -> bool:
    installers = {
        "Darwin": (
            "[cyan]Docker is missing. Attempting automatic Docker Desktop "
            "installation on macOS...[/cyan]",
            _attempt_macos_docker_desktop_install,
        ),
        "Windows": (
            "[cyan]Docker is missing. Attempting automatic Docker Desktop "
            "installation on Windows...[/cyan]",
            _attempt_windows_docker_desktop_install,
        ),
        "Linux": (
            "[cyan]Docker is missing. Attempting automatic installation on "
            "Ubuntu/Debian...[/cyan]",
            _attempt_linux_docker_autoinstall,
        ),
    }
    message_and_installer = installers.get(platform.system())
    if message_and_installer is None:
        return False

    message, installer = message_and_installer
    console.print(message)
    return installer(console=console)


def _resolve_mode(
    mode: SetupMode | None, *, yes: bool, env_exists: bool = False
) -> SetupMode:
    if mode is not None:
        return mode
    default: SetupMode = "upgrade" if env_exists else "install"
    if yes:
        return default
    selected = typer.prompt("Setup mode [install/upgrade]", default=default).strip()
    return "upgrade" if selected == "upgrade" else "install"


def _resolve_backend_url(
    backend_url: str | None,
    *,
    mode: SetupMode,
    yes: bool,
    env_file: Path | None = None,
    env_exists: bool = False,
    default_backend_url: str = "http://localhost:2025",
    preserve_existing_default: bool = True,
) -> tuple[str, bool]:
    if backend_url:
        return backend_url, False
    if preserve_existing_default and (mode == "upgrade" or env_exists):
        if yes:
            return default_backend_url, True
        existing = (
            _read_env_value(env_file, "ORCHEO_API_URL")
            if env_file is not None and env_exists
            else None
        )
        prompt_default = existing or default_backend_url
        selected = _normalize_optional_value(
            typer.prompt("Backend URL", default=prompt_default)
        )
        if selected is None or selected == existing:
            return default_backend_url, True
        return selected, False
    if yes:
        return default_backend_url, False
    return typer.prompt("Backend URL", default=default_backend_url), False


def _resolve_studio_url(
    studio_url: str | None,
    *,
    public_ingress_enabled: bool,
    public_host: str | None,
    yes: bool,
    env_file: Path,
    env_exists: bool,
) -> str:
    normalized = _normalize_optional_value(studio_url)
    if normalized is not None:
        return normalized
    default = (
        f"https://{public_host}"
        if public_ingress_enabled and public_host is not None
        else "http://localhost:2026"
    )
    if env_exists:
        existing = _read_env_value(env_file, "ORCHEO_STUDIO_URL")
        if existing:
            default = existing
    if yes:
        return default
    return typer.prompt("Studio URL", default=default)


def _resolve_public_ingress_enabled(
    public_ingress: bool | None,
    *,
    yes: bool,
    env_file: Path,
    env_exists: bool,
    mode: SetupMode,
) -> bool:
    if public_ingress is not None:
        return public_ingress
    existing_default = False
    if env_exists:
        parsed = _parse_bool_value(
            _read_env_value(env_file, "ORCHEO_PUBLIC_INGRESS_ENABLED")
        )
        if parsed is not None:
            existing_default = parsed
    if yes:
        return existing_default
    return typer.confirm(
        "Enable bundled public HTTPS ingress with Caddy?",
        default=existing_default,
    )


def _resolve_public_host(
    public_host: str | None,
    *,
    public_ingress_enabled: bool,
    yes: bool,
    env_file: Path,
    env_exists: bool,
) -> str | None:
    if not public_ingress_enabled:
        return None
    normalized = _normalize_optional_value(public_host)
    if normalized is not None:
        return _normalize_public_host(normalized)
    if env_exists:
        existing = _read_env_value(env_file, "ORCHEO_PUBLIC_HOST")
        if existing:
            return _normalize_public_host(existing)
    if yes:
        raise typer.BadParameter(
            "--public-host is required when bundled public ingress is enabled."
        )
    return _normalize_public_host(typer.prompt("Public hostname"))


def _resolve_publish_local_ports(
    publish_local_ports: bool | None,
    *,
    public_ingress_enabled: bool,
    yes: bool,
    env_file: Path,
    env_exists: bool,
) -> bool:
    if publish_local_ports is not None:
        return publish_local_ports
    if env_exists:
        existing = _parse_bool_value(
            _read_env_value(env_file, "ORCHEO_PUBLISH_LOCAL_PORTS")
        )
        if existing is not None:
            return existing
    if not public_ingress_enabled:
        return True
    if yes:
        return True
    return typer.confirm(
        "Keep localhost backend and Studio ports published?",
        default=True,
    )


def _resolve_public_ingress_config(
    *,
    public_ingress: bool | None,
    public_host: str | None,
    publish_local_ports: bool | None,
    yes: bool,
    env_file: Path,
    env_exists: bool,
    mode: SetupMode,
) -> tuple[bool, str | None, bool]:
    resolved_public_ingress_enabled = _resolve_public_ingress_enabled(
        public_ingress,
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
        mode=mode,
    )
    resolved_public_host = _resolve_public_host(
        public_host,
        public_ingress_enabled=resolved_public_ingress_enabled,
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
    )
    resolved_publish_local_ports = _resolve_publish_local_ports(
        publish_local_ports,
        public_ingress_enabled=resolved_public_ingress_enabled,
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
    )
    return (
        resolved_public_ingress_enabled,
        resolved_public_host,
        resolved_publish_local_ports,
    )


def _resolve_setup_toggles(
    *,
    start_stack: bool | None,
    install_docker: bool | None,
    yes: bool,
) -> tuple[bool, bool]:
    resolved_start_stack = _resolve_bool(
        start_stack,
        yes_default=yes,
        prompt="Start stack with docker compose after install?",
        default=True,
    )
    resolved_install_docker = _resolve_bool(
        install_docker,
        yes_default=yes,
        prompt="Install Docker when missing?",
        default=True,
    )
    return (
        resolved_start_stack,
        resolved_install_docker,
    )


def _resolve_stack_upstreams(env_file: Path, *, env_exists: bool) -> tuple[str, str]:
    backend_upstreams = "backend:2025"
    studio_upstream = "studio:2026"
    if not env_exists:
        return backend_upstreams, studio_upstream
    existing_backend_upstreams = _read_env_value(
        env_file, "ORCHEO_CADDY_BACKEND_UPSTREAMS"
    )
    existing_studio_upstream = _read_env_value(env_file, "ORCHEO_CADDY_STUDIO_UPSTREAM")
    if existing_backend_upstreams:
        backend_upstreams = existing_backend_upstreams
    if existing_studio_upstream:
        studio_upstream = existing_studio_upstream
    return backend_upstreams, studio_upstream


def _print_setup_resolution_notes(
    *,
    console: Console,
    resolved_api_key: str | None,
    manual_secrets: bool,
    yes: bool,
    resolved_auth_mode: AuthMode,
    preserve_existing_backend_url: bool,
    resolved_public_ingress_enabled: bool,
    resolved_public_host: str | None,
    resolved_publish_local_ports: bool,
) -> None:
    if resolved_api_key and not manual_secrets and not yes:
        console.print("[green]Generated API key locally.[/green]")
    if resolved_auth_mode == "api-key" and resolved_api_key is None:
        console.print(
            "[cyan]Keeping existing API bootstrap token. "
            "Pass --api-key to rotate it.[/cyan]"
        )
    if preserve_existing_backend_url:
        console.print(
            "[cyan]Keeping existing backend URL. "
            "Pass --backend-url to update it.[/cyan]"
        )
    if resolved_public_ingress_enabled:
        console.print(
            "[cyan]Bundled public ingress enabled for "
            f"{resolved_public_host}. Caddy expects DNS for that hostname and "
            "inbound 80/443 to reach this host.[/cyan]"
        )
        if not resolved_publish_local_ports:
            console.print(
                "[cyan]Local backend/studio ports will stay disabled; "
                "access should go through the public hostname only.[/cyan]"
            )
    if resolved_auth_mode == "oauth":
        console.print(
            "[yellow]OAuth mode selected. Configure ORCHEO_AUTH_JWT_SECRET, "
            "ORCHEO_AUTH_ISSUER, and ORCHEO_AUTH_AUDIENCE in your stack "
            ".env for the first-party email IdP.[/yellow]"
        )


def _resolve_auth_mode(auth_mode: AuthMode | None, *, yes: bool) -> AuthMode:
    if auth_mode is not None:
        return auth_mode
    if yes:
        return "api-key"
    selected = typer.prompt("Auth mode [api-key/oauth]", default="api-key").strip()
    return "oauth" if selected == "oauth" else "api-key"


def _resolve_bool(
    explicit: bool | None,
    *,
    yes_default: bool,
    prompt: str,
    default: bool,
) -> bool:
    if explicit is not None:
        return explicit
    if yes_default:
        return default
    return typer.confirm(prompt, default=default)


def _resolve_api_key(
    auth_mode: AuthMode,
    api_key: str | None,
    *,
    mode: SetupMode,
    manual: bool,
    env_exists: bool = False,
) -> str | None:
    if auth_mode != "api-key":
        return None
    if api_key:
        return api_key
    if mode == "upgrade" or env_exists:
        if manual:
            provided = typer.prompt(
                "Enter API key (Enter to keep existing)",
                default="",
                show_default=False,
                hide_input=True,
            ).strip()
            return provided or None
        return None
    if manual:
        return typer.prompt("Enter API key", hide_input=True)
    return secrets.token_urlsafe(32)


def _normalize_optional_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_dotenv_value(value: str | None) -> str | None:
    """Normalize a value read from a dotenv line.

    This strips whitespace and unwraps matching single or double quotes.
    """
    normalized = _normalize_optional_value(value)
    if normalized is None:
        return None
    if (
        len(normalized) >= 2
        and normalized[0] == normalized[-1]
        and normalized[0]
        in {
            '"',
            "'",
        }
    ):
        normalized = normalized[1:-1].strip()
    return normalized or None


def _parse_bool_value(value: str | None) -> bool | None:
    normalized = _normalize_dotenv_value(value)
    if normalized is None:
        return None
    lowered = normalized.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def _parse_int_value(value: str | None) -> int | None:
    normalized = _normalize_dotenv_value(value)
    if normalized is None:
        return None
    try:
        return int(normalized)
    except ValueError:
        return None


def _normalize_public_host(value: str) -> str:
    candidate = value.strip().lower()
    if not candidate:
        raise typer.BadParameter("Public hostname is required.")
    if "://" in candidate:
        raise typer.BadParameter(
            "Public hostname must be a hostname only, without http:// or https://."
        )
    if any(token in candidate for token in {"/", "?", "#", " "}):
        raise typer.BadParameter(
            "Public hostname must not contain paths, query strings, or spaces."
        )
    if not _PUBLIC_HOST_PATTERN.fullmatch(candidate):
        raise typer.BadParameter(
            "Public hostname may only contain letters, numbers, dots, and hyphens."
        )
    return candidate


def _mask_secret(value: str) -> str:
    if len(value) <= 4:
        return value
    return f"****{value[-4:]}"


def _mask_chatkit_domain_key(value: str) -> str:
    return _mask_secret(value)


def _resolve_chatkit_domain_key(
    chatkit_domain_key: str | None,
    *,
    yes: bool,
    env_file: Path | None = None,
    env_exists: bool = False,
) -> str | None:
    resolved = _normalize_optional_value(chatkit_domain_key)
    if resolved is not None:
        return resolved
    existing = (
        _read_env_value(env_file, "VITE_ORCHEO_CHATKIT_DOMAIN_KEY")
        if env_file is not None and env_exists
        else None
    )
    if yes:
        return existing

    prompt = (
        "ChatKit domain key"
        if existing is not None
        else "ChatKit domain key - press Enter to skip"
    )
    masked_default = _mask_chatkit_domain_key(existing) if existing is not None else ""
    selected = _normalize_optional_value(
        typer.prompt(
            prompt,
            default=masked_default,
            show_default=existing is not None,
        )
    )
    if existing is not None and selected == masked_default:
        return existing
    if selected is not None:
        return selected
    return existing


_DEFAULT_SMTP_FROM_EMAIL = "no-reply@orcheo.cloud"
_DEFAULT_SMTP_PORT = 587


@dataclass(slots=True)
class SmtpEmailConfig:
    """Resolved SMTP transactional-email settings for the stack .env."""

    host: str | None
    port: int
    username: str | None
    password: str | None
    from_email: str | None
    use_tls: bool


def _resolve_smtp_email_config(
    smtp_host: str | None,
    smtp_port: int | None,
    smtp_username: str | None,
    smtp_password: str | None,
    smtp_from_email: str | None,
    smtp_use_tls: bool | None,
    *,
    yes: bool,
    env_file: Path,
    env_exists: bool,
) -> SmtpEmailConfig:
    """Resolve the SMTP transactional-email sender configuration.

    SMTP delivers both workspace invitations and first-party auth sign-in
    links/codes. With no host the backend logs links/codes instead of sending
    email, so every SMTP setting is optional.
    """
    existing_host = (
        _read_env_value(env_file, "ORCHEO_SMTP_HOST") if env_exists else None
    )
    existing_port = (
        _read_env_value(env_file, "ORCHEO_SMTP_PORT") if env_exists else None
    )
    existing_username = (
        _read_env_value(env_file, "ORCHEO_SMTP_USERNAME") if env_exists else None
    )
    existing_password = (
        _read_env_value(env_file, "ORCHEO_SMTP_PASSWORD") if env_exists else None
    )
    existing_from = (
        _read_env_value(env_file, "ORCHEO_SMTP_FROM_EMAIL") if env_exists else None
    )
    existing_use_tls = (
        _parse_bool_value(_read_env_value(env_file, "ORCHEO_SMTP_USE_TLS"))
        if env_exists
        else None
    )

    host = _normalize_optional_value(smtp_host) or existing_host
    if not yes:
        if host is None:
            host = _normalize_optional_value(
                typer.prompt(
                    "SMTP host",
                    default="",
                    show_default=False,
                )
            )
        else:
            host = (
                _normalize_optional_value(typer.prompt("SMTP host", default=host))
                or host
            )

    port = smtp_port or _parse_int_value(existing_port) or _DEFAULT_SMTP_PORT
    username = _normalize_optional_value(smtp_username) or existing_username
    password = _normalize_optional_value(smtp_password) or existing_password
    from_email = _normalize_optional_value(smtp_from_email) or existing_from
    use_tls = smtp_use_tls if smtp_use_tls is not None else existing_use_tls

    if host is not None and not yes:
        port = _parse_int_value(typer.prompt("SMTP port", default=str(port))) or port
        username = (
            _normalize_optional_value(
                typer.prompt("SMTP username", default=username or "")
            )
            or username
        )
        masked_password = _mask_secret(password) if password else ""
        entered_password = _normalize_optional_value(
            typer.prompt(
                "SMTP password",
                default=masked_password,
                show_default=bool(password),
            )
        )
        if entered_password is not None and entered_password != masked_password:
            password = entered_password
        from_email = (
            _normalize_optional_value(
                typer.prompt(
                    "Transactional email sender address",
                    default=from_email or _DEFAULT_SMTP_FROM_EMAIL,
                )
            )
            or from_email
            or _DEFAULT_SMTP_FROM_EMAIL
        )
        use_tls = typer.confirm(
            "Use STARTTLS for the SMTP connection?",
            default=True if use_tls is None else use_tls,
        )

    return SmtpEmailConfig(
        host=host,
        port=port,
        username=username,
        password=password,
        from_email=from_email or (_DEFAULT_SMTP_FROM_EMAIL if host else None),
        use_tls=True if use_tls is None else use_tls,
    )


@dataclass(slots=True)
class HostedAppsInstallConfig:
    """Resolved Hosted Apps settings for the bundled single-node stack."""

    enabled: bool
    base_domain: str
    workspace_allowlist: str
    gateway_secret: str
    trusted_proxy_cidrs: str
    trusted_proxy_hops: int
    tls_method: str
    tls_cert_file: str | None
    tls_key_file: str | None


def _normalize_apps_base_domain(value: str) -> str:
    """Normalize one bare registrable app-hosting domain."""
    candidate = value.strip().lower()
    if candidate.startswith("*."):
        candidate = candidate[2:]
    if "://" in candidate or "/" in candidate or "." not in candidate:
        raise typer.BadParameter(
            "The Hosted Apps base domain must be a bare DNS name such as "
            "apps.example.com."
        )
    return _normalize_public_host(candidate)


def _resolve_readable_file(value: str | None, *, option_name: str) -> str:
    """Resolve a required readable file supplied to the production installer."""
    normalized = _normalize_optional_value(value)
    if normalized is None:
        raise typer.BadParameter(
            f"{option_name} is required when Hosted Apps uses public ingress."
        )
    path = Path(normalized).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.R_OK):
        raise typer.BadParameter(f"{option_name} must reference a readable file.")
    return str(path)


def _resolve_hosted_apps_enabled(
    hosted_apps: bool | None,
    *,
    public_ingress_enabled: bool,
    yes: bool,
    env_file: Path,
    env_exists: bool,
) -> bool:
    """Resolve enablement without silently opting public installs into hosting."""
    existing_enabled = (
        _parse_bool_value(_read_env_value(env_file, "ORCHEO_HOSTED_APPS_ENABLED"))
        if env_exists
        else None
    )
    if hosted_apps is not None:
        return hosted_apps
    if public_ingress_enabled and not yes:
        return typer.confirm(
            "Enable Hosted Apps for this public installation?",
            default=existing_enabled if existing_enabled is not None else False,
        )
    if public_ingress_enabled:
        return existing_enabled if existing_enabled is not None else False
    return existing_enabled if existing_enabled is not None else True


def _validate_supported_hosted_apps_storage(
    *, enabled: bool, env_file: Path, env_exists: bool
) -> None:
    """Reject existing topologies that this installer cannot safely manage."""
    if not enabled or not env_exists:
        return
    existing_bundle_backend = _read_env_value(env_file, "ORCHEO_APP_BUNDLE_BACKEND")
    existing_deployment_mode = _read_env_value(env_file, "ORCHEO_DEPLOYMENT_MODE")
    if existing_bundle_backend not in {None, "filesystem"}:
        raise typer.BadParameter(
            "The bundled Hosted Apps installer currently supports filesystem "
            "bundle storage only. Preserve custom S3 deployments outside this "
            "installer until the production presigned-upload flow is available."
        )
    if existing_deployment_mode not in {None, "local", "single-node"}:
        raise typer.BadParameter(
            "The bundled Hosted Apps installer supports local or single-node "
            "deployments only."
        )


def _resolve_hosted_apps_domain_and_allowlist(
    *,
    enabled: bool,
    apps_base_domain: str | None,
    hosted_apps_workspace_allowlist: str | None,
    public_ingress_enabled: bool,
    public_host: str | None,
    yes: bool,
    env_file: Path,
    env_exists: bool,
) -> tuple[str, str]:
    """Resolve the wildcard domain and optional workspace rollout allowlist."""
    existing_domain = (
        _read_env_value(env_file, "ORCHEO_APPS_BASE_DOMAIN") if env_exists else None
    )
    default_domain = existing_domain or (
        f"apps.{public_host}"
        if public_ingress_enabled and public_host is not None
        else "apps.localhost"
    )
    selected_domain = apps_base_domain
    if enabled and public_ingress_enabled and selected_domain is None and not yes:
        selected_domain = typer.prompt(
            "Hosted Apps base domain",
            default=default_domain,
        )
    base_domain = _normalize_apps_base_domain(selected_domain or default_domain)

    existing_allowlist = (
        _read_env_value(env_file, "ORCHEO_HOSTED_APPS_WORKSPACE_ALLOWLIST")
        if env_exists
        else None
    )
    workspace_allowlist = (
        hosted_apps_workspace_allowlist
        if hosted_apps_workspace_allowlist is not None
        else existing_allowlist or ""
    )
    if enabled and public_ingress_enabled and not yes:
        workspace_allowlist = typer.prompt(
            "Hosted Apps workspace allowlist (comma-separated; empty allows all)",
            default=workspace_allowlist,
            show_default=bool(workspace_allowlist),
        ).strip()
    return base_domain, workspace_allowlist


def _resolve_app_gateway_secret(
    *, manual_secrets: bool, yes: bool, env_file: Path, env_exists: bool
) -> str:
    """Preserve, prompt for, or generate the dedicated gateway identity."""
    existing_secret = (
        _normalize_optional_value(
            _read_env_value(env_file, "ORCHEO_APP_GATEWAY_SECRET")
        )
        if env_exists
        else None
    )
    gateway_secret = existing_secret
    if gateway_secret is None and manual_secrets and not yes:
        gateway_secret = typer.prompt(
            "Hosted Apps gateway secret",
            hide_input=True,
        ).strip()
    if gateway_secret is None:
        gateway_secret = secrets.token_hex(32)
    if len(gateway_secret) < 32:
        raise typer.BadParameter(
            "The Hosted Apps gateway secret must be at least 32 characters."
        )
    return gateway_secret


def _resolve_app_proxy_config(
    *,
    enabled: bool,
    app_trusted_proxy_cidrs: str | None,
    app_trusted_proxy_hops: int | None,
    public_ingress_enabled: bool,
    yes: bool,
    env_file: Path,
    env_exists: bool,
) -> tuple[str, int]:
    """Resolve the exact forwarding boundary used for app client addresses."""
    existing_cidrs = (
        _read_env_value(env_file, "ORCHEO_APP_TRUSTED_PROXY_CIDRS")
        if env_exists
        else None
    )
    trusted_proxy_cidrs = (
        app_trusted_proxy_cidrs
        if app_trusted_proxy_cidrs is not None
        else existing_cidrs or ("172.16.0.0/12" if public_ingress_enabled else "")
    )
    existing_hops_raw = (
        _read_env_value(env_file, "ORCHEO_APP_TRUSTED_PROXY_HOPS")
        if env_exists
        else None
    )
    trusted_proxy_hops = (
        app_trusted_proxy_hops
        if app_trusted_proxy_hops is not None
        else _parse_int_value(existing_hops_raw) or (1 if public_ingress_enabled else 0)
    )
    if enabled and public_ingress_enabled and not yes:
        trusted_proxy_cidrs = typer.prompt(
            "Trusted proxy CIDRs for the app gateway",
            default=trusted_proxy_cidrs,
        ).strip()
        trusted_proxy_hops = (
            _parse_int_value(
                typer.prompt(
                    "Trusted proxy hop count",
                    default=str(trusted_proxy_hops),
                )
            )
            or 0
        )
    return trusted_proxy_cidrs, trusted_proxy_hops


def _resolve_app_tls_config(
    *,
    enabled: bool,
    app_tls_cert_file: str | None,
    app_tls_key_file: str | None,
    public_ingress_enabled: bool,
    yes: bool,
    env_file: Path,
    env_exists: bool,
) -> tuple[str, str | None, str | None]:
    """Resolve the bundled Caddy local or provided-certificate configuration."""
    if not enabled or not public_ingress_enabled:
        return "local", None, None
    existing_tls_method = (
        _read_env_value(env_file, "ORCHEO_APP_TLS_METHOD") if env_exists else None
    )
    if existing_tls_method not in {None, "local", "provided"}:
        raise typer.BadParameter(
            "The bundled installer currently supports operator-provided wildcard "
            "certificates for public Hosted Apps. Configure custom DNS-01 ingress "
            "outside the bundled installer."
        )
    existing_cert = (
        _read_env_value(env_file, "ORCHEO_APP_TLS_CERT_FILE") if env_exists else None
    )
    existing_key = (
        _read_env_value(env_file, "ORCHEO_APP_TLS_KEY_FILE") if env_exists else None
    )
    selected_cert = app_tls_cert_file or existing_cert
    selected_key = app_tls_key_file or existing_key
    if not yes:
        selected_cert = typer.prompt(
            "Wildcard TLS certificate file",
            default=selected_cert or "",
            show_default=bool(selected_cert),
        )
        selected_key = typer.prompt(
            "Wildcard TLS private-key file",
            default=selected_key or "",
            show_default=bool(selected_key),
        )
    return (
        "provided",
        _resolve_readable_file(
            selected_cert,
            option_name="--app-tls-cert-file",
        ),
        _resolve_readable_file(
            selected_key,
            option_name="--app-tls-key-file",
        ),
    )


def _resolve_hosted_apps_config(
    *,
    hosted_apps: bool | None,
    apps_base_domain: str | None,
    hosted_apps_workspace_allowlist: str | None,
    app_tls_cert_file: str | None,
    app_tls_key_file: str | None,
    app_trusted_proxy_cidrs: str | None,
    app_trusted_proxy_hops: int | None,
    public_ingress_enabled: bool,
    public_host: str | None,
    yes: bool,
    manual_secrets: bool,
    env_file: Path,
    env_exists: bool,
) -> HostedAppsInstallConfig:
    """Resolve local defaults or the complete supported public-app topology."""
    enabled = _resolve_hosted_apps_enabled(
        hosted_apps,
        public_ingress_enabled=public_ingress_enabled,
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
    )
    _validate_supported_hosted_apps_storage(
        enabled=enabled,
        env_file=env_file,
        env_exists=env_exists,
    )
    base_domain, workspace_allowlist = _resolve_hosted_apps_domain_and_allowlist(
        enabled=enabled,
        apps_base_domain=apps_base_domain,
        hosted_apps_workspace_allowlist=hosted_apps_workspace_allowlist,
        public_ingress_enabled=public_ingress_enabled,
        public_host=public_host,
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
    )
    gateway_secret = _resolve_app_gateway_secret(
        manual_secrets=manual_secrets,
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
    )
    trusted_proxy_cidrs, trusted_proxy_hops = _resolve_app_proxy_config(
        enabled=enabled,
        app_trusted_proxy_cidrs=app_trusted_proxy_cidrs,
        app_trusted_proxy_hops=app_trusted_proxy_hops,
        public_ingress_enabled=public_ingress_enabled,
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
    )
    tls_method, tls_cert_file, tls_key_file = _resolve_app_tls_config(
        enabled=enabled,
        app_tls_cert_file=app_tls_cert_file,
        app_tls_key_file=app_tls_key_file,
        public_ingress_enabled=public_ingress_enabled,
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
    )

    return HostedAppsInstallConfig(
        enabled=enabled,
        base_domain=base_domain,
        workspace_allowlist=workspace_allowlist,
        gateway_secret=gateway_secret,
        trusted_proxy_cidrs=trusted_proxy_cidrs,
        trusted_proxy_hops=trusted_proxy_hops,
        tls_method=tls_method,
        tls_cert_file=tls_cert_file,
        tls_key_file=tls_key_file,
    )


def _backend_url_requires_https_auth(backend_url: str) -> bool:
    parsed = urlsplit(backend_url)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _is_loopback_backend_url(backend_url: str) -> bool:
    parsed = urlsplit(backend_url)
    if parsed.scheme not in {"http", "ws"} or not parsed.hostname:
        return False
    host = parsed.hostname.strip().lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_local_hosting(config: SetupConfig) -> bool:
    """Return ``True`` when the stack is hosted locally for trusted authors.

    Local hosting means no bundled public ingress and a non-HTTPS loopback
    backend.  In that case every workflow author is the operator themselves, so
    client workflow uploads are enabled.  Any publicly reachable deployment
    stays in the secure-by-default managed mode.
    """
    return not config.public_ingress_enabled and _is_loopback_backend_url(
        config.backend_url
    )


def _resolve_workflow_modes(config: SetupConfig) -> tuple[str, str]:
    """Resolve ``(trust_mode, definition_mode)`` for the deployment topology.

    * **Local hosting** (loopback ``http://`` backend, no bundled public
      ingress): the operator is the sole workflow author, so client uploads are
      allowed and run in-process (``unrestricted``).
    * **Trusted HTTPS backend**: client uploads are allowed but compiled to the
      frozen IR and executed with ``CodeNode`` bodies sandboxed
      (``restricted``), giving tenant isolation for publicly reachable stacks.
    * **Anything else** (untrusted non-loopback ``http://`` backend): uploads
      stay ``managed`` and execution remains ``unrestricted``.
    """
    if _is_local_hosting(config):
        return "allow_client_uploads", "unrestricted"
    if _backend_url_requires_https_auth(config.backend_url):
        return "allow_client_uploads", "restricted"
    return "managed", "unrestricted"


_DEFAULT_AUTH_ISSUER = "https://auth.orcheo.cloud"
_DEFAULT_AUTH_AUDIENCE = "orcheo-api"


def _resolve_defaulted_env_prompt(
    *,
    label: str,
    current_value: str,
    yes: bool,
) -> str:
    if yes:
        return current_value
    selected = _normalize_optional_value(typer.prompt(label, default=current_value))
    return selected or current_value


def _resolve_required_auth_config(
    *,
    auth_mode_required: bool,
    backend_url: str,
    yes: bool,
    env_file: Path,
    env_exists: bool,
) -> tuple[str | None, str | None, str | None]:
    """Resolve first-party auth settings when the backend requires auth.

    Returns ``(jwt_secret, issuer, audience)``. The HS256 signing secret is
    preserved from the existing .env or generated when absent; the issuer
    defaults to the HTTPS backend URL when available and the audience falls
    back to the first-party IdP default.
    """
    if not auth_mode_required:
        return None, None, None

    existing_secret = (
        _read_env_value(env_file, "ORCHEO_AUTH_JWT_SECRET") if env_exists else None
    )
    existing_issuer = (
        _read_env_value(env_file, "ORCHEO_AUTH_ISSUER") if env_exists else None
    )
    existing_audience = (
        _read_env_value(env_file, "ORCHEO_AUTH_AUDIENCE") if env_exists else None
    )

    jwt_secret = existing_secret or secrets.token_hex(32)
    issuer_default = (
        backend_url
        if _backend_url_requires_https_auth(backend_url)
        else _DEFAULT_AUTH_ISSUER
    )
    issuer = _resolve_defaulted_env_prompt(
        label="Auth issuer",
        current_value=existing_issuer or issuer_default,
        yes=yes,
    )
    audience = _resolve_defaulted_env_prompt(
        label="Auth audience",
        current_value=existing_audience or _DEFAULT_AUTH_AUDIENCE,
        yes=yes,
    )
    return jwt_secret, issuer, audience


def _resolve_https_auth_config(
    *,
    backend_url: str,
    yes: bool,
    env_file: Path,
    env_exists: bool,
) -> tuple[str | None, str | None, str | None]:
    """Backward-compatible wrapper for the required-auth resolver."""
    return _resolve_required_auth_config(
        auth_mode_required=_backend_url_requires_https_auth(backend_url),
        backend_url=backend_url,
        yes=yes,
        env_file=env_file,
        env_exists=env_exists,
    )


def _resolve_stack_project_dir() -> Path:
    configured = os.getenv("ORCHEO_STACK_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".orcheo" / "stack"


def _resolve_stack_env_file() -> Path:
    return _resolve_stack_project_dir() / ".env"


def _resolve_stack_asset_base_url(*, stack_version: str | None = None) -> str:
    configured = os.getenv("ORCHEO_STACK_ASSET_BASE_URL")
    if configured:
        return configured.rstrip("/")
    if stack_version is None:
        return _STACK_ASSET_BASE_URL
    ref = f"{_STACK_RELEASE_TAG_PREFIX}{stack_version}"
    return _STACK_ASSET_BASE_URL_TEMPLATE.format(ref=ref)


def _is_prerelease_stack_version(version: str) -> bool:
    return "-" in version


def _normalize_stack_version(version: str | None) -> str | None:
    resolved = _normalize_optional_value(version)
    if resolved is None:
        return None
    if resolved.startswith(_STACK_RELEASE_TAG_PREFIX):
        resolved = resolved.removeprefix(_STACK_RELEASE_TAG_PREFIX)
    return resolved or None


def _resolve_stack_version(explicit: str | None) -> str | None:
    resolved = _normalize_stack_version(explicit)
    if resolved is not None:
        return resolved
    return _normalize_stack_version(os.getenv("ORCHEO_STACK_VERSION"))


def _discover_latest_stack_version(console: Console) -> str | None:
    tags_url = f"{_GITHUB_TAGS_API_URL}?per_page=100"
    try:
        with urlopen(tags_url, timeout=10) as response:  # noqa: S310
            payload = response.read().decode("utf-8")
        tags = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        console.print(
            "[yellow]Unable to discover latest stack version from tags; "
            f"falling back to main branch assets: {exc}[/yellow]"
        )
        return None

    if not isinstance(tags, list):
        console.print(
            "[yellow]Unexpected stack tags API response; "
            "falling back to main branch assets.[/yellow]"
        )
        return None

    for tag in tags:
        if not isinstance(tag, dict):
            continue
        tag_name = tag.get("name")
        if not isinstance(tag_name, str):
            continue
        if not tag_name.startswith(_STACK_RELEASE_TAG_PREFIX):
            continue

        version = _normalize_stack_version(tag_name)
        if version is not None and not _is_prerelease_stack_version(version):
            return version

    return None


def _list_chatkit_widget_paths(
    stack_version: str | None,
    console: Console,
) -> tuple[str, ...]:
    """List files in chatkit_widgets/ via the GitHub Contents API.

    Returns an empty tuple when the API is unreachable so setup can continue
    without relying on a stale hard-coded widget catalog.
    """
    ref = f"{_STACK_RELEASE_TAG_PREFIX}{stack_version}" if stack_version else "main"
    url = f"{_GITHUB_CONTENTS_API_URL}/{_CHATKIT_WIDGETS_DIR}?ref={ref}"
    try:
        with urlopen(url, timeout=10) as response:  # noqa: S310
            entries = json.loads(response.read().decode("utf-8"))
        if not isinstance(entries, list):
            raise ValueError("expected a JSON array")
        return tuple(
            f"{_CHATKIT_WIDGETS_DIR}/{entry['name']}"
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
            f"[yellow]Could not list {_CHATKIT_WIDGETS_DIR}/ from GitHub; "
            f"skipping widget sync: {exc}[/yellow]"
        )
        return ()


def _download_stack_asset(
    relative_path: str,
    *,
    stack_version: str | None,
    console: Console,
) -> bytes:
    asset_url = (
        f"{_resolve_stack_asset_base_url(stack_version=stack_version)}"
        f"/{quote(relative_path, safe='/')}"
    )
    console.print(f"[cyan]Fetching stack asset: {relative_path}[/cyan]")
    try:
        with urlopen(asset_url, timeout=30) as response:  # noqa: S310
            return response.read()
    except OSError as exc:
        raise typer.BadParameter(
            f"Failed to download stack asset '{relative_path}' from {asset_url}: {exc}"
        ) from exc


def _sync_stack_asset(
    relative_path: str,
    stack_dir: Path,
    *,
    stack_version: str | None,
    console: Console,
) -> None:
    destination = stack_dir / relative_path
    remote_payload = _download_stack_asset(
        relative_path,
        stack_version=stack_version,
        console=console,
    )

    if destination.exists():
        local_payload = destination.read_bytes()
        if local_payload == remote_payload:
            return
        destination.write_bytes(remote_payload)
        console.print(f"[green]Updated stack asset: {relative_path}[/green]")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(remote_payload)
    console.print(f"[green]Downloaded stack asset: {relative_path}[/green]")


def _sync_stack_assets_per_file(
    stack_dir: Path,
    *,
    stack_version: str | None,
    console: Console,
) -> None:
    for relative_path in _STACK_ASSET_FILES:
        _sync_stack_asset(
            relative_path,
            stack_dir,
            stack_version=stack_version,
            console=console,
        )
    for relative_path in _list_chatkit_widget_paths(stack_version, console):
        _sync_stack_asset(
            relative_path,
            stack_dir,
            stack_version=stack_version,
            console=console,
        )


def _sync_stack_assets_with_best_source(
    stack_dir: Path,
    *,
    stack_version: str | None,
    console: Console,
) -> str | None:
    resolved_stack_version = _resolve_stack_version(stack_version)
    configured_stack_asset_base_url = _normalize_optional_value(
        os.getenv("ORCHEO_STACK_ASSET_BASE_URL")
    )
    if configured_stack_asset_base_url is None and resolved_stack_version is None:
        resolved_stack_version = _discover_latest_stack_version(console)

    _sync_stack_assets_per_file(
        stack_dir,
        stack_version=resolved_stack_version,
        console=console,
    )
    return resolved_stack_version


_ENV_KEY_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
_PUBLIC_HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")


def _compose_profiles(config: SetupConfig) -> str:
    profiles: list[str] = []
    if config.public_ingress_enabled:
        profiles.append("public-ingress")
    return ",".join(profiles)


def _studio_origin(studio_url: str) -> str | None:
    parsed = urlsplit(studio_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


def _studio_host(studio_url: str) -> str | None:
    return urlsplit(studio_url).hostname


def _build_cors_origins(config: SetupConfig) -> str:
    origins: list[str] = []
    studio_origin = _studio_origin(config.studio_url)
    if studio_origin is not None:
        origins.append(studio_origin)
    if config.public_ingress_enabled and config.public_host is not None:
        origins.append(f"https://{config.public_host}")
    if not config.public_ingress_enabled or config.publish_local_ports:
        origins.extend(
            [
                "http://localhost:2026",
                "http://127.0.0.1:2026",
            ]
        )
    deduped = list(dict.fromkeys(origins))
    return ",".join(deduped)


def _build_allowed_hosts(config: SetupConfig) -> str:
    hosts = ["localhost", "127.0.0.1"]
    studio_host = _studio_host(config.studio_url)
    if studio_host is not None:
        hosts.append(studio_host)
    if config.public_ingress_enabled and config.public_host is not None:
        hosts.append(config.public_host)
    return ",".join(dict.fromkeys(hosts))


def _build_healthcheck_url(config: SetupConfig) -> str | None:
    if config.public_ingress_enabled:
        if config.publish_local_ports:
            return "http://localhost:2025"
        return None
    return config.backend_url


def _build_env_updates(
    config: SetupConfig,
    *,
    requested_stack_version: str | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(updates, defaults)`` for .env upsert.

    ``updates`` are always applied.  ``defaults`` contain auto-generated
    secrets that should only be written to a freshly-created .env file.
    """
    trust_mode, definition_mode = _resolve_workflow_modes(config)
    updates: dict[str, str] = {
        "ORCHEO_API_URL": config.backend_url,
        "VITE_ORCHEO_BACKEND_URL": config.backend_url,
        "ORCHEO_STUDIO_URL": config.studio_url,
        "ORCHEO_CORS_ALLOW_ORIGINS": _build_cors_origins(config),
        "VITE_ORCHEO_ALLOWED_HOSTS": _build_allowed_hosts(config),
        "ORCHEO_PUBLIC_INGRESS_ENABLED": str(config.public_ingress_enabled).lower(),
        "ORCHEO_PUBLIC_HOST": config.public_host or "",
        "ORCHEO_PUBLISH_LOCAL_PORTS": str(config.publish_local_ports).lower(),
        "COMPOSE_PROFILES": _compose_profiles(config),
        "ORCHEO_CADDY_SITE_ADDRESS": config.public_host or "",
        "ORCHEO_CADDY_BACKEND_UPSTREAMS": config.backend_upstreams,
        "ORCHEO_CADDY_STUDIO_UPSTREAM": config.studio_upstream,
        "ORCHEO_WORKFLOW_TRUST_MODE": trust_mode,
        "ORCHEO_WORKFLOW_DEFINITION_MODE": definition_mode,
        "ORCHEO_HOSTED_APPS_ENABLED": str(config.hosted_apps_enabled).lower(),
        "ORCHEO_HOSTED_APPS_AUTO_ENABLE_RUNTIME": "true",
        "ORCHEO_APPS_BASE_DOMAIN": config.apps_base_domain,
        "ORCHEO_HOSTED_APPS_WORKSPACE_ALLOWLIST": (
            config.hosted_apps_workspace_allowlist
        ),
        "ORCHEO_APP_BUNDLE_BACKEND": "filesystem",
        "ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT": "/data/app-bundles",
        "ORCHEO_DEPLOYMENT_MODE": "single-node",
        "ORCHEO_APP_GATEWAY_SECRET": config.app_gateway_secret or "",
        "ORCHEO_APP_TRUSTED_PROXY_CIDRS": config.app_trusted_proxy_cidrs,
        "ORCHEO_APP_TRUSTED_PROXY_HOPS": str(config.app_trusted_proxy_hops),
        "ORCHEO_HOSTED_APPS_VALIDATION_QUEUE": "hosted-app-validation",
        "ORCHEO_APP_TLS_METHOD": config.app_tls_method,
        "ORCHEO_APP_TLS_CERT_FILE": config.app_tls_cert_file or "",
        "ORCHEO_APP_TLS_KEY_FILE": config.app_tls_key_file or "",
    }
    if config.auth_mode == "api-key" and config.api_key:
        updates["ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN"] = config.api_key
    elif config.auth_mode == "oauth":
        updates["ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN"] = ""
    if config.chatkit_domain_key:
        updates["VITE_ORCHEO_CHATKIT_DOMAIN_KEY"] = config.chatkit_domain_key
    if config.smtp_host:
        updates["ORCHEO_SMTP_HOST"] = config.smtp_host
        updates["ORCHEO_SMTP_PORT"] = str(config.smtp_port)
        updates["ORCHEO_SMTP_USERNAME"] = config.smtp_username or ""
        updates["ORCHEO_SMTP_PASSWORD"] = config.smtp_password or ""
        updates["ORCHEO_SMTP_FROM_EMAIL"] = (
            config.smtp_from_email or _DEFAULT_SMTP_FROM_EMAIL
        )
        updates["ORCHEO_SMTP_USE_TLS"] = str(config.smtp_use_tls).lower()
    if config.auth_mode_required:
        jwt_secret = _normalize_optional_value(config.auth_jwt_secret)
        if jwt_secret is None:
            jwt_secret = secrets.token_hex(32)
        issuer = _normalize_optional_value(config.auth_issuer) or _DEFAULT_AUTH_ISSUER
        audience = (
            _normalize_optional_value(config.auth_audience) or _DEFAULT_AUTH_AUDIENCE
        )
        updates["ORCHEO_AUTH_MODE"] = "required"
        updates["ORCHEO_AUTH_JWT_SECRET"] = jwt_secret
        updates["ORCHEO_AUTH_ISSUER"] = issuer
        updates["ORCHEO_AUTH_AUDIENCE"] = audience
        updates["VITE_ORCHEO_AUTH_DISABLED"] = "false"
    if requested_stack_version:
        updates["ORCHEO_STACK_IMAGE"] = (
            f"{_STACK_IMAGE_REPOSITORY}:{requested_stack_version}"
        )

    defaults = build_generated_stack_env_defaults()
    return updates, defaults


def build_generated_stack_env_defaults() -> dict[str, str]:
    """Return auto-generated defaults for a freshly-created stack env file."""
    return {
        "ORCHEO_POSTGRES_PASSWORD": secrets.token_urlsafe(16),
        "ORCHEO_VAULT_ENCRYPTION_KEY": secrets.token_hex(32),
        "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY": secrets.token_urlsafe(32),
        "ORCHEO_APP_GATEWAY_SECRET": secrets.token_hex(32),
    }


def _read_env_assignments(env_file: Path) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        match = _ENV_KEY_PATTERN.match(line)
        if not match:
            continue
        key = match.group(1)
        _, _, value = line.partition("=")
        assignments[key] = value.strip()
    return assignments


def _read_env_value(env_file: Path, key: str) -> str | None:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        match = _ENV_KEY_PATTERN.match(line)
        if not match or match.group(1) != key:
            continue
        _, _, value = line.partition("=")
        return _normalize_dotenv_value(value)
    return None


def _warn_chatkit_domain_key_missing(*, env_file: Path, console: Console) -> None:
    configured_value = _read_env_value(env_file, "VITE_ORCHEO_CHATKIT_DOMAIN_KEY")
    if configured_value and configured_value != _CHATKIT_DOMAIN_KEY_PLACEHOLDER:
        return
    console.print(
        "[yellow]ChatKit domain key is not configured. ChatKit UI features will not "
        "work until VITE_ORCHEO_CHATKIT_DOMAIN_KEY is set in "
        f"{env_file}. You can rerun setup with --chatkit-domain-key.[/yellow]"
    )


def _upsert_env_values(
    env_file: Path,
    updates: dict[str, str],
    *,
    defaults: dict[str, str] | None = None,
    console: Console,
) -> None:
    """Upsert environment values into the .env file.

    Keys in *updates* always overwrite existing values.  Keys in *defaults*
    only overwrite when the key is already present in the file; missing keys
    are appended.
    """
    original = env_file.read_text(encoding="utf-8")
    lines = original.splitlines()
    pending_updates = dict(updates)
    pending_defaults = dict(defaults or {})
    rewritten: list[str] = []

    for line in lines:
        match = _ENV_KEY_PATTERN.match(line)
        if not match:
            rewritten.append(line)
            continue

        key = match.group(1)
        if key in pending_updates:
            rewritten.append(f"{key}={pending_updates.pop(key)}")
        elif key in pending_defaults:
            rewritten.append(f"{key}={pending_defaults.pop(key)}")
        else:
            rewritten.append(line)

    for key, value in pending_updates.items():
        rewritten.append(f"{key}={value}")
    for key, value in pending_defaults.items():
        rewritten.append(f"{key}={value}")

    updated = "\n".join(rewritten)
    if updated:
        updated += "\n"
    if updated == original:
        return

    env_file.write_text(updated, encoding="utf-8")
    console.print(f"[green]Updated stack env file at {env_file}[/green]")


def ensure_stack_env_file(
    *,
    env_file: Path,
    env_template: Path,
    console: Console,
    generated_defaults: dict[str, str] | None = None,
) -> bool:
    """Create or backfill a stack env file from a template.

    Returns ``True`` when the env file was created during this call.
    """
    if not env_template.exists():
        raise typer.BadParameter(  # pragma: no cover - defensive check
            f"Stack env template not found: {env_template}"
        )

    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_created = not env_file.exists()
    if env_created:
        shutil.copyfile(env_template, env_file)
        console.print(f"[green]Created stack env file at {env_file}[/green]")

    template_defaults = _read_env_assignments(env_template)
    defaults_to_apply: dict[str, str]
    if env_created:
        defaults_to_apply = dict(template_defaults)
        defaults_to_apply.update(generated_defaults or {})
    else:
        existing_assignments = _read_env_assignments(env_file)
        defaults_to_apply = {
            key: value
            for key, value in template_defaults.items()
            if key not in existing_assignments
        }
        for key, value in (generated_defaults or {}).items():
            if not existing_assignments.get(key):
                defaults_to_apply[key] = value

    if defaults_to_apply:
        _upsert_env_values(env_file, {}, defaults=defaults_to_apply, console=console)
    return env_created


def _preserve_existing_stack_browser_urls(
    *,
    env_file: Path,
    updates: dict[str, str],
    config: SetupConfig,
) -> None:
    if config.preserve_existing_backend_url:
        preserved_orcheo_api_url = _read_env_value(env_file, "ORCHEO_API_URL")
        if preserved_orcheo_api_url is not None:
            updates.pop("ORCHEO_API_URL", None)
            config.backend_url = preserved_orcheo_api_url

        if _read_env_value(env_file, "VITE_ORCHEO_BACKEND_URL") is not None:
            updates.pop("VITE_ORCHEO_BACKEND_URL", None)

    if not config.public_ingress_enabled:  # pragma: no branch
        for key in (
            "ORCHEO_CORS_ALLOW_ORIGINS",
            "VITE_ORCHEO_ALLOWED_HOSTS",
        ):
            if _read_env_value(env_file, key) is not None:
                updates.pop(key, None)


def _configure_hosted_apps_tls(
    config: SetupConfig,
    *,
    stack_dir: Path,
) -> None:
    """Write the Caddy TLS snippet and copy operator-provided wildcard keys."""
    tls_dir = stack_dir / "app-tls"
    tls_dir.mkdir(parents=True, exist_ok=True)
    directive_file = tls_dir / "Caddyfile"

    if (
        config.hosted_apps_enabled
        and config.public_ingress_enabled
        and config.app_tls_method == "provided"
    ):
        certificate_source = Path(config.app_tls_cert_file or "")
        key_source = Path(config.app_tls_key_file or "")
        certificate_target = tls_dir / "cert.pem"
        key_target = tls_dir / "key.pem"
        if certificate_source.resolve() != certificate_target.resolve():
            shutil.copy2(certificate_source, certificate_target)
        if key_source.resolve() != key_target.resolve():
            shutil.copy2(key_source, key_target)
        certificate_target.chmod(0o600)
        key_target.chmod(0o600)
        config.app_tls_cert_file = str(certificate_target.resolve())
        config.app_tls_key_file = str(key_target.resolve())
        directive = "tls /etc/orcheo/app-tls/cert.pem /etc/orcheo/app-tls/key.pem\n"
    else:
        directive = "tls internal\n"

    directive_file.write_text(directive, encoding="utf-8")
    directive_file.chmod(0o644)


def _run_hosted_apps_preflight(
    config: SetupConfig,
    *,
    env_file: Path,
    console: Console,
) -> None:
    """Validate the resolved Hosted Apps topology before Compose starts."""
    if not config.hosted_apps_enabled:
        console.print(
            "[cyan]Hosted Apps disabled; skipping app-hosting preflight.[/cyan]"
        )
        return

    environment = {**os.environ, **_read_env_assignments(env_file)}
    if config.app_tls_cert_file is not None:
        environment["ORCHEO_APP_TLS_CERT_FILE"] = config.app_tls_cert_file
    if config.app_tls_key_file is not None:
        environment["ORCHEO_APP_TLS_KEY_FILE"] = config.app_tls_key_file
    try:
        facts = validate_hosted_apps_setup(
            environment,
            check_dns=config.public_ingress_enabled and config.start_stack,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise typer.BadParameter(f"Hosted Apps preflight failed: {exc}") from exc

    console.print("[green]Hosted Apps preflight passed:[/green]")
    for fact in facts:
        console.print(f"  {fact}")


def _ensure_stack_assets(
    *,
    config: SetupConfig,
    console: Console,
    stack_version: str | None = None,
) -> tuple[Path, Path]:
    stack_dir = _resolve_stack_project_dir()
    stack_dir.mkdir(parents=True, exist_ok=True)

    requested_stack_version = _resolve_stack_version(stack_version)
    synced_stack_version = _sync_stack_assets_with_best_source(
        stack_dir,
        stack_version=requested_stack_version,
        console=console,
    )

    env_file = stack_dir / ".env"
    if config.preserve_existing_backend_url and env_file.exists():
        preserved_backend_url = _read_env_value(env_file, "ORCHEO_API_URL")
        if preserved_backend_url is not None:
            config.backend_url = preserved_backend_url

    updates, defaults = _build_env_updates(
        config,
        requested_stack_version=requested_stack_version,
    )
    env_template = stack_dir / ".env.example"
    if not env_template.exists():
        _sync_stack_asset(
            ".env.example",
            stack_dir,
            stack_version=synced_stack_version,
            console=console,
        )
    env_created = ensure_stack_env_file(
        env_file=env_file,
        env_template=env_template,
        console=console,
        generated_defaults=defaults,
    )
    if not env_created:
        _preserve_existing_stack_browser_urls(
            env_file=env_file,
            updates=updates,
            config=config,
        )

    _upsert_env_values(env_file, updates, console=console)
    return stack_dir, env_file


def run_setup(
    *,
    mode: SetupMode | None,
    backend_url: str | None,
    studio_url: str | None,
    auth_mode: AuthMode | None,
    api_key: str | None,
    chatkit_domain_key: str | None,
    public_ingress: bool | None,
    public_host: str | None,
    publish_local_ports: bool | None,
    start_stack: bool | None,
    install_docker: bool | None,
    yes: bool,
    manual_secrets: bool,
    console: Console,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    smtp_username: str | None = None,
    smtp_password: str | None = None,
    smtp_from_email: str | None = None,
    smtp_use_tls: bool | None = None,
    hosted_apps: bool | None = None,
    apps_base_domain: str | None = None,
    hosted_apps_workspace_allowlist: str | None = None,
    app_tls_cert_file: str | None = None,
    app_tls_key_file: str | None = None,
    app_trusted_proxy_cidrs: str | None = None,
    app_trusted_proxy_hops: int | None = None,
) -> SetupConfig:
    """Collect interactive/non-interactive setup options."""
    stack_env_file = _resolve_stack_env_file()
    has_existing_stack_env = stack_env_file.exists()
    if has_existing_stack_env:
        console.print(
            "[cyan]Detected existing stack env file at "
            f"{stack_env_file}. Existing values will be preserved by default "
            "unless you explicitly override them.[/cyan]"
        )

    resolved_mode = _resolve_mode(mode, yes=yes, env_exists=has_existing_stack_env)
    (
        resolved_public_ingress_enabled,
        resolved_public_host,
        resolved_publish_local_ports,
    ) = _resolve_public_ingress_config(
        public_ingress=public_ingress,
        public_host=public_host,
        publish_local_ports=publish_local_ports,
        yes=yes,
        env_file=stack_env_file,
        env_exists=has_existing_stack_env,
        mode=resolved_mode,
    )
    resolved_hosted_apps = _resolve_hosted_apps_config(
        hosted_apps=hosted_apps,
        apps_base_domain=apps_base_domain,
        hosted_apps_workspace_allowlist=hosted_apps_workspace_allowlist,
        app_tls_cert_file=app_tls_cert_file,
        app_tls_key_file=app_tls_key_file,
        app_trusted_proxy_cidrs=app_trusted_proxy_cidrs,
        app_trusted_proxy_hops=app_trusted_proxy_hops,
        public_ingress_enabled=resolved_public_ingress_enabled,
        public_host=resolved_public_host,
        yes=yes,
        manual_secrets=manual_secrets,
        env_file=stack_env_file,
        env_exists=has_existing_stack_env,
    )
    default_backend_url = (
        f"https://{resolved_public_host}"
        if resolved_public_ingress_enabled and resolved_public_host is not None
        else "http://localhost:2025"
    )
    preserve_existing_backend_default = not (
        backend_url is None
        and public_ingress is True
        and resolved_public_ingress_enabled
        and resolved_public_host is not None
    )
    resolved_backend_url, preserve_existing_backend_url = _resolve_backend_url(
        backend_url,
        mode=resolved_mode,
        yes=yes,
        env_file=stack_env_file,
        env_exists=has_existing_stack_env,
        default_backend_url=default_backend_url,
        preserve_existing_default=preserve_existing_backend_default,
    )
    resolved_studio_url = _resolve_studio_url(
        studio_url,
        public_ingress_enabled=resolved_public_ingress_enabled,
        public_host=resolved_public_host,
        yes=yes,
        env_file=stack_env_file,
        env_exists=has_existing_stack_env,
    )
    auth_backend_url = resolved_backend_url
    if preserve_existing_backend_url and has_existing_stack_env:
        preserved_backend_url = _read_env_value(stack_env_file, "ORCHEO_API_URL")
        if preserved_backend_url is not None:
            auth_backend_url = preserved_backend_url
    existing_auth_mode = (
        _read_env_value(stack_env_file, "ORCHEO_AUTH_MODE")
        if has_existing_stack_env
        else None
    )
    resolved_auth_mode_required = (
        _backend_url_requires_https_auth(auth_backend_url)
        or existing_auth_mode == "required"
    )
    (
        resolved_auth_jwt_secret,
        resolved_auth_issuer,
        resolved_auth_audience,
    ) = _resolve_required_auth_config(
        auth_mode_required=resolved_auth_mode_required,
        backend_url=auth_backend_url,
        yes=yes,
        env_file=stack_env_file,
        env_exists=has_existing_stack_env,
    )
    resolved_auth_mode = _resolve_auth_mode(auth_mode, yes=yes)

    resolved_api_key = _resolve_api_key(
        resolved_auth_mode,
        api_key,
        mode=resolved_mode,
        manual=manual_secrets,
        env_exists=has_existing_stack_env,
    )
    resolved_chatkit_domain_key = _resolve_chatkit_domain_key(
        chatkit_domain_key,
        yes=yes,
        env_file=stack_env_file,
        env_exists=has_existing_stack_env,
    )
    resolved_smtp = _resolve_smtp_email_config(
        smtp_host,
        smtp_port,
        smtp_username,
        smtp_password,
        smtp_from_email,
        smtp_use_tls,
        yes=yes,
        env_file=stack_env_file,
        env_exists=has_existing_stack_env,
    )
    resolved_backend_upstreams, resolved_studio_upstream = _resolve_stack_upstreams(
        stack_env_file,
        env_exists=has_existing_stack_env,
    )
    (
        resolved_start_stack,
        resolved_install_docker,
    ) = _resolve_setup_toggles(
        start_stack=start_stack,
        install_docker=install_docker,
        yes=yes,
    )
    resolved_install_agent_skills = _resolve_bool(
        None,
        yes_default=yes,
        prompt="Install Orcheo skill for Claude Code and Codex?",
        default=True,
    )
    _print_setup_resolution_notes(
        console=console,
        resolved_api_key=resolved_api_key,
        manual_secrets=manual_secrets,
        yes=yes,
        resolved_auth_mode=resolved_auth_mode,
        preserve_existing_backend_url=preserve_existing_backend_url,
        resolved_public_ingress_enabled=resolved_public_ingress_enabled,
        resolved_public_host=resolved_public_host,
        resolved_publish_local_ports=resolved_publish_local_ports,
    )

    return SetupConfig(
        mode=resolved_mode,
        backend_url=resolved_backend_url,
        studio_url=resolved_studio_url,
        auth_mode=resolved_auth_mode,
        api_key=resolved_api_key,
        chatkit_domain_key=resolved_chatkit_domain_key,
        public_ingress_enabled=resolved_public_ingress_enabled,
        public_host=resolved_public_host,
        publish_local_ports=resolved_publish_local_ports,
        backend_upstreams=resolved_backend_upstreams,
        studio_upstream=resolved_studio_upstream,
        start_stack=resolved_start_stack,
        install_docker_if_missing=resolved_install_docker,
        install_agent_skills=resolved_install_agent_skills,
        preserve_existing_backend_url=preserve_existing_backend_url,
        auth_mode_required=resolved_auth_mode_required,
        auth_jwt_secret=resolved_auth_jwt_secret,
        auth_issuer=resolved_auth_issuer,
        auth_audience=resolved_auth_audience,
        smtp_host=resolved_smtp.host,
        smtp_port=resolved_smtp.port,
        smtp_username=resolved_smtp.username,
        smtp_password=resolved_smtp.password,
        smtp_from_email=resolved_smtp.from_email,
        smtp_use_tls=resolved_smtp.use_tls,
        hosted_apps_enabled=resolved_hosted_apps.enabled,
        apps_base_domain=resolved_hosted_apps.base_domain,
        hosted_apps_workspace_allowlist=resolved_hosted_apps.workspace_allowlist,
        app_gateway_secret=resolved_hosted_apps.gateway_secret,
        app_trusted_proxy_cidrs=resolved_hosted_apps.trusted_proxy_cidrs,
        app_trusted_proxy_hops=resolved_hosted_apps.trusted_proxy_hops,
        app_tls_method=resolved_hosted_apps.tls_method,
        app_tls_cert_file=resolved_hosted_apps.tls_cert_file,
        app_tls_key_file=resolved_hosted_apps.tls_key_file,
    )


_HEALTH_POLL_INTERVAL_SECONDS = 5
_DEFAULT_HEALTH_POLL_TIMEOUT_SECONDS = 60


def _read_health_poll_timeout_seconds() -> int:
    raw = os.getenv("ORCHEO_SETUP_HEALTH_POLL_TIMEOUT_SECONDS")
    if not raw:
        return _DEFAULT_HEALTH_POLL_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_HEALTH_POLL_TIMEOUT_SECONDS
    if value < 0:
        return _DEFAULT_HEALTH_POLL_TIMEOUT_SECONDS
    return value


def _poll_backend_health(
    backend_url: str,
    *,
    console: Console,
) -> bool:
    """Poll the backend until it responds or the timeout expires."""
    health_url = f"{backend_url.rstrip('/')}/api/system/health"
    timeout_seconds = _read_health_poll_timeout_seconds()
    console.print(
        f"[cyan]Waiting for backend at {health_url} "
        f"(up to {timeout_seconds}s)...[/cyan]"
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(health_url, timeout=_HEALTH_POLL_INTERVAL_SECONDS) as resp:  # noqa: S310
                if resp.status == 200:
                    console.print("[green]Backend is healthy.[/green]")
                    return True
        except OSError:
            pass
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(_HEALTH_POLL_INTERVAL_SECONDS, remaining))
    return False


def _compose_profile_args(stack_dir: Path) -> list[str]:
    env_file = stack_dir / ".env"
    if not env_file.exists():
        return []
    raw_profiles = _read_env_value(env_file, "COMPOSE_PROFILES")
    if raw_profiles is None:
        return []
    profiles = [
        profile.strip() for profile in raw_profiles.split(",") if profile.strip()
    ]
    args: list[str] = []
    for profile in profiles:
        args.extend(["--profile", profile])
    return args


def _prepare_stack_start(
    config: SetupConfig,
    *,
    console: Console,
) -> tuple[bool, bool]:
    docker_installed_this_run = False
    use_privileged_docker = False

    if config.start_stack and not _has_binary("docker"):
        if not config.install_docker_if_missing:
            raise typer.BadParameter(
                "Docker is required to start the stack, and you chose "
                "--skip-docker-install. Install Docker and rerun setup."
            )
        if not _attempt_docker_autoinstall(console=console):
            console.print(
                "[yellow]Docker is missing and automatic installation could "
                "not complete. Continuing without starting the stack. "
                "Install Docker (https://docs.docker.com/get-docker/) and "
                "rerun with --start-stack.[/yellow]"
            )
            config.start_stack = False
            return docker_installed_this_run, use_privileged_docker
        docker_installed_this_run = True

    if config.start_stack and not _current_shell_has_docker_access():
        if docker_installed_this_run:
            console.print(
                "[yellow]Docker was installed during setup, but this shell has not "
                "picked up docker group access yet. Continuing with privileged "
                "docker commands for this run.[/yellow]"
            )
            use_privileged_docker = True
        else:
            console.print(
                "[yellow]Docker is installed, but this shell cannot access the "
                "daemon yet. Run `newgrp docker` or re-login, then rerun with "
                "--start-stack.[/yellow]"
            )
            config.start_stack = False
    return docker_installed_this_run, use_privileged_docker


def _compose_args(stack_dir: Path) -> list[str]:
    docker_command = _docker_command()
    if docker_command is None:
        raise typer.BadParameter(
            "Docker appears to be installed, but the docker CLI could not be "
            "resolved in PATH."
        )
    return [
        *docker_command,
        "compose",
        *_compose_profile_args(stack_dir),
        "-f",
        str(stack_dir / "docker-compose.yml"),
        "--project-directory",
        str(stack_dir),
    ]


def _report_stack_health(
    config: SetupConfig,
    *,
    stack_dir: Path,
    console: Console,
) -> None:
    healthcheck_url = _build_healthcheck_url(config)
    if healthcheck_url is None:
        console.print(
            "[yellow]Skipped backend health polling because public ingress is "
            "enabled without localhost access ports. After DNS points "
            f"{config.public_host} at this host and inbound 80/443 are open, "
            f"verify https://{config.public_host} manually.[/yellow]"
        )
        return
    if _poll_backend_health(healthcheck_url, console=console):
        return
    compose_file = stack_dir / "docker-compose.yml"
    timeout_seconds = _read_health_poll_timeout_seconds()
    console.print(
        "[yellow]Backend did not become healthy within "
        f"{timeout_seconds} seconds.\n"
        "Check service logs with:[/yellow]\n"
        f"  docker compose -f {compose_file} logs"
    )


def execute_setup(
    config: SetupConfig,
    *,
    console: Console,
    stack_version: str | None = None,
) -> None:
    """Run setup/upgrade actions based on the selected options."""
    stack_dir, env_file = _ensure_stack_assets(
        config=config,
        console=console,
        stack_version=stack_version,
    )
    config.stack_project_dir = str(stack_dir)
    config.stack_env_file = str(env_file)
    _warn_chatkit_domain_key_missing(env_file=env_file, console=console)
    _configure_hosted_apps_tls(config, stack_dir=stack_dir)
    _upsert_env_values(
        env_file,
        {
            "ORCHEO_APP_TLS_CERT_FILE": config.app_tls_cert_file or "",
            "ORCHEO_APP_TLS_KEY_FILE": config.app_tls_key_file or "",
        },
        console=console,
    )
    _run_hosted_apps_preflight(config, env_file=env_file, console=console)
    _, use_privileged_docker = _prepare_stack_start(config, console=console)

    if config.start_stack and _has_binary("docker"):
        compose_args = _compose_args(stack_dir)
        command_runner = (
            _run_privileged_command if use_privileged_docker else _run_command
        )
        command_runner([*compose_args, "pull"], console=console)
        command_runner([*compose_args, "up", "-d"], console=console)
        _report_stack_health(config, stack_dir=stack_dir, console=console)


def print_summary(config: SetupConfig, *, console: Console) -> None:
    """Print setup summary with versions and next steps."""
    summary = {
        "mode": config.mode,
        "backend_url": config.backend_url,
        "studio_url": config.studio_url,
        "auth_mode": config.auth_mode,
        "public_ingress_enabled": config.public_ingress_enabled,
        "public_host": config.public_host,
        "publish_local_ports": config.publish_local_ports,
        "backend_upstreams": config.backend_upstreams,
        "hosted_apps_enabled": config.hosted_apps_enabled,
        "apps_base_domain": (
            config.apps_base_domain if config.hosted_apps_enabled else None
        ),
        "app_tls_method": (
            config.app_tls_method if config.hosted_apps_enabled else None
        ),
        "stack_assets_synced": True,
        "stack_started": config.start_stack,
        "stack_project_dir": config.stack_project_dir,
        "stack_env_file": config.stack_env_file,
        "completed_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    }

    console.print("\n[bold green]Setup complete[/bold green]")
    console.print_json(json.dumps(summary))

    if config.start_stack:
        if not config.public_ingress_enabled:
            console.print("\n[bold cyan]Studio:[/bold cyan] http://localhost:2026")
        console.print(
            "\n[yellow]Note:[/yellow] Studio may take 2-3 minutes on first "
            "startup while npm installs dependencies."
        )
    if config.public_ingress_enabled and config.public_host is not None:
        console.print(f"\n[bold cyan]Studio:[/bold cyan] https://{config.public_host}")
        console.print(
            "\n[yellow]Public ingress prerequisites:[/yellow] "
            f"point DNS for {config.public_host} at this host and allow inbound "
            "80/443 to the Caddy container."
        )
        console.print(
            "[yellow]Scope:[/yellow] Use bundled Caddy for reachable self-hosted "
            "hosts. Keep Cloudflare Tunnel for localhost or NAT-restricted setups."
        )
        if config.hosted_apps_enabled:
            console.print(
                "[yellow]Hosted Apps DNS:[/yellow] point "
                f"*.{config.apps_base_domain} at this host. The supplied wildcard "
                "certificate is copied into the managed stack directory."
            )

    console.print("\nNext steps:")
    console.print(
        "  1. Run [cyan]orcheo auth login[/cyan] (or configure a service token)."
    )
    console.print("  2. Run [cyan]orcheo workflow list[/cyan] to verify connectivity.")
    if config.start_stack and not config.public_ingress_enabled:
        console.print("  3. Open [cyan]http://localhost:2026[/cyan] in your browser.")


__all__ = [
    "AuthMode",
    "SetupConfig",
    "SetupMode",
    "execute_setup",
    "print_summary",
    "run_setup",
]
