"""Restricted-mode guard against local (stdio) MCP servers.

A restricted-mode workflow is untrusted, so an ``mcp_servers`` connection using
the ``stdio`` transport — which launches a subprocess on the worker — must be
refused before the MCP client spawns anything. Remote transports stay allowed.
"""

from __future__ import annotations
from collections.abc import Generator
import pytest
from dynaconf import Dynaconf
from orcheo.config import loader as config_loader
from orcheo.nodes.ai.mcp_guard import (
    RestrictedModeMcpError,
    reject_local_mcp_servers_in_restricted_mode,
)


def _no_dotenv_loader() -> Dynaconf:
    """Build a Dynaconf loader that ignores .env files for deterministic tests."""
    return Dynaconf(
        envvar_prefix="ORCHEO", settings_files=[], load_dotenv=False, environments=False
    )


@pytest.fixture()
def isolated_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Isolate definition-mode settings from .env and refresh after the test."""
    monkeypatch.setattr(config_loader, "_build_loader", _no_dotenv_loader)
    monkeypatch.delenv("ORCHEO_WORKFLOW_DEFINITION_MODE", raising=False)
    yield
    monkeypatch.delenv("ORCHEO_WORKFLOW_DEFINITION_MODE", raising=False)
    config_loader.get_settings(refresh=True)


def _set_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """Set the definition mode env var and refresh the cached settings."""
    monkeypatch.setenv("ORCHEO_WORKFLOW_DEFINITION_MODE", mode)
    config_loader.get_settings(refresh=True)


_STDIO_SERVER = {
    "pwn": {"transport": "stdio", "command": "sh", "args": ["-c", "id"]},
}
_COMMAND_ONLY_SERVER = {"pwn": {"command": "sh", "args": ["-c", "id"]}}
_REMOTE_SERVER = {
    "ok": {"transport": "streamable_http", "url": "https://example.com/mcp"},
}


def test_restricted_mode_blocks_stdio_mcp_server(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: None
) -> None:
    """A stdio MCP server (local subprocess) is refused in restricted mode."""
    _set_mode(monkeypatch, "restricted")

    with pytest.raises(RestrictedModeMcpError, match="stdio"):
        reject_local_mcp_servers_in_restricted_mode(_STDIO_SERVER, node_name="a")


def test_restricted_mode_blocks_bare_command_mcp_server(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: None
) -> None:
    """A connection carrying a bare ``command`` is treated as a local spawn."""
    _set_mode(monkeypatch, "restricted")

    with pytest.raises(RestrictedModeMcpError):
        reject_local_mcp_servers_in_restricted_mode(_COMMAND_ONLY_SERVER, node_name="a")


def test_restricted_mode_allows_remote_mcp_server(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: None
) -> None:
    """Remote transports do not spawn processes and remain allowed."""
    _set_mode(monkeypatch, "restricted")

    reject_local_mcp_servers_in_restricted_mode(_REMOTE_SERVER, node_name="a")


def test_unrestricted_mode_allows_stdio_mcp_server(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: None
) -> None:
    """Trusted (unrestricted) deployments keep local MCP servers working."""
    _set_mode(monkeypatch, "unrestricted")

    reject_local_mcp_servers_in_restricted_mode(_STDIO_SERVER, node_name="a")


def test_empty_servers_are_noop(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: None
) -> None:
    """No configured servers is always a no-op, even in restricted mode."""
    _set_mode(monkeypatch, "restricted")

    reject_local_mcp_servers_in_restricted_mode({}, node_name="a")
    reject_local_mcp_servers_in_restricted_mode(None, node_name="a")
