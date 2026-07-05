"""Codebook utilities: id minting, parsing, and rendering."""

from __future__ import annotations
import csv
import html
import re
from collections import Counter
from orcheo.nodes.qualitative.models import (
    CodeAssignment,
    Codebook,
    Subtheme,
    Theme,
)


def make_unit_id(index: int) -> str:
    """Return a zero-padded unit id."""
    return f"U{index:04d}"


def make_code_id(index: int) -> str:
    """Return a zero-padded code id."""
    return f"C{index:03d}"


def make_theme_id(index: int) -> str:
    """Return a zero-padded theme id."""
    return f"T{index:02d}"


def make_insight_id(index: int) -> str:
    """Return a zero-padded insight id."""
    return f"I{index:02d}"


def normalise_label(value: str) -> str:
    """Normalise a raw code label into a human-readable title."""
    return re.sub(r"\s+", " ", value.replace("_", " ").replace("-", " ")).strip()


def normalise_codebook_ids(codebook: Codebook) -> Codebook:
    """Ensure codebook IDs are present and stable."""
    themes: list[Theme] = []
    code_counter = 1
    theme_index = 1
    for theme in codebook.themes:
        subthemes: list[Subtheme] = []
        for subtheme in theme.subthemes:
            code_id = (
                subtheme.code_id.strip()
                if subtheme.code_id
                else make_code_id(code_counter)
            )
            subthemes.append(subtheme.model_copy(update={"code_id": code_id}))
            code_counter += 1
        theme_id = (
            theme.theme_id.strip() if theme.theme_id else make_theme_id(theme_index)
        )
        themes.append(
            theme.model_copy(update={"theme_id": theme_id, "subthemes": subthemes})
        )
        theme_index += 1
    return Codebook(themes=themes)


def code_to_theme_map(codebook: Codebook) -> dict[str, tuple[str, str]]:
    """Map code_id to (theme_id, theme_title)."""
    mapping: dict[str, tuple[str, str]] = {}
    for theme in codebook.themes:
        for subtheme in theme.subthemes:
            mapping[subtheme.code_id] = (theme.theme_id, theme.title)
    return mapping


def merge_codebooks(seed: Codebook, emergent: Codebook) -> Codebook:
    """Merge an emergent codebook into a seed codebook, deduping by title and id."""
    seed_titles = {
        subtheme.title.strip().lower()
        for theme in seed.themes
        for subtheme in theme.subthemes
    }
    used_code_ids = {
        subtheme.code_id.strip()
        for theme in seed.themes
        for subtheme in theme.subthemes
        if subtheme.code_id.strip()
    }
    themes = [theme.model_copy(deep=True) for theme in seed.themes]
    next_code_index = len(used_code_ids) + 1
    for theme in emergent.themes:
        subthemes: list[Subtheme] = []
        for subtheme in theme.subthemes:
            if subtheme.title.strip().lower() in seed_titles:
                continue
            code_id = subtheme.code_id.strip() if subtheme.code_id else ""
            while not code_id or code_id in used_code_ids:
                code_id = make_code_id(next_code_index)
                next_code_index += 1
            used_code_ids.add(code_id)
            subthemes.append(subtheme.model_copy(update={"code_id": code_id}))
        if subthemes:
            themes.append(theme.model_copy(update={"subthemes": subthemes}))
    return normalise_codebook_ids(Codebook(themes=themes))


def fallback_codebook(assignments: list[CodeAssignment]) -> Codebook:
    """Build a deterministic codebook from raw open-coding assignments."""
    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for assignment in assignments:
        for entry in assignment.assignments:
            if not entry.code_id:
                continue
            counts.update([entry.code_id])
            examples.setdefault(entry.code_id, entry.evidence)
    subthemes: list[Subtheme] = []
    index = 1
    for raw_code, _ in counts.most_common():
        title = normalise_label(raw_code)
        subthemes.append(
            Subtheme(
                code_id=make_code_id(index),
                title=title,
                definition=f"Mentions related to {title}.",
                include=[title],
                exclude=[],
                example_quotes=[{"unit_id": "", "text": examples[raw_code]}]
                if examples.get(raw_code)
                else [],
            )
        )
        index += 1
    return Codebook(
        themes=[
            Theme(
                theme_id=make_theme_id(1), title="Emergent themes", subthemes=subthemes
            )
        ]
    )


def render_codebook_for_prompt(codebook: Codebook) -> str:
    """Render a codebook as compact prompt text."""
    lines: list[str] = []
    for theme in codebook.themes:
        lines.append(f"{theme.theme_id}: {theme.title}")
        for subtheme in theme.subthemes:
            lines.append(
                f"- {subtheme.code_id}: {subtheme.title} - {subtheme.definition}"
            )
    return "\n".join(lines)


def parse_codebook_csv(
    content: str, *, reject_coded_data: bool = False
) -> Codebook | None:
    """Parse a codebook CSV into a validated :class:`Codebook`.

    When ``reject_coded_data`` is True, a CSV that also carries a ``unit_id``
    column (i.e. a coded-data export) is rejected so the two file types stay
    cleanly distinguishable.
    """
    try:
        reader = csv.DictReader(content.splitlines(keepends=True))
    except Exception:  # noqa: BLE001
        return None
    fieldnames = reader.fieldnames or []
    if "code_id" not in fieldnames:
        return None
    if reject_coded_data and "unit_id" in fieldnames:
        return None

    themes: dict[str, Theme] = {}
    for row in reader:
        theme_id = (row.get("theme_id") or "").strip()
        theme_title = (row.get("theme_title") or "").strip()
        code_id = (row.get("code_id") or "").strip()
        if not code_id:
            continue
        theme = themes.get(theme_id)
        if theme is None:
            theme = Theme(theme_id=theme_id, title=theme_title)
            themes[theme_id] = theme
        include_raw = (row.get("include") or "").strip()
        exclude_raw = (row.get("exclude") or "").strip()
        theme.subthemes.append(
            Subtheme(
                code_id=code_id,
                title=(row.get("code_title") or "").strip(),
                definition=(row.get("definition") or "").strip(),
                include=[s.strip() for s in include_raw.split(";") if s.strip()],
                exclude=[s.strip() for s in exclude_raw.split(";") if s.strip()],
            )
        )
    if not themes or not any(theme.subthemes for theme in themes.values()):
        return None
    return normalise_codebook_ids(Codebook(themes=list(themes.values())))


def escape_markdown_table_cell(value: str) -> str:
    """Escape a value for safe rendering inside a Markdown table cell."""
    escaped = html.escape(value, quote=False)
    return escaped.replace("|", "&#124;").replace("\n", "<br>")


__all__ = [
    "code_to_theme_map",
    "escape_markdown_table_cell",
    "fallback_codebook",
    "make_code_id",
    "make_insight_id",
    "make_theme_id",
    "make_unit_id",
    "merge_codebooks",
    "normalise_codebook_ids",
    "normalise_label",
    "parse_codebook_csv",
    "render_codebook_for_prompt",
]
