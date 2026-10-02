"""Protect lean health probes and egress from application import overhead."""

from __future__ import annotations
import subprocess
import sys
import pytest


@pytest.mark.parametrize(
    "module", ["orcheo.broker_healthcheck", "orcheo.security.https_proxy_worker"]
)
def test_service_entrypoints_do_not_import_backend_or_nodes(module: str) -> None:
    """Import in a fresh interpreter so the test suite cannot hide eager imports."""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import importlib,sys; importlib.import_module(sys.argv[1]); "
            "assert 'orcheo_backend' not in sys.modules; "
            "assert 'orcheo.nodes' not in sys.modules",
            module,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
