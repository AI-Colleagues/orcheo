"""Tests for CLI setup helper utilities."""

from __future__ import annotations
from pathlib import Path
import pytest
import typer
from rich.console import Console
from orcheo_sdk.cli import setup as setup_mod


def _make_config(**overrides: object) -> setup_mod.SetupConfig:
    base: dict[str, object] = {
        "mode": "install",
        "backend_url": "http://localhost:2025",
        "studio_url": "http://localhost:2026",
        "auth_mode": "api-key",
        "api_key": "key",
        "chatkit_domain_key": None,
        "public_ingress_enabled": False,
        "public_host": None,
        "publish_local_ports": True,
        "backend_upstreams": "backend:2025",
        "canvas_upstream": "canvas:2026",
        "start_stack": False,
        "install_docker_if_missing": False,
    }
    base.update(overrides)
    return setup_mod.SetupConfig(**base)  # type: ignore[arg-type]


class _MissingOsReleasePath:
    """Fake Path that always reports /etc/os-release as missing."""

    def __init__(self, path: str) -> None:
        self._path = path

    def exists(self) -> bool:
        return False

    def read_text(self, *, encoding: str) -> str:
        raise AssertionError("read_text should not be called when path is absent")


def test_read_os_release_missing_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_mod, "Path", _MissingOsReleasePath)
    assert setup_mod._read_os_release() == {}


def test_run_privileged_command_runs_without_sudo_when_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod.os, "geteuid", lambda: 0)
    recorded: list[list[str]] = []

    def _fake_run(command: list[str], *, console: Console) -> None:
        recorded.append(command)

    monkeypatch.setattr(setup_mod, "_run_command", _fake_run)
    setup_mod._run_privileged_command(["/bin/true"], console=Console(record=True))
    assert recorded == [["/bin/true"]]


def test_run_privileged_command_rejects_without_sudo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(setup_mod, "_has_binary", lambda _: False)
    with pytest.raises(typer.BadParameter):
        setup_mod._run_privileged_command(["/bin/true"], console=Console(record=True))


def test_run_privileged_command_prefixes_with_sudo_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(setup_mod, "_has_binary", lambda name: name == "sudo")
    recorded: list[list[str]] = []

    def _fake_run(command: list[str], *, console: Console) -> None:
        recorded.append(command)

    monkeypatch.setattr(setup_mod, "_run_command", _fake_run)
    setup_mod._run_privileged_command(["/bin/true"], console=Console(record=True))
    assert recorded == [["sudo", "/bin/true"]]


def test_current_shell_has_docker_access_false_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod, "_docker_command", lambda: None)
    assert not setup_mod._current_shell_has_docker_access()


def test_current_shell_has_docker_access_true_when_docker_info_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod, "_docker_command", lambda: ["docker"])

    class _Result:
        returncode = 0

    monkeypatch.setattr(
        setup_mod.subprocess,
        "run",
        lambda *args, **kwargs: _Result(),
    )
    assert setup_mod._current_shell_has_docker_access()


def test_attempt_docker_autoinstall_delegates_to_platform_installers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        setup_mod,
        "_attempt_macos_docker_desktop_install",
        lambda *, console: True,
    )
    assert setup_mod._attempt_docker_autoinstall(console=Console(record=True)) is True

    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        setup_mod,
        "_attempt_windows_docker_desktop_install",
        lambda *, console: True,
    )
    assert setup_mod._attempt_docker_autoinstall(console=Console(record=True)) is True


def test_attempt_docker_autoinstall_runs_commands_when_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        setup_mod, "_is_supported_docker_autoinstall_linux", lambda: True
    )
    monkeypatch.setattr(
        setup_mod,
        "_has_binary",
        lambda name: name in {"apt-get", "docker"},
    )
    calls: list[list[str]] = []

    def _fake_run(command: list[str], *, console: Console) -> None:
        calls.append(command)

    monkeypatch.setattr(setup_mod, "_run_privileged_command", _fake_run)
    monkeypatch.setattr(setup_mod, "_current_username", lambda: "alice")

    assert setup_mod._attempt_docker_autoinstall(console=Console(record=True))
    assert calls[:3] == [
        ["apt-get", "update"],
        ["apt-get", "install", "-y", "docker.io", "docker-compose-v2"],
        ["systemctl", "enable", "--now", "docker"],
    ]
    assert calls[3] == ["usermod", "-aG", "docker", "alice"]


def test_attempt_docker_autoinstall_handles_privileged_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        setup_mod, "_is_supported_docker_autoinstall_linux", lambda: True
    )
    monkeypatch.setattr(setup_mod, "_has_binary", lambda _: True)

    def _fake_run(*args: object, **kwargs: object) -> None:
        raise typer.BadParameter("boom")

    monkeypatch.setattr(setup_mod, "_run_privileged_command", _fake_run)
    assert not setup_mod._attempt_docker_autoinstall(console=Console(record=True))


def test_attempt_docker_autoinstall_returns_false_when_docker_still_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        setup_mod, "_is_supported_docker_autoinstall_linux", lambda: True
    )

    def _fake_has_binary(name: str) -> bool:
        return name == "apt-get"

    monkeypatch.setattr(setup_mod, "_has_binary", _fake_has_binary)
    monkeypatch.setattr(
        setup_mod, "_run_privileged_command", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(setup_mod, "_current_username", lambda: None)

    assert not setup_mod._attempt_docker_autoinstall(console=Console(record=True))


def test_resolve_studio_url_prompts_with_existing_env_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ORCHEO_STUDIO_URL=https://saved.example.com\n", encoding="utf-8"
    )

    captured: dict[str, object] = {}

    def _prompt(message: str, *, default: str) -> str:
        captured["message"] = message
        captured["default"] = default
        return default

    monkeypatch.setattr(setup_mod.typer, "prompt", _prompt)

    assert (
        setup_mod._resolve_studio_url(
            None,
            public_ingress_enabled=False,
            public_host=None,
            yes=False,
            env_file=env_file,
            env_exists=True,
        )
        == "https://saved.example.com"
    )
    assert captured["message"] == "Studio URL"
    assert captured["default"] == "https://saved.example.com"


def test_studio_origin_returns_none_without_scheme_or_netloc() -> None:
    assert setup_mod._studio_origin("localhost") is None


def test_build_cors_origins_skips_missing_studio_origin() -> None:
    config = _make_config(studio_url="", publish_local_ports=True)
    assert setup_mod._build_cors_origins(config) == (
        "http://localhost:2026,http://127.0.0.1:2026"
    )


def test_build_allowed_hosts_skips_missing_studio_host() -> None:
    config = _make_config(studio_url="")
    assert setup_mod._build_allowed_hosts(config) == "localhost,127.0.0.1"
