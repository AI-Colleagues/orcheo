"""Multi-tenancy configuration settings."""

from __future__ import annotations
from typing import cast
from pydantic import BaseModel, Field, field_validator
from orcheo.config.defaults import _DEFAULTS
from orcheo.tenancy.models import normalize_slug


__all__ = ["MultiTenancySettings"]


class MultiTenancySettings(BaseModel):
    """Runtime configuration for the multi-tenancy feature."""

    enabled: bool = Field(default=cast(bool, _DEFAULTS["MULTI_TENANCY_ENABLED"]))
    default_tenant_slug: str = Field(
        default=cast(str, _DEFAULTS["MULTI_TENANCY_DEFAULT_TENANT_SLUG"])
    )
    tenant_header: str = Field(
        default=cast(str, _DEFAULTS["MULTI_TENANCY_TENANT_HEADER"])
    )

    @field_validator("enabled", mode="before")
    @classmethod
    def _coerce_enabled(cls, value: object) -> bool:
        if value is None:
            return cast(bool, _DEFAULTS["MULTI_TENANCY_ENABLED"])
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off", ""}:
                return False
        return bool(value)

    @field_validator("default_tenant_slug", mode="before")
    @classmethod
    def _coerce_slug(cls, value: object) -> str:
        if value is None or value == "":
            return cast(str, _DEFAULTS["MULTI_TENANCY_DEFAULT_TENANT_SLUG"])
        return normalize_slug(str(value))

    @field_validator("tenant_header", mode="before")
    @classmethod
    def _coerce_header(cls, value: object) -> str:
        if value is None or value == "":
            return cast(str, _DEFAULTS["MULTI_TENANCY_TENANT_HEADER"])
        candidate = str(value).strip()
        if not candidate:
            msg = "Tenant header must not be empty."
            raise ValueError(msg)
        return candidate
