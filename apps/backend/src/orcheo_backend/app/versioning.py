"""Version metadata helpers for system update checks."""

from __future__ import annotations
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any
import httpx
from packaging.version import InvalidVersion, Version


_PYPI_API_URL = "https://pypi.org/pypi"
_NPM_LATEST_URL = "https://registry.npmjs.org"
_DEFAULT_TIMEOUT_SECONDS = 3.0
_DEFAULT_RETRIES = 1
_UPDATE_CHECK_TTL_HOURS = 24


@dataclass(slots=True)
class _CacheEntry:
    payload: dict[str, Any]
    expires_at: datetime


_cache_lock = threading.Lock()
_cache_state: dict[str, _CacheEntry | None] = {"entry": None}


def _is_stable(version_value: str | None) -> bool:
    """Return whether a version string is a stable/public release."""
    if not version_value:
        return False
    try:
        parsed = Version(version_value)
    except InvalidVersion:
        return False
    return not (parsed.is_prerelease or parsed.is_devrelease or parsed.local)


def _safe_parse(version_value: str | None) -> Version | None:
    if not version_value:
        return None
    try:
        return Version(version_value)
    except InvalidVersion:
        return None


def _update_available(current: str | None, latest: str | None) -> bool:
    current_parsed = _safe_parse(current)
    latest_parsed = _safe_parse(latest)
    if current_parsed is None or latest_parsed is None:
        return False
    if not _is_stable(current):
        return False
    return latest_parsed > current_parsed


def _read_current_version(package_name: str) -> str | None:
    try:
        return package_version(package_name)
    except PackageNotFoundError:
        return None


def _read_studio_current_version() -> str | None:
    version_from_env = os.getenv("ORCHEO_STUDIO_VERSION")
    if not version_from_env:
        return None
    normalized = version_from_env.strip()
    return normalized or None


def _fetch_json(url: str, *, timeout: float, retries: int) -> dict[str, Any]:
    last_exc: httpx.HTTPError | ValueError | None = None
    for _ in range(max(1, retries + 1)):
        try:
            response = httpx.get(url, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return payload
            msg = f"Unexpected registry payload from {url}."
            raise ValueError(msg)
        except (httpx.HTTPError, ValueError) as exc:  # pragma: no cover
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def _fetch_pypi_latest(
    package_name: str, *, timeout: float, retries: int
) -> str | None:
    try:
        payload = _fetch_json(
            f"{_PYPI_API_URL}/{package_name}/json",
            timeout=timeout,
            retries=retries,
        )
    except (httpx.HTTPError, ValueError):
        return None

    info = payload.get("info")
    if isinstance(info, dict):
        version_value = info.get("version")
        if isinstance(version_value, str):
            return version_value
    return None


def _fetch_npm_latest(package_name: str, *, timeout: float, retries: int) -> str | None:
    try:
        payload = _fetch_json(
            f"{_NPM_LATEST_URL}/{package_name}/latest",
            timeout=timeout,
            retries=retries,
        )
    except (httpx.HTTPError, ValueError):
        return None

    version_value = payload.get("version")
    return version_value if isinstance(version_value, str) else None


def _read_timeout_seconds() -> float:
    raw = os.getenv("ORCHEO_UPDATE_CHECK_TIMEOUT_SECONDS")
    if not raw:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_TIMEOUT_SECONDS


def _read_retries() -> int:
    raw = os.getenv("ORCHEO_UPDATE_CHECK_RETRIES")
    if not raw:
        return _DEFAULT_RETRIES
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_RETRIES
    return value if value >= 0 else _DEFAULT_RETRIES


def _build_payload() -> dict[str, Any]:
    timeout = _read_timeout_seconds()
    retries = _read_retries()

    backend_current = _read_current_version("orcheo-backend")
    cli_current = _read_current_version("orcheo-sdk")
    studio_current = _read_studio_current_version()

    backend_latest = _fetch_pypi_latest(
        "orcheo-backend", timeout=timeout, retries=retries
    )
    cli_latest = _fetch_pypi_latest("orcheo-sdk", timeout=timeout, retries=retries)
    studio_latest = _fetch_npm_latest("orcheo-studio", timeout=timeout, retries=retries)

    checked_at = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")

    return {
        "backend": {
            "package": "orcheo-backend",
            "current_version": backend_current,
            "latest_version": backend_latest,
            "minimum_recommended_version": None,
            "release_notes_url": None,
            "update_available": _update_available(backend_current, backend_latest),
        },
        "cli": {
            "package": "orcheo-sdk",
            "current_version": cli_current,
            "latest_version": cli_latest,
            "minimum_recommended_version": None,
            "release_notes_url": None,
            "update_available": _update_available(cli_current, cli_latest),
        },
        "studio": {
            "package": "orcheo-studio",
            "current_version": studio_current,
            "latest_version": studio_latest,
            "minimum_recommended_version": None,
            "release_notes_url": None,
            "update_available": _update_available(studio_current, studio_latest),
        },
        "checked_at": checked_at,
    }


def get_system_info_payload() -> dict[str, Any]:
    """Return cached system version metadata payload."""
    now = datetime.now(tz=UTC)
    ttl = timedelta(hours=_UPDATE_CHECK_TTL_HOURS)

    with _cache_lock:
        entry = _cache_state["entry"]
        if entry is not None and now < entry.expires_at:
            return entry.payload

        payload = _build_payload()
        _cache_state["entry"] = _CacheEntry(payload=payload, expires_at=now + ttl)
        return payload


__all__ = ["get_system_info_payload"]
