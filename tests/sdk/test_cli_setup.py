import io
import json
import os
import secrets
from pathlib import Path
import pytest
from rich.console import Console
from orcheo_sdk.cli import setup


class DummyProcess:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.stdout = b""
        self.stderr = b""


def make_console() -> Console:
    return Console(file=io.StringIO())


def test_normalized_machine(monkeypatch):
    monkeypatch.setattr(setup.platform, "machine", lambda: "AMD64")
    assert setup._normalized_machine() == "x86_64"
    monkeypatch.setattr(setup.platform, "machine", lambda: "aarch64")
    assert setup._normalized_machine() == "arm64"
    monkeypatch.setattr(setup.platform, "machine", lambda: "ppc64")
    assert setup._normalized_machine() == "ppc64"


def test_docker_cli_path_candidates(monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Darwin")
    candidate = setup._docker_cli_path_candidates()
    assert isinstance(candidate, list)
    monkeypatch.setattr(setup.platform, "system", lambda: "Windows")
    windows = setup._docker_cli_path_candidates()
    assert windows and windows[0].name.endswith("docker.exe")
    monkeypatch.setattr(setup.platform, "system", lambda: "Linux")
    assert setup._docker_cli_path_candidates() == []


def test_refresh_docker_cli_path_for_current_process(tmp_path):
    candidate = tmp_path / "bin" / "docker"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("")
    original_candidates = setup._docker_cli_path_candidates
    try:
        setup._docker_cli_path_candidates = lambda: [candidate]
        orig_path = os.environ.get("PATH", "")
        os.environ["PATH"] = ""
        setup._refresh_docker_cli_path_for_current_process()
        assert os.environ["PATH"].startswith(str(candidate.parent))
    finally:
        setup._docker_cli_path_candidates = original_candidates
        os.environ["PATH"] = orig_path


def test_docker_command(monkeypatch):
    monkeypatch.setattr(
        setup, "_refresh_docker_cli_path_for_current_process", lambda: None
    )
    monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/docker")
    assert setup._docker_command() == ["/usr/bin/docker"]


def test_read_os_release(monkeypatch):
    sample = 'NAME=Test\nID=ubuntu\nBAD line\nQUOTED="value"\n'
    monkeypatch.setattr(
        setup.Path, "exists", lambda self: str(self) == "/etc/os-release"
    )
    monkeypatch.setattr(setup.Path, "read_text", lambda self, encoding: sample)
    result = setup._read_os_release()
    assert result["NAME"] == "Test"
    assert result["QUOTED"] == "value"


def test_supported_docker_autoinstall_linux(monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Linux")
    monkeypatch.setattr(setup, "_read_os_release", lambda: {"ID": "ubuntu"})
    assert setup._is_supported_docker_autoinstall_linux()
    monkeypatch.setattr(setup, "_read_os_release", lambda: {"ID_LIKE": "debian"})
    assert setup._is_supported_docker_autoinstall_linux()


def test_run_privileged_command(monkeypatch):
    called = []

    def dummy(cmd, console):
        called.append(cmd)

    monkeypatch.setattr(setup, "_run_command", dummy)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    setup._run_privileged_command(["echo"], console=make_console())
    assert called

    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(setup, "_has_binary", lambda name: False)
    with pytest.raises(setup.typer.BadParameter):
        setup._run_privileged_command(["echo"], console=make_console())


def test_current_username(monkeypatch):
    monkeypatch.setenv("SUDO_USER", "binuser")
    assert setup._current_username() == "binuser"
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr(setup.getpass, "getuser", lambda: "runner")
    assert setup._current_username() == "runner"


def test_current_shell_has_docker_access(monkeypatch):
    monkeypatch.setattr(setup, "_docker_command", lambda: ["docker"])
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda *args, **kwargs: DummyProcess(returncode=0),
    )
    assert setup._current_shell_has_docker_access()


def test_powershell_literal():
    assert setup._powershell_literal("O'Reilly") == "'O''Reilly'"


def test_run_windows_elevated_command(monkeypatch):
    captured = []

    def fake(command, console):
        captured.append(command)

    monkeypatch.setattr(setup, "_run_command", fake)
    setup._run_windows_elevated_command(["cmd", "arg"], console=make_console())
    assert captured


def test_read_docker_ready_timeout_seconds(monkeypatch):
    monkeypatch.setenv("ORCHEO_SETUP_DOCKER_READY_TIMEOUT_SECONDS", "5")
    assert setup._read_docker_ready_timeout_seconds() == 5
    monkeypatch.setenv("ORCHEO_SETUP_DOCKER_READY_TIMEOUT_SECONDS", "-1")
    assert (
        setup._read_docker_ready_timeout_seconds()
        == setup._DEFAULT_DOCKER_READY_TIMEOUT_SECONDS
    )
    monkeypatch.setenv("ORCHEO_SETUP_DOCKER_READY_TIMEOUT_SECONDS", "bad")
    assert (
        setup._read_docker_ready_timeout_seconds()
        == setup._DEFAULT_DOCKER_READY_TIMEOUT_SECONDS
    )


def test_wait_for_docker_access(monkeypatch):
    calls = [False, True]
    monkeypatch.setattr(setup, "_read_docker_ready_timeout_seconds", lambda: 1)
    monkeypatch.setattr(
        setup,
        "_current_shell_has_docker_access",
        lambda: calls.pop(0),
    )
    monkeypatch.setattr(setup.time, "sleep", lambda *args, **kwargs: None)
    assert setup._wait_for_docker_access(console=make_console())


def test_download_binary_asset(tmp_path, monkeypatch):
    class DummyResponse:
        def __init__(self):
            self._count = 0

        def read(self, size):
            if self._count == 0:
                self._count += 1
                return b"data"
            return b""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(setup, "urlopen", lambda url, timeout: DummyResponse())
    destination = tmp_path / "install"
    setup._download_binary_asset(
        "https://example.com", destination, console=make_console()
    )
    assert destination.read_bytes() == b"data"


def test_download_binary_asset_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        setup, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom"))
    )
    with pytest.raises(setup.typer.BadParameter):
        setup._download_binary_asset("url", tmp_path / "file", console=make_console())


def test_start_docker_desktop(monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Darwin")
    calls = []
    monkeypatch.setattr(
        setup, "_run_command", lambda command, console: calls.append(command)
    )
    setup._start_docker_desktop(console=make_console())
    assert calls


def test_start_docker_desktop_windows_missing(monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Windows")
    monkeypatch.delenv("ProgramFiles", raising=False)
    with pytest.raises(setup.typer.BadParameter):
        setup._start_docker_desktop(console=make_console())


def test_current_windows_wsl_ready(monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda *args, **kwargs: DummyProcess(returncode=0),
    )
    assert setup._current_windows_wsl_ready()


def test_ensure_windows_wsl(monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup, "_current_windows_wsl_ready", lambda: False)
    monkeypatch.setattr(
        setup,
        "_run_windows_elevated_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("bad")),
    )
    console = make_console()
    assert not setup._ensure_windows_wsl(console=console)


def test_resolve_macos_docker_volume_path(monkeypatch):
    class FakePath:
        def __init__(self, value="/Volumes"):
            self.value = value

        def glob(self, pattern):
            return [FakePath("/Volumes/Docker-1")]

        def __truediv__(self, other):
            return FakePath(f"{self.value}/{other}")

        def exists(self):
            return self.value.endswith("install")

        def stat(self):
            return type("Stat", (), {"st_mtime": 1})

        def __lt__(self, other):
            return False

        def __str__(self):
            return self.value

    monkeypatch.setattr(setup, "Path", FakePath)
    result = setup._resolve_macos_docker_volume_path()
    assert result is not None


def test_attempt_macos_docker_desktop_install(monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(setup, "_normalized_machine", lambda: "x86_64")
    monkeypatch.setattr(setup, "_current_username", lambda: "tester")
    monkeypatch.setattr(setup, "_download_binary_asset", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup, "_run_privileged_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        setup, "_resolve_macos_docker_volume_path", lambda: Path("/Volumes/Docker")
    )
    monkeypatch.setattr(setup, "_start_docker_desktop", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup, "_wait_for_docker_access", lambda **kwargs: True)
    assert setup._attempt_macos_docker_desktop_install(console=make_console())


def test_attempt_windows_docker_desktop_install(monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup, "_ensure_windows_wsl", lambda **kwargs: True)
    monkeypatch.setattr(setup, "_download_binary_asset", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        setup, "_run_windows_elevated_command", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(setup, "_start_docker_desktop", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup, "_wait_for_docker_access", lambda **kwargs: True)
    assert setup._attempt_windows_docker_desktop_install(console=make_console())


def test_attempt_linux_docker_autoinstall(monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Linux")
    monkeypatch.setattr(setup, "_is_supported_docker_autoinstall_linux", lambda: True)
    monkeypatch.setattr(setup, "_has_binary", lambda name: True)
    monkeypatch.setattr(setup, "_run_privileged_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup, "_current_username", lambda: "tester")
    assert setup._attempt_linux_docker_autoinstall(console=make_console())


def test_attempt_docker_autoinstall(monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        setup, "_attempt_macos_docker_desktop_install", lambda **kwargs: True
    )
    assert setup._attempt_docker_autoinstall(console=make_console())


def test_resolve_mode_and_backend(monkeypatch):
    assert setup._resolve_mode("install", yes=False) == "install"
    monkeypatch.setattr(setup.typer, "prompt", lambda *args, **kwargs: "upgrade")
    assert setup._resolve_mode(None, yes=False) == "upgrade"

    # --yes defaults to "upgrade" when an existing installation exists
    assert setup._resolve_mode(None, yes=True, env_exists=True) == "upgrade"
    # --yes defaults to "install" for fresh installs
    assert setup._resolve_mode(None, yes=True, env_exists=False) == "install"

    backend, preserved = setup._resolve_backend_url(
        "http://a", mode="install", yes=False
    )
    assert backend == "http://a" and not preserved
    monkeypatch.setattr(setup.typer, "prompt", lambda *args, **kwargs: "")
    backend, preserved = setup._resolve_backend_url(
        None, mode="upgrade", yes=False, env_exists=True
    )
    assert preserved, "upgrade with yes defaults should preserve"


def test_resolve_auth_and_bool(monkeypatch):
    monkeypatch.setattr(setup.typer, "prompt", lambda *args, **kwargs: "oauth")
    assert setup._resolve_auth_mode(None, yes=False) == "oauth"
    monkeypatch.setattr(setup.typer, "confirm", lambda *args, **kwargs: True)
    assert setup._resolve_bool(None, yes_default=False, prompt="ok", default=False)


def test_resolve_api_key(monkeypatch):
    assert setup._resolve_api_key("oauth", None, mode="install", manual=False) is None
    monkeypatch.setattr(setup.typer, "prompt", lambda *args, **kwargs: "secret")
    assert (
        setup._resolve_api_key("api-key", None, mode="install", manual=True) == "secret"
    )
    monkeypatch.setattr(secrets, "token_urlsafe", lambda _: "token")
    assert (
        setup._resolve_api_key("api-key", None, mode="install", manual=False) == "token"
    )


def test_normalize_values():
    assert setup._normalize_optional_value("  ok ") == "ok"
    assert setup._normalize_optional_value("  ") is None
    assert setup._normalize_dotenv_value("'value'") == "value"


def test_resolve_chatkit_and_paths(monkeypatch):
    monkeypatch.setattr(setup.typer, "prompt", lambda *args, **kwargs: "key")
    assert setup._resolve_chatkit_domain_key(None, yes=False) == "key"
    monkeypatch.setenv("ORCHEO_STACK_DIR", "/tmp/test-stack")
    assert setup._resolve_stack_project_dir() == Path("/tmp/test-stack")
    assert setup._resolve_stack_env_file() == Path("/tmp/test-stack") / ".env"


def test_stack_asset_urls(monkeypatch):
    monkeypatch.setenv("ORCHEO_STACK_ASSET_BASE_URL", "https://custom")
    assert setup._resolve_stack_asset_base_url() == "https://custom"
    monkeypatch.delenv("ORCHEO_STACK_ASSET_BASE_URL", raising=False)
    assert setup._resolve_stack_asset_base_url(stack_version="1.0")
    assert setup._is_prerelease_stack_version("1.0-beta")
    assert setup._normalize_stack_version("stack-v1.0") == "1.0"
    monkeypatch.setenv("ORCHEO_STACK_VERSION", "stack-v2.0")
    assert setup._resolve_stack_version(None) == "2.0"


def test_discover_latest_stack_version(monkeypatch):
    payload = json.dumps([{"name": "stack-v1.1"}]).encode()

    class DummyResp:
        def __init__(self, status=200):
            self.status = status

        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(setup, "urlopen", lambda *args, **kwargs: DummyResp())
    assert setup._discover_latest_stack_version(make_console()) == "1.1"
    monkeypatch.setattr(
        setup, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError())
    )
    assert setup._discover_latest_stack_version(make_console()) is None


def test_download_and_sync_stack_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "_download_stack_asset", lambda *args, **kwargs: b"data")
    dest = tmp_path / "dir"
    setup._sync_stack_asset("file", dest, stack_version="1.0", console=make_console())
    assert (dest / "file").read_bytes() == b"data"
    calls = []
    monkeypatch.setattr(
        setup, "_sync_stack_asset", lambda *args, **kwargs: calls.append(True)
    )
    setup._sync_stack_assets_per_file(
        tmp_path, stack_version=None, console=make_console()
    )
    assert calls


def test_sync_stack_assets_with_best_source(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCHEO_STACK_ASSET_BASE_URL", raising=False)
    monkeypatch.setattr(setup, "_resolve_stack_version", lambda explicit: explicit)
    monkeypatch.setattr(setup, "_discover_latest_stack_version", lambda console: "1.3")
    calls = []
    monkeypatch.setattr(
        setup, "_sync_stack_assets_per_file", lambda *args, **kwargs: calls.append(True)
    )
    result = setup._sync_stack_assets_with_best_source(
        tmp_path, stack_version=None, console=make_console()
    )
    assert result == "1.3"
    assert calls


def test_build_env_updates(monkeypatch):
    monkeypatch.setattr(secrets, "token_urlsafe", lambda _: "safe")
    monkeypatch.setattr(secrets, "token_hex", lambda _: "hex")
    config = setup.SetupConfig(
        mode="install",
        backend_url="http://backend",
        auth_mode="api-key",
        api_key="provided",
        chatkit_domain_key="domain",
        public_ingress_enabled=False,
        public_host=None,
        publish_local_ports=True,
        backend_upstreams="backend:8000",
        canvas_upstream="canvas:5173",
        start_stack=False,
        install_docker_if_missing=False,
    )
    updates, defaults = setup._build_env_updates(config, requested_stack_version="2.0")
    assert updates["ORCHEO_API_URL"] == "http://backend"
    assert updates["ORCHEO_CHATKIT_PUBLIC_BASE_URL"] == "http://localhost:5173"
    assert updates["ORCHEO_CORS_ALLOW_ORIGINS"] == (
        "http://localhost:5173,http://127.0.0.1:5173"
    )
    assert updates["COMPOSE_PROFILES"] == ""
    assert updates["VITE_ORCHEO_CHATKIT_DOMAIN_KEY"] == "domain"
    assert updates["ORCHEO_STACK_IMAGE"] == f"{setup._STACK_IMAGE_REPOSITORY}:2.0"
    assert defaults["ORCHEO_POSTGRES_PASSWORD"] == "safe"


def test_build_env_updates_hides_debug_ports_in_local_only_mode(monkeypatch):
    monkeypatch.setattr(secrets, "token_urlsafe", lambda _: "safe")
    monkeypatch.setattr(secrets, "token_hex", lambda _: "hex")
    config = setup.SetupConfig(
        mode="install",
        backend_url="http://backend",
        auth_mode="api-key",
        api_key="provided",
        chatkit_domain_key="domain",
        public_ingress_enabled=False,
        public_host=None,
        publish_local_ports=False,
        backend_upstreams="backend:8000",
        canvas_upstream="canvas:5173",
        start_stack=False,
        install_docker_if_missing=False,
    )

    updates, _ = setup._build_env_updates(config)
    assert updates["COMPOSE_PROFILES"] == ""


def test_setup_resolution_helpers_cover_env_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ORCHEO_PUBLIC_INGRESS_ENABLED=true",
                "ORCHEO_PUBLIC_HOST=Orcheo.Example.com",
                "ORCHEO_PUBLISH_LOCAL_PORTS=off",
                "ORCHEO_CADDY_BACKEND_UPSTREAMS=backend:9000",
                "ORCHEO_CADDY_CANVAS_UPSTREAM=canvas:6000",
            ]
        ),
        encoding="utf-8",
    )

    confirm_calls: list[tuple[str, bool]] = []

    def _confirm(prompt: str, default: bool) -> bool:
        confirm_calls.append((prompt, default))
        return default

    monkeypatch.setattr(setup.typer, "confirm", _confirm)
    assert (
        setup._resolve_public_ingress_enabled(
            None, yes=False, env_file=env_file, env_exists=True, mode="upgrade"
        )
        is True
    )
    assert confirm_calls == [("Enable bundled public HTTPS ingress with Caddy?", True)]
    assert (
        setup._resolve_public_host(
            None,
            public_ingress_enabled=True,
            yes=False,
            env_file=env_file,
            env_exists=True,
        )
        == "orcheo.example.com"
    )
    assert (
        setup._resolve_publish_local_ports(
            None,
            public_ingress_enabled=True,
            yes=False,
            env_file=env_file,
            env_exists=True,
        )
        is False
    )
    assert setup._resolve_stack_upstreams(env_file, env_exists=True) == (
        "backend:9000",
        "canvas:6000",
    )
    assert setup._parse_bool_value(" yes ") is True
    assert setup._parse_bool_value("off") is False
    assert setup._parse_bool_value("maybe") is None
    assert setup._parse_bool_value(None) is None

    monkeypatch.setattr(
        setup.typer, "prompt", lambda *args, **kwargs: "Prompted.Example.com"
    )
    assert (
        setup._resolve_public_host(
            None,
            public_ingress_enabled=True,
            yes=False,
            env_file=tmp_path / "missing.env",
            env_exists=False,
        )
        == "prompted.example.com"
    )

    empty_host_env = tmp_path / "empty-host.env"
    empty_host_env.write_text("ORCHEO_PUBLIC_HOST=\n", encoding="utf-8")
    assert (
        setup._resolve_public_host(
            None,
            public_ingress_enabled=True,
            yes=False,
            env_file=empty_host_env,
            env_exists=True,
        )
        == "prompted.example.com"
    )

    monkeypatch.setattr(setup.typer, "confirm", lambda *args, **kwargs: False)
    assert (
        setup._resolve_publish_local_ports(
            None,
            public_ingress_enabled=True,
            yes=False,
            env_file=tmp_path / "missing.env",
            env_exists=False,
        )
        is False
    )
    assert (
        setup._resolve_publish_local_ports(
            None,
            public_ingress_enabled=True,
            yes=True,
            env_file=tmp_path / "missing.env",
            env_exists=False,
        )
        is True
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "Public hostname is required."),
        ("https://example.com", "hostname only"),
        ("example.com/path", "must not contain paths"),
        ("bad_host", "letters, numbers, dots, and hyphens"),
    ],
)
def test_normalize_public_host_validation(value: str, message: str) -> None:
    with pytest.raises(setup.typer.BadParameter, match=message):
        setup._normalize_public_host(value)


def test_compose_profile_args_missing_env_file(tmp_path: Path) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    assert setup._compose_profile_args(stack_dir) == []


def test_compose_profile_args_no_profiles_key(tmp_path: Path) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    (stack_dir / ".env").write_text("OTHER=value\n", encoding="utf-8")
    assert setup._compose_profile_args(stack_dir) == []


def test_compose_profile_args_with_profiles(tmp_path: Path) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    (stack_dir / ".env").write_text(
        "COMPOSE_PROFILES=public-ingress,local-access\n", encoding="utf-8"
    )
    assert setup._compose_profile_args(stack_dir) == [
        "--profile",
        "public-ingress",
        "--profile",
        "local-access",
    ]


def test_compose_profile_args_blank_entries_ignored(tmp_path: Path) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    (stack_dir / ".env").write_text(
        "COMPOSE_PROFILES=public-ingress, ,local-access\n", encoding="utf-8"
    )
    assert setup._compose_profile_args(stack_dir) == [
        "--profile",
        "public-ingress",
        "--profile",
        "local-access",
    ]


def test_read_env_value_and_warn(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ORCHEO_API_URL=http://\nVITE_ORCHEO_CHATKIT_DOMAIN_KEY=\n")
    assert setup._read_env_value(env_file, "ORCHEO_API_URL") == "http://"
    console = make_console()
    setup._warn_chatkit_domain_key_missing(env_file=env_file, console=console)
    assert "ChatKit domain key" in console.file.getvalue()


def test_upsert_env_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ORCHEO_API_URL=http://old\nOTHER=value\n")
    console = make_console()
    setup._upsert_env_values(
        env_file,
        {"ORCHEO_API_URL": "http://new"},
        defaults={"NEW_KEY": "value"},
        console=console,
    )
    result = env_file.read_text()
    assert "http://new" in result
    assert "NEW_KEY=value" in result
    assert "Updated stack env file" in console.file.getvalue()


def test_ensure_stack_env_file_creates_env_and_generates_defaults(tmp_path):
    env_template = tmp_path / ".env.example"
    env_template.write_text(
        "ORCHEO_POSTGRES_PASSWORD=change-me\n"
        "ORCHEO_VAULT_ENCRYPTION_KEY=replace-with-64-hex-chars\n"
        "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY=strong-random-secret\n"
        "VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_replace_me\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"

    setup.ensure_stack_env_file(
        env_file=env_file,
        env_template=env_template,
        console=make_console(),
        generated_defaults={
            "ORCHEO_POSTGRES_PASSWORD": "generated-password",
            "ORCHEO_VAULT_ENCRYPTION_KEY": "generated-vault-key",
            "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY": "generated-signing-key",
        },
    )

    result = env_file.read_text(encoding="utf-8")
    assert "ORCHEO_POSTGRES_PASSWORD=generated-password" in result
    assert "ORCHEO_VAULT_ENCRYPTION_KEY=generated-vault-key" in result
    assert "ORCHEO_CHATKIT_TOKEN_SIGNING_KEY=generated-signing-key" in result
    assert "VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_replace_me" in result


def test_ensure_stack_env_file_preserves_existing_values_and_backfills_missing(
    tmp_path,
):
    env_template = tmp_path / ".env.example"
    env_template.write_text(
        "ORCHEO_POSTGRES_PASSWORD=change-me\n"
        "ORCHEO_VAULT_ENCRYPTION_KEY=replace-with-64-hex-chars\n"
        "VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_replace_me\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ORCHEO_POSTGRES_PASSWORD=existing-password\n", encoding="utf-8"
    )

    setup.ensure_stack_env_file(
        env_file=env_file,
        env_template=env_template,
        console=make_console(),
        generated_defaults={
            "ORCHEO_POSTGRES_PASSWORD": "generated-password",
            "ORCHEO_VAULT_ENCRYPTION_KEY": "generated-vault-key",
        },
    )

    result = env_file.read_text(encoding="utf-8")
    assert "ORCHEO_POSTGRES_PASSWORD=existing-password" in result
    assert "ORCHEO_VAULT_ENCRYPTION_KEY=generated-vault-key" in result
    assert "VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_replace_me" in result


def test_ensure_stack_assets_fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(tmp_path))

    def stub_sync(stack_dir, stack_version, console):
        template = stack_dir / ".env.example"
        template.write_text("ORCHEO_API_URL=http://template\n")
        return "1.0"

    monkeypatch.setattr(setup, "_sync_stack_assets_with_best_source", stub_sync)
    calls = []
    monkeypatch.setattr(
        setup, "_upsert_env_values", lambda *args, **kwargs: calls.append(True)
    )
    monkeypatch.setattr(
        setup,
        "_build_env_updates",
        lambda config, requested_stack_version=None: (
            {"ORCHEO_API_URL": config.backend_url},
            {"DEFAULT_KEY": "value"},
        ),
    )
    config = setup.SetupConfig(
        mode="install",
        backend_url="http://backend",
        auth_mode="api-key",
        api_key=None,
        chatkit_domain_key=None,
        public_ingress_enabled=False,
        public_host=None,
        publish_local_ports=True,
        backend_upstreams="backend:8000",
        canvas_upstream="canvas:5173",
        start_stack=False,
        install_docker_if_missing=False,
    )
    stack_dir, env_file = setup._ensure_stack_assets(
        config=config, console=make_console()
    )
    assert stack_dir.exists()
    assert env_file.exists()
    assert calls


def test_ensure_stack_assets_existing_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        "ORCHEO_API_URL=http://existing\nVITE_ORCHEO_BACKEND_URL=http://existing"
    )

    def stub_sync(stack_dir, stack_version, console):
        del stack_version, console
        (stack_dir / ".env.example").write_text(
            "VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_replace_me\n", encoding="utf-8"
        )
        return "1.0"

    monkeypatch.setattr(setup, "_sync_stack_assets_with_best_source", stub_sync)
    updates = []

    def upsert(env_file, updates_dict, **kwargs):
        updates.append((env_file, dict(updates_dict)))

    monkeypatch.setattr(setup, "_upsert_env_values", upsert)

    def read_env(env_file, key):
        if key == "ORCHEO_API_URL":
            return "http://existing"
        if key == "VITE_ORCHEO_BACKEND_URL":
            return "http://existing"
        if key == "VITE_ORCHEO_CHATKIT_DOMAIN_KEY":
            return None
        return None

    monkeypatch.setattr(setup, "_read_env_value", read_env)
    config = setup.SetupConfig(
        mode="install",
        backend_url="http://backend",
        auth_mode="api-key",
        api_key=None,
        chatkit_domain_key=None,
        public_ingress_enabled=False,
        public_host=None,
        publish_local_ports=True,
        backend_upstreams="backend:8000",
        canvas_upstream="canvas:5173",
        start_stack=False,
        install_docker_if_missing=False,
        preserve_existing_backend_url=True,
    )
    setup._build_env_updates(config)
    setup._ensure_stack_assets(config=config, console=make_console())
    assert updates


def test_run_setup_generates_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets, "token_urlsafe", lambda _: "tokenized")
    monkeypatch.setattr(setup, "_resolve_stack_env_file", lambda: tmp_path / ".env")
    monkeypatch.setattr(setup.typer, "confirm", lambda _prompt, default: default)
    console = make_console()
    config = setup.run_setup(
        mode="install",
        backend_url="http://backend",
        auth_mode="api-key",
        api_key=None,
        chatkit_domain_key="domain",
        public_ingress=None,
        public_host=None,
        publish_local_ports=None,
        start_stack=False,
        install_docker=False,
        yes=False,
        manual_secrets=False,
        console=console,
    )
    assert config.api_key == "tokenized"
    assert "Generated API key" in console.file.getvalue()


def test_read_health_poll_timeout_seconds(monkeypatch):
    monkeypatch.setenv("ORCHEO_SETUP_HEALTH_POLL_TIMEOUT_SECONDS", "3")
    assert setup._read_health_poll_timeout_seconds() == 3


def test_poll_backend_health(monkeypatch):
    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class Monotonic:
        def __init__(self):
            self.value = 0

        def __call__(self):
            self.value += 1
            return self.value

    monkeypatch.setattr(setup.time, "monotonic", Monotonic())
    monkeypatch.setattr(setup, "urlopen", lambda *args, **kwargs: Resp())
    monkeypatch.setattr(setup.time, "sleep", lambda *args, **kwargs: None)
    assert setup._poll_backend_health("http://api", console=make_console())


def test_execute_setup_without_start(monkeypatch, tmp_path):
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    env_path = stack_dir / ".env"
    env_path.write_text("key=value")
    config = setup.SetupConfig(
        mode="install",
        backend_url="http://backend",
        auth_mode="api-key",
        api_key="token",
        chatkit_domain_key=None,
        public_ingress_enabled=False,
        public_host=None,
        publish_local_ports=True,
        backend_upstreams="backend:8000",
        canvas_upstream="canvas:5173",
        start_stack=False,
        install_docker_if_missing=False,
    )

    def fake_ensure(*args, **kwargs):
        return stack_dir, env_path

    called = []
    monkeypatch.setattr(setup, "_ensure_stack_assets", fake_ensure)
    monkeypatch.setattr(
        setup,
        "_warn_chatkit_domain_key_missing",
        lambda *args, **kwargs: called.append("warn"),
    )
    setup.execute_setup(config, console=make_console())
    assert "warn" in called


def test_execute_setup_with_start(monkeypatch, tmp_path):
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    (stack_dir / "docker-compose.yml").write_text("version: '3'")
    env_path = stack_dir / ".env"
    env_path.write_text("key=value")
    config = setup.SetupConfig(
        mode="install",
        backend_url="http://backend",
        auth_mode="api-key",
        api_key="token",
        chatkit_domain_key=None,
        public_ingress_enabled=False,
        public_host=None,
        publish_local_ports=True,
        backend_upstreams="backend:8000",
        canvas_upstream="canvas:5173",
        start_stack=True,
        install_docker_if_missing=False,
    )
    monkeypatch.setattr(
        setup, "_ensure_stack_assets", lambda **kwargs: (stack_dir, env_path)
    )
    monkeypatch.setattr(setup, "_has_binary", lambda name: True)
    monkeypatch.setattr(setup, "_current_shell_has_docker_access", lambda: True)
    monkeypatch.setattr(setup, "_docker_command", lambda: ["docker"])
    commands = []
    monkeypatch.setattr(
        setup, "_run_command", lambda command, console: commands.append(command)
    )
    monkeypatch.setattr(setup, "_poll_backend_health", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup, "_read_health_poll_timeout_seconds", lambda: 2)
    setup.execute_setup(config, console=make_console())
    assert len(commands) == 2


def test_execute_setup_missing_docker_command(monkeypatch, tmp_path):
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    env_path = stack_dir / ".env"
    env_path.write_text("key=value")
    config = setup.SetupConfig(
        mode="install",
        backend_url="http://backend",
        auth_mode="api-key",
        api_key="token",
        chatkit_domain_key=None,
        public_ingress_enabled=False,
        public_host=None,
        publish_local_ports=True,
        backend_upstreams="backend:8000",
        canvas_upstream="canvas:5173",
        start_stack=True,
        install_docker_if_missing=False,
    )
    monkeypatch.setattr(
        setup, "_ensure_stack_assets", lambda **kwargs: (stack_dir, env_path)
    )
    monkeypatch.setattr(setup, "_has_binary", lambda name: True)
    monkeypatch.setattr(setup, "_current_shell_has_docker_access", lambda: True)
    monkeypatch.setattr(setup, "_docker_command", lambda: None)
    with pytest.raises(setup.typer.BadParameter):
        setup.execute_setup(config, console=make_console())


def test_print_setup_resolution_notes_public_ingress_debug_disabled() -> None:
    console = make_console()

    setup._print_setup_resolution_notes(
        console=console,
        resolved_api_key=None,
        manual_secrets=True,
        yes=True,
        resolved_auth_mode="oauth",
        preserve_existing_backend_url=False,
        resolved_public_ingress_enabled=True,
        resolved_public_host="orcheo.example.com",
        resolved_publish_local_ports=False,
    )

    output = console.file.getvalue()
    assert "Bundled public ingress enabled for orcheo.example.com" in output
    assert "Local backend/canvas ports will stay disabled" in output


def test_print_summary():
    console = make_console()
    config = setup.SetupConfig(
        mode="install",
        backend_url="http://backend",
        auth_mode="api-key",
        api_key="token",
        chatkit_domain_key=None,
        public_ingress_enabled=False,
        public_host=None,
        publish_local_ports=True,
        backend_upstreams="backend:8000",
        canvas_upstream="canvas:5173",
        start_stack=True,
        install_docker_if_missing=False,
    )
    config.stack_project_dir = "/tmp/stack"
    config.stack_env_file = "/tmp/stack/.env"
    setup.print_summary(config, console=console)
    output = console.file.getvalue()
    assert "Setup complete" in output
    assert "Canvas may take" in output
    assert "localhost:5173" in output


def test_print_summary_public_ingress():
    console = make_console()
    config = setup.SetupConfig(
        mode="install",
        backend_url="https://orcheo.example.com",
        auth_mode="api-key",
        api_key="token",
        chatkit_domain_key=None,
        public_ingress_enabled=True,
        public_host="orcheo.example.com",
        publish_local_ports=False,
        backend_upstreams="backend:8000",
        canvas_upstream="canvas:5173",
        start_stack=True,
        install_docker_if_missing=False,
    )
    config.stack_project_dir = "/tmp/stack"
    config.stack_env_file = "/tmp/stack/.env"
    setup.print_summary(config, console=console)
    output = console.file.getvalue()
    assert "Setup complete" in output
    assert "https://orcheo.example.com" in output
    assert "localhost:5173" not in output


def test_resolve_public_ingress_enabled_upgrade_mode_unparseable_existing(
    tmp_path: Path,
) -> None:
    """Covers line 616->618: upgrade path with unparseable existing value falls through to yes."""  # noqa: E501
    env_file = tmp_path / ".env"
    env_file.write_text("ORCHEO_PUBLIC_INGRESS_ENABLED=\n", encoding="utf-8")
    result = setup._resolve_public_ingress_enabled(
        None, yes=True, env_file=env_file, env_exists=True, mode="upgrade"
    )
    assert result is False


def test_resolve_public_ingress_enabled_env_exists_sets_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interactive install uses the stored env value as the confirm default."""
    env_file = tmp_path / ".env"
    env_file.write_text("ORCHEO_PUBLIC_INGRESS_ENABLED=true\n", encoding="utf-8")

    confirm_calls: list[tuple[str, bool]] = []

    def _confirm(prompt: str, default: bool) -> bool:
        confirm_calls.append((prompt, default))
        return default

    monkeypatch.setattr(setup.typer, "confirm", _confirm)
    result = setup._resolve_public_ingress_enabled(
        None, yes=False, env_file=env_file, env_exists=True, mode="install"
    )
    assert result is True
    assert confirm_calls == [("Enable bundled public HTTPS ingress with Caddy?", True)]


def test_resolve_public_ingress_enabled_upgrade_mode_prompts_with_env_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrade should still prompt, using the stored env value as the default."""
    env_file = tmp_path / ".env"
    env_file.write_text("ORCHEO_PUBLIC_INGRESS_ENABLED=true\n", encoding="utf-8")

    confirm_calls: list[tuple[str, bool]] = []

    def _confirm(prompt: str, default: bool) -> bool:
        confirm_calls.append((prompt, default))
        return default

    monkeypatch.setattr(setup.typer, "confirm", _confirm)
    result = setup._resolve_public_ingress_enabled(
        None, yes=False, env_file=env_file, env_exists=True, mode="upgrade"
    )
    assert result is True
    assert confirm_calls == [("Enable bundled public HTTPS ingress with Caddy?", True)]


def test_resolve_public_ingress_enabled_env_exists_unparseable_uses_false_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Covers line 625->627: env exists but value is unparseable, confirm defaults to False."""  # noqa: E501
    env_file = tmp_path / ".env"
    env_file.write_text("ORCHEO_PUBLIC_INGRESS_ENABLED=\n", encoding="utf-8")
    monkeypatch.setattr(setup.typer, "confirm", lambda *args, **kwargs: False)
    result = setup._resolve_public_ingress_enabled(
        None, yes=False, env_file=env_file, env_exists=True, mode="install"
    )
    assert result is False


def test_resolve_public_ingress_enabled_yes_uses_env_default(
    tmp_path: Path,
) -> None:
    """--yes should reuse the stored default when one exists."""
    env_file = tmp_path / ".env"
    env_file.write_text("ORCHEO_PUBLIC_INGRESS_ENABLED=true\n", encoding="utf-8")
    result = setup._resolve_public_ingress_enabled(
        None, yes=True, env_file=env_file, env_exists=True, mode="upgrade"
    )
    assert result is True
