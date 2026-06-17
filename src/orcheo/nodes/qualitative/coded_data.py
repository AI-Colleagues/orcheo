"""Parse and render the coded-data CSV exchanged between qualitative workflows."""

# ruff: noqa: C901, PLR0912, PLR0915

from __future__ import annotations
import csv
import json
from typing import Any
from orcheo.nodes.qualitative.codebook import normalise_codebook_ids
from orcheo.nodes.qualitative.models import (
    CodeAssignment,
    CodeAssignmentEntry,
    Codebook,
    Subtheme,
    Theme,
    Unit,
)
from orcheo.nodes.storage import build_csv


CODED_DATA_CSV_HEADERS = [
    "unit_id",
    "record_id",
    "source",
    "speaker",
    "text",
    "original_text",
    "metadata",
    "quality_flags",
    "assignment_index",
    "code_id",
    "theme_id",
    "theme_title",
    "code_title",
    "definition",
    "evidence",
    "confidence",
    "sentiment",
]


def build_coded_data_csv(
    units: list[Unit],
    assignments: list[CodeAssignment],
    codebook: Codebook,
) -> tuple[str, int]:
    """Render coded units and assignments to CSV (one row per code assignment).

    Returns ``(csv_text, assignment_count)``. Units with no assignments still
    get a row so the export is a complete audit trail.
    """
    assignments_by_unit = {a.unit_id: a for a in assignments}
    code_lookup: dict[str, dict[str, str]] = {}
    for theme in codebook.themes:
        for subtheme in theme.subthemes:
            code_lookup[subtheme.code_id] = {
                "theme_id": theme.theme_id,
                "theme_title": theme.title,
                "code_title": subtheme.title,
                "definition": subtheme.definition,
            }

    rows: list[list[str]] = []
    for unit in units:
        assignment = assignments_by_unit.get(unit.unit_id)
        base = [
            unit.unit_id,
            unit.record_id,
            unit.source,
            unit.speaker or "",
            unit.text,
            unit.original_text,
            json.dumps(unit.metadata, ensure_ascii=False),
            "; ".join(unit.quality_flags),
        ]
        if assignment is None or not assignment.assignments:
            rows.append([*base, "", "", "", "", "", "", "", "", ""])
            continue
        for index, entry in enumerate(assignment.assignments, start=1):
            code_info = code_lookup.get(entry.code_id, {})
            rows.append(
                [
                    *base,
                    str(index),
                    entry.code_id,
                    code_info.get("theme_id", ""),
                    code_info.get("theme_title", ""),
                    code_info.get("code_title", ""),
                    code_info.get("definition", ""),
                    entry.evidence,
                    f"{entry.confidence:.3f}",
                    entry.sentiment,
                ]
            )

    total_assignments = sum(len(a.assignments) for a in assignments)
    return build_csv(CODED_DATA_CSV_HEADERS, rows), total_assignments


def parse_coded_data_csv(
    content: str,
) -> tuple[list[Unit], list[CodeAssignment], Codebook | None] | None:
    """Parse a ``coded_data.csv`` export back into models.

    Returns ``(units, assignments, reconstructed_codebook)`` or ``None`` when
    the content is not a coded-data export. The codebook is rebuilt from the
    embedded ``theme_id``/``code_id``/``definition`` columns.
    """
    try:
        reader = csv.DictReader(content.splitlines(keepends=True))
    except Exception:  # noqa: BLE001
        return None
    fieldnames = reader.fieldnames or []
    if "unit_id" not in fieldnames or "text" not in fieldnames:
        return None
    coded_headers = {"code_id", "assignment_index", "theme_id", "theme_title"}
    if not coded_headers.intersection(fieldnames):
        return None

    units_by_id: dict[str, Unit] = {}
    order: list[str] = []
    assignments_by_unit: dict[str, list[CodeAssignmentEntry]] = {}
    themes: dict[str, Theme] = {}
    seen_codes: set[str] = set()

    for row in reader:
        unit_id = (row.get("unit_id") or "").strip()
        if not unit_id:
            continue
        if unit_id not in units_by_id:
            metadata: dict[str, Any] = {}
            raw_meta = (row.get("metadata") or "").strip()
            if raw_meta:
                try:
                    parsed_meta = json.loads(raw_meta)
                    if isinstance(parsed_meta, dict):
                        metadata = parsed_meta
                except Exception:  # noqa: BLE001
                    metadata = {}
            quality_flags = [
                flag.strip()
                for flag in (row.get("quality_flags") or "").split(";")
                if flag.strip()
            ]
            text = (row.get("text") or "").strip()
            units_by_id[unit_id] = Unit(
                unit_id=unit_id,
                record_id=(row.get("record_id") or "").strip() or unit_id,
                source=(row.get("source") or "").strip(),
                speaker=(row.get("speaker") or "").strip() or None,
                text=text,
                original_text=(row.get("original_text") or "").strip() or text,
                metadata=metadata,
                quality_flags=quality_flags,
            )
            order.append(unit_id)

        code_id = (row.get("code_id") or "").strip()
        if not code_id:
            continue
        try:
            confidence = float(row.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        sentiment = (row.get("sentiment") or "neutral").strip().lower()
        if sentiment not in {"positive", "neutral", "negative", "mixed"}:
            sentiment = "neutral"
        assignments_by_unit.setdefault(unit_id, []).append(
            CodeAssignmentEntry(
                code_id=code_id,
                evidence=(row.get("evidence") or "").strip(),
                confidence=confidence,
                sentiment=sentiment,  # type: ignore[arg-type]
            )
        )

        theme_id = (row.get("theme_id") or "").strip()
        theme = themes.get(theme_id)
        if theme is None:
            theme = Theme(
                theme_id=theme_id, title=(row.get("theme_title") or "").strip()
            )
            themes[theme_id] = theme
        if code_id not in seen_codes:
            theme.subthemes.append(
                Subtheme(
                    code_id=code_id,
                    title=(row.get("code_title") or "").strip(),
                    definition=(row.get("definition") or "").strip(),
                )
            )
            seen_codes.add(code_id)

    if not units_by_id:
        return None

    units = [units_by_id[uid] for uid in order]
    assignments = [
        CodeAssignment(unit_id=uid, assignments=entries)
        for uid, entries in assignments_by_unit.items()
    ]
    codebook = (
        normalise_codebook_ids(Codebook(themes=list(themes.values())))
        if themes
        else None
    )
    return units, assignments, codebook


__all__ = [
    "CODED_DATA_CSV_HEADERS",
    "build_coded_data_csv",
    "parse_coded_data_csv",
]
