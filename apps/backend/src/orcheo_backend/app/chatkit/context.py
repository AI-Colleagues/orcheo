"""Shared request context objects for ChatKit integrations."""

from __future__ import annotations
from typing import Literal, TypedDict
from pydantic import BaseModel


class ChatKitRequestContext(TypedDict, total=False):
    """Context passed to store operations and response handlers."""

    chatkit_request: BaseModel
    workflow_id: str
    workspace_id: str | None
    actor: str
    auth_mode: Literal["jwt", "publish"]
    subject: str | None


__all__ = ["ChatKitRequestContext"]
