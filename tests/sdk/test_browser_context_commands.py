"""Tests for browser context CLI commands."""

from __future__ import annotations
import json
import threading
from datetime import UTC, datetime
from http.server import HTTPServer
from unittest.mock import patch
import httpx
import pytest
from typer.testing import CliRunner
from orcheo_sdk.cli.browser_context.server import create_request_handler
from orcheo_sdk.cli.browser_context.store import BrowserContextStore
from orcheo_sdk.cli.main import app


def _ts() -> str:
    """Return a fresh ISO 8601 timestamp string."""
    return datetime.now(UTC).isoformat()


@pytest.fixture()
def _context_server() -> tuple[int, HTTPServer]:
    """Start a context server on a random port."""
    store = BrowserContextStore()
    handler = create_request_handler(store)
    server = HTTPServer(("localhost", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port, server
    server.shutdown()


def test_context_no_server(runner: CliRunner, env: dict[str, str]) -> None:
    """orcheo context fails gracefully when server is not running."""
    result = runner.invoke(app, ["context", "--port", "19999"], env=env)
    assert result.exit_code == 1
    assert (
        "browser-aware" in result.stdout.lower() or "connect" in result.stdout.lower()
    )


def test_context_no_sessions(
    runner: CliRunner, env: dict[str, str], _context_server: tuple[int, HTTPServer]
) -> None:
    """orcheo context shows warning when no sessions exist."""
    port, _ = _context_server
    result = runner.invoke(app, ["context", "--port", str(port)], env=env)
    assert result.exit_code == 0
    assert "no active" in result.stdout.lower()


def test_context_with_session(
    runner: CliRunner, env: dict[str, str], _context_server: tuple[int, HTTPServer]
) -> None:
    """orcheo context shows active context after posting a session."""
    port, _ = _context_server
    httpx.post(
        f"http://localhost:{port}/context",
        json={
            "session_id": "tab-1",
            "page": "workflow",
            "workflow_id": "wf-abc",
            "workflow_name": "My Flow",
            "focused": True,
            "timestamp": _ts(),
        },
    )
    result = runner.invoke(app, ["context", "--port", str(port)], env=env)
    assert result.exit_code == 0
    assert "wf-abc" in result.stdout


def test_context_sessions_command(
    runner: CliRunner, env: dict[str, str], _context_server: tuple[int, HTTPServer]
) -> None:
    """orcheo context sessions lists all sessions."""
    port, _ = _context_server
    for i in range(2):
        httpx.post(
            f"http://localhost:{port}/context",
            json={
                "session_id": f"tab-{i}",
                "page": "workflow",
                "workflow_id": f"wf-{i}",
                "workflow_name": f"Flow {i}",
                "focused": i == 0,
                "timestamp": _ts(),
            },
        )
    result = runner.invoke(app, ["context", "sessions", "--port", str(port)], env=env)
    assert result.exit_code == 0
    assert "tab-0" in result.stdout
    assert "tab-1" in result.stdout


def test_context_machine_output(
    runner: CliRunner,
    machine_env: dict[str, str],
    _context_server: tuple[int, HTTPServer],
) -> None:
    """orcheo context outputs JSON in machine mode."""
    port, _ = _context_server
    httpx.post(
        f"http://localhost:{port}/context",
        json={
            "session_id": "tab-1",
            "page": "workflow",
            "workflow_id": "wf-abc",
            "workflow_name": "My Flow",
            "focused": True,
            "timestamp": _ts(),
        },
    )
    result = runner.invoke(app, ["context", "--port", str(port)], env=machine_env)
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["session_id"] == "tab-1"


def test_context_sessions_no_server(runner: CliRunner, env: dict[str, str]) -> None:
    """orcheo context sessions fails gracefully when server is not running."""
    result = runner.invoke(app, ["context", "sessions", "--port", "19999"], env=env)
    assert result.exit_code == 1


def test_context_sessions_empty(
    runner: CliRunner, env: dict[str, str], _context_server: tuple[int, HTTPServer]
) -> None:
    """orcheo context sessions shows message when no sessions exist."""
    port, _ = _context_server
    result = runner.invoke(app, ["context", "sessions", "--port", str(port)], env=env)
    assert result.exit_code == 0
    assert "no active" in result.stdout.lower()


def test_browser_aware_starts_server(runner: CliRunner, env: dict[str, str]) -> None:
    """orcheo browser-aware invokes run_server with correct arguments."""
    with patch("orcheo_sdk.cli.browser_context.server.run_server") as mock_run_server:
        result = runner.invoke(app, ["browser-aware", "--port", "4444"], env=env)
    assert result.exit_code == 0
    mock_run_server.assert_called_once_with(host="localhost", port=4444)
    assert "4444" in result.stdout


def test_context_no_server_machine_mode(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """orcheo context outputs JSON error in machine mode when server not running."""
    result = runner.invoke(app, ["context", "--port", "19999"], env=machine_env)
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert "error" in data


def test_context_no_sessions_machine_mode(
    runner: CliRunner,
    machine_env: dict[str, str],
    _context_server: tuple[int, HTTPServer],
) -> None:
    """orcheo context outputs JSON warning in machine mode when no sessions exist."""
    port, _ = _context_server
    result = runner.invoke(app, ["context", "--port", str(port)], env=machine_env)
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "warning" in data
    assert data["total_sessions"] == 0


def test_context_sessions_no_server_machine_mode(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """orcheo context sessions outputs JSON error in machine mode when no server."""
    result = runner.invoke(
        app, ["context", "sessions", "--port", "19999"], env=machine_env
    )
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert "error" in data


def test_context_sessions_empty_machine_mode(
    runner: CliRunner,
    machine_env: dict[str, str],
    _context_server: tuple[int, HTTPServer],
) -> None:
    """orcheo context sessions outputs empty JSON list in machine mode."""
    port, _ = _context_server
    result = runner.invoke(
        app, ["context", "sessions", "--port", str(port)], env=machine_env
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data == []


def test_context_sessions_machine_output(
    runner: CliRunner,
    machine_env: dict[str, str],
    _context_server: tuple[int, HTTPServer],
) -> None:
    """orcheo context sessions outputs JSON list of sessions in machine mode."""
    port, _ = _context_server
    httpx.post(
        f"http://localhost:{port}/context",
        json={
            "session_id": "tab-machine",
            "page": "workflow",
            "workflow_id": "wf-1",
            "workflow_name": "Machine Flow",
            "focused": True,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )
    result = runner.invoke(
        app, ["context", "sessions", "--port", str(port)], env=machine_env
    )
    assert result.exit_code == 0
    sessions = json.loads(result.stdout)
    assert isinstance(sessions, list)
    assert any(s["session_id"] == "tab-machine" for s in sessions)
