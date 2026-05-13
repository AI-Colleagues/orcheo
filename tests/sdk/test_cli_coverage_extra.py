"""Extra coverage for CLI helpers and workspace state handling."""

from __future__ import annotations
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import httpx
import pytest
import respx
from orcheo_sdk.cli import http as http_mod
from orcheo_sdk.cli import main as main_mod
from orcheo_sdk.cli.config import CLISettings
from orcheo_sdk.cli.errors import APICallError
from orcheo_sdk.cli.http import ApiClient


def test_resolve_active_workspace_prefers_workspace_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The workspace file should be used when no env override is present."""

    workspace_file = tmp_path / "workspace"
    workspace_file.write_text("workspace-from-file\n", encoding="utf-8")
    monkeypatch.delenv("ORCHEO_WORKSPACE", raising=False)
    monkeypatch.setattr(http_mod, "_WORKSPACE_CONFIG_FILE", workspace_file)

    assert http_mod._resolve_active_workspace() == "workspace-from-file"


def test_resolve_active_workspace_handles_missing_or_blank_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Missing and blank workspace files should resolve to ``None``."""

    workspace_file = tmp_path / "workspace"
    monkeypatch.delenv("ORCHEO_WORKSPACE", raising=False)
    monkeypatch.setattr(http_mod, "_WORKSPACE_CONFIG_FILE", workspace_file)
    assert http_mod._resolve_active_workspace() is None

    workspace_file.write_text("   \n", encoding="utf-8")
    assert http_mod._resolve_active_workspace() is None


def test_api_client_patch_success_and_error_formatting() -> None:
    """PATCH requests should return JSON and surface structured errors."""

    client = ApiClient(base_url="http://test.com", token="token123")
    with respx.mock(assert_all_called=True) as router:
        router.patch("http://test.com/api/test").mock(
            return_value=httpx.Response(200, json={"updated": True})
        )
        assert client.patch("/api/test", json_body={"value": 1}) == {"updated": True}

    with respx.mock(assert_all_called=True) as router:
        router.patch("http://test.com/api/test").mock(
            return_value=httpx.Response(400, json={"detail": {"message": "Bad patch"}})
        )
        with pytest.raises(APICallError) as exc_info:
            client.patch("/api/test", json_body={"value": 1})

    assert "Bad patch" in str(exc_info.value)


def test_api_client_patch_no_content_and_transport_error() -> None:
    """PATCH requests should return None for 204 and surface transport failures."""

    client = ApiClient(base_url="http://test.com", token="token123")
    with respx.mock(assert_all_called=True) as router:
        router.patch("http://test.com/api/test").mock(return_value=httpx.Response(204))
        assert client.patch("/api/test", json_body={"value": 1}) is None

    with respx.mock(assert_all_called=True) as router:
        router.patch("http://test.com/api/test").mock(
            side_effect=httpx.ConnectError(
                "boom", request=httpx.Request("PATCH", "http://test.com/api/test")
            )
        )
        with pytest.raises(APICallError) as exc_info:
            client.patch("/api/test", json_body={"value": 1})

    assert "Failed to reach" in str(exc_info.value)


def test_main_blank_workspace_clears_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing a blank workspace should remove the env override."""

    monkeypatch.setenv("ORCHEO_WORKSPACE", "previous")
    monkeypatch.setattr(
        main_mod,
        "resolve_settings",
        lambda **kwargs: CLISettings(
            api_url="http://example.test",
            service_token=None,
            profile="default",
        ),
    )
    monkeypatch.setattr(main_mod, "CacheManager", lambda **kwargs: MagicMock())
    monkeypatch.setattr(main_mod, "ApiClient", lambda **kwargs: MagicMock())
    monkeypatch.setattr(main_mod, "maybe_print_update_notice", lambda **kwargs: None)

    ctx = SimpleNamespace(obj=None)
    main_mod.main(ctx, workspace="   ", no_update_check=True)

    assert "ORCHEO_WORKSPACE" not in os.environ


def test_extract_workspace_from_argv_handles_variants() -> None:
    """Workspace extraction should support both flag forms."""

    assert main_mod._extract_workspace_from_argv(["--workspace", "one"]) == "one"
    assert main_mod._extract_workspace_from_argv(["--workspace=two"]) == "two"
    assert main_mod._extract_workspace_from_argv(["--workspace", " "]) is None


def test_run_clears_blank_workspace_from_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run() should clear any pre-existing workspace when the flag is blank."""

    monkeypatch.setenv("ORCHEO_WORKSPACE", "old")
    monkeypatch.setattr(main_mod, "app", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_mod.sys, "argv", ["orcheo", "--workspace="])

    main_mod.run()

    assert "ORCHEO_WORKSPACE" not in os.environ
