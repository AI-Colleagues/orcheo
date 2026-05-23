"""Shared fixtures for sandbox tests."""

from __future__ import annotations
import pytest
from orcheo.sandbox import manager as manager_module


@pytest.fixture(autouse=True)
def _stub_broker_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve the credential-broker host deterministically in tests.

    ``SandboxRuntimeManager`` resolves the broker hostname at spec-build time
    so child gVisor sandboxes can pin it into ``/etc/hosts`` (gVisor cannot
    reach Docker's embedded DNS at 127.0.0.11). Tests never have
    ``sandbox-runtime`` registered in real DNS, so we replace the lookup with
    a fixed loopback IP. Individual tests that exercise resolution failures
    re-override this with their own ``monkeypatch.setattr``.
    """
    monkeypatch.setattr(
        manager_module.socket,
        "gethostbyname",
        lambda host: "127.0.0.1",
    )
