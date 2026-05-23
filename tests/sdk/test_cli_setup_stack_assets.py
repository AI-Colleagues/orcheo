"""Tests for stack bootstrap assets in `orcheo install`."""

from __future__ import annotations
import json
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote
import pytest
import typer
from rich.console import Console
from orcheo_sdk.cli.setup import SetupConfig, execute_setup, run_setup


def test_default_stack_asset_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHEO_STACK_ASSET_BASE_URL", raising=False)
    from orcheo_sdk.cli import setup as setup_mod

    assert (
        setup_mod._resolve_stack_asset_base_url()
        == "https://raw.githubusercontent.com/AI-Colleagues/orcheo/main/deploy/stack"
    )


def test_download_stack_asset_uses_deploy_stack_compose_path_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.delenv("ORCHEO_STACK_ASSET_BASE_URL", raising=False)
    captured_urls: list[str] = []

    def _urlopen(url: str, timeout: int) -> _Response:
        del timeout
        captured_urls.append(url)
        return _Response(b"services: {}\n")

    monkeypatch.setattr(setup_mod, "urlopen", _urlopen)

    setup_mod._download_stack_asset(
        "docker-compose.yml",
        stack_version=None,
        console=Console(record=True),
    )

    assert captured_urls == [
        "https://raw.githubusercontent.com/AI-Colleagues/orcheo/main/deploy/stack/docker-compose.yml"
    ]


def test_download_stack_asset_uses_deploy_stack_compose_path_for_stack_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.delenv("ORCHEO_STACK_ASSET_BASE_URL", raising=False)
    captured_urls: list[str] = []

    def _urlopen(url: str, timeout: int) -> _Response:
        del timeout
        captured_urls.append(url)
        return _Response(b"services: {}\n")

    monkeypatch.setattr(setup_mod, "urlopen", _urlopen)

    setup_mod._download_stack_asset(
        "docker-compose.yml",
        stack_version="0.2.0",
        console=Console(record=True),
    )

    assert captured_urls == [
        "https://raw.githubusercontent.com/AI-Colleagues/orcheo/stack-v0.2.0/deploy/stack/docker-compose.yml"
    ]


class _Response:
    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        self._buffer = BytesIO(payload)
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


_ENV_EXAMPLE = (
    b"ORCHEO_POSTGRES_PASSWORD=change-me\n"
    b"ORCHEO_VAULT_ENCRYPTION_KEY=replace-with-64-hex-chars\n"
    b"ORCHEO_CHATKIT_TOKEN_SIGNING_KEY=strong-random-secret\n"
    b"ORCHEO_SANDBOX_CONTROL_TOKEN=replace-with-sandbox-control-token\n"
    b"VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_replace_me\n"
)


def _default_assets() -> dict[str, bytes]:
    return {
        "docker-compose.yml": b"services: {}\n",
        "Caddyfile": b'localhost { respond "ok" }\n',
        "Dockerfile.orcheo": b"FROM python:3.12-slim\n",
        "envoy-forward-proxy.yaml": b"static_resources: {}\n",
        ".env.example": _ENV_EXAMPLE,
        "chatkit_widgets/Single-choice list.widget": b"single",
        "chatkit_widgets/Multi-choice Selector.widget": b"multi",
    }


def _setup_config() -> SetupConfig:
    return SetupConfig(
        mode="install",
        backend_url="http://localhost:2025",
        studio_url="http://localhost:2026",
        auth_mode="api-key",
        api_key="generated",
        chatkit_domain_key=None,
        public_ingress_enabled=False,
        public_host=None,
        publish_local_ports=True,
        backend_upstreams="backend:2025",
        canvas_upstream="canvas:2026",
        start_stack=True,
        install_docker_if_missing=True,
    )


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stack_dir: Path,
    base_url: str = "https://example.test/assets",
    assets: dict[str, bytes] | None = None,
    has_docker: bool = True,
    docker_access: bool = True,
    commands: list[list[str]] | None = None,
    health_ok: bool = True,
    docker_autoinstall_success: bool | None = None,
) -> None:
    """Wire up common monkeypatches for execute_setup tests."""
    resolved_assets = assets or _default_assets()
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))
    monkeypatch.setenv("ORCHEO_STACK_ASSET_BASE_URL", base_url)
    monkeypatch.setattr(
        "orcheo_sdk.cli.setup._has_binary",
        lambda name: has_docker and name in {"docker"},
    )
    monkeypatch.setattr(
        "orcheo_sdk.cli.setup._docker_command",
        lambda: ["docker"] if has_docker else None,
    )

    def _run_command(command: list[str], *, console: Console) -> None:
        del console
        if commands is not None:
            commands.append(command)

    monkeypatch.setattr("orcheo_sdk.cli.setup._run_command", _run_command)
    monkeypatch.setattr(
        "orcheo_sdk.cli.setup._poll_backend_health",
        lambda backend_url, *, console: health_ok,
    )
    monkeypatch.setattr(
        "orcheo_sdk.cli.setup._current_shell_has_docker_access",
        lambda: docker_access,
    )
    if docker_autoinstall_success is not None:
        monkeypatch.setattr(
            "orcheo_sdk.cli.setup._attempt_docker_autoinstall",
            lambda *, console: docker_autoinstall_success,
        )

    def _urlopen(url: str, timeout: int) -> _Response:
        del timeout
        relative_path = unquote(url.removeprefix(f"{base_url}/"))
        return _Response(resolved_assets[relative_path])

    monkeypatch.setattr("orcheo_sdk.cli.setup.urlopen", _urlopen)


def test_execute_setup_bootstraps_stack_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_dir = tmp_path / "stack"
    commands: list[list[str]] = []
    _patch_common(monkeypatch, stack_dir=stack_dir, commands=commands)

    config = _setup_config()
    execute_setup(config, console=Console(record=True))

    assert commands[0] == [
        "docker",
        "compose",
        "-f",
        str(stack_dir / "docker-compose.yml"),
        "--project-directory",
        str(stack_dir),
        "pull",
    ]
    assert commands[1] == [
        "docker",
        "compose",
        "-f",
        str(stack_dir / "docker-compose.yml"),
        "--project-directory",
        str(stack_dir),
        "up",
        "-d",
    ]
    assert config.stack_project_dir == str(stack_dir)
    assert config.stack_env_file == str(stack_dir / ".env")

    assert (stack_dir / "docker-compose.yml").exists()
    assert (stack_dir / "Caddyfile").exists()
    assert (stack_dir / "Dockerfile.orcheo").exists()
    assert (stack_dir / ".env.example").exists()
    assert (stack_dir / "chatkit_widgets/Single-choice list.widget").exists()
    assert (stack_dir / "chatkit_widgets/Multi-choice Selector.widget").exists()

    env_content = (stack_dir / ".env").read_text(encoding="utf-8")
    assert "ORCHEO_API_URL=http://localhost:2025" in env_content
    assert "VITE_ORCHEO_BACKEND_URL=http://localhost:2025" in env_content
    assert "ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN=generated" in env_content


def test_execute_setup_upgrade_pulls_then_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_dir = tmp_path / "stack"
    commands: list[list[str]] = []
    _patch_common(monkeypatch, stack_dir=stack_dir, commands=commands)

    config = _setup_config()
    config.mode = "upgrade"
    execute_setup(config, console=Console(record=True))

    assert commands[0] == [
        "docker",
        "compose",
        "-f",
        str(stack_dir / "docker-compose.yml"),
        "--project-directory",
        str(stack_dir),
        "pull",
    ]
    assert commands[1] == [
        "docker",
        "compose",
        "-f",
        str(stack_dir / "docker-compose.yml"),
        "--project-directory",
        str(stack_dir),
        "up",
        "-d",
    ]


def test_execute_setup_generates_secrets_on_fresh_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh install should replace template placeholders with real secrets."""
    stack_dir = tmp_path / "stack"
    _patch_common(monkeypatch, stack_dir=stack_dir, has_docker=False)

    config = _setup_config()
    config.start_stack = False
    execute_setup(config, console=Console(record=True))

    env_content = (stack_dir / ".env").read_text(encoding="utf-8")
    # Placeholders must be gone.
    assert "change-me" not in env_content
    assert "replace-with-64-hex-chars" not in env_content
    assert "strong-random-secret" not in env_content
    assert "replace-with-sandbox-control-token" not in env_content
    # Keys must still exist with generated values.
    assert "ORCHEO_POSTGRES_PASSWORD=" in env_content
    assert "ORCHEO_VAULT_ENCRYPTION_KEY=" in env_content
    assert "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY=" in env_content
    assert "ORCHEO_SANDBOX_CONTROL_TOKEN=" in env_content
    # VITE_ORCHEO_CHATKIT_DOMAIN_KEY is NOT auto-generated.
    assert "VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_replace_me" in env_content


def test_run_setup_upgrade_preserves_existing_api_key_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(tmp_path / "stack"))

    config = run_setup(
        mode="upgrade",
        backend_url=None,
        studio_url=None,
        auth_mode=None,
        api_key=None,
        chatkit_domain_key=None,
        public_ingress=None,
        public_host=None,
        publish_local_ports=None,
        start_stack=None,
        install_docker=None,
        yes=True,
        manual_secrets=False,
        console=Console(record=True),
    )

    assert config.mode == "upgrade"
    assert config.auth_mode == "api-key"
    assert config.api_key is None
    assert config.preserve_existing_backend_url is True


def test_run_setup_upgrade_honors_explicit_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(tmp_path / "stack"))

    config = run_setup(
        mode="upgrade",
        backend_url=None,
        studio_url=None,
        auth_mode="api-key",
        api_key="explicit-token",
        chatkit_domain_key=None,
        public_ingress=None,
        public_host=None,
        publish_local_ports=None,
        start_stack=None,
        install_docker=None,
        yes=True,
        manual_secrets=False,
        console=Console(record=True),
    )

    assert config.mode == "upgrade"
    assert config.auth_mode == "api-key"
    assert config.api_key == "explicit-token"


def test_run_setup_install_preserves_existing_env_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(parents=True, exist_ok=True)
    (stack_dir / ".env").write_text(
        "ORCHEO_API_URL=http://existing-api.test\n"
        "ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN=existing-token\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))

    console = Console(record=True)
    config = run_setup(
        mode="install",
        backend_url=None,
        studio_url=None,
        auth_mode=None,
        api_key=None,
        chatkit_domain_key=None,
        public_ingress=None,
        public_host=None,
        publish_local_ports=None,
        start_stack=False,
        install_docker=False,
        yes=True,
        manual_secrets=False,
        console=console,
    )

    output = console.export_text()
    assert "Detected existing stack env file" in output
    assert "Keeping existing backend URL" in output
    assert "Keeping existing API bootstrap token" in output
    assert config.mode == "install"
    assert config.auth_mode == "api-key"
    assert config.preserve_existing_backend_url is True
    assert config.api_key is None


def test_run_setup_install_explicit_public_ingress_updates_backend_url_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(parents=True, exist_ok=True)
    (stack_dir / ".env").write_text(
        "ORCHEO_API_URL=http://existing-api.test\n"
        "VITE_ORCHEO_BACKEND_URL=http://existing-api.test\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))

    config = run_setup(
        mode="install",
        backend_url=None,
        studio_url=None,
        auth_mode="api-key",
        api_key=None,
        chatkit_domain_key=None,
        public_ingress=True,
        public_host="orcheo.example.com",
        publish_local_ports=True,
        start_stack=False,
        install_docker=False,
        yes=True,
        manual_secrets=False,
        console=Console(record=True),
    )

    assert config.backend_url == "https://orcheo.example.com"
    assert config.preserve_existing_backend_url is False


def test_execute_setup_preserves_secrets_on_upgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing .env values must not be overwritten on upgrade."""
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(parents=True)
    # Pre-existing .env with user-customised secrets.
    (stack_dir / ".env").write_text(
        "ORCHEO_POSTGRES_PASSWORD=my-custom-password\n"
        "ORCHEO_VAULT_ENCRYPTION_KEY=aabbccdd\n"
        "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY=my-signing-key\n",
        encoding="utf-8",
    )
    _patch_common(monkeypatch, stack_dir=stack_dir, has_docker=False)

    config = _setup_config()
    config.mode = "upgrade"
    config.start_stack = False
    execute_setup(config, console=Console(record=True))

    env_content = (stack_dir / ".env").read_text(encoding="utf-8")
    # Original user values preserved.
    assert "ORCHEO_POSTGRES_PASSWORD=my-custom-password" in env_content
    assert "ORCHEO_VAULT_ENCRYPTION_KEY=aabbccdd" in env_content
    assert "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY=my-signing-key" in env_content
    # Config updates still applied.
    assert "ORCHEO_API_URL=http://localhost:2025" in env_content


def test_execute_setup_preserves_backend_urls_on_upgrade_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upgrade defaults should preserve existing backend URL values."""
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(parents=True)
    (stack_dir / ".env").write_text(
        "ORCHEO_API_URL=http://existing-api.test\n"
        "VITE_ORCHEO_BACKEND_URL=http://existing-vite.test\n",
        encoding="utf-8",
    )
    _patch_common(monkeypatch, stack_dir=stack_dir, has_docker=False)

    config = _setup_config()
    config.mode = "upgrade"
    config.start_stack = False
    config.preserve_existing_backend_url = True
    execute_setup(config, console=Console(record=True))

    env_content = (stack_dir / ".env").read_text(encoding="utf-8")
    assert "ORCHEO_API_URL=http://existing-api.test" in env_content
    assert "VITE_ORCHEO_BACKEND_URL=http://existing-vite.test" in env_content
    assert config.backend_url == "http://existing-api.test"


def test_execute_setup_preserves_existing_tunnel_browser_origin_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upgrade should not clobber split-origin tunnel settings with localhost defaults."""  # noqa: E501
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(parents=True)
    (stack_dir / ".env").write_text(
        "ORCHEO_STUDIO_URL=https://orcheo-canvas.example.com\n"
        "ORCHEO_CORS_ALLOW_ORIGINS=https://orcheo-canvas.example.com\n"
        "VITE_ORCHEO_ALLOWED_HOSTS=localhost,127.0.0.1,orcheo-canvas.example.com\n",
        encoding="utf-8",
    )
    _patch_common(monkeypatch, stack_dir=stack_dir, has_docker=False)

    config = _setup_config()
    config.mode = "upgrade"
    config.start_stack = False
    config.studio_url = "https://orcheo-canvas.example.com"
    execute_setup(config, console=Console(record=True))

    env_content = (stack_dir / ".env").read_text(encoding="utf-8")
    assert "ORCHEO_STUDIO_URL=https://orcheo-canvas.example.com" in env_content
    assert "ORCHEO_CORS_ALLOW_ORIGINS=https://orcheo-canvas.example.com" in env_content
    assert (
        "VITE_ORCHEO_ALLOWED_HOSTS=localhost,127.0.0.1,orcheo-canvas.example.com"
        in env_content
    )


def test_execute_setup_preserves_backend_urls_on_install_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Install preserve mode should keep existing backend URL values."""
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(parents=True)
    (stack_dir / ".env").write_text(
        "ORCHEO_API_URL=http://existing-api.test\n"
        "VITE_ORCHEO_BACKEND_URL=http://existing-vite.test\n",
        encoding="utf-8",
    )
    _patch_common(monkeypatch, stack_dir=stack_dir, has_docker=False)

    config = _setup_config()
    config.mode = "install"
    config.start_stack = False
    config.preserve_existing_backend_url = True
    execute_setup(config, console=Console(record=True))

    env_content = (stack_dir / ".env").read_text(encoding="utf-8")
    assert "ORCHEO_API_URL=http://existing-api.test" in env_content
    assert "VITE_ORCHEO_BACKEND_URL=http://existing-vite.test" in env_content
    assert config.backend_url == "http://existing-api.test"


def test_execute_setup_upgrade_preserve_backend_when_existing_urls_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upgrade preserve mode should fall back to config URL if env URLs are absent."""
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(parents=True)
    (stack_dir / ".env").write_text("UNRELATED_KEY=value\n", encoding="utf-8")
    _patch_common(monkeypatch, stack_dir=stack_dir, has_docker=False)

    config = _setup_config()
    config.mode = "upgrade"
    config.start_stack = False
    config.preserve_existing_backend_url = True
    execute_setup(config, console=Console(record=True))

    env_content = (stack_dir / ".env").read_text(encoding="utf-8")
    assert "ORCHEO_API_URL=http://localhost:2025" in env_content
    assert "VITE_ORCHEO_BACKEND_URL=http://localhost:2025" in env_content
    assert config.backend_url == "http://localhost:2025"


def test_execute_setup_normalizes_quoted_backend_url_on_upgrade_preserve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserved backend URL should be unquoted before health checks and summary."""
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(parents=True)
    (stack_dir / ".env").write_text(
        'ORCHEO_API_URL="https://api.example.com"\n'
        'VITE_ORCHEO_BACKEND_URL="https://api.example.com"\n',
        encoding="utf-8",
    )
    _patch_common(monkeypatch, stack_dir=stack_dir, has_docker=False)

    config = _setup_config()
    config.mode = "upgrade"
    config.start_stack = False
    config.preserve_existing_backend_url = True
    execute_setup(config, console=Console(record=True))

    assert config.backend_url == "https://api.example.com"


def test_execute_setup_raises_when_asset_download_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(tmp_path / "stack"))
    monkeypatch.setattr(
        "orcheo_sdk.cli.setup._has_binary",
        lambda name: name in {"docker"},
    )
    monkeypatch.setattr(
        "orcheo_sdk.cli.setup._run_command",
        lambda command, *, console: None,
    )

    def _urlopen(url: str, timeout: int) -> Any:
        del url, timeout
        raise OSError("network error")

    monkeypatch.setattr("orcheo_sdk.cli.setup.urlopen", _urlopen)

    with pytest.raises(typer.BadParameter, match="Failed to download stack asset"):
        execute_setup(_setup_config(), console=Console(record=True))


def test_execute_setup_updates_different_local_asset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(parents=True, exist_ok=True)
    (stack_dir / "docker-compose.yml").write_text(
        "services: {old: 1}\n", encoding="utf-8"
    )

    assets = _default_assets()
    assets["docker-compose.yml"] = b"services: {new: 1}\n"
    _patch_common(monkeypatch, stack_dir=stack_dir, assets=assets, has_docker=False)

    config = _setup_config()
    config.start_stack = False
    execute_setup(config, console=Console(record=True))

    assert (stack_dir / "docker-compose.yml").read_bytes() == assets[
        "docker-compose.yml"
    ]


def test_poll_backend_health_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health poll should succeed when the backend responds with 200."""
    from orcheo_sdk.cli.setup import _poll_backend_health

    call_count = {"n": 0}
    captured_url = {"value": ""}

    def _urlopen(url: str, timeout: int) -> _Response:
        captured_url["value"] = url
        del timeout
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise OSError("not ready")
        return _Response(b'{"status":"ok"}', status=200)

    monkeypatch.setattr("orcheo_sdk.cli.setup.urlopen", _urlopen)
    monkeypatch.setattr("orcheo_sdk.cli.setup._HEALTH_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setenv("ORCHEO_SETUP_HEALTH_POLL_TIMEOUT_SECONDS", "5")

    console = Console(record=True)
    assert _poll_backend_health("http://localhost:2025", console=console)
    output = console.export_text()
    assert "Backend is healthy" in output
    assert captured_url["value"] == "http://localhost:2025/api/system/health"


def test_poll_backend_health_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health poll should return False when backend never becomes healthy."""
    from orcheo_sdk.cli.setup import _poll_backend_health

    def _urlopen(url: str, timeout: int) -> _Response:
        del url, timeout
        raise OSError("not ready")

    monkeypatch.setattr("orcheo_sdk.cli.setup.urlopen", _urlopen)
    monkeypatch.setattr("orcheo_sdk.cli.setup._HEALTH_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setenv("ORCHEO_SETUP_HEALTH_POLL_TIMEOUT_SECONDS", "0")

    console = Console(record=True)
    assert not _poll_backend_health("http://localhost:2025", console=console)


def test_execute_setup_prints_health_warning_on_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When backend doesn't come up, setup should print a warning."""
    stack_dir = tmp_path / "stack"
    _patch_common(monkeypatch, stack_dir=stack_dir, health_ok=False)

    config = _setup_config()
    console = Console(record=True)
    execute_setup(config, console=console)

    output = console.export_text()
    assert "did not become healthy" in output
    assert "docker compose" in output


def test_execute_setup_skips_stack_start_when_docker_missing_and_install_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_dir = tmp_path / "stack"
    _patch_common(
        monkeypatch,
        stack_dir=stack_dir,
        has_docker=False,
        docker_autoinstall_success=False,
    )

    config = _setup_config()
    config.install_docker_if_missing = True
    console = Console(record=True)
    execute_setup(config, console=console)

    assert config.start_stack is False
    assert "automatic installation could not complete" in console.export_text()


def test_execute_setup_autoinstalls_docker_then_starts_stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_dir = tmp_path / "stack"
    commands: list[list[str]] = []
    _patch_common(monkeypatch, stack_dir=stack_dir, commands=commands, has_docker=False)

    from orcheo_sdk.cli import setup as setup_mod

    docker_state = {"installed": False}
    monkeypatch.setattr(
        setup_mod,
        "_has_binary",
        lambda name: docker_state["installed"] and name == "docker",
    )
    monkeypatch.setattr(
        setup_mod,
        "_docker_command",
        lambda: ["docker"] if docker_state["installed"] else None,
    )

    def _autoinstall(*, console: Console) -> bool:
        del console
        docker_state["installed"] = True
        return True

    monkeypatch.setattr(setup_mod, "_attempt_docker_autoinstall", _autoinstall)
    monkeypatch.setattr(setup_mod, "_current_shell_has_docker_access", lambda: True)

    config = _setup_config()
    execute_setup(config, console=Console(record=True))

    assert config.start_stack is True
    assert commands[0][-1] == "pull"
    assert commands[1][-2:] == ["up", "-d"]


def test_execute_setup_uses_privileged_docker_after_autoinstall_until_shell_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_dir = tmp_path / "stack"
    commands: list[list[str]] = []
    _patch_common(monkeypatch, stack_dir=stack_dir, commands=commands, has_docker=False)

    from orcheo_sdk.cli import setup as setup_mod

    docker_state = {"installed": False}
    monkeypatch.setattr(
        setup_mod,
        "_has_binary",
        lambda name: (docker_state["installed"] and name == "docker") or name == "sudo",
    )
    monkeypatch.setattr(
        setup_mod,
        "_docker_command",
        lambda: ["docker"] if docker_state["installed"] else None,
    )

    def _autoinstall(*, console: Console) -> bool:
        del console
        docker_state["installed"] = True
        return True

    monkeypatch.setattr(setup_mod, "_attempt_docker_autoinstall", _autoinstall)
    monkeypatch.setattr(setup_mod, "_current_shell_has_docker_access", lambda: False)
    monkeypatch.setattr(
        setup_mod,
        "_run_privileged_command",
        lambda command, *, console: commands.append(["sudo", *command]),
    )

    config = _setup_config()
    console = Console(record=True)
    execute_setup(config, console=console)

    assert config.start_stack is True
    assert commands[0] == [
        "sudo",
        "docker",
        "compose",
        "-f",
        str(stack_dir / "docker-compose.yml"),
        "--project-directory",
        str(stack_dir),
        "pull",
    ]
    assert commands[1] == [
        "sudo",
        "docker",
        "compose",
        "-f",
        str(stack_dir / "docker-compose.yml"),
        "--project-directory",
        str(stack_dir),
        "up",
        "-d",
    ]
    assert "Continuing with privileged docker commands" in console.export_text()


def test_execute_setup_skips_stack_when_docker_daemon_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_dir = tmp_path / "stack"
    commands: list[list[str]] = []
    _patch_common(
        monkeypatch,
        stack_dir=stack_dir,
        commands=commands,
        has_docker=True,
        docker_access=False,
    )

    config = _setup_config()
    console = Console(record=True)
    execute_setup(config, console=console)

    assert config.start_stack is False
    assert commands == []
    assert "cannot access the daemon yet" in console.export_text()


def test_execute_setup_raises_when_docker_missing_and_install_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_dir = tmp_path / "stack"
    _patch_common(monkeypatch, stack_dir=stack_dir, has_docker=False)

    config = _setup_config()
    config.install_docker_if_missing = False
    with pytest.raises(typer.BadParameter, match="--skip-docker-install"):
        execute_setup(config, console=Console(record=True))


def test_execute_setup_warns_when_chatkit_domain_key_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_dir = tmp_path / "stack"
    _patch_common(monkeypatch, stack_dir=stack_dir, has_docker=False)

    config = _setup_config()
    config.start_stack = False
    console = Console(record=True)
    execute_setup(config, console=console)

    assert "ChatKit domain key is not configured" in console.export_text()


def test_execute_setup_backfills_chatkit_domain_key_when_missing_in_existing_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(parents=True, exist_ok=True)
    (stack_dir / ".env").write_text(
        "ORCHEO_POSTGRES_PASSWORD=my-custom-password\n",
        encoding="utf-8",
    )
    _patch_common(monkeypatch, stack_dir=stack_dir, has_docker=False)

    config = _setup_config()
    config.start_stack = False
    execute_setup(config, console=Console(record=True))

    env_content = (stack_dir / ".env").read_text(encoding="utf-8")
    assert "VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_replace_me" in env_content


def test_execute_setup_clears_bootstrap_token_for_oauth_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(parents=True, exist_ok=True)
    (stack_dir / ".env").write_text(
        "ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN=legacy-token\n",
        encoding="utf-8",
    )
    _patch_common(monkeypatch, stack_dir=stack_dir, has_docker=False)

    config = _setup_config()
    config.auth_mode = "oauth"
    config.api_key = None
    config.start_stack = False
    execute_setup(config, console=Console(record=True))

    env_content = (stack_dir / ".env").read_text(encoding="utf-8")
    assert "ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN=\n" in env_content


def test_setup_low_level_resolvers_and_command_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.setattr(
        setup_mod.subprocess,
        "run",
        lambda command, check: subprocess.CompletedProcess(command, returncode=7),
    )
    with pytest.raises(typer.BadParameter, match="Command failed with exit code 7"):
        setup_mod._run_command(["echo", "x"], console=Console(record=True))

    monkeypatch.setattr(setup_mod.shutil, "which", lambda _name: None)
    assert setup_mod._has_binary("docker") is False

    monkeypatch.setattr(setup_mod.typer, "prompt", lambda _p, default: "upgrade")
    assert setup_mod._resolve_mode(None, yes=False) == "upgrade"
    monkeypatch.setattr(setup_mod.typer, "prompt", lambda _p, default: "http://x")
    assert setup_mod._resolve_backend_url(None, mode="install", yes=False) == (
        "http://x",
        False,
    )
    monkeypatch.setattr(setup_mod.typer, "prompt", lambda *args, **kwargs: " ")
    assert setup_mod._resolve_backend_url(None, mode="upgrade", yes=False) == (
        "http://localhost:2025",
        True,
    )
    monkeypatch.setattr(setup_mod.typer, "prompt", lambda *args, **kwargs: "http://u")
    assert setup_mod._resolve_backend_url(None, mode="upgrade", yes=False) == (
        "http://u",
        False,
    )
    monkeypatch.setattr(setup_mod.typer, "prompt", lambda _p, default: "oauth")
    assert setup_mod._resolve_auth_mode(None, yes=False) == "oauth"
    monkeypatch.setattr(setup_mod.typer, "confirm", lambda _p, default: False)
    assert (
        setup_mod._resolve_bool(None, yes_default=False, prompt="ok?", default=True)
        is False
    )


def test_setup_api_key_and_optional_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    assert (
        setup_mod._resolve_api_key("oauth", None, mode="install", manual=True) is None
    )
    monkeypatch.setattr(setup_mod.typer, "prompt", lambda *args, **kwargs: " ")
    assert (
        setup_mod._resolve_api_key("api-key", None, mode="upgrade", manual=True) is None
    )
    monkeypatch.setattr(setup_mod.typer, "prompt", lambda *args, **kwargs: "entered")
    assert (
        setup_mod._resolve_api_key("api-key", None, mode="install", manual=True)
        == "entered"
    )

    assert setup_mod._normalize_optional_value("  ") is None
    assert setup_mod._normalize_optional_value(" v ") == "v"
    assert setup_mod._normalize_dotenv_value(None) is None

    assert setup_mod._resolve_chatkit_domain_key("  key ", yes=True) == "key"
    monkeypatch.setattr(setup_mod.typer, "prompt", lambda *args, **kwargs: "  ")
    assert setup_mod._resolve_chatkit_domain_key(None, yes=False) is None


def test_supported_docker_autoinstall_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Darwin")
    assert setup_mod._is_supported_docker_autoinstall_linux() is False

    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(setup_mod, "_read_os_release", lambda: {"ID": "ubuntu"})
    assert setup_mod._is_supported_docker_autoinstall_linux() is True

    monkeypatch.setattr(
        setup_mod,
        "_read_os_release",
        lambda: {"ID": "linuxmint", "ID_LIKE": "ubuntu debian"},
    )
    assert setup_mod._is_supported_docker_autoinstall_linux() is True

    monkeypatch.setattr(
        setup_mod,
        "_read_os_release",
        lambda: {"ID": "fedora", "ID_LIKE": "rhel"},
    )
    assert setup_mod._is_supported_docker_autoinstall_linux() is False


def test_read_os_release_handles_read_errors_and_malformed_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.setattr(setup_mod.Path, "exists", lambda self: True)

    def _raise_read_error(_self: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        raise PermissionError("denied")

    monkeypatch.setattr(setup_mod.Path, "read_text", _raise_read_error)
    assert setup_mod._read_os_release() == {}

    monkeypatch.setattr(
        setup_mod.Path,
        "read_text",
        lambda _self, *, encoding="utf-8": (
            "ID=ubuntu\n"
            "#comment\n"
            "BROKEN\n"
            'ID_LIKE="debian ubuntu"\n'
            "bad-key=ignored\n"
            "   =missing\n"
        ),
    )
    assert setup_mod._read_os_release() == {
        "ID": "ubuntu",
        "ID_LIKE": "debian ubuntu",
    }


def test_current_username_handles_lookup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr(
        setup_mod.getpass, "getuser", lambda: (_ for _ in ()).throw(OSError)
    )
    assert setup_mod._current_username() is None

    monkeypatch.setenv("SUDO_USER", "root-user")
    assert setup_mod._current_username() == "root-user"


def test_attempt_docker_autoinstall_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        setup_mod, "_is_supported_docker_autoinstall_linux", lambda: False
    )
    assert setup_mod._attempt_docker_autoinstall(console=Console(record=True)) is False

    monkeypatch.setattr(
        setup_mod, "_is_supported_docker_autoinstall_linux", lambda: True
    )
    monkeypatch.setattr(
        setup_mod, "_has_binary", lambda name: name in {"docker", "sudo"}
    )
    assert setup_mod._attempt_docker_autoinstall(console=Console(record=True)) is False

    commands: list[list[str]] = []
    monkeypatch.setattr(
        setup_mod, "_has_binary", lambda name: name in {"apt-get", "docker", "sudo"}
    )
    monkeypatch.setattr(
        setup_mod,
        "_run_privileged_command",
        lambda command, *, console: commands.append(command),
    )
    monkeypatch.setattr(setup_mod, "_current_username", lambda: "alice")
    assert setup_mod._attempt_docker_autoinstall(console=Console(record=True)) is True
    assert commands == [
        ["apt-get", "update"],
        ["apt-get", "install", "-y", "docker.io", "docker-compose-v2"],
        ["systemctl", "enable", "--now", "docker"],
        ["usermod", "-aG", "docker", "alice"],
    ]

    monkeypatch.setattr(
        setup_mod,
        "_run_privileged_command",
        lambda command, *, console: (_ for _ in ()).throw(typer.BadParameter("boom")),
    )
    assert setup_mod._attempt_docker_autoinstall(console=Console(record=True)) is False

    monkeypatch.setattr(
        setup_mod,
        "_run_privileged_command",
        lambda command, *, console: (_ for _ in ()).throw(
            FileNotFoundError("systemctl")
        ),
    )
    assert setup_mod._attempt_docker_autoinstall(console=Console(record=True)) is False


def test_attempt_macos_docker_desktop_install_runs_installer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(setup_mod, "_normalized_machine", lambda: "arm64")
    monkeypatch.setattr(setup_mod, "_current_username", lambda: "alice")
    monkeypatch.setattr(
        setup_mod,
        "_resolve_macos_docker_volume_path",
        lambda: Path("/Volumes/Docker 1"),
    )

    downloads: list[tuple[str, Path]] = []

    def _download(download_url: str, destination: Path, *, console: Console) -> None:
        del console
        downloads.append((download_url, destination))
        destination.write_bytes(b"dmg")

    commands: list[list[str]] = []
    monkeypatch.setattr(setup_mod, "_download_binary_asset", _download)
    monkeypatch.setattr(
        setup_mod,
        "_run_privileged_command",
        lambda command, *, console: commands.append(command),
    )
    monkeypatch.setattr(
        setup_mod, "_start_docker_desktop", lambda *, console: commands.append(["open"])
    )
    monkeypatch.setattr(setup_mod, "_wait_for_docker_access", lambda *, console: True)
    monkeypatch.setattr(
        setup_mod, "_refresh_docker_cli_path_for_current_process", lambda: None
    )

    assert setup_mod._attempt_macos_docker_desktop_install(console=Console(record=True))
    assert downloads[0][0] == setup_mod._MACOS_DOCKER_DESKTOP_DOWNLOADS["arm64"]
    assert commands == [
        ["hdiutil", "attach", str(downloads[0][1]), "-nobrowse"],
        [
            "/Volumes/Docker 1/Docker.app/Contents/MacOS/install",
            "--accept-license",
            "--user=alice",
        ],
        ["hdiutil", "detach", "/Volumes/Docker 1"],
        ["open"],
    ]


def test_current_windows_wsl_ready_handles_missing_wsl_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        setup_mod.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("wsl.exe")),
    )

    assert setup_mod._current_windows_wsl_ready() is False


def test_attempt_windows_docker_desktop_install_runs_installer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup_mod, "_normalized_machine", lambda: "x86_64")
    monkeypatch.setattr(setup_mod, "_ensure_windows_wsl", lambda *, console: True)

    downloads: list[tuple[str, Path]] = []

    def _download(download_url: str, destination: Path, *, console: Console) -> None:
        del console
        downloads.append((download_url, destination))
        destination.write_bytes(b"exe")

    commands: list[list[str]] = []
    monkeypatch.setattr(setup_mod, "_download_binary_asset", _download)
    monkeypatch.setattr(
        setup_mod,
        "_run_windows_elevated_command",
        lambda command, *, console: commands.append(command),
    )
    monkeypatch.setattr(
        setup_mod,
        "_start_docker_desktop",
        lambda *, console: commands.append(["start"]),
    )
    monkeypatch.setattr(setup_mod, "_wait_for_docker_access", lambda *, console: True)
    monkeypatch.setattr(
        setup_mod, "_refresh_docker_cli_path_for_current_process", lambda: None
    )

    assert setup_mod._attempt_windows_docker_desktop_install(
        console=Console(record=True)
    )
    assert downloads[0][0] == setup_mod._WINDOWS_DOCKER_DESKTOP_DOWNLOADS["x86_64"]
    assert commands == [
        [
            str(downloads[0][1]),
            "install",
            "--accept-license",
            "--backend=wsl-2",
            "--quiet",
        ],
        ["start"],
    ]


def test_attempt_windows_docker_desktop_install_stops_when_wsl_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup_mod, "_normalized_machine", lambda: "x86_64")
    monkeypatch.setattr(setup_mod, "_ensure_windows_wsl", lambda *, console: False)

    assert (
        setup_mod._attempt_windows_docker_desktop_install(console=Console(record=True))
        is False
    )


def test_resolve_backend_url_install_yes_uses_default() -> None:
    from orcheo_sdk.cli import setup as setup_mod

    assert setup_mod._resolve_backend_url(None, mode="install", yes=True) == (
        "http://localhost:2025",
        False,
    )


def test_resolve_backend_url_upgrade_without_env_file_does_not_read_existing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    missing_env = tmp_path / ".env"
    monkeypatch.setattr(setup_mod.typer, "prompt", lambda *_args, **_kwargs: " ")
    assert setup_mod._resolve_backend_url(
        None,
        mode="upgrade",
        yes=False,
        env_file=missing_env,
        env_exists=False,
    ) == ("http://localhost:2025", True)


def test_setup_stack_dir_default_and_sync_no_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.delenv("ORCHEO_STACK_DIR", raising=False)
    monkeypatch.setattr(setup_mod.Path, "home", lambda: tmp_path / "home")
    assert (
        setup_mod._resolve_stack_project_dir()
        == tmp_path / "home" / ".orcheo" / "stack"
    )

    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    local_file = stack_dir / "docker-compose.yml"
    local_file.write_bytes(b"same")
    monkeypatch.setattr(
        setup_mod,
        "_download_stack_asset",
        lambda path, *, stack_version, console: b"same",
    )
    setup_mod._sync_stack_asset(
        "docker-compose.yml",
        stack_dir,
        stack_version=None,
        console=Console(record=True),
    )
    assert local_file.read_bytes() == b"same"


def test_setup_build_env_updates_and_warn_missing_branch(
    tmp_path: Path,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    config = SetupConfig(
        mode="install",
        backend_url="http://localhost:2025",
        studio_url="http://localhost:2026",
        auth_mode="api-key",
        api_key=None,
        chatkit_domain_key="domain_pk_live",
        public_ingress_enabled=False,
        public_host=None,
        publish_local_ports=True,
        backend_upstreams="backend:2025",
        canvas_upstream="canvas:2026",
        start_stack=False,
        install_docker_if_missing=True,
    )
    updates, defaults = setup_mod._build_env_updates(config)
    assert "ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN" not in updates
    assert "ORCHEO_AUTH_MODE" not in updates
    assert updates["COMPOSE_PROFILES"] == ""
    assert updates["VITE_ORCHEO_CHATKIT_DOMAIN_KEY"] == "domain_pk_live"
    assert defaults["ORCHEO_POSTGRES_PASSWORD"]

    env_file = tmp_path / ".env"
    env_file.write_text(
        "VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_live\n", encoding="utf-8"
    )
    console = Console(record=True)
    setup_mod._warn_chatkit_domain_key_missing(env_file=env_file, console=console)
    assert "not configured" not in console.export_text()


def test_setup_upsert_no_change_and_append_defaults(tmp_path: Path) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    env_file = tmp_path / ".env"
    env_file.write_text("UNCHANGED=value\n", encoding="utf-8")
    console = Console(record=True)
    setup_mod._upsert_env_values(
        env_file,
        {},
        defaults={"NEW_DEFAULT": "x"},
        console=console,
    )
    assert "NEW_DEFAULT=x\n" in env_file.read_text(encoding="utf-8")

    original = env_file.read_text(encoding="utf-8")
    setup_mod._upsert_env_values(env_file, {}, console=console)
    assert env_file.read_text(encoding="utf-8") == original


def test_execute_setup_env_example_sync_and_chatkit_backfill_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(parents=True, exist_ok=True)
    (stack_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (stack_dir / "Dockerfile.orcheo").write_text("FROM x\n", encoding="utf-8")
    (stack_dir / "chatkit_widgets").mkdir(parents=True, exist_ok=True)
    (stack_dir / "chatkit_widgets/Single-choice list.widget").write_text(
        "s", encoding="utf-8"
    )
    (stack_dir / "chatkit_widgets/Multi-choice Selector.widget").write_text(
        "m", encoding="utf-8"
    )
    (stack_dir / ".env").write_text(
        "VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_live\n",
        encoding="utf-8",
    )
    assets = _default_assets()
    _patch_common(monkeypatch, stack_dir=stack_dir, assets=assets, has_docker=False)
    sync_calls: list[str] = []
    from orcheo_sdk.cli import setup as setup_mod

    original_sync = setup_mod._sync_stack_asset
    monkeypatch.setattr(
        setup_mod,
        "_sync_stack_asset",
        lambda relative_path, target_stack_dir, *, stack_version, console: (
            sync_calls.append(relative_path),
            original_sync(
                relative_path,
                target_stack_dir,
                stack_version=stack_version,
                console=console,
            ),
        )[1],
    )

    config = _setup_config()
    config.start_stack = False
    execute_setup(config, console=Console(record=True))
    assert ".env.example" in sync_calls
    env_content = (stack_dir / ".env").read_text(encoding="utf-8")
    assert env_content.count("VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_live") == 1


def test_run_setup_prints_generated_key_and_oauth_notice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.setenv("ORCHEO_STACK_DIR", str(tmp_path / "stack"))
    monkeypatch.setattr(setup_mod.typer, "confirm", lambda _prompt, default: False)
    monkeypatch.setattr(setup_mod.typer, "prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        setup_mod.secrets, "token_urlsafe", lambda _n: "generated-token"
    )

    console = Console(record=True)
    setup_mod.run_setup(
        mode="install",
        backend_url="http://localhost:2025",
        studio_url="http://localhost:2026",
        auth_mode="api-key",
        api_key=None,
        chatkit_domain_key=None,
        public_ingress=None,
        public_host=None,
        publish_local_ports=None,
        start_stack=None,
        install_docker=None,
        yes=False,
        manual_secrets=False,
        console=console,
    )
    assert "Generated API key locally" in console.export_text()

    oauth_console = Console(record=True)
    setup_mod.run_setup(
        mode="install",
        backend_url="http://localhost:2025",
        studio_url="http://localhost:2026",
        auth_mode="oauth",
        api_key=None,
        chatkit_domain_key=None,
        public_ingress=None,
        public_host=None,
        publish_local_ports=None,
        start_stack=False,
        install_docker=False,
        yes=True,
        manual_secrets=False,
        console=oauth_console,
    )
    assert "OAuth mode selected" in oauth_console.export_text()


def test_run_setup_public_ingress_derives_public_env_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    stack_dir = tmp_path / "stack"
    stack_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))

    config = setup_mod.run_setup(
        mode="install",
        backend_url=None,
        studio_url=None,
        auth_mode="api-key",
        api_key="token",
        chatkit_domain_key=None,
        public_ingress=True,
        public_host="orcheo.example.com",
        publish_local_ports=True,
        start_stack=False,
        install_docker=False,
        yes=True,
        manual_secrets=False,
        console=Console(record=True),
    )

    updates, _ = setup_mod._build_env_updates(config)
    assert config.backend_url == "https://orcheo.example.com"
    assert updates["ORCHEO_API_URL"] == "https://orcheo.example.com"
    assert updates["VITE_ORCHEO_BACKEND_URL"] == "https://orcheo.example.com"
    assert updates["ORCHEO_STUDIO_URL"] == "https://orcheo.example.com"
    assert updates["ORCHEO_CORS_ALLOW_ORIGINS"] == (
        "https://orcheo.example.com,http://localhost:2026,http://127.0.0.1:2026"
    )
    assert (
        updates["VITE_ORCHEO_ALLOWED_HOSTS"] == "localhost,127.0.0.1,orcheo.example.com"
    )
    assert updates["COMPOSE_PROFILES"] == "public-ingress"
    assert updates["ORCHEO_CADDY_SITE_ADDRESS"] == "orcheo.example.com"


def test_run_setup_public_ingress_requires_hostname_with_yes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(tmp_path / "stack"))

    with pytest.raises(typer.BadParameter, match="--public-host is required"):
        run_setup(
            mode="install",
            backend_url=None,
            studio_url=None,
            auth_mode="api-key",
            api_key=None,
            chatkit_domain_key=None,
            public_ingress=True,
            public_host=None,
            publish_local_ports=None,
            start_stack=False,
            install_docker=False,
            yes=True,
            manual_secrets=False,
            console=Console(record=True),
        )


def test_setup_public_ingress_helpers_and_summary(
    tmp_path: Path,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    config = _setup_config()
    config.public_ingress_enabled = True
    config.public_host = "orcheo.example.com"
    config.publish_local_ports = False
    config.start_stack = False
    config.backend_url = "https://orcheo.example.com"
    config.studio_url = "https://orcheo.example.com"

    assert setup_mod._build_cors_origins(config) == "https://orcheo.example.com"
    assert setup_mod._build_healthcheck_url(config) is None

    healthcheck_config = _setup_config()
    healthcheck_config.public_ingress_enabled = True
    healthcheck_config.public_host = "orcheo.example.com"
    healthcheck_config.publish_local_ports = True
    assert (
        setup_mod._build_healthcheck_url(healthcheck_config) == "http://localhost:2025"
    )

    console = Console(record=True)
    setup_mod._report_stack_health(config, stack_dir=tmp_path, console=console)
    assert "Skipped backend health polling" in console.export_text()

    config.stack_project_dir = str(tmp_path / "stack")
    config.stack_env_file = str(tmp_path / "stack/.env")
    summary_console = Console(record=True)
    setup_mod.print_summary(config, console=summary_console)
    summary_text = summary_console.export_text()
    assert "Public ingress prerequisites" in summary_text
    assert "Scope:" in summary_text


def test_setup_read_health_timeout_invalid_negative_and_print_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.setenv("ORCHEO_SETUP_HEALTH_POLL_TIMEOUT_SECONDS", "bad")
    assert setup_mod._read_health_poll_timeout_seconds() == 60
    monkeypatch.setenv("ORCHEO_SETUP_HEALTH_POLL_TIMEOUT_SECONDS", "-1")
    assert setup_mod._read_health_poll_timeout_seconds() == 60

    config = _setup_config()
    config.start_stack = True
    config.stack_project_dir = "/tmp/stack"
    config.stack_env_file = "/tmp/stack/.env"
    console = Console(record=True)
    setup_mod.print_summary(config, console=console)
    output = console.export_text()
    assert "Setup complete" in output
    assert "Canvas may take 2-3 minutes" in output
    assert "orcheo workflow list" in output


def test_poll_backend_health_non_200_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    sleeps: list[float] = []
    monotonic_values = iter([0.0, 0.1, 0.2, 2.0])

    monkeypatch.setattr(setup_mod.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(setup_mod.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setenv("ORCHEO_SETUP_HEALTH_POLL_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(
        setup_mod,
        "urlopen",
        lambda url, timeout: _Response(b"{}", status=500),
    )
    monkeypatch.setattr(setup_mod, "_HEALTH_POLL_INTERVAL_SECONDS", 5)
    assert (
        setup_mod._poll_backend_health(
            "http://localhost:2025", console=Console(record=True)
        )
        is False
    )
    assert sleeps


def test_setup_misc_uncovered_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    assert setup_mod._resolve_mode(None, yes=True) == "install"

    monkeypatch.setattr(
        setup_mod.subprocess,
        "run",
        lambda command, check: subprocess.CompletedProcess(command, returncode=0),
    )
    setup_mod._run_command(["echo", "ok"], console=Console(record=True))

    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    setup_mod._upsert_env_values(env_file, {}, console=Console(record=True))
    assert env_file.read_text(encoding="utf-8") == ""

    stack_dir = tmp_path / "stack-no-env-example"
    stack_dir.mkdir(parents=True, exist_ok=True)
    for relative_path in setup_mod._STACK_ASSET_FILES:
        if relative_path == ".env.example":
            continue
        target = stack_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder", encoding="utf-8")

    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))

    def _sync(
        relative_path: str,
        target_stack_dir: Path,
        *,
        stack_version: str | None,
        console: Console,
    ) -> None:
        del console, stack_version
        destination = target_stack_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("downloaded", encoding="utf-8")

    monkeypatch.setattr(setup_mod, "_sync_stack_asset", _sync)
    monkeypatch.setattr(
        setup_mod.shutil,
        "copyfile",
        lambda src, dst: Path(dst).write_text(
            Path(src).read_text(encoding="utf-8"), encoding="utf-8"
        ),
    )
    monkeypatch.setattr(
        setup_mod,
        "_build_env_updates",
        lambda _config, **kwargs: ({"ORCHEO_API_URL": "http://localhost:2025"}, {}),
    )
    setup_mod._ensure_stack_assets(config=_setup_config(), console=Console(record=True))
    assert (stack_dir / ".env.example").exists()
    assert (stack_dir / ".env").exists()

    config = _setup_config()
    config.start_stack = False
    console = Console(record=True)
    setup_mod.print_summary(config, console=console)
    assert "Canvas may take 2-3 minutes" not in console.export_text()

    monotonic_values = iter([0.0, 0.5, 1.0, 1.1, 1.2])
    sleeps: list[float] = []
    monkeypatch.setattr(setup_mod.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(setup_mod.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setenv("ORCHEO_SETUP_HEALTH_POLL_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(
        setup_mod,
        "urlopen",
        lambda url, timeout: _Response(b"{}", status=500),
    )
    monkeypatch.setattr(setup_mod, "_HEALTH_POLL_INTERVAL_SECONDS", 5)
    assert (
        setup_mod._poll_backend_health(
            "http://localhost:2025", console=Console(record=True)
        )
        is False
    )
    assert sleeps == []


def test_ensure_stack_assets_syncs_env_example_when_not_in_asset_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    stack_dir = tmp_path / "stack-missing-env-example"
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))
    monkeypatch.setattr(
        setup_mod,
        "_STACK_ASSET_FILES",
        (
            "docker-compose.yml",
            "Dockerfile.orcheo",
            "chatkit_widgets/Single-choice list.widget",
            "chatkit_widgets/Multi-choice Selector.widget",
        ),
    )

    sync_calls: list[str] = []

    def _sync(
        relative_path: str,
        target_stack_dir: Path,
        *,
        stack_version: str | None,
        console: Console,
    ) -> None:
        del console, stack_version
        sync_calls.append(relative_path)
        destination = target_stack_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("payload", encoding="utf-8")

    monkeypatch.setattr(setup_mod, "_sync_stack_asset", _sync)
    monkeypatch.setattr(
        setup_mod.shutil,
        "copyfile",
        lambda src, dst: Path(dst).write_text(
            Path(src).read_text(encoding="utf-8"), encoding="utf-8"
        ),
    )
    monkeypatch.setattr(
        setup_mod,
        "_build_env_updates",
        lambda _config, **kwargs: ({"ORCHEO_API_URL": "http://localhost:2025"}, {}),
    )

    setup_mod._ensure_stack_assets(
        config=_setup_config(),
        console=Console(record=True),
    )

    assert ".env.example" in sync_calls


def test_ensure_stack_assets_uses_latest_stack_tag_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    stack_dir = tmp_path / "stack-latest-tag"
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))
    monkeypatch.delenv("ORCHEO_STACK_ASSET_BASE_URL", raising=False)
    monkeypatch.delenv("ORCHEO_STACK_VERSION", raising=False)

    assets = _default_assets()
    tags_payload = json.dumps(
        [{"name": "stack-v0.3.0"}, {"name": "core-v9.9.9"}]
    ).encode("utf-8")
    tag_base = setup_mod._STACK_ASSET_BASE_URL_TEMPLATE.format(ref="stack-v0.3.0")

    def _urlopen(url: str, timeout: int) -> _Response:
        del timeout
        if url == f"{setup_mod._GITHUB_TAGS_API_URL}?per_page=100":
            return _Response(tags_payload)
        if url.startswith(f"{tag_base}/"):
            relative_path = unquote(url.removeprefix(f"{tag_base}/"))
            return _Response(assets[relative_path])
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(setup_mod, "urlopen", _urlopen)

    setup_mod._ensure_stack_assets(
        config=_setup_config(),
        console=Console(record=True),
    )

    for relative_path, payload in assets.items():
        assert (stack_dir / relative_path).read_bytes() == payload


def test_ensure_stack_assets_falls_back_to_main_assets_when_tag_lookup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    stack_dir = tmp_path / "stack-fallback-main"
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))
    monkeypatch.delenv("ORCHEO_STACK_ASSET_BASE_URL", raising=False)
    monkeypatch.delenv("ORCHEO_STACK_VERSION", raising=False)

    assets = _default_assets()
    main_base = setup_mod._STACK_ASSET_BASE_URL

    def _urlopen(url: str, timeout: int) -> _Response:
        del timeout
        if url == f"{setup_mod._GITHUB_TAGS_API_URL}?per_page=100":
            raise OSError("tags unavailable")
        if url.startswith(f"{main_base}/"):
            relative_path = unquote(url.removeprefix(f"{main_base}/"))
            return _Response(assets[relative_path])
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(setup_mod, "urlopen", _urlopen)

    setup_mod._ensure_stack_assets(
        config=_setup_config(),
        console=Console(record=True),
    )

    for relative_path, payload in assets.items():
        assert (stack_dir / relative_path).read_bytes() == payload


def test_ensure_stack_assets_uses_explicit_stack_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    stack_dir = tmp_path / "stack-explicit-version"
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))
    monkeypatch.delenv("ORCHEO_STACK_ASSET_BASE_URL", raising=False)
    monkeypatch.delenv("ORCHEO_STACK_VERSION", raising=False)

    assets = _default_assets()
    expected_base = setup_mod._STACK_ASSET_BASE_URL_TEMPLATE.format(ref="stack-v0.5.0")
    calls: list[str] = []

    def _urlopen(url: str, timeout: int) -> _Response:
        del timeout
        calls.append(url)
        if url.startswith(f"{expected_base}/"):
            relative_path = unquote(url.removeprefix(f"{expected_base}/"))
            return _Response(assets[relative_path])
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(setup_mod, "urlopen", _urlopen)

    setup_mod._ensure_stack_assets(
        config=_setup_config(),
        console=Console(record=True),
        stack_version="0.5.0",
    )

    assert f"{setup_mod._GITHUB_TAGS_API_URL}?per_page=100" not in calls
    env_content = (stack_dir / ".env").read_text(encoding="utf-8")
    assert "ORCHEO_STACK_IMAGE=ghcr.io/ai-colleagues/orcheo-stack:0.5.0" in env_content


def test_ensure_stack_assets_uses_env_stack_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    stack_dir = tmp_path / "stack-env-version"
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))
    monkeypatch.delenv("ORCHEO_STACK_ASSET_BASE_URL", raising=False)
    monkeypatch.setenv("ORCHEO_STACK_VERSION", "stack-v0.6.1")

    assets = _default_assets()
    expected_base = setup_mod._STACK_ASSET_BASE_URL_TEMPLATE.format(ref="stack-v0.6.1")

    def _urlopen(url: str, timeout: int) -> _Response:
        del timeout
        if url.startswith(f"{expected_base}/"):
            relative_path = unquote(url.removeprefix(f"{expected_base}/"))
            return _Response(assets[relative_path])
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(setup_mod, "urlopen", _urlopen)

    setup_mod._ensure_stack_assets(
        config=_setup_config(),
        console=Console(record=True),
    )

    env_content = (stack_dir / ".env").read_text(encoding="utf-8")
    assert "ORCHEO_STACK_IMAGE=ghcr.io/ai-colleagues/orcheo-stack:0.6.1" in env_content


def test_ensure_stack_assets_custom_base_url_forces_per_file_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    stack_dir = tmp_path / "stack-custom-base-url"
    base_url = "https://mirror.example.test/orcheo-stack"
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))
    monkeypatch.setenv("ORCHEO_STACK_ASSET_BASE_URL", base_url)
    monkeypatch.setenv("ORCHEO_STACK_VERSION", "0.9.0")

    assets = _default_assets()
    tags_url = f"{setup_mod._GITHUB_TAGS_API_URL}?per_page=100"

    def _urlopen(url: str, timeout: int) -> _Response:
        del timeout
        if url == tags_url:
            raise AssertionError(
                "Tag lookup should not run when base URL is configured."
            )
        relative_path = unquote(url.removeprefix(f"{base_url}/"))
        return _Response(assets[relative_path])

    monkeypatch.setattr(setup_mod, "urlopen", _urlopen)

    setup_mod._ensure_stack_assets(
        config=_setup_config(),
        console=Console(record=True),
    )

    for relative_path, payload in assets.items():
        assert (stack_dir / relative_path).read_bytes() == payload
    env_content = (stack_dir / ".env").read_text(encoding="utf-8")
    assert "ORCHEO_STACK_IMAGE=ghcr.io/ai-colleagues/orcheo-stack:0.9.0" in env_content


def test_resolve_stack_version_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    monkeypatch.setenv("ORCHEO_STACK_VERSION", "0.7.0")
    assert setup_mod._resolve_stack_version("0.8.0") == "0.8.0"
    assert setup_mod._resolve_stack_version(None) == "0.7.0"

    monkeypatch.setenv("ORCHEO_STACK_VERSION", "stack-v0.9.1")
    assert setup_mod._resolve_stack_version(None) == "0.9.1"


def test_discover_latest_stack_version_from_tags_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    payload = json.dumps(
        [
            {"name": "stack-v1.0.0-rc1"},
            {"name": "core-v9.9.9"},
            {"name": "stack-v0.8.2"},
        ]
    ).encode("utf-8")
    calls: list[str] = []

    def _urlopen(url: str, timeout: int) -> _Response:
        del timeout
        calls.append(url)
        return _Response(payload)

    monkeypatch.setattr(setup_mod, "urlopen", _urlopen)

    version = setup_mod._discover_latest_stack_version(Console(record=True))
    assert version == "0.8.2"
    assert calls == [f"{setup_mod._GITHUB_TAGS_API_URL}?per_page=100"]


def test_discover_latest_stack_version_soft_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    def _raise(url: str, timeout: int) -> _Response:
        del url, timeout
        raise OSError("network down")

    monkeypatch.setattr(setup_mod, "urlopen", _raise)
    assert setup_mod._discover_latest_stack_version(Console(record=True)) is None

    monkeypatch.setattr(setup_mod, "urlopen", lambda url, timeout: _Response(b"{bad"))
    assert setup_mod._discover_latest_stack_version(Console(record=True)) is None

    monkeypatch.setattr(
        setup_mod,
        "urlopen",
        lambda url, timeout: _Response(b'{"name":"stack-v0.1.0"}'),
    )
    assert setup_mod._discover_latest_stack_version(Console(record=True)) is None


def test_discover_latest_stack_version_skips_invalid_tag_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    payload = json.dumps(
        [
            "not-a-dict",
            {"name": 123},
            {"name": "stack-v"},
            {"name": "stack-v0.8.3"},
        ]
    ).encode("utf-8")
    monkeypatch.setattr(
        setup_mod,
        "urlopen",
        lambda url, timeout: _Response(payload),
    )

    version = setup_mod._discover_latest_stack_version(Console(record=True))
    assert version == "0.8.3"


def test_discover_latest_stack_version_returns_none_when_no_stable_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_sdk.cli import setup as setup_mod

    payload = json.dumps(
        [
            {"name": "core-v9.9.9"},
            {"name": "stack-v1.0.0-rc1"},
            {"name": "stack-v"},
        ]
    ).encode("utf-8")
    monkeypatch.setattr(
        setup_mod,
        "urlopen",
        lambda url, timeout: _Response(payload),
    )

    assert setup_mod._discover_latest_stack_version(Console(record=True)) is None
