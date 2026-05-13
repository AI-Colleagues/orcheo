"""Tests for the CLI config command."""

from __future__ import annotations
import re
import tomllib
from pathlib import Path
import pytest
from typer.testing import CliRunner
from orcheo_sdk.cli.config import CONFIG_FILENAME, PROFILE_ENV
from orcheo_sdk.cli.config_command import (
    _check_profile,
    _format_toml_value,
    _read_env_file,
    _redact_value,
    _resolve_value,
    _run_config_check,
)
from orcheo_sdk.cli.errors import CLIError
from orcheo_sdk.cli.main import app


def test_config_command_writes_selected_profile_from_env_file(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ORCHEO_API_URL=http://env-file.test",
                "ORCHEO_SERVICE_TOKEN=env-token",
                "ORCHEO_CHATKIT_PUBLIC_BASE_URL=http://canvas.test",
                "ORCHEO_AUTH_ISSUER=https://auth.env-file.test",
                "ORCHEO_AUTH_CLIENT_ID=env-client-id",
                "ORCHEO_AUTH_SCOPES=openid email",
                "ORCHEO_AUTH_AUDIENCE=https://api.env-file.test",
                "ORCHEO_AUTH_ORGANIZATION=org-env",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["--profile", "local", "config", "--env-file", str(env_file)],
        env=env,
    )

    assert result.exit_code == 0

    config_path = Path(env["ORCHEO_CONFIG_DIR"]) / CONFIG_FILENAME
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    profile = data["profiles"]["local"]
    assert profile["api_url"] == "http://env-file.test"
    assert profile["service_token"] == "env-token"
    assert profile["chatkit_public_base_url"] == "http://canvas.test"
    assert profile["auth_issuer"] == "https://auth.env-file.test"
    assert profile["auth_client_id"] == "env-client-id"
    assert profile["auth_scopes"] == "openid email"
    assert profile["auth_audience"] == "https://api.env-file.test"
    assert profile["auth_organization"] == "org-env"


def test_config_command_uses_environment_defaults(
    runner: CliRunner, env: dict[str, str]
) -> None:
    result = runner.invoke(app, ["config"], env=env)

    assert result.exit_code == 0

    config_path = Path(env["ORCHEO_CONFIG_DIR"]) / CONFIG_FILENAME
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    profile = data["profiles"]["default"]

    assert profile["api_url"] == env["ORCHEO_API_URL"]
    assert profile["service_token"] == env["ORCHEO_SERVICE_TOKEN"]


def test_config_command_rejects_profile_option(
    runner: CliRunner, env: dict[str, str]
) -> None:
    result = runner.invoke(app, ["config", "--profile", "remote"], env=env)

    assert result.exit_code == 2
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.output).lower()
    assert "--profile" in output
    assert "option" in output


def test_config_command_without_auth_or_service_token_fails(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Config command should fail when neither auth method is configured."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    env_without_token = {
        "ORCHEO_CONFIG_DIR": str(config_dir),
        "ORCHEO_CACHE_DIR": str(tmp_path / "cache"),
        "ORCHEO_API_URL": "http://api.test",
        "ORCHEO_SERVICE_TOKEN": "",
        "ORCHEO_AUTH_ISSUER": "",
        "ORCHEO_AUTH_CLIENT_ID": "",
        "ORCHEO_AUTH_SCOPES": "",
        "ORCHEO_AUTH_AUDIENCE": "",
        "ORCHEO_AUTH_ORGANIZATION": "",
        "ORCHEO_HUMAN": "1",
        "NO_COLOR": "1",
    }

    result = runner.invoke(app, ["config"], env=env_without_token)

    assert result.exit_code == 1
    assert isinstance(result.exception, CLIError)
    assert (
        "one of service_token or (auth_issuer and auth_client_id and"
        " auth_audience) needs to be configured." in str(result.exception)
    )


def test_read_env_file_not_found(tmp_path: Path) -> None:
    """Test CLIError raised when env file does not exist."""
    missing = tmp_path / "missing.env"
    with pytest.raises(CLIError) as exc_info:
        _read_env_file(missing)
    assert "not found" in str(exc_info.value)


def test_read_env_file_skips_comments_and_empty_lines(tmp_path: Path) -> None:
    """Test that comments, empty lines, and lines without '=' are skipped."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# This is a comment",
                "",
                "   ",
                "VALID_KEY=value",
                "no_equals_sign",
                "ANOTHER=another_value",
            ]
        ),
        encoding="utf-8",
    )
    data = _read_env_file(env_file)
    assert data == {"VALID_KEY": "value", "ANOTHER": "another_value"}


def test_read_env_file_handles_export_prefix(tmp_path: Path) -> None:
    """Test that export prefix is stripped from env lines."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "export MY_VAR=exported_value",
                "NORMAL_VAR=normal_value",
            ]
        ),
        encoding="utf-8",
    )
    data = _read_env_file(env_file)
    assert data["MY_VAR"] == "exported_value"
    assert data["NORMAL_VAR"] == "normal_value"


def test_read_env_file_handles_quoted_values(tmp_path: Path) -> None:
    """Test that quoted values have quotes stripped."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                'DOUBLE_QUOTED="double quoted value"',
                "SINGLE_QUOTED='single quoted value'",
                "UNQUOTED=unquoted_value",
            ]
        ),
        encoding="utf-8",
    )
    data = _read_env_file(env_file)
    assert data["DOUBLE_QUOTED"] == "double quoted value"
    assert data["SINGLE_QUOTED"] == "single quoted value"
    assert data["UNQUOTED"] == "unquoted_value"


def test_resolve_value_prefers_override() -> None:
    """Test that override parameter takes precedence."""
    result = _resolve_value(
        "ANY_KEY",
        env_data={"ANY_KEY": "env_value"},
        override="override_value",
    )
    assert result == "override_value"


def test_format_toml_value_bool() -> None:
    """Test formatting boolean values."""
    assert _format_toml_value(True) == "true"
    assert _format_toml_value(False) == "false"


def test_format_toml_value_numeric() -> None:
    """Test formatting integer and float values."""
    assert _format_toml_value(42) == "42"
    assert _format_toml_value(3.14) == "3.14"


def test_format_toml_value_list() -> None:
    """Test formatting list values."""
    assert _format_toml_value(["a", "b"]) == '["a", "b"]'
    assert _format_toml_value([1, 2, 3]) == "[1, 2, 3]"


def test_format_toml_value_unsupported_type() -> None:
    """Test that unsupported types raise CLIError."""
    with pytest.raises(CLIError) as exc_info:
        _format_toml_value({"key": "value"})
    assert "Unsupported config value type" in str(exc_info.value)


def test_config_command_uses_profile_from_env_file(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Test that ORCHEO_PROFILE from env file is used as default profile name."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"{PROFILE_ENV}=custom-profile",
                "ORCHEO_API_URL=http://custom.test",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["config", "--env-file", str(env_file)],
        env=env,
    )

    assert result.exit_code == 0

    config_path = Path(env["ORCHEO_CONFIG_DIR"]) / CONFIG_FILENAME
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "custom-profile" in data["profiles"]
    assert data["profiles"]["custom-profile"]["api_url"] == "http://custom.test"


def test_config_command_falls_back_to_existing_profile_api_url(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Test that api_url is read from the existing profile when not provided."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / CONFIG_FILENAME
    config_path.write_text(
        '[profiles.default]\napi_url = "http://existing.test"\n',
        encoding="utf-8",
    )
    minimal_env = {
        "ORCHEO_CONFIG_DIR": str(config_dir),
        "ORCHEO_CACHE_DIR": str(tmp_path / "cache"),
        "ORCHEO_API_URL": "",
        "ORCHEO_HUMAN": "1",
        "NO_COLOR": "1",
    }

    result = runner.invoke(
        app,
        [
            "config",
            "--auth-issuer",
            "https://auth.example.com/",
            "--auth-client-id",
            "test-client-id",
            "--auth-audience",
            "https://api.example.com/",
        ],
        env=minimal_env,
    )

    assert result.exit_code == 0
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    profile = data["profiles"]["default"]
    assert profile["api_url"] == "http://existing.test"
    assert profile["auth_issuer"] == "https://auth.example.com/"
    assert profile["auth_client_id"] == "test-client-id"
    assert profile["auth_audience"] == "https://api.example.com/"


def test_config_command_preserves_other_profiles_when_using_root_profile(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Test only the selected profile is updated."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / CONFIG_FILENAME
    config_path.write_text(
        "\n".join(
            [
                "[profiles.alpha]",
                'api_url = "http://alpha.test"',
                "",
                "[profiles.beta]",
                'api_url = "http://beta.test"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    minimal_env = {
        "ORCHEO_CONFIG_DIR": str(config_dir),
        "ORCHEO_CACHE_DIR": str(tmp_path / "cache"),
        "ORCHEO_API_URL": "",
        "ORCHEO_HUMAN": "1",
        "NO_COLOR": "1",
    }

    result = runner.invoke(
        app,
        [
            "--profile",
            "alpha",
            "config",
            "--auth-issuer",
            "https://auth.example.com/",
            "--auth-client-id",
            "test-client-id",
            "--auth-audience",
            "https://api.example.com/",
        ],
        env=minimal_env,
    )

    assert result.exit_code == 0
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    profiles = data["profiles"]
    assert profiles["alpha"]["api_url"] == "http://alpha.test"
    assert profiles["beta"]["api_url"] == "http://beta.test"
    assert profiles["alpha"]["auth_audience"] == "https://api.example.com/"
    assert "auth_audience" not in profiles["beta"]


def test_config_command_missing_api_url(runner: CliRunner, tmp_path: Path) -> None:
    """Test that missing API URL raises CLIError."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    minimal_env = {
        "ORCHEO_CONFIG_DIR": str(config_dir),
        "ORCHEO_API_URL": "",
        "ORCHEO_AUTH_ISSUER": "",
        "ORCHEO_AUTH_CLIENT_ID": "",
        "ORCHEO_AUTH_SCOPES": "",
        "ORCHEO_AUTH_AUDIENCE": "",
        "ORCHEO_AUTH_ORGANIZATION": "",
        "ORCHEO_HUMAN": "1",
        "NO_COLOR": "1",
    }

    result = runner.invoke(app, ["config"], env=minimal_env)

    assert result.exit_code == 1
    assert isinstance(result.exception, CLIError)
    assert "Missing api_url" in str(result.exception)


def test_config_command_invalid_toml(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    """Test that invalid TOML in existing config raises CLIError."""
    import tomllib
    from unittest.mock import MagicMock
    from rich.console import Console
    from typer import Context
    from orcheo_sdk.cli import config_command
    from orcheo_sdk.cli.config_command import configure

    # Set environment variables
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", env["ORCHEO_CONFIG_DIR"])
    monkeypatch.setenv("ORCHEO_API_URL", env["ORCHEO_API_URL"])

    # Create a real TOMLDecodeError by parsing invalid TOML
    try:
        tomllib.loads("[[")
    except tomllib.TOMLDecodeError as toml_error:
        captured_error = toml_error

    # Patch load_profiles to raise TOMLDecodeError as it would with invalid TOML
    def _raise_toml_error(path: Path) -> dict[str, dict[str, str]]:
        raise captured_error

    monkeypatch.setattr(config_command, "load_profiles", _raise_toml_error)

    # Create a mock context that provides a console
    mock_state = MagicMock()
    mock_state.console = Console()
    mock_ctx = MagicMock(spec=Context)
    mock_ctx.invoked_subcommand = None
    mock_ctx.ensure_object.return_value = mock_state

    with pytest.raises(CLIError) as exc_info:
        configure(
            ctx=mock_ctx,
            api_url=env["ORCHEO_API_URL"],
            service_token=None,
            chatkit_public_base_url=None,
            env_file=None,
        )

    assert "Invalid TOML" in str(exc_info.value)


def test_config_command_incomplete_oauth_issuer_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test that providing auth_issuer without auth_client_id raises CLIError."""
    from unittest.mock import MagicMock
    from rich.console import Console
    from typer import Context
    from orcheo_sdk.cli.auth.config import (
        AUTH_AUDIENCE_ENV,
        AUTH_CLIENT_ID_ENV,
        AUTH_ISSUER_ENV,
        AUTH_ORGANIZATION_ENV,
        AUTH_SCOPES_ENV,
    )
    from orcheo_sdk.cli.config_command import configure

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ORCHEO_API_URL", "http://api.test")
    for key in [
        AUTH_ISSUER_ENV,
        AUTH_CLIENT_ID_ENV,
        AUTH_SCOPES_ENV,
        AUTH_AUDIENCE_ENV,
        AUTH_ORGANIZATION_ENV,
    ]:
        monkeypatch.delenv(key, raising=False)

    mock_state = MagicMock()
    mock_state.console = Console()
    mock_ctx = MagicMock(spec=Context)
    mock_ctx.invoked_subcommand = None
    mock_ctx.ensure_object.return_value = mock_state

    with pytest.raises(CLIError, match="auth_client_id"):
        configure(
            ctx=mock_ctx,
            api_url="http://api.test",
            service_token=None,
            chatkit_public_base_url=None,
            auth_issuer="https://auth.example.com",
            auth_client_id=None,
            auth_scopes=None,
            auth_audience=None,
            auth_organization=None,
            env_file=None,
        )


def test_config_command_incomplete_oauth_client_id_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test that providing auth_client_id without auth_issuer raises CLIError."""
    from unittest.mock import MagicMock
    from rich.console import Console
    from typer import Context
    from orcheo_sdk.cli.auth.config import (
        AUTH_AUDIENCE_ENV,
        AUTH_CLIENT_ID_ENV,
        AUTH_ISSUER_ENV,
        AUTH_ORGANIZATION_ENV,
        AUTH_SCOPES_ENV,
    )
    from orcheo_sdk.cli.config_command import configure

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ORCHEO_API_URL", "http://api.test")
    for key in [
        AUTH_ISSUER_ENV,
        AUTH_CLIENT_ID_ENV,
        AUTH_SCOPES_ENV,
        AUTH_AUDIENCE_ENV,
        AUTH_ORGANIZATION_ENV,
    ]:
        monkeypatch.delenv(key, raising=False)

    mock_state = MagicMock()
    mock_state.console = Console()
    mock_ctx = MagicMock(spec=Context)
    mock_ctx.invoked_subcommand = None
    mock_ctx.ensure_object.return_value = mock_state

    with pytest.raises(CLIError, match="auth_issuer"):
        configure(
            ctx=mock_ctx,
            api_url="http://api.test",
            service_token=None,
            chatkit_public_base_url=None,
            auth_issuer=None,
            auth_client_id="my-client",
            auth_scopes=None,
            auth_audience=None,
            auth_organization=None,
            env_file=None,
        )


def test_config_command_incomplete_oauth_missing_audience(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test issuer/client_id without audience raises CLIError."""
    from unittest.mock import MagicMock
    from rich.console import Console
    from typer import Context
    from orcheo_sdk.cli.auth.config import (
        AUTH_AUDIENCE_ENV,
        AUTH_CLIENT_ID_ENV,
        AUTH_ISSUER_ENV,
        AUTH_ORGANIZATION_ENV,
        AUTH_SCOPES_ENV,
    )
    from orcheo_sdk.cli.config_command import configure

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ORCHEO_API_URL", "http://api.test")
    for key in [
        AUTH_ISSUER_ENV,
        AUTH_CLIENT_ID_ENV,
        AUTH_SCOPES_ENV,
        AUTH_AUDIENCE_ENV,
        AUTH_ORGANIZATION_ENV,
    ]:
        monkeypatch.delenv(key, raising=False)

    mock_state = MagicMock()
    mock_state.console = Console()
    mock_ctx = MagicMock(spec=Context)
    mock_ctx.invoked_subcommand = None
    mock_ctx.ensure_object.return_value = mock_state

    with pytest.raises(CLIError, match="auth_audience"):
        configure(
            ctx=mock_ctx,
            api_url="http://api.test",
            service_token=None,
            chatkit_public_base_url=None,
            auth_issuer="https://auth.example.com",
            auth_client_id="my-client",
            auth_scopes=None,
            auth_audience=None,
            auth_organization=None,
            env_file=None,
        )


def test_resolve_oauth_values_all_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover false branches when no OAuth values resolve."""
    from orcheo_sdk.cli.auth.config import (
        AUTH_AUDIENCE_ENV,
        AUTH_CLIENT_ID_ENV,
        AUTH_ISSUER_ENV,
        AUTH_ORGANIZATION_ENV,
        AUTH_SCOPES_ENV,
    )
    from orcheo_sdk.cli.config_command import _resolve_oauth_values

    for key in [
        AUTH_ISSUER_ENV,
        AUTH_CLIENT_ID_ENV,
        AUTH_SCOPES_ENV,
        AUTH_AUDIENCE_ENV,
        AUTH_ORGANIZATION_ENV,
    ]:
        monkeypatch.delenv(key, raising=False)

    result = _resolve_oauth_values(
        env_data=None,
        auth_issuer=None,
        auth_client_id=None,
        auth_scopes=None,
        auth_audience=None,
        auth_organization=None,
    )
    assert result == {}


def test_config_check_fails_without_api_url(
    runner: CliRunner, env: dict[str, str]
) -> None:
    config_path = Path(env["ORCHEO_CONFIG_DIR"]) / CONFIG_FILENAME
    config_path.write_text(
        "\n".join(
            [
                "[profiles.default]",
                'service_token = "token-123456"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    check_env = {**env, "ORCHEO_API_URL": "", "ORCHEO_SERVICE_TOKEN": ""}
    result = runner.invoke(app, ["config", "--check"], env=check_env)

    assert result.exit_code == 1
    assert isinstance(result.exception, CLIError)
    assert "Missing api_url." in str(result.exception)


def test_config_check_fails_without_auth_or_service_token(
    runner: CliRunner, env: dict[str, str]
) -> None:
    config_path = Path(env["ORCHEO_CONFIG_DIR"]) / CONFIG_FILENAME
    config_path.write_text(
        "\n".join(
            [
                "[profiles.default]",
                'api_url = "http://api.test"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    check_env = {**env, "ORCHEO_SERVICE_TOKEN": ""}
    result = runner.invoke(app, ["config", "--check"], env=check_env)

    assert result.exit_code == 1
    assert isinstance(result.exception, CLIError)
    assert (
        "one of service_token or (auth_issuer and auth_client_id and"
        " auth_audience) needs to be configured." in str(result.exception)
    )


def test_config_check_applies_cli_overrides(
    runner: CliRunner, env: dict[str, str]
) -> None:
    config_path = Path(env["ORCHEO_CONFIG_DIR"]) / CONFIG_FILENAME
    config_path.write_text("[profiles.default]\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "config",
            "--check",
            "--api-url",
            "http://override.test",
            "--service-token",
            "override-token-1234",
        ],
        env=env,
    )

    assert result.exit_code == 0
    assert "api-url: http://override.test" in result.stdout
    assert "service-token: ov...34" in result.stdout


def test_config_check_applies_env_file_overrides(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    config_path = Path(env["ORCHEO_CONFIG_DIR"]) / CONFIG_FILENAME
    config_path.write_text("[profiles.default]\n", encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ORCHEO_API_URL=http://env-check.test",
                "ORCHEO_AUTH_ISSUER=https://issuer.env-check.test",
                "ORCHEO_AUTH_CLIENT_ID=env-client-1234",
                "ORCHEO_AUTH_AUDIENCE=https://api.env-check.test",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["config", "--check", "--env-file", str(env_file)],
        env=env,
    )

    assert result.exit_code == 0
    assert "api-url: http://env-check.test" in result.stdout
    assert "auth-issuer: ht...st" in result.stdout
    assert "auth-client-id: en...34" in result.stdout


def test_config_check_uses_root_profile_selection(
    runner: CliRunner, env: dict[str, str]
) -> None:
    config_path = Path(env["ORCHEO_CONFIG_DIR"]) / CONFIG_FILENAME
    config_path.write_text(
        "\n".join(
            [
                "[profiles.default]",
                'service_token = "default-token"',
                "",
                "[profiles.remote]",
                'api_url = "http://remote.test"',
                'service_token = "remote-token"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    check_env = {**env, "ORCHEO_API_URL": "", "ORCHEO_SERVICE_TOKEN": ""}
    result = runner.invoke(
        app, ["--profile", "remote", "config", "--check"], env=check_env
    )

    assert result.exit_code == 0
    assert "remote" in result.stdout
    assert "api-url: http://remote.test" in result.stdout


def test_config_check_passes_with_redacted_service_token(
    runner: CliRunner, env: dict[str, str]
) -> None:
    config_path = Path(env["ORCHEO_CONFIG_DIR"]) / CONFIG_FILENAME
    config_path.write_text(
        "\n".join(
            [
                "[profiles.default]",
                'api_url = "http://api.test"',
                'service_token = "token-123456"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    check_env = {**env, "ORCHEO_SERVICE_TOKEN": ""}
    result = runner.invoke(app, ["config", "--check"], env=check_env)

    assert result.exit_code == 0
    assert "api-url: http://api.test" in result.stdout
    assert "service-token: to...56" in result.stdout
    assert "token-123456" not in result.stdout


def test_config_check_passes_with_redacted_oauth(
    runner: CliRunner, env: dict[str, str]
) -> None:
    config_path = Path(env["ORCHEO_CONFIG_DIR"]) / CONFIG_FILENAME
    config_path.write_text(
        "\n".join(
            [
                "[profiles.default]",
                'api_url = "http://api.test"',
                'auth_issuer = "https://issuer.example.com"',
                'auth_client_id = "oauth-client-1234"',
                'auth_audience = "https://api.example.com"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["config", "--check"], env=env)

    assert result.exit_code == 0
    assert "api-url: http://api.test" in result.stdout
    assert "auth-issuer: ht...om" in result.stdout
    assert "auth-client-id: oa...34" in result.stdout
    assert "auth-audience: ht...om" in result.stdout
    assert "https://issuer.example.com" not in result.stdout
    assert "oauth-client-1234" not in result.stdout
    assert "https://api.example.com" not in result.stdout


def test_redact_value_short_string() -> None:
    """Test that short values (<=4 chars) are fully redacted."""
    assert _redact_value("ab") == "**"
    assert _redact_value("abcd") == "****"
    assert _redact_value("") == ""


def test_check_profile_missing_api_url() -> None:
    """Test _check_profile returns error when api_url is missing."""
    reason, output = _check_profile({"service_token": "tok"})
    assert reason == "'api_url' is not configured."
    assert output is None


def test_check_profile_missing_auth_method() -> None:
    """Test _check_profile returns error when no auth method is configured."""
    reason, output = _check_profile({"api_url": "http://api.test"})
    assert reason is not None
    assert (
        "one of service_token or (auth_issuer and auth_client_id and"
        " auth_audience) needs to be configured." == reason
    )
    assert output is None


def test_run_config_check_none_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _run_config_check raises when output is None."""
    from unittest.mock import MagicMock
    import orcheo_sdk.cli.config_command as cc

    state = MagicMock()
    state.human = True

    profiles: dict[str, dict[str, str]] = {
        "bad": {"api_url": "http://a.test"},
    }

    def _fake_check(
        profile_data: dict[str, object],
    ) -> tuple[str | None, dict[str, str] | None]:
        return None, None

    monkeypatch.setattr(cc, "_check_profile", _fake_check)

    with pytest.raises(CLIError, match="failed check"):
        _run_config_check(
            state=state,
            profile_names=["bad"],
            profiles=profiles,
        )


def test_run_config_check_reason_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _run_config_check raises with profile-specific reason."""
    from unittest.mock import MagicMock
    import orcheo_sdk.cli.config_command as cc

    state = MagicMock()
    state.human = True
    profiles: dict[str, dict[str, str]] = {
        "bad": {"api_url": "http://a.test"},
    }

    def _fake_check(
        profile_data: dict[str, object],
    ) -> tuple[str | None, dict[str, str] | None]:
        return "mocked failure", None

    monkeypatch.setattr(cc, "_check_profile", _fake_check)

    with pytest.raises(CLIError, match="Profile 'bad' failed check: mocked failure"):
        _run_config_check(
            state=state,
            profile_names=["bad"],
            profiles=profiles,
        )


def test_config_check_oauth_only_no_service_token(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Test --check with OAuth auth only covers 206->208, 247->249."""
    config_path = Path(env["ORCHEO_CONFIG_DIR"]) / CONFIG_FILENAME
    config_path.write_text(
        "\n".join(
            [
                "[profiles.default]",
                'api_url = "http://api.test"',
                'auth_issuer = "https://issuer.example.com"',
                'auth_client_id = "oauth-client-1234"',
                'auth_audience = "https://api.example.com"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    check_env = {**env, "ORCHEO_SERVICE_TOKEN": ""}
    result = runner.invoke(app, ["config", "--check"], env=check_env)

    assert result.exit_code == 0
    assert "api-url: http://api.test" in result.stdout
    assert "service-token" not in result.stdout
    assert "auth-issuer:" in result.stdout
    assert "auth-client-id:" in result.stdout
    assert "auth-audience:" in result.stdout


def test_config_check_machine_output(runner: CliRunner, env: dict[str, str]) -> None:
    """Test --check in machine mode outputs JSON."""
    import json

    config_path = Path(env["ORCHEO_CONFIG_DIR"]) / CONFIG_FILENAME
    config_path.write_text(
        "\n".join(
            [
                "[profiles.default]",
                'api_url = "http://api.test"',
                'service_token = "token-123456"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    machine_env = {**env, "ORCHEO_HUMAN": "", "ORCHEO_SERVICE_TOKEN": ""}
    result = runner.invoke(app, ["config", "--check"], env=machine_env)

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "success"
    assert "default" in data["profiles"]


def test_config_command_allows_setting_only_missing_oauth_field_on_existing_profile(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Allow completing legacy OAuth profiles by setting only missing fields."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / CONFIG_FILENAME
    config_path.write_text(
        "\n".join(
            [
                "[profiles.default]",
                'api_url = "http://api.test"',
                'auth_issuer = "https://auth.example.com"',
                'auth_client_id = "my-client"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    env = {
        "ORCHEO_CONFIG_DIR": str(config_dir),
        "ORCHEO_CACHE_DIR": str(tmp_path / "cache"),
        "ORCHEO_API_URL": "http://api.test",
        "ORCHEO_AUTH_ISSUER": "",
        "ORCHEO_AUTH_CLIENT_ID": "",
        "ORCHEO_AUTH_SCOPES": "",
        "ORCHEO_AUTH_AUDIENCE": "",
        "ORCHEO_AUTH_ORGANIZATION": "",
        "ORCHEO_HUMAN": "1",
        "NO_COLOR": "1",
    }

    result = runner.invoke(
        app,
        ["config", "--auth-audience", "https://api.example.com"],
        env=env,
    )

    assert result.exit_code == 0

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    profile = data["profiles"]["default"]
    assert profile["auth_issuer"] == "https://auth.example.com"
    assert profile["auth_client_id"] == "my-client"
    assert profile["auth_audience"] == "https://api.example.com"


def test_config_list_no_profiles(runner: CliRunner, env: dict[str, str]) -> None:
    """config list shows empty message when no profiles are written yet."""
    config_dir = Path(env["ORCHEO_CONFIG_DIR"])
    config_dir.mkdir(parents=True, exist_ok=True)
    result = runner.invoke(app, ["--human", "config", "list"], env=env)
    assert result.exit_code == 0
    assert "No profiles" in result.output


def test_config_list_shows_profiles(runner: CliRunner, env: dict[str, str]) -> None:
    """config list shows profile api_url and redacts the service token."""
    runner.invoke(app, ["config"], env=env)
    result = runner.invoke(app, ["--human", "config", "list"], env=env)
    assert result.exit_code == 0
    assert env["ORCHEO_API_URL"] in result.output
    assert "..." in result.output


def test_config_list_machine_mode_json(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """config list in machine mode outputs valid JSON with profiles key."""
    import json as _json

    runner.invoke(app, ["config"], env=machine_env)
    result = runner.invoke(app, ["config", "list"], env=machine_env)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    assert "profiles" in payload
    assert "default" in payload["profiles"]
    assert payload["profiles"]["default"]["api_url"] == machine_env["ORCHEO_API_URL"]


def test_config_list_does_not_run_configure_callback(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Invoking 'config list' must not trigger the configure write callback."""
    env_no_token = {**env}
    env_no_token.pop("ORCHEO_SERVICE_TOKEN", None)
    env_no_token.pop("ORCHEO_API_URL", None)
    config_dir = Path(env["ORCHEO_CONFIG_DIR"])
    config_dir.mkdir(parents=True, exist_ok=True)
    result = runner.invoke(app, ["--human", "config", "list"], env=env_no_token)
    assert result.exit_code == 0


def test_config_list_invalid_toml(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """config list raises CLIError when load_profiles raises TOMLDecodeError."""
    import tomllib as _tomllib
    from orcheo_sdk.cli import config_command
    from orcheo_sdk.cli.errors import CLIError

    try:
        _tomllib.loads("[[invalid")
    except _tomllib.TOMLDecodeError as exc:
        toml_error = exc

    def raise_toml_error(path: Path) -> dict:
        raise toml_error

    monkeypatch.setattr(config_command, "load_profiles", raise_toml_error)
    result = runner.invoke(app, ["--human", "config", "list"], env=env)
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "Invalid TOML" in str(result.exception)


def test_config_list_no_profiles_machine_mode(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """config list in machine mode returns JSON with empty profiles dict."""
    import json as _json

    result = runner.invoke(app, ["config", "list"], env=machine_env)
    assert result.exit_code == 0
    data = _json.loads(result.output)
    assert data == {"profiles": {}}


def test_config_list_shows_oauth_only_profile(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """config list in human mode shows oauth fields and omits service_token when absent."""  # noqa: E501
    config_path = Path(env["ORCHEO_CONFIG_DIR"]) / CONFIG_FILENAME
    config_path.write_text(
        "\n".join(
            [
                "[profiles.default]",
                'api_url = "http://api.test"',
                'auth_issuer = "https://issuer.example.com"',
                'auth_client_id = "my-client"',
                'auth_audience = "https://api.example.com"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    no_token_env = {**env, "ORCHEO_SERVICE_TOKEN": ""}
    result = runner.invoke(app, ["--human", "config", "list"], env=no_token_env)
    assert result.exit_code == 0
    assert "auth-issuer" in result.output
    assert "auth-client-id" in result.output
    assert "auth-audience" in result.output
    assert "service-token" not in result.output
