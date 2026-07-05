"""Best-effort parsing of qualitative source files into records."""

# ruff: noqa: C901, D102, PLR0911

from __future__ import annotations
import csv
import json
from collections.abc import Mapping
from typing import Any
from orcheo.graph.state import State
from orcheo.nodes.qualitative.models import ParsedRecord


def pick_text_field(fieldnames: list[str]) -> str:
    """Pick the open-ended text column from CSV headers."""
    lowered = {f.lower(): f for f in fieldnames}
    for preferred in ("response", "answer", "text", "feedback", "comment"):
        if preferred in lowered:
            return lowered[preferred]
    return fieldnames[1] if len(fieldnames) > 1 else fieldnames[0]


def pick_id_field(fieldnames: list[str]) -> str | None:
    """Pick the record-id column from CSV headers, if one is named."""
    lowered = {f.lower(): f for f in fieldnames}
    for candidate in ("respondent_id", "record_id", "id"):
        if candidate in lowered:
            return lowered[candidate]
    return None


class SourceParser:
    """Stateless parser for qualitative source files.

    ``flexible_columns`` toggles open-ended column detection for survey CSVs: a
    strict parser (the default) requires an exact ``text`` column, while the
    flexible parser falls back to ``response``/``answer``/``feedback`` columns
    or the second column.
    """

    @staticmethod
    def sniff_type(filename: str | None, content: str) -> str:
        if filename:
            lower = filename.lower()
            if "ticket" in lower or "support" in lower:
                return "support_tickets"
            if "chat" in lower or "conversation" in lower:
                return "chat_log"
            if lower.endswith((".csv", ".tsv")):
                return "survey_csv"
            if lower.endswith((".json", ".jsonl")):
                return "transcript"
            if lower.endswith((".txt", ".md", ".transcript")):
                return "transcript"

        head = content.lstrip()[:512]
        if head.startswith("{") or head.startswith("["):
            lowered = head.lower()
            if "messages" in lowered or "conversation" in lowered:
                return "chat_log"
            if "ticket" in lowered or "subject" in lowered:
                return "support_tickets"
            return "transcript"

        if "\n" in content and "," in content.splitlines()[0]:
            header = content.splitlines()[0].lower()
            if "ticket" in header or "subject" in header:
                return "support_tickets"
            return "survey_csv"
        return "transcript"

    @staticmethod
    def parse_survey_csv(
        content: str, *, flexible_columns: bool = False
    ) -> list[ParsedRecord]:
        sample = content[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(content.splitlines(keepends=True), dialect=dialect)
        fieldnames = [field.strip() for field in (reader.fieldnames or []) if field]
        if not fieldnames:
            return []
        if flexible_columns:
            text_field = pick_text_field(fieldnames)
            id_field = pick_id_field(fieldnames)
        else:
            if "text" not in fieldnames:
                return []
            text_field = "text"
            id_field = "id" if "id" in fieldnames else None
        records: list[ParsedRecord] = []
        for row_index, row in enumerate(reader, start=1):
            clean = {
                (key or "").strip(): (value or "").strip() for key, value in row.items()
            }
            text = clean.get(text_field, "").strip()
            if not text:
                continue
            record_id = clean.get(id_field or "", "").strip() or f"R{row_index:05d}"
            skip_fields = {text_field}
            if id_field is not None:
                skip_fields.add(id_field)
            metadata = {k: v for k, v in clean.items() if k not in skip_fields and v}
            records.append(
                ParsedRecord(
                    record_id=record_id,
                    source=f"survey:{text_field}",
                    speaker=None,
                    text=text,
                    metadata=metadata,
                )
            )
        return records

    @staticmethod
    def parse_transcript_json(content: str) -> list[ParsedRecord]:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []
        turns = data if isinstance(data, list) else data.get("turns", [])
        records: list[ParsedRecord] = []
        idx = 1
        for turn in turns:
            if not isinstance(turn, Mapping):
                idx += 1
                continue
            text = str(turn.get("text") or "").strip()
            if not text:
                idx += 1
                continue
            speaker = turn.get("speaker") or turn.get("participant")
            speaker_str = str(speaker) if speaker else None
            records.append(
                ParsedRecord(
                    record_id=f"L{idx:05d}",
                    source=f"transcript:{speaker_str or 'unknown'}",
                    speaker=speaker_str,
                    text=text,
                    metadata={
                        k: v
                        for k, v in turn.items()
                        if k not in {"text", "speaker", "participant"}
                    },
                )
            )
            idx += 1
        return records

    @staticmethod
    def parse_transcript_plain(content: str) -> list[ParsedRecord]:
        records: list[ParsedRecord] = []
        for idx, raw in enumerate(content.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            speaker: str | None
            if ":" in line:
                raw_speaker, _, raw_text = line.partition(":")
                speaker = raw_speaker.strip() or None
                text = raw_text.strip()
            else:
                speaker = None
                text = line
            if not text:
                continue
            records.append(
                ParsedRecord(
                    record_id=f"L{idx:05d}",
                    source=f"transcript:{speaker or 'unknown'}",
                    speaker=speaker,
                    text=text,
                    metadata={},
                )
            )
        return records

    @classmethod
    def parse_transcript(cls, content: str) -> list[ParsedRecord]:
        stripped = content.lstrip()
        if stripped.startswith("[") or stripped.startswith("{"):
            return cls.parse_transcript_json(content)
        return cls.parse_transcript_plain(content)

    @classmethod
    def parse_chat_log(cls, content: str) -> list[ParsedRecord]:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return cls.parse_transcript_plain(content)
        conversations = data if isinstance(data, list) else data.get("conversations")
        if conversations is None:
            conversations = [data]
        records: list[ParsedRecord] = []
        index = 1
        conv_idx = 1
        for conversation in conversations:
            if not isinstance(conversation, Mapping):
                conv_idx += 1
                continue
            messages = conversation.get("messages") or conversation.get("turns") or []
            conversation_id = (
                conversation.get("conversation_id")
                or conversation.get("id")
                or f"CHAT{conv_idx:04d}"
            )
            for message in messages:
                if not isinstance(message, Mapping):
                    continue
                text = str(message.get("text") or message.get("content") or "").strip()
                if not text:
                    continue
                speaker = (
                    message.get("speaker")
                    or message.get("role")
                    or message.get("sender")
                )
                speaker_str = str(speaker) if speaker else None
                metadata = {
                    k: v
                    for k, v in message.items()
                    if k not in {"text", "content", "speaker", "role", "sender"}
                }
                metadata["conversation_id"] = str(conversation_id)
                records.append(
                    ParsedRecord(
                        record_id=f"{conversation_id}:{index}",
                        source=f"chat_log:{speaker_str or 'unknown'}",
                        speaker=speaker_str,
                        text=text,
                        metadata=metadata,
                    )
                )
                index += 1
            conv_idx += 1
        return records

    @classmethod
    def parse_support_tickets(
        cls,
        content: str,
        filename: str | None = None,
        *,
        flexible_columns: bool = False,
    ) -> list[ParsedRecord]:
        stripped = content.lstrip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                return []
            tickets = data if isinstance(data, list) else data.get("tickets", [])
            records: list[ParsedRecord] = []
            idx = 1
            for ticket in tickets:
                if not isinstance(ticket, Mapping):
                    idx += 1
                    continue
                text = str(
                    ticket.get("text")
                    or ticket.get("description")
                    or ticket.get("body")
                    or ticket.get("message")
                    or ""
                ).strip()
                if not text:
                    idx += 1
                    continue
                ticket_id = str(
                    ticket.get("ticket_id") or ticket.get("id") or f"T{idx:05d}"
                )
                metadata = {
                    k: v
                    for k, v in ticket.items()
                    if k
                    not in {"text", "description", "body", "message", "ticket_id", "id"}
                }
                records.append(
                    ParsedRecord(
                        record_id=ticket_id,
                        source="support_ticket",
                        speaker=str(ticket.get("requester"))
                        if ticket.get("requester")
                        else None,
                        text=text,
                        metadata=metadata,
                    )
                )
                idx += 1
            return records

        rows = cls.parse_survey_csv(content, flexible_columns=flexible_columns)
        return [
            ParsedRecord(
                record_id=row.record_id,
                source=f"support_ticket:{filename or 'csv'}",
                speaker=row.speaker,
                text=row.text,
                metadata=row.metadata,
            )
            for row in rows
        ]

    @staticmethod
    def load_payload_content(source_payload: Mapping[str, Any]) -> str:
        content = source_payload.get("content")
        if isinstance(content, str) and content.strip():
            return content
        # ``storage_path`` can be supplied from workflow inputs, so do not open
        # raw paths here. Qualitative workflows should receive inline content
        # after the attachment/storage layer has resolved trusted uploads.
        if source_payload.get("storage_path"):
            return ""
        return ""

    @classmethod
    def parse_payload(
        cls,
        source_payload: Mapping[str, Any] | None,
        *,
        flexible_columns: bool = False,
    ) -> tuple[list[ParsedRecord], str]:
        if not source_payload:
            return [], "survey_csv"
        content = cls.load_payload_content(source_payload)
        if not isinstance(content, str) or not content.strip():
            return [], str(source_payload.get("source_type") or "survey_csv")
        declared = source_payload.get("source_type")
        supported_types = {"survey_csv", "transcript", "chat_log", "support_tickets"}
        if declared not in supported_types:
            declared = cls.sniff_type(source_payload.get("filename"), content)
        if declared not in supported_types:
            return [], str(declared or "unsupported")
        if declared == "survey_csv":
            return (
                cls.parse_survey_csv(content, flexible_columns=flexible_columns),
                "survey_csv",
            )
        if declared == "chat_log":
            return cls.parse_chat_log(content), "chat_log"
        if declared == "support_tickets":
            return (
                cls.parse_support_tickets(
                    content,
                    source_payload.get("filename"),
                    flexible_columns=flexible_columns,
                ),
                "support_tickets",
            )
        return cls.parse_transcript(content), "transcript"

    @staticmethod
    def normalise_payload(state: State) -> dict[str, Any] | None:
        inputs = state.get("inputs") or {}
        if isinstance(inputs, Mapping):
            documents = inputs.get("documents")
            if isinstance(documents, list) and documents:
                first = documents[0]
                if isinstance(first, Mapping):
                    content = first.get("content")
                    storage_path = first.get("storage_path")
                    if (isinstance(content, str) and content.strip()) or (
                        isinstance(storage_path, str) and storage_path.strip()
                    ):
                        return {
                            "source_type": first.get("source_type"),
                            "content": content if isinstance(content, str) else "",
                            "storage_path": storage_path,
                            "filename": first.get("filename")
                            or first.get("name")
                            or first.get("source"),
                        }
        return None


__all__ = ["SourceParser", "pick_id_field", "pick_text_field"]
