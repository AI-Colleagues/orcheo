"""Helper utilities for JWT authentication."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from .context import RequestContext
from .utils import coerce_str_items, parse_timestamp


def claims_to_context(claims: Mapping[str, Any]) -> RequestContext:
    """Convert JWT claims into a normalized request context."""
    subject = str(claims.get("sub") or "")
    identity_type = _infer_identity_type(claims)
    scopes = frozenset(_extract_scopes(claims))
    workspaces = frozenset(_extract_workspace_ids(claims))
    token_id_source = (
        claims.get("jti") or claims.get("token_id") or subject or identity_type
    )
    token_id = str(token_id_source)
    issued_at = parse_timestamp(claims.get("iat"))
    expires_at = parse_timestamp(claims.get("exp"))
    return RequestContext(
        subject=subject or token_id,
        identity_type=identity_type,
        scopes=scopes,
        workspace_ids=workspaces,
        token_id=token_id,
        issued_at=issued_at,
        expires_at=expires_at,
        claims=dict(claims),
    )


def _claim_str(claims: Mapping[str, Any], key: str) -> str | None:
    value = claims.get(key)
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def extract_identity(claims: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(email, display_name)`` derived from first-party JWT claims.

    First-party access tokens carry the standard ``email`` / ``name`` claims
    directly, so they are read straight from the token (``preferred_username``
    / ``nickname`` / ``given_name`` / ``family_name`` are honoured as common
    fallbacks). Mirrors the Studio client's resolution order so the persisted
    identity matches what users see elsewhere.
    """
    email = _claim_str(claims, "email")
    name = (
        _claim_str(claims, "name")
        or _claim_str(claims, "preferred_username")
        or _claim_str(claims, "nickname")
    )
    if name is None:
        given = _claim_str(claims, "given_name")
        family = _claim_str(claims, "family_name")
        if given and family:
            name = f"{given} {family}"
        else:
            name = given or family
    return email, name


def extract_email_verified(claims: Mapping[str, Any]) -> bool:
    """Return True when the token asserts a verified email.

    Reads the standard ``email_verified`` claim carried by first-party tokens
    (set true only by a completed email challenge). Accepts the boolean ``True``
    or its string form.
    """
    if "email_verified" in claims:
        value = claims["email_verified"]
        return value is True or str(value).strip().lower() == "true"
    return False


def _parse_max_age(cache_control: str | None) -> int | None:
    if not cache_control:
        return None
    segments = [segment.strip() for segment in cache_control.split(",")]
    for segment in segments:
        if segment.lower().startswith("max-age"):
            try:
                _, value = segment.split("=", 1)
                return int(value.strip())
            except (ValueError, TypeError):  # pragma: no cover - defensive
                return None
    return None


def _infer_identity_type(claims: Mapping[str, Any]) -> str:
    for key in ("token_use", "type", "typ"):
        value = claims.get(key)
        if isinstance(value, str) and value:
            lowered = value.lower()
            if lowered in {"user", "service", "client"}:
                return "service" if lowered == "client" else lowered
    return "user"


def _extract_scopes(claims: Mapping[str, Any]) -> set[str]:
    candidates: list[Any] = []
    for key in ("scope", "scopes", "scp"):
        value = claims.get(key)
        if value is not None:
            candidates.append(value)
    nested = claims.get("orcheo")
    if isinstance(nested, Mapping):
        nested_value = nested.get("scopes")
        if nested_value is not None:
            candidates.append(nested_value)

    scopes: set[str] = set()
    for candidate in candidates:
        scopes.update(coerce_str_items(candidate))
    return scopes


def _extract_workspace_ids(claims: Mapping[str, Any]) -> set[str]:
    candidates: list[Any] = []
    for key in ("workspace_ids", "workspaces", "workspace", "workspace_id"):
        value = claims.get(key)
        if value is not None:
            candidates.append(value)
    nested = claims.get("orcheo")
    if isinstance(nested, Mapping):
        nested_value = nested.get("workspace_ids")
        if nested_value is not None:
            candidates.append(nested_value)

    workspaces: set[str] = set()
    for candidate in candidates:
        workspaces.update(
            workspace_id.strip().lower()
            for workspace_id in coerce_str_items(candidate)
            if workspace_id.strip()
        )
    return workspaces


__all__ = [
    "_extract_scopes",
    "_extract_workspace_ids",
    "_infer_identity_type",
    "_parse_max_age",
    "claims_to_context",
    "extract_email_verified",
    "extract_identity",
]
