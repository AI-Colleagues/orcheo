"""High-level CLI entrypoint tests."""

from __future__ import annotations
import io
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import click
import httpx
import pytest
import respx
import typer
from rich.console import Console
from typer.testing import CliRunner
from orcheo_sdk.cli import main as main_mod
from orcheo_sdk.cli.errors import APICallError, CLIError
from orcheo_sdk.cli.main import app, run, run_human
from orcheo_sdk.cli.setup import SetupConfig
from orcheo_sdk.cli.state import CLIState


def test_main_config_error_handling(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # With the default API URL, this should attempt to connect to localhost:8000
    # The test should mock the API call to verify it uses the default
    # Clear env vars to ensure the default localhost:8000 is used
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("ORCHEO_API_URL", raising=False)
    monkeypatch.delenv("ORCHEO_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("ORCHEO_CHATKIT_PUBLIC_BASE_URL", raising=False)
    payload = [{"id": "wf-1", "name": "Demo", "slug": "demo", "is_archived": False}]
    with respx.mock(assert_all_called=True) as router:
        router.get("http://localhost:8000/api/workflows").mock(
            return_value=httpx.Response(200, json=payload)
        )
        router.get(
            "http://localhost:8000/api/workflows/wf-1/triggers/cron/config"
        ).mock(return_value=httpx.Response(404))
        result = runner.invoke(
            app, ["workflow", "list"], env={"NO_COLOR": "1", "ORCHEO_HUMAN": "1"}
        )
    assert result.exit_code == 0
    assert "Demo" in result.stdout


def test_run_cli_error_handling(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def mock_app(*args: object, **kwargs: object) -> None:
        raise CLIError("Test error")

    monkeypatch.setattr(main_mod, "app", mock_app)
    monkeypatch.setattr(main_mod.sys, "argv", ["orcheo"])
    monkeypatch.setenv("ORCHEO_HUMAN", "1")

    with pytest.raises(SystemExit) as exc_info:
        run()
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "Error: Test error" in captured.out


def test_run_usage_error_handling(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that UsageError is caught and displayed with a friendly message."""
    import click

    def mock_app(*args: object, **kwargs: object) -> None:
        # Create a command with a proper name
        cmd = click.Command("workflow")
        # Create parent context for "orcheo" with info_name set
        parent_ctx = click.Context(click.Command("orcheo"), info_name="orcheo")
        # Create child context with parent to get "orcheo workflow" path
        ctx = click.Context(cmd, parent=parent_ctx, info_name="workflow")
        raise click.UsageError("Missing command.", ctx=ctx)

    monkeypatch.setattr(main_mod, "app", mock_app)
    monkeypatch.setattr(main_mod.sys, "argv", ["orcheo"])
    monkeypatch.setenv("ORCHEO_HUMAN", "1")

    with pytest.raises(SystemExit) as exc_info:
        run()
    assert exc_info.value.code == 1

    # Verify help command suggestion is printed (covers lines 81-82)
    captured = capsys.readouterr()
    assert "Missing command." in captured.out
    assert "orcheo workflow --help" in captured.out


def test_run_usage_error_without_context(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that UsageError without context doesn't show help command."""
    import click

    def mock_app(*args: object, **kwargs: object) -> None:
        # Raise UsageError without context
        raise click.UsageError("Invalid option.")

    monkeypatch.setattr(main_mod, "app", mock_app)
    monkeypatch.setattr(main_mod.sys, "argv", ["orcheo"])
    monkeypatch.setenv("ORCHEO_HUMAN", "1")

    with pytest.raises(SystemExit) as exc_info:
        run()
    assert exc_info.value.code == 1

    # Verify error is printed but no help command suggestion (covers branch 80->83)
    captured = capsys.readouterr()
    assert "Invalid option." in captured.out
    assert "--help" not in captured.out


def test_run_authentication_error_401(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that 401 errors show helpful authentication hints."""

    def mock_app(*args: object, **kwargs: object) -> None:
        raise APICallError("Invalid bearer token", status_code=401)

    monkeypatch.setattr(main_mod, "app", mock_app)
    monkeypatch.setattr(main_mod.sys, "argv", ["orcheo"])
    monkeypatch.setenv("ORCHEO_HUMAN", "1")

    with pytest.raises(SystemExit) as exc_info:
        run()
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "Error: Invalid bearer token" in captured.out
    assert "Hint:" in captured.out
    assert "ORCHEO_SERVICE_TOKEN" in captured.out


def test_run_authentication_error_403(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that 403 errors show helpful permission hints."""

    def mock_app(*args: object, **kwargs: object) -> None:
        raise APICallError("Missing required scopes: workflows:write", status_code=403)

    monkeypatch.setattr(main_mod, "app", mock_app)
    monkeypatch.setattr(main_mod.sys, "argv", ["orcheo"])
    monkeypatch.setenv("ORCHEO_HUMAN", "1")

    with pytest.raises(SystemExit) as exc_info:
        run()
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "Error: Missing required scopes: workflows:write" in captured.out
    assert "Hint:" in captured.out
    assert "permissions" in captured.out


def test_run_api_error_without_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that non-auth API errors don't show hints."""

    def mock_app(*args: object, **kwargs: object) -> None:
        raise APICallError("Server error", status_code=500)

    monkeypatch.setattr(main_mod, "app", mock_app)
    monkeypatch.setattr(main_mod.sys, "argv", ["orcheo"])
    monkeypatch.setenv("ORCHEO_HUMAN", "1")

    with pytest.raises(SystemExit) as exc_info:
        run()
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "Error: Server error" in captured.out
    assert "Hint:" not in captured.out


def test_version_callback(capsys: pytest.CaptureFixture[str]) -> None:
    """Test _version_callback prints version and raises Exit."""
    from orcheo_sdk.cli.main import _version_callback

    with pytest.raises(typer.Exit):
        _version_callback(True)
    captured = capsys.readouterr()
    assert "orcheo " in captured.out


def test_version_callback_false() -> None:
    """Test _version_callback returns early when value is False."""
    from orcheo_sdk.cli.main import _version_callback

    _version_callback(False)  # Should return without raising


def test_version_callback_package_not_found(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test _version_callback prints 'unknown' when package is not installed."""
    from importlib.metadata import PackageNotFoundError
    from orcheo_sdk.cli import main as main_mod
    from orcheo_sdk.cli.main import _version_callback

    def _raise(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(main_mod, "package_version", _raise)

    with pytest.raises(typer.Exit):
        _version_callback(True)
    captured = capsys.readouterr()
    assert "orcheo unknown" in captured.out


def test_env_bool_falsey(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHEO_TEST_BOOL", "0")
    assert main_mod._env_bool("ORCHEO_TEST_BOOL") is False


def test_main_workspace_option_sets_process_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--workspace should populate ORCHEO_WORKSPACE for downstream API calls."""

    monkeypatch.delenv("ORCHEO_WORKSPACE", raising=False)

    def mock_app(*args: object, **kwargs: object) -> None:
        assert os.environ["ORCHEO_WORKSPACE"] == "workspace-1"

    monkeypatch.setattr(main_mod, "app", mock_app)
    monkeypatch.setattr(main_mod.sys, "argv", ["orcheo", "--workspace", "workspace-1"])

    run()


def test_print_cli_error_machine_with_status_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test that machine error output includes status_code for APICallError."""
    import json
    from orcheo_sdk.cli.main import _print_cli_error_machine

    exc = APICallError("Unauthorized", status_code=401)
    _print_cli_error_machine(exc)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["error"] == "Unauthorized"
    assert data["status_code"] == 401


def test_run_usage_error_machine_mode_with_context(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Machine mode UsageError with context includes help key."""
    import json
    import click

    def mock_app(*args: object, **kwargs: object) -> None:
        cmd = click.Command("workflow")
        parent_ctx = click.Context(click.Command("orcheo"), info_name="orcheo")
        ctx = click.Context(cmd, parent=parent_ctx, info_name="workflow")
        raise click.UsageError("Bad arg.", ctx=ctx)

    monkeypatch.setattr(main_mod, "app", mock_app)
    monkeypatch.setattr(main_mod.sys, "argv", ["orcheo"])
    monkeypatch.delenv("ORCHEO_HUMAN", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        run()
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["error"] == "Bad arg."
    assert "orcheo workflow --help" in data["help"]


def test_run_usage_error_machine_mode_without_context(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Machine mode UsageError without context omits help key."""
    import json
    import click

    def mock_app(*args: object, **kwargs: object) -> None:
        raise click.UsageError("No ctx.")

    monkeypatch.setattr(main_mod, "app", mock_app)
    monkeypatch.setattr(main_mod.sys, "argv", ["orcheo"])
    monkeypatch.delenv("ORCHEO_HUMAN", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        run()
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["error"] == "No ctx."
    assert "help" not in data


def test_run_cli_error_machine_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def mock_app(*args: object, **kwargs: object) -> None:
        raise CLIError("Machine failure")

    monkeypatch.setattr(main_mod, "app", mock_app)
    monkeypatch.setattr(main_mod.sys, "argv", ["orcheo"])
    monkeypatch.delenv("ORCHEO_HUMAN", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        run()
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "Machine failure" in captured.out


def test_run_disables_rich_markup_in_machine_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Machine mode should disable rich markup and restore it after run."""
    import click
    from orcheo_sdk.cli import main as main_mod

    class DummyApp:
        def __init__(self) -> None:
            self.rich_markup_mode = "rich"
            self.called_with_none = False

        def __call__(self, *args: object, **kwargs: object) -> None:
            self.called_with_none = self.rich_markup_mode is None
            raise click.UsageError("Missing command.")

    dummy = DummyApp()
    monkeypatch.setattr(main_mod, "app", dummy)
    monkeypatch.delenv("ORCHEO_HUMAN", raising=False)
    monkeypatch.setattr(main_mod.sys, "argv", ["orcheo"])

    with pytest.raises(SystemExit) as exc_info:
        main_mod.run()
    assert exc_info.value.code == 1
    assert dummy.called_with_none
    assert dummy.rich_markup_mode == "rich"


def test_run_human_injects_human_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from orcheo_sdk.cli import main as main_mod

    captured_argv: list[str] = []

    def mock_run() -> None:
        nonlocal captured_argv
        captured_argv = main_mod.sys.argv.copy()

    monkeypatch.setattr(main_mod, "run", mock_run)
    monkeypatch.setattr(main_mod.sys, "argv", ["orcheo-human", "node", "list"])

    run_human()

    assert captured_argv[0] == "orcheo-human"
    assert captured_argv[1] == "--human"
    assert captured_argv[2:] == ["node", "list"]


def test_run_human_skips_inject_when_flag_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test run_human does not duplicate --human when already present."""
    from orcheo_sdk.cli import main as main_mod

    captured_argv: list[str] = []

    def mock_run() -> None:
        nonlocal captured_argv
        captured_argv = main_mod.sys.argv.copy()

    monkeypatch.setattr(main_mod, "run", mock_run)
    monkeypatch.setattr(
        main_mod.sys,
        "argv",
        ["orcheo-human", "--human", "node", "list"],
    )

    run_human()

    assert captured_argv == [
        "orcheo-human",
        "--human",
        "node",
        "list",
    ]


def test_parse_setup_mode_branches() -> None:
    from orcheo_sdk.cli.main import _parse_setup_mode

    assert _parse_setup_mode(None) is None
    assert _parse_setup_mode("  install ") == "install"
    with pytest.raises(typer.BadParameter, match="--mode must be one of"):
        _parse_setup_mode("invalid")


def test_parse_auth_mode_branches() -> None:
    from orcheo_sdk.cli.main import _parse_auth_mode

    assert _parse_auth_mode(None) is None
    assert _parse_auth_mode("  oauth ") == "oauth"
    with pytest.raises(typer.BadParameter, match="--auth-mode must be one of"):
        _parse_auth_mode("invalid")


def test_run_install_flow_forced_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    console = Console()
    config = SetupConfig(
        mode="install",
        backend_url="http://example",
        auth_mode="api-key",
        api_key="key",
        chatkit_domain_key=None,
        public_ingress_enabled=False,
        public_host=None,
        publish_local_ports=True,
        backend_upstreams="backend:8000",
        canvas_upstream="canvas:5173",
        start_stack=False,
        install_docker_if_missing=False,
    )

    run_setup_args: dict[str, object] = {}

    def fake_run_setup(**kwargs: object) -> SetupConfig:
        run_setup_args.update(kwargs)
        return config

    execute_kwargs: list[tuple[SetupConfig, str | None]] = []

    def fake_execute_setup(
        cfg: SetupConfig, *, console: Console, stack_version: str | None
    ) -> None:
        execute_kwargs.append((cfg, stack_version))

    printed: list[SetupConfig] = []

    def fake_print_summary(cfg: SetupConfig, *, console: Console) -> None:
        printed.append(cfg)

    monkeypatch.setattr(main_mod, "run_setup", fake_run_setup)
    monkeypatch.setattr(main_mod, "execute_setup", fake_execute_setup)
    monkeypatch.setattr(main_mod, "print_summary", fake_print_summary)
    monkeypatch.setattr(
        main_mod,
        "_parse_setup_mode",
        lambda value: pytest.fail("should not parse when forced"),
    )
    monkeypatch.setattr(main_mod, "_parse_auth_mode", lambda value: "api-key")

    main_mod._run_install_flow(
        console=console,
        yes=True,
        mode="install",
        stack_version="0.1.0",
        backend_url="http://example",
        auth_mode="api-key",
        api_key=None,
        chatkit_domain_key=None,
        public_ingress=None,
        public_host=None,
        publish_local_ports=None,
        start_stack=None,
        install_docker=None,
        manual_secrets=False,
        forced_mode="upgrade",
    )

    assert run_setup_args.get("mode") == "upgrade"
    assert execute_kwargs == [(config, "0.1.0")]
    assert printed == [config]


def test_run_install_flow_parses_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    console = Console()
    config = SetupConfig(
        mode="install",
        backend_url="http://example",
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

    called: list[str] = []

    def fake_run_setup(**kwargs: object) -> SetupConfig:
        called.append(str(kwargs.get("mode")))
        return config

    monkeypatch.setattr(main_mod, "run_setup", fake_run_setup)
    monkeypatch.setattr(main_mod, "execute_setup", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_mod, "print_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_mod, "_install_agent_skills", lambda **kwargs: None)

    main_mod._run_install_flow(
        console=console,
        yes=False,
        mode="install",
        stack_version=None,
        backend_url=None,
        auth_mode="oauth",
        api_key=None,
        chatkit_domain_key=None,
        public_ingress=None,
        public_host=None,
        publish_local_ports=None,
        start_stack=None,
        install_docker=None,
        manual_secrets=False,
    )

    assert called == ["install"]


def test_resolve_install_console_prefers_ctx_console() -> None:
    ctx = typer.Context(click.Command("orcheo"))
    shared_console = Console()
    ctx.obj = CLIState(
        settings=object(),
        client=object(),
        cache=object(),
        console=shared_console,
    )

    result = main_mod._resolve_install_console(ctx)
    assert result is shared_console


def test_resolve_install_console_default() -> None:
    ctx = typer.Context(click.Command("orcheo"))
    ctx.obj = None
    result = main_mod._resolve_install_console(ctx)
    assert isinstance(result, Console)


def test_resolve_stack_project_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHEO_STACK_DIR", raising=False)
    assert main_mod._resolve_stack_project_dir() == Path.home() / ".orcheo" / "stack"


def test_stack_compose_base_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))

    with pytest.raises(typer.BadParameter):
        main_mod._stack_compose_base_args()

    compose_file = stack_dir / "docker-compose.yml"
    compose_file.write_text("version: '3'")
    result = main_mod._stack_compose_base_args()
    assert str(compose_file) in result


def test_compose_profile_args(
    tmp_path: Path,
) -> None:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()

    assert main_mod._compose_profile_args(stack_dir) == []

    env_file = stack_dir / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "OTHER=value",
                "COMPOSE_PROFILES= public-ingress , 'local-access', \"extra\" ",
                "IGNORED=after",
            ]
        ),
        encoding="utf-8",
    )

    assert main_mod._compose_profile_args(stack_dir) == [
        "--profile",
        "public-ingress",
        "--profile",
        "local-access",
        "--profile",
        "extra",
    ]

    empty_profiles_dir = tmp_path / "stack-empty"
    empty_profiles_dir.mkdir()
    (empty_profiles_dir / ".env").write_text("OTHER=value\n", encoding="utf-8")
    assert main_mod._compose_profile_args(empty_profiles_dir) == []

    blank_profiles_dir = tmp_path / "stack-blank"
    blank_profiles_dir.mkdir()
    (blank_profiles_dir / ".env").write_text(
        "COMPOSE_PROFILES=public-ingress, ,local-access\n", encoding="utf-8"
    )
    assert main_mod._compose_profile_args(blank_profiles_dir) == [
        "--profile",
        "public-ingress",
        "--profile",
        "local-access",
    ]


def test_run_stack_command_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    console = Console()

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(main_mod.subprocess, "run", fake_run)
    main_mod._run_stack_command(["docker", "compose"], console=console)

    def failing_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=3)

    monkeypatch.setattr(main_mod.subprocess, "run", failing_run)
    with pytest.raises(typer.BadParameter):
        main_mod._run_stack_command(["docker"], console=console)

    monkeypatch.setattr(main_mod.subprocess, "run", fake_run)
    main_mod._run_stack_command(
        ["docker", "compose"],
        console=console,
        expected_exit_codes={0, 1},
    )


def test_install_command_skips_subcommands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main_mod, "_run_install_flow", lambda **kwargs: pytest.fail("should not run")
    )
    ctx = typer.Context(click.Command("orcheo"))
    ctx.invoked_subcommand = "upgrade"
    main_mod.install_command(ctx)


def test_install_upgrade_command_forces_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str | None] = []

    def fake_run_install_flow(
        *, forced_mode: str | None = None, **kwargs: object
    ) -> None:  # type: ignore[override]
        called.append(str(kwargs.get("mode")))

    monkeypatch.setattr(main_mod, "_run_install_flow", fake_run_install_flow)
    ctx = typer.Context(click.Command("orcheo"))
    main_mod.install_upgrade_command(ctx)
    assert called == ["None"]


def test_stack_command_errors_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    console = Console()
    monkeypatch.setattr(main_mod, "_resolve_install_console", lambda ctx: console)
    monkeypatch.setattr(
        main_mod,
        "_stack_compose_base_args",
        lambda: ["docker", "compose", "-f", "docker-compose.yml"],
    )

    monkeypatch.setattr(main_mod.shutil, "which", lambda name: None)
    with pytest.raises(typer.BadParameter, match="Docker is not installed"):
        main_mod.stack_command(typer.Context(click.Command("stack")))

    monkeypatch.setattr(main_mod.shutil, "which", lambda name: "/usr/bin/docker")
    with pytest.raises(typer.BadParameter, match="Choose one action"):
        main_mod.stack_command(typer.Context(click.Command("stack")))

    with pytest.raises(typer.BadParameter, match="Choose only one stack action"):
        main_mod.stack_command(
            typer.Context(click.Command("stack")), logs=True, start=True
        )

    captured: list[dict[str, object]] = []

    def fake_run_stack_command(
        command: list[str],
        *,
        console: Console,
        expected_exit_codes: set[int] | None = None,
    ) -> None:
        captured.append(
            {
                "command": command,
                "expected": expected_exit_codes,
            }
        )

    monkeypatch.setattr(main_mod, "_run_stack_command", fake_run_stack_command)
    ctx = typer.Context(click.Command("stack"))
    main_mod.stack_command(ctx, logs=True)
    assert captured[-1]["expected"] == {0, 130}


def test_print_cli_error_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    console = Console(file=io.StringIO(), force_terminal=False)
    main_mod._print_cli_error(console, CLIError("boom"))
    captured = console.file.getvalue()
    assert "Error: boom" in captured
    assert "Hint" not in captured


def test_print_cli_error_machine_plain(capsys: pytest.CaptureFixture[str]) -> None:
    main_mod._print_cli_error_machine(CLIError("boom"))
    captured = capsys.readouterr()
    assert "boom" in captured.out


def test_main_skips_update_check_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = typer.Context(click.Command("orcheo"))
    settings = SimpleNamespace(
        profile="default",
        api_url="http://localhost:8000",
        service_token="token",
        chatkit_public_base_url="http://localhost:5173",
    )
    cache_calls: list[tuple[Path, timedelta]] = []
    client_calls: list[dict[str, object]] = []
    update_calls: list[dict[str, object]] = []

    monkeypatch.setattr(main_mod, "resolve_settings", lambda **kwargs: settings)
    monkeypatch.setattr(main_mod, "get_cache_dir", lambda: tmp_path / "cache")

    class DummyCache:
        def __init__(self, *, directory: Path, ttl: timedelta) -> None:
            cache_calls.append((directory, ttl))

    class DummyClient:
        def __init__(self, **kwargs: object) -> None:
            client_calls.append(kwargs)

    monkeypatch.setattr(main_mod, "CacheManager", DummyCache)
    monkeypatch.setattr(main_mod, "ApiClient", DummyClient)
    monkeypatch.setattr(
        main_mod,
        "maybe_print_update_notice",
        lambda **kwargs: update_calls.append(kwargs),
    )
    monkeypatch.setattr(main_mod, "_is_completion_mode", lambda: False)

    main_mod.main(
        ctx,
        profile=None,
        version=False,
        api_url=None,
        service_token=None,
        offline=False,
        cache_ttl_hours=24,
        human=False,
        no_update_check=True,
    )

    assert cache_calls
    assert client_calls
    assert update_calls == []
    assert isinstance(ctx.obj, CLIState)


def test_install_agent_skills_skips_when_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """_install_agent_skills returns immediately when should_install=False."""
    console = Console()
    monkeypatch.setattr(
        main_mod.shutil, "which", lambda name: pytest.fail("should not check")
    )
    main_mod._install_agent_skills(console=console, should_install=False)


def test_install_agent_skills_with_skill_mgr_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uses skill-mgr binary when it is found in PATH."""
    import io

    console = Console(file=io.StringIO())
    monkeypatch.setattr(
        main_mod.shutil,
        "which",
        lambda name: "/usr/bin/skill-mgr" if name == "skill-mgr" else None,
    )

    ran_cmds: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        ran_cmds.append(cmd)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(main_mod.subprocess, "run", fake_run)
    main_mod._install_agent_skills(console=console, should_install=True)
    assert ran_cmds[0][0] == "/usr/bin/skill-mgr"
    assert ran_cmds[0] == [
        "/usr/bin/skill-mgr",
        "install",
        "AI-Colleagues/agent-skills/orcheo",
    ]


def test_install_agent_skills_without_skill_mgr_falls_back_to_uv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falls back to 'uv run skill-mgr' when skill-mgr is not in PATH."""
    import io

    console = Console(file=io.StringIO())
    monkeypatch.setattr(main_mod.shutil, "which", lambda name: None)

    ran_cmds: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        ran_cmds.append(cmd)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(main_mod.subprocess, "run", fake_run)
    main_mod._install_agent_skills(console=console, should_install=True)
    assert ran_cmds[0][:2] == ["uv", "run"]
    assert ran_cmds[0][2:] == [
        "skill-mgr",
        "install",
        "AI-Colleagues/agent-skills/orcheo",
    ]


def test_install_agent_skills_nonzero_exit_prints_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prints a warning when skill-mgr exits with a non-zero status."""
    import io

    out = io.StringIO()
    console = Console(file=out, no_color=True, highlight=False)
    monkeypatch.setattr(main_mod.shutil, "which", lambda name: None)

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        return type("R", (), {"returncode": 1})()

    monkeypatch.setattr(main_mod.subprocess, "run", fake_run)
    main_mod._install_agent_skills(console=console, should_install=True)
    assert (
        "Warning" in out.getvalue()
        or "warning" in out.getvalue().lower()
        or "skill-mgr" in out.getvalue()
    )
