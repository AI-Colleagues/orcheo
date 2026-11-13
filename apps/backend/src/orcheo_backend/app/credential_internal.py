"""Credential providers reserved for internal email workflow state."""

INTERNAL_EMAIL_CREDENTIAL_PROVIDERS = frozenset(
    {"orcheo-email-schedule", "orcheo-email-idempotency"}
)

__all__ = ["INTERNAL_EMAIL_CREDENTIAL_PROVIDERS"]
