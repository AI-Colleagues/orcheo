"""Structured, node-attributed errors raised by the CodeNode sandbox."""

from __future__ import annotations


class SandboxError(RuntimeError):
    """Base class for CodeNode sandbox failures, attributed to a node."""

    def __init__(self, message: str, *, node_id: str | None = None) -> None:
        """Store the message and the attributed node id."""
        self.node_id = node_id
        self.raw_message = message
        prefix = f"CodeNode '{node_id}': " if node_id else ""
        super().__init__(f"{prefix}{message}")


class SandboxRuntimeUnavailableError(SandboxError):
    """Raised when the MicroPython-WASM runtime cannot be loaded."""


class SandboxLimitError(SandboxError):
    """Raised when the sandbox exceeds a fuel, memory, or wall-clock limit."""


class SandboxExecutionError(SandboxError):
    """Raised when the CodeNode body raises an exception inside the sandbox."""

    def __init__(
        self,
        message: str,
        *,
        node_id: str | None = None,
        error_type: str | None = None,
    ) -> None:
        """Store the in-sandbox exception type alongside the message."""
        self.error_type = error_type
        super().__init__(message, node_id=node_id)


class SandboxOutputError(SandboxError):
    """Raised when sandbox output is missing, oversized, or not JSON-coercible."""


class SandboxMarshallingError(SandboxError):
    """Raised when sandbox inputs cannot be projected to JSON."""


__all__ = [
    "SandboxError",
    "SandboxExecutionError",
    "SandboxLimitError",
    "SandboxMarshallingError",
    "SandboxOutputError",
    "SandboxRuntimeUnavailableError",
]
