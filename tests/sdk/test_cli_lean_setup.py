"""Tests for `orcheo install --lean`."""

from __future__ import annotations
import json
from io import BytesIO
from pathlib import Path
from typing import Any
import click
from click.testing import CliRunner as ClickCliRunner
import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner
from orcheo_sdk.cli import lean_setup as lean_mod
from orcheo_sdk.cli import main as main_mod
from orcheo_sdk.cli import setup as setup_mod
from orcheo_sdk.cli.main import app
from orcheo_sdk.cli.setup import SmtpEmailConfig


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DSN = (
    "postgresql://postgres.abcd:p%24ss@aws-0-eu-west-1.pooler.supabase.com:5432/"
    "postgres"
)
_MAIN_BASE = "https://raw.githubusercontent.com/AI-Colleagues/orcheo/main/deploy/lean"


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
    base = f"https://raw.githubusercontent.com/AI-Colleagues/orcheo/{ref}/deploy/lean"
    return {
        f"{base}/docker-compose.yml": b"name: orcheo-lean\n",
        f"{base}/.env.example": (_REPO_ROOT / "deploy/lean/.env.example").read_bytes(),
    }


def _settings(**overrides: Any) -> lean_mod.LeanSettings:
    values: dict[str, Any] = {
        "public_url": "http://localhost:2025",
        "chatkit_domain_key": None,
        "smtp": SmtpEmailConfig(
            host=None,
            port=587,
            username=None,
            password=None,
            from_email=None,
            use_tls=True,
        ),
        "postgres_dsn": _DSN,
        "auth_jwt_secret": "jwt-secret-for-tests",
    }
    values.update(overrides)
    return lean_mod.LeanSettings(**values)


def _patch_docker(
    monkeypatch: pytest.MonkeyPatch,
    command: list[str] | None,
    *,
    daemon: bool = True,
) -> None:
    for module in (lean_mod, setup_mod):
        monkeypatch.setattr(module, "_docker_command", lambda: command)
    monkeypatch.setattr(setup_mod, "_current_shell_has_docker_access", lambda: daemon)


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
        "https://raw.githubusercontent.com/AI-Colleagues/orcheo/lean-v0.2.0/deploy/lean"
    )
    monkeypatch.setenv("ORCHEO_LEAN_ASSET_BASE_URL", "https://mirror.example/repo/")
    assert lean_mod._resolve_lean_asset_base_url("0.2.0") == (
        "https://mirror.example/repo"
    )
    assert lean_mod._lean_image(None) == "ghcr.io/ai-colleagues/orcheo-lean:latest"
    assert lean_mod._lean_image("0.2.0") == "ghcr.io/ai-colleagues/orcheo-lean:0.2.0"


def test_sync_lean_asset_reports_download_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_urlopen(monkeypatch, {})
    with pytest.raises(typer.BadParameter, match="Failed to download lean asset"):
        lean_mod._sync_lean_asset(
            "docker-compose.yml",
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
        lean_dir, lean_version=None, settings=_settings(), console=console
    )

    assert (lean_dir / "docker-compose.yml").read_text() == "name: orcheo-lean\n"
    assert sorted(path.name for path in lean_dir.iterdir()) == [
        ".env",
        ".env.example",
        "docker-compose.yml",
    ]
    first = env_file.read_text()
    for key in (
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

    lean_mod._configure_lean_env(
        lean_dir, lean_version="0.2.0", settings=_settings(), console=console
    )
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
        str(tmp_path / "docker-compose.yml"),
        "--project-directory",
        str(tmp_path),
    ]
    runner = "sudo" if privileged else "plain"
    assert commands == [
        (runner, [*base, "pull"]),
        (runner, [*base, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120"]),
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
            supabase_connection_string=_DSN,
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
        supabase_connection_string=_DSN,
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
        supabase_connection_string=_DSN,
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
        supabase_connection_string=_DSN,
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


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _fail_on_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "_prepare_docker_for_start",
        "_start_lean_stack",
        "_run_command",
        "_run_privileged_command",
    ):
        monkeypatch.setattr(
            lean_mod,
            name,
            lambda *args, _name=name, **kwargs: pytest.fail(f"{_name} called"),
        )


def _dry_run(console: Console, **overrides: Any) -> None:
    options: dict[str, Any] = {
        "lean_version": None,
        "staging": False,
        "start_stack": True,
        "install_docker": True,
        "yes": True,
        "supabase_connection_string": _DSN,
    }
    options.update(overrides)
    lean_mod.run_lean_install(console=console, dry_run=True, **options)


def test_dry_run_on_fresh_install_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, _lean_env: Path
) -> None:
    _install_fake_urlopen(monkeypatch, _lean_payloads())
    monkeypatch.setattr(
        lean_mod, "_discover_latest_stack_version", lambda console, **kwargs: None
    )
    _patch_docker(monkeypatch, None)
    _fail_on_side_effects(monkeypatch)
    console = Console(record=True, width=300)

    _dry_run(console)

    assert not _lean_env.exists()
    output = console.export_text()
    assert "Would download lean asset: docker-compose.yml" in output
    assert "Would download lean asset: .env.example" in output
    assert "widget" not in output
    assert f"Would create {_lean_env / '.env'} from .env.example" in output
    assert "  ORCHEO_VAULT_ENCRYPTION_KEY: ****hars -> ****" in output
    assert "  ORCHEO_LEAN_IMAGE=ghcr.io/ai-colleagues/orcheo-lean:latest" in output
    assert "Docker CLI not found; would attempt an automatic install." in output
    assert f"Would run: docker compose -f {_lean_env / 'docker-compose.yml'}" in (
        output
    )
    assert "up -d --no-build" in output
    assert "http://localhost:2025/api/system/health" in output
    assert "Dry run complete; nothing was changed." in output
    assert "Lean setup complete" not in output


def test_dry_run_on_existing_install_reports_changes_without_writing(
    monkeypatch: pytest.MonkeyPatch, _lean_env: Path
) -> None:
    _install_fake_urlopen(
        monkeypatch, {**_lean_payloads(), **_lean_payloads("lean-v0.2.0")}
    )
    console = Console(record=True, width=300)
    lean_mod._sync_lean_assets(_lean_env, lean_version=None, console=console)
    env_file = lean_mod._configure_lean_env(
        _lean_env, lean_version=None, settings=_settings(), console=console
    )
    (_lean_env / "docker-compose.yml").write_text("name: stale\n")
    env_file.write_text(
        "\n".join(
            line
            for line in env_file.read_text().splitlines()
            if not line.startswith(("ORCHEO_SMTP_HOST=", "ORCHEO_LEAN_PORT="))
        )
        + "\nORCHEO_LEAN_PORT=3030\n"
    )
    before = _snapshot(_lean_env)
    _patch_docker(monkeypatch, ["/usr/bin/docker"], daemon=False)
    _fail_on_side_effects(monkeypatch)
    console = Console(record=True, width=300)

    _dry_run(
        console,
        lean_version="0.2.0",
        smtp_host="smtp.example.com",
        smtp_password="hunter22",
    )

    assert _snapshot(_lean_env) == before
    output = console.export_text()
    assert "Would update lean asset: docker-compose.yml" in output
    assert "Would download" not in output
    assert f"Would change these values in {env_file}:" in output
    assert "  ORCHEO_SMTP_HOST=smtp.example.com" in output
    assert "  ORCHEO_SMTP_PASSWORD:  -> ****er22" in output
    assert (
        "  ORCHEO_LEAN_IMAGE: ghcr.io/ai-colleagues/orcheo-lean:latest -> "
        "ghcr.io/ai-colleagues/orcheo-lean:0.2.0"
    ) in output
    assert "cannot reach the Docker daemon" in output
    assert "Would run: /usr/bin/docker compose" in output
    assert "http://localhost:3030/api/system/health" in output


def test_dry_run_reports_unchanged_env_and_ready_docker(
    monkeypatch: pytest.MonkeyPatch, _lean_env: Path
) -> None:
    _install_fake_urlopen(monkeypatch, _lean_payloads())
    console = Console(record=True, width=300)
    lean_mod._sync_lean_assets(_lean_env, lean_version=None, console=console)
    lean_mod._configure_lean_env(
        _lean_env, lean_version=None, settings=_settings(), console=console
    )
    monkeypatch.setattr(
        lean_mod, "_discover_latest_stack_version", lambda console, **kwargs: None
    )
    _patch_docker(monkeypatch, ["docker"])
    _fail_on_side_effects(monkeypatch)
    console = Console(record=True, width=300)

    _dry_run(console)

    output = console.export_text()
    assert "Would keep existing values" in output
    assert "ORCHEO_LEAN_IMAGE" not in output
    assert "Would update" not in output
    assert "Docker CLI and daemon are available." in output


def test_dry_run_reports_skip_docker_install_and_skip_stack(
    monkeypatch: pytest.MonkeyPatch, _lean_env: Path
) -> None:
    _install_fake_urlopen(monkeypatch, _lean_payloads())
    monkeypatch.setattr(
        lean_mod, "_discover_latest_stack_version", lambda console, **kwargs: None
    )
    _patch_docker(monkeypatch, None)
    _fail_on_side_effects(monkeypatch)

    console = Console(record=True, width=300)
    _dry_run(console, install_docker=False)
    assert "setup would stop (--skip-docker-install)" in console.export_text()

    console = Console(record=True, width=300)
    _dry_run(console, start_stack=False)
    output = console.export_text()
    assert "Would skip starting the stack (--skip-stack)." in output
    assert "Would run:" not in output
    assert not _lean_env.exists()


def test_install_dry_run_passes_through_with_lean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        main_mod, "run_lean_install", lambda **kwargs: calls.append(kwargs)
    )

    result = CliRunner().invoke(app, ["install", "--lean", "--dry-run", "--yes"])

    assert result.exit_code == 0, result.output
    assert calls[0]["dry_run"] is True


def test_install_dry_run_without_lean_runs_stack_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        main_mod, "_run_install_flow", lambda **kwargs: calls.append(kwargs)
    )
    monkeypatch.setattr(
        main_mod, "run_lean_install", lambda **kwargs: pytest.fail("lean install")
    )

    result = CliRunner().invoke(app, ["install", "--dry-run", "--yes"])

    assert result.exit_code == 0, result.output
    assert calls[0]["dry_run"] is True


def test_lean_prompts_reuse_full_install_resolvers(
    monkeypatch: pytest.MonkeyPatch, _lean_env: Path
) -> None:
    _stub_install_steps(monkeypatch)
    monkeypatch.setattr(
        lean_mod,
        "_prepare_docker_for_start",
        lambda **kwargs: (True, False, False),
    )
    monkeypatch.setattr(
        main_mod, "_install_agent_skills", lambda **kwargs: pytest.fail("skills")
    )
    answers = [
        "https://orcheo.example.com",
        _DSN,
        "Example.com, @b.org",
        "domain_pk_live",
        "smtp.example.com",
        "mailer",
        "hunter22",
        "ops@example.com",
    ]

    result = CliRunner().invoke(
        app, ["install", "--lean"], input="\n".join(answers) + "\n"
    )

    assert result.exit_code == 0, result.output
    output = click.unstyle(result.output)
    prompts = [
        "Backend URL [http://localhost:2025]: ",
        "Supabase connection string: ",
        "Login email domains (comma-separated) - press Enter to allow any: ",
        "ChatKit domain key - press Enter to skip: ",
        "SMTP host: ",
        "SMTP username []: ",
        "SMTP password: ",
        "Transactional email sender address [no-reply@orcheo.cloud]: ",
    ]
    positions = [output.index(prompt) for prompt in prompts]
    assert positions == sorted(positions)
    for skipped in (
        "SMTP port",
        "STARTTLS",
        "Start stack",
        "Install Docker",
        "Install Orcheo skill",
        "public HTTPS ingress",
        "Hosted Apps",
    ):
        assert skipped not in output
    env_file = _lean_env / ".env"
    expected = {
        "ORCHEO_LEAN_PUBLIC_URL": "https://orcheo.example.com",
        "ORCHEO_POSTGRES_DSN": _DSN,
        "ORCHEO_AUTH_ALLOWED_EMAIL_DOMAINS": "example.com,b.org",
        "VITE_ORCHEO_CHATKIT_DOMAIN_KEY": "domain_pk_live",
        "ORCHEO_SMTP_HOST": "smtp.example.com",
        "ORCHEO_SMTP_PORT": "587",
        "ORCHEO_SMTP_USERNAME": "mailer",
        "ORCHEO_SMTP_PASSWORD": "hunter22",
        "ORCHEO_SMTP_FROM_EMAIL": "ops@example.com",
        "ORCHEO_SMTP_USE_TLS": "true",
        # An HTTPS URL requires sign-in, as in the full install.
        "ORCHEO_AUTH_MODE": "required",
        "VITE_ORCHEO_AUTH_DISABLED": "false",
        "ORCHEO_AUTH_ISSUER": "https://orcheo.example.com",
        "ORCHEO_AUTH_AUDIENCE": "orcheo-api",
    }
    for key, value in expected.items():
        assert lean_mod._read_env_value(env_file, key) == value
    assert lean_mod._read_env_value(env_file, "ORCHEO_AUTH_JWT_SECRET")
    assert "Auth issuer" not in output
    assert "SMTP is not configured" not in output


def test_lean_settings_default_to_existing_env_values(
    monkeypatch: pytest.MonkeyPatch, _lean_env: Path
) -> None:
    _lean_env.mkdir()
    env_file = _lean_env / ".env"
    env_file.write_text(
        "ORCHEO_LEAN_PUBLIC_URL=https://orcheo.example.com\n"
        "VITE_ORCHEO_CHATKIT_DOMAIN_KEY=domain_pk_live\n"
        "ORCHEO_SMTP_HOST=smtp.example.com\n"
        "ORCHEO_SMTP_PORT=465\n"
        "ORCHEO_SMTP_USE_TLS=false\n"
    )

    settings = lean_mod._resolve_lean_settings(
        supabase_connection_string=_DSN,
        login_email_domains=None,
        backend_url=None,
        chatkit_domain_key=None,
        smtp_host=None,
        smtp_port=None,
        smtp_username=None,
        smtp_password=None,
        smtp_from_email=None,
        smtp_use_tls=None,
        yes=True,
        env_file=env_file,
    )

    assert settings.public_url == "https://orcheo.example.com"
    assert settings.chatkit_domain_key == "domain_pk_live"
    assert settings.smtp.host == "smtp.example.com"
    assert settings.smtp.port == 465
    assert settings.smtp.use_tls is False


def test_lean_env_updates_skip_unset_settings() -> None:
    assert lean_mod._build_lean_env_updates(
        _settings(postgres_dsn="", auth_jwt_secret=None), lean_version=None
    ) == {
        "ORCHEO_LEAN_IMAGE": "ghcr.io/ai-colleagues/orcheo-lean:latest",
        "ORCHEO_LEAN_PUBLIC_URL": "http://localhost:2025",
        "ORCHEO_AUTH_ALLOWED_EMAIL_DOMAINS": "",
    }


def test_install_lean_accepts_prompted_setting_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        main_mod, "run_lean_install", lambda **kwargs: calls.append(kwargs)
    )

    result = CliRunner().invoke(
        app,
        [
            "install",
            "--lean",
            "--yes",
            "--backend-url",
            "https://orcheo.example.com",
            "--chatkit-domain-key",
            "domain_pk_live",
            "--smtp-host",
            "smtp.example.com",
            "--smtp-port",
            "465",
            "--no-smtp-use-tls",
        ],
    )

    assert result.exit_code == 0, result.output
    call = calls[0]
    assert call["backend_url"] == "https://orcheo.example.com"
    assert call["chatkit_domain_key"] == "domain_pk_live"
    assert call["smtp_host"] == "smtp.example.com"
    assert call["smtp_port"] == 465
    assert call["smtp_use_tls"] is False


def test_lean_http_url_keeps_sign_in_disabled(
    monkeypatch: pytest.MonkeyPatch, _lean_env: Path
) -> None:
    _stub_install_steps(monkeypatch)

    lean_mod.run_lean_install(
        supabase_connection_string=_DSN,
        lean_version=None,
        staging=False,
        start_stack=False,
        install_docker=None,
        yes=True,
        console=Console(record=True),
    )

    env_file = _lean_env / ".env"
    assert lean_mod._read_env_value(env_file, "ORCHEO_AUTH_MODE") == "disabled"
    assert lean_mod._read_env_value(env_file, "VITE_ORCHEO_AUTH_DISABLED") == "true"


def test_lean_https_url_without_smtp_warns_about_sign_in_links(
    monkeypatch: pytest.MonkeyPatch, _lean_env: Path
) -> None:
    _stub_install_steps(monkeypatch)
    console = Console(record=True, width=300)

    lean_mod.run_lean_install(
        supabase_connection_string=_DSN,
        lean_version=None,
        staging=False,
        start_stack=False,
        install_docker=None,
        yes=True,
        console=console,
        backend_url="https://orcheo.example.com",
    )

    env_file = _lean_env / ".env"
    assert lean_mod._read_env_value(env_file, "ORCHEO_AUTH_MODE") == "required"
    assert "sign-in links and codes are written to the backend logs" in (
        console.export_text()
    )


def test_lean_existing_required_auth_is_kept_for_http_url(
    _lean_env: Path,
) -> None:
    _lean_env.mkdir()
    env_file = _lean_env / ".env"
    env_file.write_text(
        "ORCHEO_AUTH_MODE=required\n"
        "ORCHEO_AUTH_JWT_SECRET=existing-secret\n"
        "ORCHEO_AUTH_ISSUER=https://issuer.example.com\n"
    )

    settings = lean_mod._resolve_lean_settings(
        supabase_connection_string=_DSN,
        login_email_domains=None,
        backend_url="http://localhost:2025",
        chatkit_domain_key=None,
        smtp_host=None,
        smtp_port=None,
        smtp_username=None,
        smtp_password=None,
        smtp_from_email=None,
        smtp_use_tls=None,
        yes=True,
        env_file=env_file,
    )

    assert settings.auth_required is True
    updates = lean_mod._build_lean_env_updates(settings, lean_version="0.2.0")
    assert updates["ORCHEO_AUTH_MODE"] == "required"
    assert updates["ORCHEO_AUTH_JWT_SECRET"] == "existing-secret"
    assert updates["ORCHEO_AUTH_ISSUER"] == "https://issuer.example.com"
    assert updates["ORCHEO_AUTH_AUDIENCE"] == "orcheo-api"
    assert updates["VITE_ORCHEO_AUTH_DISABLED"] == "false"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("mysql://host/db", "postgresql:// connection string"),
        ("postgresql:///db", "postgresql:// connection string"),
        ("postgresql://u:it's@host/db", "URL-encode single quotes"),
        ("postgresql://u:p@host/db\nORCHEO_AUTH_MODE=disabled", "single line"),
        ("postgresql://u:p w@host/db", "single line"),
        ("postgresql://u:p@host/db\x00", "single line"),
    ],
)
def test_validate_supabase_dsn_rejects_bad_values(value: str, message: str) -> None:
    with pytest.raises(click.UsageError, match=message):
        lean_mod._validate_supabase_dsn(value)
    assert lean_mod._validate_supabase_dsn(f"  {_DSN} ") == _DSN


def test_supabase_dsn_is_required_non_interactively(_lean_env: Path) -> None:
    with pytest.raises(typer.BadParameter, match="--supabase-connection-string"):
        lean_mod._resolve_supabase_dsn(
            None, yes=True, env_file=_lean_env / ".env", env_exists=False
        )
    with pytest.raises(typer.BadParameter, match="postgresql:// connection string"):
        lean_mod._resolve_supabase_dsn(
            "not-a-dsn", yes=True, env_file=_lean_env / ".env", env_exists=False
        )


def test_supabase_dsn_prompt_keeps_masked_existing_and_reprompts(
    _lean_env: Path,
) -> None:
    _lean_env.mkdir()
    env_file = _lean_env / ".env"
    env_file.write_text(f"ORCHEO_POSTGRES_DSN='{_DSN}'\n")

    def _prompt(user_input: str) -> tuple[str, str]:
        @click.command()
        def command() -> None:
            click.echo(
                lean_mod._resolve_supabase_dsn(
                    None, yes=False, env_file=env_file, env_exists=True
                )
            )

        result = ClickCliRunner().invoke(command, input=user_input)
        assert result.exit_code == 0, result.output
        return result.output.splitlines()[-1], result.output

    kept, output = _prompt("\n")
    assert kept == _DSN
    assert "[****gres]" in output
    replaced, output = _prompt("bogus\npostgresql://u:p@db.example.com/postgres\n")
    assert replaced == "postgresql://u:p@db.example.com/postgres"
    # Click hides validation details for hidden input so secrets are not echoed.
    assert "Error: The value you entered was invalid." in output


def test_login_email_domains_resolution(_lean_env: Path) -> None:
    _lean_env.mkdir()
    env_file = _lean_env / ".env"
    env_file.write_text("ORCHEO_AUTH_ALLOWED_EMAIL_DOMAINS=example.com\n")

    def resolve(value: str | None) -> str:
        return lean_mod._resolve_login_email_domains(
            value, yes=True, env_file=env_file, env_exists=True
        )

    assert resolve(None) == "example.com"
    assert resolve("*") == ""
    assert resolve("B.org, @c.io") == "b.org,c.io"
    with pytest.raises(typer.BadParameter, match="Invalid email domain"):
        resolve("not a domain")


def test_login_email_domains_prompt_reprompts_and_clears(_lean_env: Path) -> None:
    _lean_env.mkdir()
    env_file = _lean_env / ".env"
    env_file.write_text("ORCHEO_AUTH_ALLOWED_EMAIL_DOMAINS=example.com\n")

    @click.command()
    def command() -> None:
        click.echo(
            "result="
            + lean_mod._resolve_login_email_domains(
                None, yes=False, env_file=env_file, env_exists=True
            )
        )

    result = ClickCliRunner().invoke(command, input="bad domain\n*\n")

    assert result.exit_code == 0, result.output
    assert "Login email domains (comma-separated; * allows any) [example.com]" in (
        result.output
    )
    assert "Error: Invalid email domain" in result.output
    assert result.output.splitlines()[-1] == "result="


def test_lean_env_quotes_dsn_and_writes_domains(
    monkeypatch: pytest.MonkeyPatch, _lean_env: Path
) -> None:
    _install_fake_urlopen(monkeypatch, _lean_payloads())
    console = Console(record=True)
    lean_mod._sync_lean_assets(_lean_env, lean_version=None, console=console)

    env_file = lean_mod._configure_lean_env(
        _lean_env,
        lean_version=None,
        settings=_settings(login_email_domains="example.com,b.org"),
        console=console,
    )

    text = env_file.read_text()
    assert f"ORCHEO_POSTGRES_DSN='{_DSN}'" in text.splitlines()
    assert lean_mod._read_env_value(env_file, "ORCHEO_POSTGRES_DSN") == _DSN
    assert "ORCHEO_AUTH_ALLOWED_EMAIL_DOMAINS=example.com,b.org" in text.splitlines()
    assert "ORCHEO_POSTGRES_PASSWORD" not in text


def test_lean_summary_notes_domains_without_required_sign_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_install_steps(monkeypatch)
    console = Console(record=True, width=300)

    lean_mod.run_lean_install(
        supabase_connection_string=_DSN,
        login_email_domains="example.com",
        lean_version=None,
        staging=False,
        start_stack=False,
        install_docker=None,
        yes=True,
        console=console,
    )

    assert "Login email domains only apply once sign-in is required" in (
        console.export_text()
    )


def test_dry_run_masks_supabase_dsn(
    monkeypatch: pytest.MonkeyPatch, _lean_env: Path
) -> None:
    _install_fake_urlopen(monkeypatch, _lean_payloads())
    monkeypatch.setattr(
        lean_mod, "_discover_latest_stack_version", lambda console, **kwargs: None
    )
    _patch_docker(monkeypatch, ["docker"])
    _fail_on_side_effects(monkeypatch)
    console = Console(record=True, width=300)

    _dry_run(console, start_stack=False)

    output = console.export_text()
    assert "  ORCHEO_POSTGRES_DSN:  -> ****gres\n" in output
    assert "p%24ss" not in output


def test_install_rejects_lean_only_flags_without_lean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_mod, "_run_install_flow", lambda **kwargs: pytest.fail("should not run")
    )

    result = CliRunner().invoke(
        app,
        ["install", "--yes", "--supabase-connection-string", _DSN],
    )

    assert result.exit_code != 0
    assert "can only be used with --lean" in click.unstyle(result.output)


def test_install_lean_passes_supabase_and_domain_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        main_mod, "run_lean_install", lambda **kwargs: calls.append(kwargs)
    )

    result = CliRunner().invoke(
        app,
        [
            "install",
            "--lean",
            "--yes",
            "--supabase-connection-string",
            _DSN,
            "--login-email-domains",
            "example.com",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["supabase_connection_string"] == _DSN
    assert calls[0]["login_email_domains"] == "example.com"


@pytest.mark.parametrize("existing", ["", "kept-secret"])
def test_lean_jwt_secret_is_kept_or_generated_without_required_sign_in(
    _lean_env: Path, existing: str
) -> None:
    _lean_env.mkdir()
    env_file = _lean_env / ".env"
    env_file.write_text(
        f"ORCHEO_AUTH_MODE=disabled\nORCHEO_AUTH_JWT_SECRET={existing}\n"
    )

    settings = lean_mod._resolve_lean_settings(
        supabase_connection_string=_DSN,
        login_email_domains=None,
        backend_url=None,
        chatkit_domain_key=None,
        smtp_host=None,
        smtp_port=None,
        smtp_username=None,
        smtp_password=None,
        smtp_from_email=None,
        smtp_use_tls=None,
        yes=True,
        env_file=env_file,
    )

    assert settings.auth_required is False
    if existing:
        assert settings.auth_jwt_secret == existing
    else:
        assert settings.auth_jwt_secret
        assert len(settings.auth_jwt_secret) == 64
    updates = lean_mod._build_lean_env_updates(settings, lean_version=None)
    assert updates["ORCHEO_AUTH_JWT_SECRET"] == settings.auth_jwt_secret
    assert "ORCHEO_AUTH_MODE" not in updates
