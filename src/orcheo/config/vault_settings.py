"""Vault-related configuration models."""

from __future__ import annotations
from typing import cast
from pydantic import BaseModel, Field, field_validator, model_validator
from orcheo.config.defaults import _DEFAULTS
from orcheo.config.types import VaultBackend


class VaultSettings(BaseModel):
    """Validated representation of secure storage configuration."""

    backend: VaultBackend = Field(
        default=cast(VaultBackend, _DEFAULTS["VAULT_BACKEND"])
    )
    encryption_key: str | None = None
    aws_region: str | None = None
    aws_kms_key_id: str | None = None
    token_ttl_seconds: int = Field(
        default=cast(int, _DEFAULTS["VAULT_TOKEN_TTL_SECONDS"]), gt=0
    )

    @field_validator("backend", mode="before")
    @classmethod
    def _coerce_backend(cls, value: object) -> VaultBackend:
        candidate = (
            cast(str, value).lower()
            if value is not None
            else cast(str, _DEFAULTS["VAULT_BACKEND"])
        )
        if candidate != "postgres":
            msg = "ORCHEO_VAULT_BACKEND must be 'postgres'."
            raise ValueError(msg)
        return cast(VaultBackend, candidate)

    @field_validator("encryption_key", "aws_region", "aws_kms_key_id")
    @classmethod
    def _coerce_optional_str(cls, value: object) -> str | None:
        if value is None:
            return None
        candidate = str(value)
        return candidate or None

    @field_validator("token_ttl_seconds", mode="before")
    @classmethod
    def _parse_token_ttl(cls, value: object) -> int:
        candidate_obj = (
            value if value is not None else _DEFAULTS["VAULT_TOKEN_TTL_SECONDS"]
        )
        candidate: int | str
        if isinstance(candidate_obj, int | str):
            candidate = candidate_obj
        else:
            candidate = str(candidate_obj)
        try:
            return int(candidate)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            msg = "ORCHEO_VAULT_TOKEN_TTL_SECONDS must be an integer."
            raise ValueError(msg) from exc

    @model_validator(mode="after")
    def _validate_backend_requirements(self) -> VaultSettings:
        if self.token_ttl_seconds <= 0:  # pragma: no cover - defensive
            msg = "ORCHEO_VAULT_TOKEN_TTL_SECONDS must be greater than zero."
            raise ValueError(msg)

        if self.backend == "postgres":
            if not self.encryption_key:
                msg = (
                    "ORCHEO_VAULT_ENCRYPTION_KEY must be set when using the postgres "
                    "vault backend."
                )
                raise ValueError(msg)
            self.aws_region = None
            self.aws_kms_key_id = None
        return self


__all__ = ["VaultSettings"]
