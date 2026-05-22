"""Sandbox runtime error hierarchy."""

from __future__ import annotations


class SandboxError(Exception):
    """Base error for the sandbox runtime."""


class SandboxAcquireError(SandboxError):
    """Raised when a sandbox cannot be acquired (pool exhausted, runtime down)."""


class SandboxNotFoundError(SandboxError):
    """Raised when an operation references an unknown lease or sandbox."""


class SandboxLifecycleError(SandboxError):
    """Raised when a lifecycle transition is invalid for the current state."""
