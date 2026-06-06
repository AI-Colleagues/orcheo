"""Schemas for system version metadata responses."""

from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel


class PackageVersionStatus(BaseModel):
    """Version metadata for one package/component."""

    package: str
    current_version: str | None = None
    latest_version: str | None = None
    minimum_recommended_version: str | None = None
    release_notes_url: str | None = None
    update_available: bool


class SystemInfoResponse(BaseModel):
    """Combined backend/CLI/canvas version metadata."""

    backend: PackageVersionStatus
    cli: PackageVersionStatus
    canvas: PackageVersionStatus
    checked_at: datetime


class SystemPluginStatus(BaseModel):
    """Plugin status as observed by the current backend process."""

    name: str
    enabled: bool
    status: str
    version: str
    exports: list[str]
    loaded: bool
    load_error: str | None = None


class SystemPluginsResponse(BaseModel):
    """Installed plugin inventory for the current backend process."""

    plugins: list[SystemPluginStatus]
