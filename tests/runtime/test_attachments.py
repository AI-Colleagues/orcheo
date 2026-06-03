"""Tests for runtime attachment protocol helpers."""

from __future__ import annotations

import pytest
from orcheo.runtime.attachments import AttachmentResolver, AttachmentUploader


class _Scope:
    workspace_id = "ws_1"
    workflow_id = "wf_1"
    thread_id = "thr_1"
    upload_session_id = "ups_1"


@pytest.mark.asyncio
async def test_attachment_protocol_methods_are_callable() -> None:
    dummy = object()
    scope = _Scope()

    assert await AttachmentResolver.load_attachment_bytes(dummy, "atc_1", scope) is None
    assert (
        await AttachmentUploader.upload_attachment(
            dummy, b"content", "file.txt", "text/plain"
        )
        is None
    )
