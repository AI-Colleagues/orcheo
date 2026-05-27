"""Utility helpers for manipulating ChatKit message threads."""

from __future__ import annotations
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from chatkit.errors import CustomStreamError
from chatkit.store import Store
from chatkit.types import (
    AssistantMessageContent,
    AssistantMessageItem,
    AttachmentBase,
    InferenceOptions,
    ThreadMetadata,
    UserMessageItem,
)
from orcheo_backend.app.chatkit.context import ChatKitRequestContext
from orcheo_backend.app.chatkit.message_utils import (
    collect_text_from_assistant_content,
    collect_text_from_user_content,
)
from orcheo_backend.app.repository import WorkflowRun


logger = logging.getLogger(__name__)


_UNSET = object()


async def build_history(
    store: Store[ChatKitRequestContext],
    thread: ThreadMetadata,
    context: ChatKitRequestContext,
) -> list[dict[str, str]]:
    """Return a ChatML-style history from stored thread items."""
    history: list[dict[str, str]] = []
    page = await store.load_thread_items(
        thread.id,
        after=None,
        limit=200,
        order="asc",
        context=context,
    )
    for item in page.data:
        if isinstance(item, UserMessageItem):
            history.append(
                {
                    "role": "user",
                    "content": collect_text_from_user_content(item.content),
                }
            )
        elif isinstance(item, AssistantMessageItem):
            history.append(
                {
                    "role": "assistant",
                    "content": collect_text_from_assistant_content(item.content),
                }
            )
    return history


def require_workflow_id(thread: ThreadMetadata) -> UUID:
    """Return the workflow identifier stored on ``thread``."""
    workflow_value = thread.metadata.get("workflow_id")
    if not workflow_value:
        raise CustomStreamError(
            "No workflow has been associated with this conversation.",
            allow_retry=False,
        )
    try:
        return UUID(str(workflow_value))
    except ValueError as exc:
        raise CustomStreamError(
            "The configured workflow identifier is invalid.",
            allow_retry=False,
        ) from exc


async def resolve_user_item(
    store: Store[ChatKitRequestContext],
    thread: ThreadMetadata,
    item: UserMessageItem | None,
    context: ChatKitRequestContext,
) -> UserMessageItem:
    """Return the most recent user message for the thread."""
    if item is not None:
        return item

    page = await store.load_thread_items(
        thread.id, after=None, limit=1, order="desc", context=context
    )
    for candidate in page.data:
        if isinstance(candidate, UserMessageItem):
            return candidate

    raise CustomStreamError(
        "Unable to locate the user message for this request.",
        allow_retry=False,
    )


def build_inputs_payload(
    thread: ThreadMetadata,
    message_text: str,
    history: list[dict[str, str]],
    user_item: UserMessageItem | None = None,
    *,
    selected_model: str | None | object = _UNSET,
) -> dict[str, Any]:
    """Construct the workflow input payload with optional file attachments.

    Args:
        thread: The ChatKit thread metadata
        message_text: The user's message text
        history: Conversation history
        user_item: The user message item containing potential attachments
        selected_model: Validated ChatKit model override for this request

    Returns:
        Input payload for the workflow, including documents if attachments present
    """
    payload: dict[str, Any] = {
        "message": message_text,
        "history": history,
        "thread_id": thread.id,
        "session_id": thread.id,
        "metadata": dict(thread.metadata),
    }
    resolved_model = selected_model
    if resolved_model is _UNSET:
        resolved_model = _selected_model_from_user_item(user_item)
    if resolved_model:
        payload["model"] = resolved_model

    # Convert attachments into opaque attachment_id document references.
    # No filesystem paths are used; bytes are resolved by DocumentLoaderNode
    # through the injected attachment resolver.
    if user_item is not None and hasattr(user_item, "attachments"):
        attachments = getattr(user_item, "attachments", None)
        if attachments and isinstance(attachments, list) and len(attachments) > 0:
            documents: list[dict[str, Any]] = []
            for attachment in attachments:
                if isinstance(attachment, dict):
                    attachment_id = attachment.get("id") or attachment.get(
                        "file_id", ""
                    )
                    if attachment_id:
                        doc: dict[str, Any] = {
                            "attachment_id": attachment_id,
                            "source": attachment.get("filename")
                            or attachment.get("name", "unknown"),
                            "metadata": {
                                "mime_type": attachment.get("content_type")
                                or attachment.get("mime_type", "text/plain"),
                                "size": attachment.get("size", 0),
                            },
                        }
                        documents.append(doc)
                elif isinstance(attachment, AttachmentBase):
                    doc = {
                        "attachment_id": attachment.id,
                        "source": attachment.name,
                        "metadata": {
                            "mime_type": attachment.mime_type,
                        },
                    }
                    documents.append(doc)

            if documents:
                payload["documents"] = documents

    return payload


def sync_thread_inference_metadata(
    thread: ThreadMetadata,
    user_item: UserMessageItem | None = None,
    *,
    selected_model: str | None | object = _UNSET,
) -> None:
    """Persist selected inference options on thread metadata for follow-up actions."""
    resolved_model = selected_model
    if resolved_model is _UNSET:
        resolved_model = _selected_model_from_user_item(user_item)
    if not resolved_model:
        if "chatkit_model" not in thread.metadata:
            return
        thread.metadata = {
            key: value
            for key, value in thread.metadata.items()
            if key != "chatkit_model"
        }
        return
    thread.metadata = {
        **thread.metadata,
        "chatkit_model": resolved_model,
    }


def record_run_metadata(thread: ThreadMetadata, run: WorkflowRun | None) -> None:
    """Persist run identifiers on the thread metadata."""
    thread.metadata = {
        **thread.metadata,
        "last_run_at": datetime.now(UTC).isoformat(),
    }
    if "runs" in thread.metadata and isinstance(thread.metadata["runs"], list):
        runs_list = list(thread.metadata["runs"])
    else:
        runs_list = []

    if run is not None:
        runs_list.append(str(run.id))
        thread.metadata["last_run_id"] = str(run.id)

    if runs_list:
        thread.metadata["runs"] = runs_list[-20:]


def build_assistant_item(
    store: Store[ChatKitRequestContext],
    thread: ThreadMetadata,
    reply: str,
    context: ChatKitRequestContext,
) -> AssistantMessageItem:
    """Create a ChatKit assistant message item from the reply text."""
    return AssistantMessageItem(
        id=store.generate_item_id("message", thread, context),
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[AssistantMessageContent(text=reply)],
    )


__all__ = [
    "build_assistant_item",
    "build_history",
    "build_inputs_payload",
    "record_run_metadata",
    "require_workflow_id",
    "resolve_user_item",
    "sync_thread_inference_metadata",
]


def _selected_model_from_user_item(user_item: UserMessageItem | None) -> str | None:
    """Return the selected ChatKit model from a user message when present."""
    if user_item is None:
        return None
    inference_options = getattr(user_item, "inference_options", None)
    if not isinstance(inference_options, InferenceOptions):
        return None
    model = inference_options.model
    if not isinstance(model, str):
        return None
    normalized = model.strip()
    return normalized or None
