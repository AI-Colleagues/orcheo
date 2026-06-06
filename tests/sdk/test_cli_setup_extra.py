"""Extra CLI setup tests that exercise edge paths."""

from __future__ import annotations
import io
import os
from pathlib import Path
import pytest
import typer
from rich.console import Console
from orcheo_sdk.cli import setup as setup_mod


def test_has_binary_refreshes_path(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def fake_refresh() -> None:
        called.append(True)

    monkeypatch.setattr(
        setup_mod, "_refresh_docker_cli_path_for_current_process", fake_refresh
    )
    monkeypatch.setattr(setup_mod.shutil, "which", lambda name: "/usr/bin/docker")

    assert setup_mod._has_binary("docker")
    assert called == [True]


def test_has_binary_non_docker_skips_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    monkeypatch.setattr(
        setup_mod,
        "_refresh_docker_cli_path_for_current_process",
        lambda: called.append(True),
    )
    monkeypatch.setattr(setup_mod.shutil, "which", lambda name: "/usr/bin/git")

    assert setup_mod._has_binary("git")
    assert called == []


def test_refresh_docker_cli_path_updates_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "docker-bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text("")

    def fake_candidates() -> list[Path]:
        return [bin_dir / "docker"]

    original_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", original_path)
    monkeypatch.setattr(setup_mod, "_docker_cli_path_candidates", fake_candidates)

    setup_mod._refresh_docker_cli_path_for_current_process()
    updated_path = os.environ.get("PATH", "")
    assert updated_path.startswith(str(bin_dir))


def test_refresh_docker_cli_path_leaves_empty_path_when_no_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(setup_mod, "_docker_cli_path_candidates", lambda: [])

    setup_mod._refresh_docker_cli_path_for_current_process()

    assert os.environ.get("PATH", "") == ""


def test_docker_command_handles_missing_and_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        setup_mod, "_refresh_docker_cli_path_for_current_process", lambda: None
    )
    monkeypatch.setattr(setup_mod.shutil, "which", lambda name: None)
    assert setup_mod._docker_command() is None

    monkeypatch.setattr(setup_mod.shutil, "which", lambda name: "/usr/bin/docker")
    assert setup_mod._docker_command() == ["/usr/bin/docker"]


def test_read_os_release_missing_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_mod.Path, "exists", lambda self: False)
    assert setup_mod._read_os_release() == {}


def test_read_docker_ready_timeout_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    key = "ORCHEO_SETUP_DOCKER_READY_TIMEOUT_SECONDS"
    monkeypatch.delenv(key, raising=False)
    assert (
        setup_mod._read_docker_ready_timeout_seconds()
        == setup_mod._DEFAULT_DOCKER_READY_TIMEOUT_SECONDS
    )

    monkeypatch.setenv(key, "5")
    assert setup_mod._read_docker_ready_timeout_seconds() == 5

    monkeypatch.setenv(key, "-1")
    assert (
        setup_mod._read_docker_ready_timeout_seconds()
        == setup_mod._DEFAULT_DOCKER_READY_TIMEOUT_SECONDS
    )

    monkeypatch.setenv(key, "invalid")
    assert (
        setup_mod._read_docker_ready_timeout_seconds()
        == setup_mod._DEFAULT_DOCKER_READY_TIMEOUT_SECONDS
    )


def test_wait_for_docker_access_reports_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = Console(file=io.StringIO(), force_terminal=False)
    monkeypatch.setattr(setup_mod, "_read_docker_ready_timeout_seconds", lambda: 1)
    monkeypatch.setattr(setup_mod, "_current_shell_has_docker_access", lambda: False)

    values = [0.0, 0.0, 0.1, 2.0]

    def monotonic() -> float:
        return values.pop(0) if values else 2.0

    slept: list[float] = []

    monkeypatch.setattr(setup_mod.time, "monotonic", monotonic)
    monkeypatch.setattr(setup_mod.time, "sleep", lambda seconds: slept.append(seconds))

    assert not setup_mod._wait_for_docker_access(console=console)
    assert slept == [0.9]


def test_wait_for_docker_access_succeeds_after_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = Console(file=io.StringIO(), force_terminal=False)
    monkeypatch.setattr(setup_mod, "_read_docker_ready_timeout_seconds", lambda: 1)

    checks = [False, True]
    monkeypatch.setattr(
        setup_mod, "_current_shell_has_docker_access", lambda: checks.pop(0)
    )

    monotonic_values = iter([0.0, 0.0, 0.4, 0.6])
    monkeypatch.setattr(setup_mod.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(setup_mod.time, "sleep", lambda seconds: None)

    assert setup_mod._wait_for_docker_access(console=console)


def test_wait_for_docker_access_rechecks_without_sleep_when_time_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = Console(file=io.StringIO(), force_terminal=False)
    monkeypatch.setattr(setup_mod, "_read_docker_ready_timeout_seconds", lambda: 1)
    monkeypatch.setattr(setup_mod, "_current_shell_has_docker_access", lambda: False)

    monotonic_values = iter([0.0, 0.0, 1.0, 1.1])
    slept: list[float] = []
    monkeypatch.setattr(setup_mod.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(setup_mod.time, "sleep", lambda seconds: slept.append(seconds))

    assert not setup_mod._wait_for_docker_access(console=console)
    assert slept == []


def test_start_docker_desktop_macos_and_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[list[str]] = []

    def fake_run(command: list[str], *, console: Console) -> None:
        captured.append(command)

    monkeypatch.setenv("ORCHEO_STACK_DIR", "/tmp")
    monkeypatch.setattr(setup_mod, "_run_command", fake_run)
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Darwin")

    setup_mod._start_docker_desktop(console=Console())
    assert captured[-1][:2] == ["open", "-a"]

    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Windows")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    windows_exe = tmp_path / "Docker" / "Docker" / "Docker Desktop.exe"
    windows_exe.parent.mkdir(parents=True, exist_ok=True)
    windows_exe.write_text("")

    setup_mod._start_docker_desktop(console=Console())
    assert captured[-1][0] == "powershell.exe"


def test_start_docker_desktop_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Linux")
    with pytest.raises(typer.BadParameter):
        setup_mod._start_docker_desktop(console=Console())


def test_current_windows_wsl_ready_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Linux")
    assert setup_mod._current_windows_wsl_ready()


def test_run_privileged_command_uses_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(setup_mod.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(setup_mod, "_has_binary", lambda name: name == "sudo")
    monkeypatch.setattr(
        setup_mod, "_run_command", lambda command, *, console: commands.append(command)
    )

    setup_mod._run_privileged_command(["echo", "ok"], console=Console())

    assert commands == [["sudo", "echo", "ok"]]


def test_current_shell_has_docker_access_without_docker_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod, "_docker_command", lambda: None)
    assert setup_mod._current_shell_has_docker_access() is False


def test_ensure_windows_wsl_install_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    monster = [False, False]

    def fake_status() -> bool:
        return monster.pop(0) if monster else False

    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup_mod, "_current_windows_wsl_ready", fake_status)
    monkeypatch.setattr(
        setup_mod, "_run_windows_elevated_command", lambda command, *, console: None
    )

    assert not setup_mod._ensure_windows_wsl(console=Console())

    called: list[int] = []

    def failing(command: list[str], *, console: Console) -> None:
        called.append(1)
        raise typer.BadParameter("boom")

    monkeypatch.setattr(setup_mod, "_current_windows_wsl_ready", lambda: False)
    monkeypatch.setattr(setup_mod, "_run_windows_elevated_command", failing)

    assert not setup_mod._ensure_windows_wsl(console=Console())
    assert called == [1]


def test_resolve_macos_docker_volume_path_returns_none_when_no_installer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod.Path, "glob", lambda self, pattern: [])
    assert setup_mod._resolve_macos_docker_volume_path() is None


def test_ensure_windows_wsl_short_circuits_when_not_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Linux")
    assert setup_mod._ensure_windows_wsl(console=Console())

    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup_mod, "_current_windows_wsl_ready", lambda: True)
    assert setup_mod._ensure_windows_wsl(console=Console())


def test_ensure_windows_wsl_succeeds_after_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready_states = [False, True]

    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        setup_mod, "_current_windows_wsl_ready", lambda: ready_states.pop(0)
    )
    monkeypatch.setattr(
        setup_mod, "_run_windows_elevated_command", lambda command, *, console: None
    )

    assert setup_mod._ensure_windows_wsl(console=Console())


def test_attempt_macos_docker_desktop_install_variants(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        setup_mod, "_download_binary_asset", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        setup_mod, "_run_privileged_command", lambda command, *, console: None
    )
    monkeypatch.setattr(
        setup_mod, "_refresh_docker_cli_path_for_current_process", lambda: None
    )
    monkeypatch.setattr(setup_mod, "_start_docker_desktop", lambda *, console: None)
    monkeypatch.setattr(setup_mod, "_wait_for_docker_access", lambda *, console: True)

    monkeypatch.setattr(setup_mod, "_normalized_machine", lambda: "unknown-arch")
    assert not setup_mod._attempt_macos_docker_desktop_install(console=Console())

    monkeypatch.setattr(setup_mod, "_normalized_machine", lambda: "x86_64")
    monkeypatch.setattr(setup_mod, "_current_username", lambda: None)
    assert not setup_mod._attempt_macos_docker_desktop_install(console=Console())

    monkeypatch.setattr(setup_mod, "_current_username", lambda: "user")
    monkeypatch.setattr(setup_mod, "_resolve_macos_docker_volume_path", lambda: None)
    assert not setup_mod._attempt_macos_docker_desktop_install(console=Console())

    called: list[list[str]] = []

    def record_privileged(command: list[str], *, console: Console) -> None:
        called.append(command)

    monkeypatch.setattr(
        setup_mod, "_resolve_macos_docker_volume_path", lambda: tmp_path / "volume"
    )
    monkeypatch.setattr(setup_mod, "_run_privileged_command", record_privileged)

    assert setup_mod._attempt_macos_docker_desktop_install(console=Console())
    assert any("hdiutil" in " ".join(command) for command in called)


def test_attempt_macos_docker_desktop_install_warns_when_detach_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mounted_volume = tmp_path / "DockerVolume"
    commands: list[list[str]] = []

    def fake_run_privileged(command: list[str], *, console: Console) -> None:
        commands.append(command)
        if command[:2] == ["hdiutil", "detach"]:
            raise typer.BadParameter("detach failed")

    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(setup_mod, "_normalized_machine", lambda: "x86_64")
    monkeypatch.setattr(setup_mod, "_current_username", lambda: "user")
    monkeypatch.setattr(
        setup_mod, "_download_binary_asset", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(setup_mod, "_run_privileged_command", fake_run_privileged)
    monkeypatch.setattr(
        setup_mod, "_resolve_macos_docker_volume_path", lambda: mounted_volume
    )
    monkeypatch.setattr(
        setup_mod, "_refresh_docker_cli_path_for_current_process", lambda: None
    )
    monkeypatch.setattr(setup_mod, "_start_docker_desktop", lambda *, console: None)
    monkeypatch.setattr(setup_mod, "_wait_for_docker_access", lambda *, console: True)

    console = Console(file=io.StringIO(), force_terminal=False)
    assert setup_mod._attempt_macos_docker_desktop_install(console=console)
    assert any(command[:2] == ["hdiutil", "detach"] for command in commands)
    assert "still mounted" in console.file.getvalue()


def test_attempt_windows_docker_desktop_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup_mod, "_normalized_machine", lambda: "unknown")
    assert not setup_mod._attempt_windows_docker_desktop_install(console=Console())

    monkeypatch.setattr(setup_mod, "_normalized_machine", lambda: "x86_64")
    monkeypatch.setattr(setup_mod, "_ensure_windows_wsl", lambda *, console: False)
    assert not setup_mod._attempt_windows_docker_desktop_install(console=Console())

    monkeypatch.setattr(setup_mod, "_ensure_windows_wsl", lambda *, console: True)
    monkeypatch.setattr(
        setup_mod, "_download_binary_asset", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        setup_mod, "_run_windows_elevated_command", lambda command, *, console: None
    )
    monkeypatch.setattr(
        setup_mod, "_refresh_docker_cli_path_for_current_process", lambda: None
    )
    monkeypatch.setattr(setup_mod, "_start_docker_desktop", lambda *, console: None)
    monkeypatch.setattr(setup_mod, "_wait_for_docker_access", lambda *, console: True)

    assert setup_mod._attempt_windows_docker_desktop_install(console=Console())

    def fail(command: list[str], *, console: Console) -> None:
        raise typer.BadParameter("fail")

    monkeypatch.setattr(setup_mod, "_run_windows_elevated_command", fail)
    assert not setup_mod._attempt_windows_docker_desktop_install(console=Console())


def test_attempt_linux_docker_autoinstall_without_username_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(
        setup_mod, "_is_supported_docker_autoinstall_linux", lambda: True
    )
    monkeypatch.setattr(
        setup_mod, "_has_binary", lambda name: name in {"apt-get", "docker"}
    )
    monkeypatch.setattr(setup_mod, "_current_username", lambda: None)
    monkeypatch.setattr(
        setup_mod,
        "_run_privileged_command",
        lambda command, *, console: commands.append(command),
    )

    assert setup_mod._attempt_linux_docker_autoinstall(console=Console())
    assert commands == [
        ["apt-get", "update"],
        ["apt-get", "install", "-y", "docker.io", "docker-compose-v2"],
        ["systemctl", "enable", "--now", "docker"],
    ]


def test_attempt_linux_docker_autoinstall_warns_when_binary_still_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        setup_mod, "_is_supported_docker_autoinstall_linux", lambda: True
    )
    monkeypatch.setattr(setup_mod, "_has_binary", lambda name: name == "apt-get")
    monkeypatch.setattr(setup_mod, "_current_username", lambda: None)
    monkeypatch.setattr(
        setup_mod, "_run_privileged_command", lambda command, *, console: None
    )

    console = Console(file=io.StringIO(), force_terminal=False)
    assert not setup_mod._attempt_linux_docker_autoinstall(console=console)
    assert "docker binary is still not available" in console.file.getvalue()


def test_attempt_docker_autoinstall_unknown_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "FreeBSD")
    assert not setup_mod._attempt_docker_autoinstall(console=Console())
