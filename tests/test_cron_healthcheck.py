"""Standalone scheduler health probes check recent successful progress."""

from __future__ import annotations
from pathlib import Path
from unittest.mock import Mock
import pytest
from orcheo import cron_healthcheck


@pytest.mark.parametrize("interval, lifetime", [(1, 30), (60, 180), (120, 360)])
def test_heartbeat_expires_after_dispatch_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interval: float, lifetime: float
) -> None:
    now = 1000.0
    monkeypatch.setattr(cron_healthcheck.time, "monotonic", lambda: now)
    path = tmp_path / "heartbeat"
    assert not cron_healthcheck.scheduler_is_healthy(path)
    cron_healthcheck.record_heartbeat(path, interval)
    assert cron_healthcheck.scheduler_is_healthy(path)
    now += lifetime + 1
    assert not cron_healthcheck.scheduler_is_healthy(path)
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "payload", ["", "bad", "1 invalid", "1 inf", "1 nan", "0 99999"]
)
def test_invalid_heartbeat_is_unhealthy(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "heartbeat"
    path.write_text(payload)
    assert not cron_healthcheck.scheduler_is_healthy(path)


def test_dead_scheduler_is_unhealthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "heartbeat"
    cron_healthcheck.record_heartbeat(path, 60)
    monkeypatch.setattr(
        cron_healthcheck.os, "kill", Mock(side_effect=ProcessLookupError)
    )
    assert not cron_healthcheck.scheduler_is_healthy(path)


def test_probe_requires_progress_and_redis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "heartbeat"
    monkeypatch.setenv("ORCHEO_CRON_HEALTH_FILE", str(path))
    broker = Mock(return_value=0)
    monkeypatch.setattr(cron_healthcheck, "broker_healthcheck", broker)
    assert cron_healthcheck.main() == 1
    broker.assert_not_called()
    cron_healthcheck.record_heartbeat(path, 60)
    assert cron_healthcheck.main() == 0
    broker.return_value = 1
    assert cron_healthcheck.main() == 1
