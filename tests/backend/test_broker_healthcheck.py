"""Verify broker health-check exit statuses and connection cleanup."""

from __future__ import annotations
import runpy
from unittest.mock import Mock
import pytest
from orcheo import broker_healthcheck


@pytest.mark.parametrize(
    ("ping_result", "ping_error", "expected"),
    [(True, None, 0), (False, None, 1), (None, OSError("broker offline"), 1)],
)
@pytest.mark.parametrize("redis_url", [None, "redis://broker:6379/2"])
def test_healthcheck_reports_broker_status_and_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
    ping_result: bool | None,
    ping_error: Exception | None,
    expected: int,
    redis_url: str | None,
) -> None:
    """Both failed and successful pings release the connection."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    if redis_url is not None:
        monkeypatch.setenv("REDIS_URL", redis_url)
    redis = Mock()
    redis.ping.return_value = ping_result
    redis.ping.side_effect = ping_error
    from_url = Mock(return_value=redis)
    monkeypatch.setattr(broker_healthcheck.Redis, "from_url", from_url)

    assert broker_healthcheck.main() == expected

    from_url.assert_called_once_with(
        redis_url or "redis://localhost:6379/0",
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    redis.ping.assert_called_once_with()
    redis.close.assert_called_once_with()


@pytest.mark.parametrize("reachable", [True, False])
def test_healthcheck_module_exits_with_broker_status(
    monkeypatch: pytest.MonkeyPatch, reachable: bool
) -> None:
    """The container command exits with the health-check result."""
    redis = Mock()
    redis.ping.return_value = reachable
    monkeypatch.setattr(broker_healthcheck.Redis, "from_url", Mock(return_value=redis))

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(broker_healthcheck.__file__, run_name="__main__")

    assert exc_info.value.code == (0 if reachable else 1)
    redis.close.assert_called_once_with()
