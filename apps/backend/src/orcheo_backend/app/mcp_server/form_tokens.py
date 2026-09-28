"""Signed, short-lived tokens that tie credential writes to the MCP App form.

``open_credential_form`` issues a token in the tool result's ``_meta``, which
MCP hosts hand to the embedded app but never to the model. ``save_credential``
requires it, so the agent can open the form but cannot write credentials
itself, and the secret travels only from the form to the server.
"""

from __future__ import annotations
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from fastmcp.exceptions import ToolError
from orcheo_backend.app.authentication import load_auth_settings


FORM_TOKEN_META_KEY = "orcheo/formToken"
FORM_TOKEN_TTL_SECONDS = 15 * 60

# Used only when no JWT secret is configured (auth disabled, single process).
_PROCESS_KEY = secrets.token_bytes(32)


@dataclass(frozen=True)
class CredentialForm:
    """What a form token allows its holder to save."""

    subject: str
    workspace: str | None
    credential_id: str | None


def _key() -> bytes:
    secret = load_auth_settings().jwt_secret
    if secret:
        return hashlib.sha256(f"mcp-credential-form:{secret}".encode()).digest()
    return _PROCESS_KEY


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_form_token(form: CredentialForm, *, now: float | None = None) -> str:
    """Return a token authorizing one credential form submission."""
    payload: dict[str, Any] = {
        "sub": form.subject,
        "ws": form.workspace,
        "cid": form.credential_id,
        "exp": int((now if now is not None else time.time()) + FORM_TOKEN_TTL_SECONDS),
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_form_token(
    token: str, *, subject: str, now: float | None = None
) -> CredentialForm:
    """Return the form a token authorizes for ``subject``.

    Raises:
        ToolError: If the token is malformed, forged, expired or issued to a
            different caller.
    """
    try:
        body, signature = token.split(".")
        expected = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("bad signature")
        payload = json.loads(_unb64(body))
    except ValueError as exc:
        raise ToolError("This credential form is invalid. Open a new one.") from exc
    if payload["exp"] < (now if now is not None else time.time()):
        raise ToolError("This credential form has expired. Open a new one.")
    if payload["sub"] != subject:
        raise ToolError("This credential form was opened by a different user.")
    return CredentialForm(
        subject=payload["sub"],
        workspace=payload["ws"],
        credential_id=payload["cid"],
    )


__all__ = [
    "FORM_TOKEN_META_KEY",
    "CredentialForm",
    "issue_form_token",
    "verify_form_token",
]
