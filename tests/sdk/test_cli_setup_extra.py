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


def test_hosted_apps_setup_scalar_validation_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installer helpers reject unsafe domains/files/topologies and weak secrets."""
    assert (
        setup_mod._normalize_apps_base_domain("*.Apps.Example.Test")
        == "apps.example.test"
    )
    with pytest.raises(typer.BadParameter):
        setup_mod._normalize_apps_base_domain("https://apps.example.test")
    with pytest.raises(typer.BadParameter):
        setup_mod._resolve_readable_file(
            str(tmp_path / "missing"), option_name="--cert"
        )
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ORCHEO_APP_BUNDLE_BACKEND=s3\nORCHEO_DEPLOYMENT_MODE=local\n",
        encoding="utf-8",
    )
    with pytest.raises(typer.BadParameter, match="filesystem"):
        setup_mod._validate_supported_hosted_apps_storage(
            enabled=True, env_file=env_file, env_exists=True
        )
    env_file.write_text(
        "ORCHEO_APP_BUNDLE_BACKEND=filesystem\nORCHEO_DEPLOYMENT_MODE=hosted\n",
        encoding="utf-8",
    )
    with pytest.raises(typer.BadParameter, match="local"):
        setup_mod._validate_supported_hosted_apps_storage(
            enabled=True, env_file=env_file, env_exists=True
        )
    monkeypatch.setattr(setup_mod.typer, "prompt", lambda *_a, **_k: "short")
    with pytest.raises(typer.BadParameter, match="32"):
        setup_mod._resolve_app_gateway_secret(
            manual_secrets=True, yes=False, env_file=env_file, env_exists=False
        )
    assert (
        setup_mod._stack_version_candidate({"name": "stack-vbad"}, prerelease=False)
        is None
    )


def test_hosted_apps_setup_preflight_and_tls_error_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installer preflight reports validation failures without starting Compose."""
    config = setup_mod.SetupConfig(
        mode="install",
        backend_url="https://orcheo.example.test",
        studio_url="https://orcheo.example.test",
        auth_mode="api-key",
        api_key=None,
        chatkit_domain_key=None,
        public_ingress_enabled=True,
        public_host="orcheo.example.test",
        publish_local_ports=False,
        backend_upstreams="backend:2025",
        studio_upstream="studio:2026",
        start_stack=False,
        install_docker_if_missing=False,
        hosted_apps_enabled=True,
        apps_base_domain="apps.example.test",
        app_tls_method="provided",
        app_tls_cert_file=str(tmp_path / "missing-cert"),
        app_tls_key_file=str(tmp_path / "missing-key"),
    )
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        setup_mod,
        "validate_hosted_apps_setup",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad setup")),
    )
    with pytest.raises(typer.BadParameter, match="preflight"):
        setup_mod._run_hosted_apps_preflight(
            config, env_file=env_file, console=Console()
        )


def test_hosted_apps_setup_remaining_edge_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover installer certificate, staging, and public Hosted Apps summary paths."""
    env_file = tmp_path / ".env"
    env_file.write_text("ORCHEO_APP_TLS_METHOD=dns-01\n", encoding="utf-8")
    with pytest.raises(typer.BadParameter, match="DNS-01"):
        setup_mod._resolve_app_tls_config(
            enabled=True,
            app_tls_cert_file=None,
            app_tls_key_file=None,
            external_backend=True,
            yes=True,
            env_file=env_file,
            env_exists=True,
        )
    monkeypatch.setenv("ORCHEO_STACK_ASSET_BASE_URL", "https://assets.test")
    monkeypatch.setattr(setup_mod, "_resolve_stack_version", lambda _value: None)
    with pytest.raises(typer.BadParameter, match="explicit"):
        setup_mod._sync_stack_assets_with_best_source(
            tmp_path / "stack", stack_version=None, console=Console(), prerelease=True
        )

    config = setup_mod.SetupConfig(
        mode="install",
        backend_url="https://orcheo.example.test",
        studio_url="https://orcheo.example.test",
        auth_mode="api-key",
        api_key=None,
        chatkit_domain_key=None,
        public_ingress_enabled=True,
        public_host="orcheo.example.test",
        publish_local_ports=False,
        backend_upstreams="backend:2025",
        studio_upstream="studio:2026",
        start_stack=True,
        install_docker_if_missing=False,
        hosted_apps_enabled=True,
        apps_base_domain="apps.example.test",
        app_tls_method="provided",
    )
    stack_dir = tmp_path / "stack-tls"
    tls_dir = stack_dir / "app-tls"
    tls_dir.mkdir(parents=True)
    cert = tls_dir / "cert.pem"
    key = tls_dir / "key.pem"
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    config.app_tls_cert_file = str(cert)
    config.app_tls_key_file = str(key)
    setup_mod._configure_hosted_apps_tls(config, stack_dir=stack_dir)
    assert (tls_dir / "Caddyfile").read_text(encoding="utf-8").startswith("tls ")
    config.app_tls_cert_file = str(tmp_path / "missing-cert")
    config.app_tls_key_file = str(tmp_path / "missing-key")
    with pytest.raises(typer.BadParameter, match="Unable to copy"):
        setup_mod._configure_hosted_apps_tls(config, stack_dir=tmp_path / "tls-error")

    monkeypatch.delenv("ORCHEO_STACK_ASSET_BASE_URL", raising=False)
    monkeypatch.setattr(
        setup_mod, "_resolve_stack_project_dir", lambda: tmp_path / "assets"
    )
    monkeypatch.setattr(setup_mod, "_resolve_stack_version", lambda value: value)
    with pytest.raises(typer.BadParameter, match="--staging requires"):
        setup_mod._ensure_stack_assets(
            config=config, console=Console(), stack_version="1.0", staging=True
        )
    summary_console = Console(file=io.StringIO(), record=True)
    setup_mod.print_summary(config, console=summary_console)
    assert "Hosted Apps DNS" in summary_console.file.getvalue()
