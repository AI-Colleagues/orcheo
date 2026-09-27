"""Tests for `orcheo install --lean`."""

from __future__ import annotations
import json
from io import BytesIO
from pathlib import Path
from typing import Any
import click
import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner
from orcheo_sdk.cli import lean_setup as lean_mod
from orcheo_sdk.cli import main as main_mod
from orcheo_sdk.cli.main import app


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAIN_BASE = "https://raw.githubusercontent.com/AI-Colleagues/orcheo/main"
_WIDGETS_API = (
    "https://api.github.com/repos/AI-Colleagues/orcheo/contents/"
    "deploy/stack/chatkit_widgets"
)


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._buffer = BytesIO(payload)
        self.status = 200

    def read(self) -> bytes:
        return self._buffer.read()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _install_fake_urlopen(
    monkeypatch: pytest.MonkeyPatch, payloads: dict[str, bytes]
) -> list[str]:
    requested: list[str] = []

    def _urlopen(url: str, timeout: int) -> _Response:
        del timeout
        requested.append(url)
        if url not in payloads:
            raise OSError(f"unexpected url {url}")
        return _Response(payloads[url])

    monkeypatch.setattr(lean_mod, "urlopen", _urlopen)
    return requested


def _lean_payloads(ref: str = "main") -> dict[str, bytes]:
    base = f"https://raw.githubusercontent.com/AI-Colleagues/orcheo/{ref}"
    return {
        f"{base}/docker-compose-lean.yml": b"name: orcheo-lean\n",
        f"{base}/deploy/lean/.env.example": (
            _REPO_ROOT / "deploy/lean/.env.example"
        ).read_bytes(),
        f"{_WIDGETS_API}?ref={ref}": json.dumps(
            [
                {"name": "card.widget", "type": "file"},
                {"name": "nested", "type": "dir"},
            ]
        ).encode(),
        f"{base}/deploy/stack/chatkit_widgets/card.widget": b"{}",
    }


@pytest.fixture(autouse=True)
def _lean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    lean_dir = tmp_path / "lean"
    monkeypatch.setenv("ORCHEO_LEAN_DIR", str(lean_dir))
    monkeypatch.delenv("ORCHEO_LEAN_VERSION", raising=False)
    monkeypatch.delenv("ORCHEO_LEAN_ASSET_BASE_URL", raising=False)
    return lean_dir


def test_resolve_lean_project_dir_defaults_to_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ORCHEO_LEAN_DIR")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert lean_mod._resolve_lean_project_dir() == tmp_path / ".orcheo" / "lean"


def test_normalize_lean_version() -> None:
    assert lean_mod._normalize_lean_version(None) is None
    assert lean_mod._normalize_lean_version("  ") is None
    assert lean_mod._normalize_lean_version("lean-v") is None
    assert lean_mod._normalize_lean_version(" lean-v0.2.0 ") == "0.2.0"
    assert lean_mod._normalize_lean_version("0.2.0") == "0.2.0"


def test_resolve_lean_version_prefers_explicit_then_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = Console(record=True)
    monkeypatch.setenv("ORCHEO_LEAN_VERSION", "lean-v0.1.0")
    assert (
        lean_mod._resolve_lean_version("0.3.0", staging=False, console=console)
        == "0.3.0"
    )
    assert (
        lean_mod._resolve_lean_version(None, staging=False, console=console) == "0.1.0"
    )


def test_resolve_lean_version_staging_rejects_stable_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_LEAN_VERSION", "0.1.0")
    with pytest.raises(typer.BadParameter, match="prerelease ORCHEO_LEAN_VERSION"):
        lean_mod._resolve_lean_version(None, staging=True, console=Console())
    monkeypatch.setenv("ORCHEO_LEAN_VERSION", "0.2.0-rc.1")
    assert (
        lean_mod._resolve_lean_version(None, staging=True, console=Console())
        == "0.2.0-rc.1"
    )


def test_resolve_lean_version_discovers_lean_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def _discover(console: Console, **kwargs: Any) -> str | None:
        calls.append(kwargs)
        return "0.4.0"

    monkeypatch.setattr(lean_mod, "_discover_latest_stack_version", _discover)
    assert (
        lean_mod._resolve_lean_version(None, staging=False, console=Console())
        == "0.4.0"
    )
    assert calls == [{"prerelease": False, "tag_prefix": "lean-v"}]


def test_resolve_lean_version_staging_requires_prerelease_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        lean_mod, "_discover_latest_stack_version", lambda console, **kwargs: None
    )
    with pytest.raises(typer.BadParameter, match="No published prerelease lean"):
        lean_mod._resolve_lean_version(None, staging=True, console=Console())
    assert lean_mod._resolve_lean_version(None, staging=False, console=Console()) is (
        None
    )


def test_asset_base_url_and_image(monkeypatch: pytest.MonkeyPatch) -> None:
    assert lean_mod._resolve_lean_asset_base_url(None) == _MAIN_BASE
    assert lean_mod._resolve_lean_asset_base_url("0.2.0") == (
        "https://raw.githubusercontent.com/AI-Colleagues/orcheo/lean-v0.2.0"
    )
    monkeypatch.setenv("ORCHEO_LEAN_ASSET_BASE_URL", "https://mirror.example/repo/")
    assert lean_mod._resolve_lean_asset_base_url("0.2.0") == (
        "https://mirror.example/repo"
    )
    assert lean_mod._lean_image(None) == "ghcr.io/ai-colleagues/orcheo-lean:latest"
    assert lean_mod._lean_image("0.2.0") == "ghcr.io/ai-colleagues/orcheo-lean:0.2.0"


def test_list_lean_widget_paths_skips_on_bad_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_urlopen(monkeypatch, {f"{_WIDGETS_API}?ref=main": b"{}"})
    console = Console(record=True)
    assert lean_mod._list_lean_widget_paths(None, console) == ()
    assert "skipping widget sync" in console.export_text()

    _install_fake_urlopen(monkeypatch, {})
    assert lean_mod._list_lean_widget_paths("0.2.0", console) == ()


def test_sync_lean_asset_reports_download_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_urlopen(monkeypatch, {})
    with pytest.raises(typer.BadParameter, match="Failed to download lean asset"):
        lean_mod._sync_lean_asset(
            "docker-compose-lean.yml",
            tmp_path,
            lean_version=None,
            console=Console(record=True),
        )


def test_sync_and_configure_env_generates_then_preserves_secrets(
    monkeypatch: pytest.MonkeyPatch, _lean_env: Path
) -> None:
    lean_dir = _lean_env
    _install_fake_urlopen(monkeypatch, _lean_payloads())
    console = Console(record=True)

    lean_mod._sync_lean_assets(lean_dir, lean_version=None, console=console)
    env_file = lean_mod._configure_lean_env(
        lean_dir, lean_version=None, console=console
    )

    assert (lean_dir / "docker-compose-lean.yml").read_text() == "name: orcheo-lean\n"
    assert (lean_dir / "deploy/stack/chatkit_widgets/card.widget").exists()
    first = env_file.read_text()
    for key in (
        "ORCHEO_POSTGRES_PASSWORD",
        "ORCHEO_VAULT_ENCRYPTION_KEY",
        "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY",
        "ORCHEO_AUTH_JWT_SECRET",
        "ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN",
    ):
        value = lean_mod._read_env_value(env_file, key)
        assert value
        assert value not in {"change-me", "replace-with-64-hex-chars"}
    assert (
        lean_mod._read_env_value(env_file, "ORCHEO_LEAN_IMAGE")
        == "ghcr.io/ai-colleagues/orcheo-lean:latest"
    )

    lean_mod._configure_lean_env(lean_dir, lean_version="0.2.0", console=console)
    second = env_file.read_text()
    assert (
        lean_mod._read_env_value(env_file, "ORCHEO_VAULT_ENCRYPTION_KEY")
        == first.split("ORCHEO_VAULT_ENCRYPTION_KEY=")[1].split("\n")[0]
    )
    assert (
        lean_mod._read_env_value(env_file, "ORCHEO_LEAN_IMAGE")
        == "ghcr.io/ai-colleagues/orcheo-lean:0.2.0"
    )
    assert (
        second.splitlines().count(
            "ORCHEO_LEAN_IMAGE=ghcr.io/ai-colleagues/orcheo-lean:0.2.0"
        )
        == 1
    )
    assert "orcheo-lean:latest" not in second


def test_lean_compose_args_requires_docker_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(lean_mod, "_docker_command", lambda: None)
    with pytest.raises(typer.BadParameter, match="docker CLI could not be resolved"):
        lean_mod._lean_compose_args(tmp_path)


def test_lean_backend_url_uses_configured_port(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ORCHEO_LEAN_PORT=3030\n")
    assert lean_mod._lean_backend_url(env_file) == "http://localhost:3030"
    env_file.write_text("")
    assert lean_mod._lean_backend_url(env_file) == "http://localhost:2025"


@pytest.mark.parametrize("privileged", [False, True])
def test_start_lean_stack_pulls_and_starts_without_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, privileged: bool
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("")
    commands: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(lean_mod, "_docker_command", lambda: ["docker"])
    monkeypatch.setattr(
        lean_mod,
        "_run_command",
        lambda command, console: commands.append(("plain", command)),
    )
    monkeypatch.setattr(
        lean_mod,
        "_run_privileged_command",
        lambda command, console: commands.append(("sudo", command)),
    )
    monkeypatch.setattr(lean_mod, "_poll_backend_health", lambda url, console: True)

    lean_mod._start_lean_stack(
        tmp_path,
        env_file=env_file,
        use_privileged_docker=privileged,
        console=Console(record=True),
    )

    base = [
        "docker",
        "compose",
        "-f",
        str(tmp_path / "docker-compose-lean.yml"),
        "--project-directory",
        str(tmp_path),
    ]
    runner = "sudo" if privileged else "plain"
    assert commands == [
        (runner, [*base, "pull"]),
        (runner, [*base, "up", "-d", "--no-build"]),
    ]


def test_start_lean_stack_reports_unhealthy_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("")
    monkeypatch.setattr(lean_mod, "_docker_command", lambda: ["docker"])
    monkeypatch.setattr(lean_mod, "_run_command", lambda command, console: None)
    monkeypatch.setattr(lean_mod, "_poll_backend_health", lambda url, console: False)
    console = Console(record=True, width=200)

    lean_mod._start_lean_stack(
        tmp_path, env_file=env_file, use_privileged_docker=False, console=console
    )

    assert "did not become healthy" in console.export_text()


def test_run_lean_install_rejects_staging_with_version() -> None:
    with pytest.raises(typer.BadParameter, match="either --staging or --stack-version"):
        lean_mod.run_lean_install(
            lean_version="0.1.0",
            staging=True,
            start_stack=False,
            install_docker=False,
            yes=True,
            console=Console(),
        )


def _stub_install_steps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    started: list[str] = []
    monkeypatch.setattr(
        lean_mod, "_discover_latest_stack_version", lambda console, **kwargs: None
    )
    _install_fake_urlopen(monkeypatch, _lean_payloads())
    monkeypatch.setattr(
        lean_mod,
        "_start_lean_stack",
        lambda lean_dir, **kwargs: started.append(str(lean_dir)),
    )
    return started


def test_run_lean_install_starts_stack(
    monkeypatch: pytest.MonkeyPatch, _lean_env: Path
) -> None:
    started = _stub_install_steps(monkeypatch)
    monkeypatch.setattr(
        lean_mod,
        "_prepare_docker_for_start",
        lambda **kwargs: (True, False, False),
    )
    console = Console(record=True, width=200)

    lean_mod.run_lean_install(
        lean_version=None,
        staging=False,
        start_stack=None,
        install_docker=None,
        yes=True,
        console=console,
    )

    assert started == [str(_lean_env)]
    output = console.export_text()
    assert "Studio: http://localhost:2025" in output
    assert '"stack_started": true' in output


def test_run_lean_install_skips_start_when_docker_unavailable(
    monkeypatch: pytest.MonkeyPatch, _lean_env: Path
) -> None:
    started = _stub_install_steps(monkeypatch)
    monkeypatch.setattr(
        lean_mod,
        "_prepare_docker_for_start",
        lambda **kwargs: (False, False, False),
    )
    console = Console(record=True, width=200)

    lean_mod.run_lean_install(
        lean_version=None,
        staging=False,
        start_stack=True,
        install_docker=True,
        yes=True,
        console=console,
    )

    assert started == []
    assert "up -d --no-build" in console.export_text()
    assert (_lean_env / ".env").exists()


def test_run_lean_install_honours_skip_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = _stub_install_steps(monkeypatch)
    monkeypatch.setattr(
        lean_mod,
        "_prepare_docker_for_start",
        lambda **kwargs: pytest.fail("docker should not be prepared"),
    )

    lean_mod.run_lean_install(
        lean_version=None,
        staging=False,
        start_stack=False,
        install_docker=None,
        yes=True,
        console=Console(record=True),
    )

    assert started == []


def test_install_lean_flag_runs_lean_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        main_mod, "run_lean_install", lambda **kwargs: calls.append(kwargs)
    )
    monkeypatch.setattr(
        main_mod, "_run_install_flow", lambda **kwargs: pytest.fail("stack install")
    )

    result = CliRunner().invoke(
        app,
        ["install", "--lean", "--yes", "--stack-version", "0.2.0", "--skip-stack"],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    call = calls[0]
    assert call["lean_version"] == "0.2.0"
    assert call["staging"] is False
    assert call["start_stack"] is False
    assert call["install_docker"] is None
    assert call["yes"] is True


def test_install_lean_flag_rejects_stack_only_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_mod, "run_lean_install", lambda **kwargs: pytest.fail("should not run")
    )

    result = CliRunner().invoke(
        app,
        [
            "install",
            "--lean",
            "--public-host",
            "orcheo.example.com",
            "--app-trusted-proxy-hops",
            "0",
        ],
    )

    assert result.exit_code != 0
    output = click.unstyle(result.output)
    assert "--public-host" in output
    assert "--app-trusted-proxy-hops" in output
