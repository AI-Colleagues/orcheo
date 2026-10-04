"""Signed, short-lived capabilities binding writes to private MCP App forms.

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
    purpose: str = "credential"
    workflow_id: str | None = None
    service_token_id: str | None = None
    workspace_id: str | None = None
    client_id: str | None = None


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
    """Return a token authorizing private form submissions until it expires.

    Tokens are stateless, so one can be replayed within its TTL; it only ever
    reaches the form, and binds the caller, workspace, purpose and target.
    """
    payload: dict[str, Any] = {
        "sub": form.subject,
        "ws": form.workspace,
        "cid": form.credential_id,
        "purpose": form.purpose,
        "wid": form.workflow_id,
        "tid": form.service_token_id,
        "workspace_id": form.workspace_id,
        "client_id": form.client_id,
        "exp": int((now if now is not None else time.time()) + FORM_TOKEN_TTL_SECONDS),
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_form_token(
    token: str,
    *,
    subject: str,
    now: float | None = None,
    purpose: str = "credential",
    client_id: str | None = None,
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
        expires_at = float(payload["exp"])
        form = CredentialForm(
            subject=payload["sub"],
            workspace=payload["ws"],
            credential_id=payload["cid"],
            purpose=payload.get("purpose", "credential"),
            workflow_id=payload.get("wid"),
            service_token_id=payload.get("tid"),
            workspace_id=payload.get("workspace_id"),
            client_id=payload.get("client_id"),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise ToolError("This form is invalid. Open a new one.") from exc
    if expires_at < (now if now is not None else time.time()):
        raise ToolError("This form has expired. Open a new one.")
    if form.subject != subject:
        raise ToolError("This form was opened by a different user.")
    if form.purpose != purpose:
        raise ToolError("This form token cannot be used for this operation.")
    if form.client_id != client_id:
        raise ToolError("This form was opened by a different application.")
    return form


__all__ = [
    "FORM_TOKEN_META_KEY",
    "CredentialForm",
    "issue_form_token",
    "verify_form_token",
]
