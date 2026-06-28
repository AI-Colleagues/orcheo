"""Tests for listing and showing CLI service tokens."""

from __future__ import annotations
import httpx
import respx
from typer.testing import CliRunner
from orcheo_sdk.cli.main import app


def test_token_list_empty(runner: CliRunner, env: dict[str, str]) -> None:
    """Test listing tokens when none exist."""
    response_data = {"tokens": [], "total": 0}

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "list"], env=env)

    assert result.exit_code == 0
    assert "Service Tokens (0 total)" in result.stdout
    assert "Name" in result.stdout
    assert "Scopes" in result.stdout


def test_token_list_with_tokens(runner: CliRunner, env: dict[str, str]) -> None:
    """Test listing tokens."""
    response_data = {
        "tokens": [
            {
                "identifier": "token-1",
                "scopes": ["read:workflows"],
                "workspace_ids": ["ws-1"],
                "issued_at": "2024-11-01T10:00:00Z",
                "expires_at": "2024-12-01T10:00:00Z",
            },
            {
                "identifier": "token-2",
                "scopes": [],
                "workspace_ids": [],
                "issued_at": "2024-11-02T10:00:00Z",
            },
        ],
        "total": 2,
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "list"], env=env)

    assert result.exit_code == 0
    assert "Service Tokens" in result.stdout
    assert "token-1" in result.stdout
    assert "token-2" in result.stdout
    assert "read:workflo" in result.stdout  # Table may truncate text


def test_token_list_with_revoked_token(runner: CliRunner, env: dict[str, str]) -> None:
    """Test listing tokens includes revoked status."""
    response_data = {
        "tokens": [
            {
                "identifier": "revoked-token",
                "scopes": [],
                "workspace_ids": [],
                "issued_at": "2024-11-01T10:00:00Z",
                "revoked_at": "2024-11-02T10:00:00Z",
            }
        ],
        "total": 1,
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "list"], env=env)

    assert result.exit_code == 0
    assert "revoked-token" in result.stdout
    assert "Revoked" in result.stdout


def test_token_list_with_rotated_token(runner: CliRunner, env: dict[str, str]) -> None:
    """Test listing tokens includes rotated status."""
    response_data = {
        "tokens": [
            {
                "identifier": "rotated-token",
                "scopes": [],
                "workspace_ids": [],
                "issued_at": "2024-11-01T10:00:00Z",
                "rotated_to": "new-token-id",
            }
        ],
        "total": 1,
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "list"], env=env)

    assert result.exit_code == 0
    assert "rotated-token" in result.stdout
    assert "Rotated" in result.stdout


def test_token_show_basic(runner: CliRunner, env: dict[str, str]) -> None:
    """Test showing token details."""
    response_data = {
        "identifier": "token-123",
        "scopes": ["read:workflows"],
        "workspace_ids": ["ws-1"],
        "issued_at": "2024-11-01T10:00:00Z",
        "expires_at": "2024-12-01T10:00:00Z",
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens/token-123").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "show", "token-123"], env=env)

    assert result.exit_code == 0
    assert "token-123" in result.stdout
    assert "read:workflows" in result.stdout
    assert "ws-1" in result.stdout


def test_token_show_with_name(runner: CliRunner, env: dict[str, str]) -> None:
    """Test showing token details includes the name row when present."""
    response_data = {
        "identifier": "token-123",
        "name": "My Token",
        "issued_at": "2024-11-01T10:00:00Z",
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens/token-123").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "show", "token-123"], env=env)

    assert result.exit_code == 0
    assert "My Token" in result.stdout


def test_token_show_without_scopes(runner: CliRunner, env: dict[str, str]) -> None:
    """Test showing token without scopes."""
    response_data = {
        "identifier": "token-123",
        "issued_at": "2024-11-01T10:00:00Z",
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens/token-123").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "show", "token-123"], env=env)

    assert result.exit_code == 0
    assert "token-123" in result.stdout


def test_token_show_without_expiration(runner: CliRunner, env: dict[str, str]) -> None:
    """Test showing token without expiration."""
    response_data = {
        "identifier": "token-123",
        "issued_at": "2024-11-01T10:00:00Z",
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens/token-123").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "show", "token-123"], env=env)

    assert result.exit_code == 0
    assert "Never" in result.stdout


def test_token_show_revoked_with_reason(runner: CliRunner, env: dict[str, str]) -> None:
    """Test showing revoked token with reason."""
    response_data = {
        "identifier": "token-123",
        "issued_at": "2024-11-01T10:00:00Z",
        "revoked_at": "2024-11-02T10:00:00Z",
        "revocation_reason": "Security breach",
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens/token-123").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "show", "token-123"], env=env)

    assert result.exit_code == 0
    assert "Security breach" in result.stdout


def test_token_show_revoked_without_reason(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Test showing revoked token without reason."""
    response_data = {
        "identifier": "token-123",
        "issued_at": "2024-11-01T10:00:00Z",
        "revoked_at": "2024-11-02T10:00:00Z",
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens/token-123").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "show", "token-123"], env=env)

    assert result.exit_code == 0
    assert "token-123" in result.stdout


def test_token_show_without_issued_at(runner: CliRunner, env: dict[str, str]) -> None:
    """Test showing token without issued_at field."""
    response_data = {
        "identifier": "token-123",
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens/token-123").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "show", "token-123"], env=env)

    assert result.exit_code == 0
    assert "token-123" in result.stdout


def test_token_show_rotated(runner: CliRunner, env: dict[str, str]) -> None:
    """Test showing rotated token."""
    response_data = {
        "identifier": "token-123",
        "issued_at": "2024-11-01T10:00:00Z",
        "rotated_to": "new-token-456",
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens/token-123").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "show", "token-123"], env=env)

    assert result.exit_code == 0
    assert "new-token-456" in result.stdout


def test_token_list_machine_mode(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """Machine mode outputs markdown table for token list."""

    response_data = {
        "tokens": [
            {
                "identifier": "token-1",
                "scopes": ["read:workflows"],
                "workspace_ids": [],
                "issued_at": "2024-11-01T10:00:00Z",
            }
        ],
        "total": 1,
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "list"], env=machine_env)

    assert result.exit_code == 0
    assert "|" in result.stdout
    assert "token-1" in result.stdout


def test_token_show_machine_mode(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """Machine mode outputs JSON for token show."""
    import json

    response_data = {
        "identifier": "token-123",
        "scopes": ["read:workflows"],
        "issued_at": "2024-11-01T10:00:00Z",
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/service-tokens/token-123").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "show", "token-123"], env=machine_env)

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["identifier"] == "token-123"
