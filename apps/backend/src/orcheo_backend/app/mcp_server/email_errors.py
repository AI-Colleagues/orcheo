"""Errors returned by the Orcheo MCP email client."""

from __future__ import annotations
from typing import Any


class ServiceError(Exception):
    """A safe, user-facing email operation failure."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize a safe error and its stable API details."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


__all__ = ["ServiceError"]
