"""Tests for `orcheo install --dry-run` on the full stack."""

from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from typing import Any
import click
import pytest
from rich.console import Console
from typer.testing import CliRunner
from orcheo_sdk.cli import main as main_mod
from orcheo_sdk.cli import setup as setup_mod
from orcheo_sdk.cli.main import app
from orcheo_sdk.cli.setup import execute_setup, preview_setup
from tests.sdk.test_cli_setup_stack_assets import (
    _default_assets,
    _patch_common,
    _setup_config,
)


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def test_preview_fresh_install_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack_dir = tmp_path / "stack"
    commands: list[list[str]] = []
    _patch_common(monkeypatch, stack_dir=stack_dir, commands=commands)
    console = Console(record=True, width=1000)
    config = _setup_config()

    preview_setup(config, console=console)

    assert not stack_dir.exists()
    assert commands == []
    assert config.stack_env_file == str(stack_dir / ".env")
    output = console.export_text()
    assert "Would download stack asset: docker-compose.yml" in output
    assert "Would download stack asset: chatkit_widgets/Todo list.widget" in output
    assert f"Would create {stack_dir / '.env'} from .env.example" in output
    assert "  ORCHEO_API_URL=http://localhost:2025" in output
    assert "  ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN=****ated" in output
    assert "ORCHEO_POSTGRES_PASSWORD: ****e-me -> ****" in output
    assert f"Would write {stack_dir / 'app-tls' / 'Caddyfile'}" in output
    assert "Hosted Apps disabled; skipping app-hosting preflight." in output
    assert "Docker CLI and daemon are available." in output
    assert (
        f"Would run: docker compose -f {stack_dir / 'docker-compose.yml'} "
        f"--project-directory {stack_dir} pull"
    ) in output
    assert "up -d" in output
    assert "http://localhost:2025/api/system/health" in output


def test_preview_existing_install_reports_diff_without_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack_dir = tmp_path / "stack"
    _patch_common(monkeypatch, stack_dir=stack_dir)
    execute_setup(_setup_config(), console=Console(record=True))
    (stack_dir / "Caddyfile").write_text("stale\n")
    before = _snapshot(stack_dir)
    console = Console(record=True, width=1000)

    preview_setup(
        replace(
            _setup_config(),
            mode="upgrade",
            api_key=None,
            chatkit_domain_key="domain_pk_newvalue",
            studio_url="http://studio.localhost:2026",
            start_stack=False,
        ),
        console=console,
    )

    assert _snapshot(stack_dir) == before
    output = console.export_text()
    assert "Would update stack asset: Caddyfile" in output
    assert "Would download" not in output
    assert f"Would change these values in {stack_dir / '.env'}:" in output
    assert (
        "  ORCHEO_STUDIO_URL: http://localhost:2026 -> http://studio.localhost:2026"
    ) in output
    assert "  VITE_ORCHEO_CHATKIT_DOMAIN_KEY: ****e_me -> ****alue" in output
    assert "Would skip starting the stack (--skip-stack)." in output


def test_preview_unchanged_install_keeps_existing_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack_dir = tmp_path / "stack"
    _patch_common(monkeypatch, stack_dir=stack_dir)
    execute_setup(_setup_config(), console=Console(record=True))
    console = Console(record=True, width=1000)

    preview_setup(replace(_setup_config(), api_key=None), console=console)

    output = console.export_text()
    assert f"Would keep existing values in {stack_dir / '.env'}" in output
    assert "Would update" not in output


def test_preview_env_value_masks_only_non_empty_secrets() -> None:
    assert setup_mod._preview_env_value("ORCHEO_SMTP_PASSWORD", "") == ""
    assert setup_mod._preview_env_value("ORCHEO_SMTP_PASSWORD", "hunter22") == (
        "****er22"
    )
    assert setup_mod._preview_env_value("ORCHEO_APP_TLS_KEY_FILE", "/k.pem") == (
        "/k.pem"
    )


def test_report_hosted_apps_tls_plan_for_provided_certificates(
    tmp_path: Path,
) -> None:
    console = Console(record=True, width=1000)
    config = replace(
        _setup_config(),
        hosted_apps_enabled=True,
        public_ingress_enabled=True,
        app_tls_method="provided",
        app_tls_cert_file="/certs/cert.pem",
        app_tls_key_file="/certs/key.pem",
    )

    setup_mod._report_hosted_apps_tls_plan(config, stack_dir=tmp_path, console=console)

    output = console.export_text()
    assert "Would copy the Hosted Apps TLS certificate (/certs/cert.pem)" in output
    assert f"into {tmp_path / 'app-tls'}" in output


@pytest.mark.parametrize(
    ("docker_command", "docker_access", "install_docker", "expected"),
    [
        (None, False, True, "Docker CLI not found; would attempt an automatic install"),
        (None, False, False, "setup would stop (--skip-docker-install)"),
        (["docker"], False, True, "cannot reach the Docker daemon"),
    ],
)
def test_report_stack_start_plan_docker_states(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    docker_command: list[str] | None,
    docker_access: bool,
    install_docker: bool,
    expected: str,
) -> None:
    monkeypatch.setattr(setup_mod, "_docker_command", lambda: docker_command)
    monkeypatch.setattr(
        setup_mod, "_current_shell_has_docker_access", lambda: docker_access
    )
    console = Console(record=True, width=1000)

    setup_mod._report_stack_start_plan(
        replace(_setup_config(), install_docker_if_missing=install_docker),
        stack_dir=tmp_path / "stack",
        preview_dir=tmp_path,
        console=console,
    )

    output = console.export_text()
    assert expected in output
    assert "Would run: docker compose" in output


def test_report_stack_start_plan_public_ingress_without_local_ports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(setup_mod, "_docker_command", lambda: ["docker"])
    monkeypatch.setattr(setup_mod, "_current_shell_has_docker_access", lambda: True)
    (tmp_path / ".env").write_text("COMPOSE_PROFILES=public-ingress\n")
    console = Console(record=True, width=1000)

    setup_mod._report_stack_start_plan(
        replace(
            _setup_config(),
            public_ingress_enabled=True,
            public_host="orcheo.example.com",
            publish_local_ports=False,
        ),
        stack_dir=tmp_path / "stack",
        preview_dir=tmp_path,
        console=console,
    )

    output = console.export_text()
    assert "docker compose --profile public-ingress -f" in output
    assert "Would wait for backend health" not in output


@pytest.mark.parametrize("install_agent_skills", [True, False])
def test_run_install_flow_dry_run_previews_only(
    monkeypatch: pytest.MonkeyPatch, install_agent_skills: bool
) -> None:
    previews: list[dict[str, Any]] = []
    config = replace(_setup_config(), install_agent_skills=install_agent_skills)
    monkeypatch.setattr(main_mod, "run_setup", lambda **kwargs: config)
    monkeypatch.setattr(
        main_mod,
        "preview_setup",
        lambda config, **kwargs: previews.append(kwargs),
    )
    for name in ("execute_setup", "print_summary", "_install_agent_skills"):
        monkeypatch.setattr(
            main_mod,
            name,
            lambda *args, _name=name, **kwargs: pytest.fail(f"{_name} called"),
        )
    console = Console(record=True, width=1000)

    main_mod._run_install_flow(
        console=console,
        yes=True,
        mode=None,
        stack_version="0.2.0",
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
        manual_secrets=False,
        dry_run=True,
    )

    assert previews == [
        {"console": console, "stack_version": "0.2.0", "staging": False}
    ]
    output = console.export_text()
    assert ("Would install the Orcheo skill" in output) is install_agent_skills
    assert "Dry run complete; nothing was changed." in output


def test_install_upgrade_passes_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        main_mod, "_run_install_flow", lambda **kwargs: calls.append(kwargs)
    )

    result = CliRunner().invoke(app, ["install", "upgrade", "--dry-run", "--yes"])

    assert result.exit_code == 0, click.unstyle(result.output)
    assert calls[0]["dry_run"] is True
    assert calls[0]["forced_mode"] == "upgrade"


def test_default_assets_include_env_template() -> None:
    assert ".env.example" in _default_assets()
