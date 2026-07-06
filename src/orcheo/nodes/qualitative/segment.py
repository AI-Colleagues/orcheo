"""Segment in-state records into coding units.

Unlike :class:`~orcheo.nodes.qualitative.pipeline.IngestNode`, which parses
uploaded CSV/transcript attachments, this node turns structured records that
are already in workflow state (e.g. MongoDB documents or RSS items) into
:class:`~orcheo.nodes.qualitative.models.Unit` objects so the downstream
coding stages can be reused unchanged by headless/scheduled workflows.
"""

from __future__ import annotations
import html
import re
from collections.abc import Mapping
from typing import Any, Literal
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.qualitative.codebook import make_unit_id
from orcheo.nodes.qualitative.models import Unit
from orcheo.nodes.registry import NodeMetadata, registry


_LINE_BREAK_TAG_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_TAG_RE = re.compile(r"(?i)<\s*/(?:p|div|li|h[1-6]|blockquote)\s*>")
_TAG_RE = re.compile(r"<[^>]+>")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_INLINE_SPACE_RE = re.compile(r"[ \t]+")


def strip_html_tags(value: str) -> str:
    """Strip HTML tags from *value* while preserving paragraph breaks."""
    text = _LINE_BREAK_TAG_RE.sub("\n", value)
    text = _BLOCK_TAG_RE.sub("\n\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    lines = [_INLINE_SPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(lines).strip()


@registry.register(
    NodeMetadata(
        name="SegmentRecordsNode",
        description="Segment structured records into qualitative coding units",
        category="workflow",
    )
)
class SegmentRecordsNode(TaskNode):
    """Segment structured records into provenance-bearing coding units.

    Each record contributes one or more units: the configured text fields are
    concatenated, optionally stripped of HTML, and split at paragraph
    granularity. Units carry the record id, source, and selected metadata
    fields so evidence quotes can be traced back to the originating record.
    """

    records: Any | None = Field(
        default=None,
        description="List of record mappings to segment (template-resolved).",
    )
    text_fields: list[str] = Field(
        default_factory=lambda: ["text"],
        description="Record fields concatenated (in order) into the unit text.",
    )
    record_id_field: str = Field(
        default="_id",
        description="Record field used as the unit record_id.",
    )
    source_field: str | None = Field(
        default="source",
        description="Record field used as the unit source label.",
    )
    metadata_fields: list[str] = Field(
        default_factory=list,
        description="Record fields copied into each unit's metadata.",
    )
    strip_html: bool = Field(
        default=True,
        description="Whether to strip HTML tags/entities from the text fields.",
    )
    granularity: Literal["paragraph", "record"] = Field(
        default="paragraph",
        description=(
            "Unit granularity: 'paragraph' splits record text at blank lines, "
            "'record' keeps one unit per record."
        ),
    )

    def _record_text(self, record: Mapping[str, Any]) -> str:
        parts: list[str] = []
        for field in self.text_fields:
            value = record.get(field)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                parts.append(text)
        combined = "\n\n".join(parts)
        if self.strip_html:
            combined = strip_html_tags(combined)
        return combined.strip()

    def _segments(self, text: str) -> list[str]:
        if self.granularity == "record":
            return [text] if text else []
        return [part.strip() for part in _PARAGRAPH_RE.split(text) if part.strip()]

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Segment the configured records into ``Unit`` payloads."""
        del state, config
        raw_records = self.records if isinstance(self.records, list) else []
        units: list[Unit] = []
        record_ids: list[Any] = []
        record_count = 0
        unit_index = 1
        for index, record in enumerate(raw_records, start=1):
            if not isinstance(record, Mapping):
                continue
            record_count += 1
            record_id_value = record.get(self.record_id_field)
            if record_id_value is not None:
                record_ids.append(record_id_value)
            record_id = (
                str(record_id_value) if record_id_value is not None else f"R{index:05d}"
            )
            source_value = record.get(self.source_field) if self.source_field else None
            source = str(source_value) if source_value else "records"
            metadata = {
                field: record[field]
                for field in self.metadata_fields
                if record.get(field) is not None
            }
            for segment in self._segments(self._record_text(record)):
                units.append(
                    Unit(
                        unit_id=make_unit_id(unit_index),
                        record_id=record_id,
                        source=source,
                        text=segment,
                        original_text=segment,
                        metadata=dict(metadata),
                    )
                )
                unit_index += 1
        return {
            "units": [unit.model_dump(mode="json") for unit in units],
            "unit_count": len(units),
            "record_count": record_count,
            "record_ids": record_ids,
            "has_units": bool(units),
        }


__all__ = ["SegmentRecordsNode", "strip_html_tags"]
