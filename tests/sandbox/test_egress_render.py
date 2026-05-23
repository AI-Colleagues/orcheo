"""Tests for the egress proxy render CLI."""

from __future__ import annotations
import runpy
import sys
from pathlib import Path

from orcheo.sandbox.egress.proxy import EnvoyForwardProxyConfig
from orcheo.sandbox.egress import render


def test_read_env_value_and_render_main_with_env_file(
    tmp_path: Path,
) -> None:
    """The CLI reads dotenv values and renders the proxy config."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ORCHEO_SANDBOX_EGRESS_ALLOWED_HOSTS=api.openai.com, api.example.com\n",
        encoding="utf-8",
    )
    output = tmp_path / "out" / "envoy.yaml"

    result = render.main(["--env-file", str(env_file), "--output", str(output)])

    assert result == 0
    rendered = output.read_text(encoding="utf-8")
    assert "api.openai.com" in rendered
    assert "api.example.com" in rendered


def test_read_env_value_strips_quotes_and_ignores_missing_keys(
    tmp_path: Path,
) -> None:
    """The helper tolerates unrelated lines and quoted values."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OTHER=value\n"
        "ORCHEO_SANDBOX_EGRESS_ALLOWED_HOSTS='api.openai.com, api.example.com'\n",
        encoding="utf-8",
    )

    assert render._read_env_value(env_file, "MISSING") is None
    assert (
        render._read_env_value(env_file, "ORCHEO_SANDBOX_EGRESS_ALLOWED_HOSTS")
        == "api.openai.com, api.example.com"
    )


def test_render_module_entrypoint_uses_from_env(monkeypatch, tmp_path: Path) -> None:
    """Running the module as ``__main__`` still calls ``main()``."""

    class _FakeConfig:
        def render_yaml(self) -> str:
            return "rendered-from-env\n"

    monkeypatch.setattr(
        EnvoyForwardProxyConfig,
        "from_env",
        classmethod(lambda cls: _FakeConfig()),
    )
    output = tmp_path / "proxy.yaml"
    monkeypatch.setattr(sys, "argv", ["render", "-o", str(output)])
    module_name = "orcheo.sandbox.egress.render"
    loaded_module = sys.modules.pop(module_name, None)

    try:
        runpy.run_module(module_name, run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0
    finally:
        if loaded_module is not None:
            sys.modules[module_name] = loaded_module

    assert output.read_text(encoding="utf-8") == "rendered-from-env\n"
