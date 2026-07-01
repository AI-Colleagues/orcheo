"""Exceptions raised while validating, compiling, or building the workflow IR."""

from __future__ import annotations


class IRError(ValueError):
    """Base class for IR-related failures."""


class IRValidationError(IRError):
    """Raised when an IR fails validation before build or run.

    Covers unsupported schema versions, unknown node types, duplicate ids,
    dangling edge endpoints, and otherwise malformed specs.
    """


class WorkflowValidationError(IRError):
    """Raised when a ``workflow.py`` fails restricted-mode validation.

    Carries the offending source line so callers can surface actionable,
    line-referenced messages. The string form is always prefixed with
    ``line <n>:`` when a line is known.
    """

    def __init__(self, message: str, *, lineno: int | None = None) -> None:
        """Store the message and optional source line.

        Args:
            message: Human-readable description of the violation.
            lineno: 1-based source line the violation occurred on, if known.
        """
        self.lineno = lineno
        self.raw_message = message
        if lineno is not None:
            super().__init__(f"line {lineno}: {message}")
        else:
            super().__init__(message)


__all__ = ["IRError", "IRValidationError", "WorkflowValidationError"]
