"""Codebook utilities: id minting, parsing, rendering, and recovery."""

# ruff: noqa: C901, PLR0912

from __future__ import annotations
import csv
import html
import json
import re
from collections import Counter
from typing import Any
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.qualitative.accessors import (
    get_configurable,
    get_draft_codebook,
    get_seed_codebook_from_file,
)
from orcheo.nodes.qualitative.keys import QualitativeResultKeys
from orcheo.nodes.qualitative.models import (
    CodeAssignment,
    Codebook,
    Subtheme,
    Theme,
)
from orcheo.runtime.results import assistant_message_texts


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
    """Merge an emergent codebook into a seed codebook, deduping by title."""
    seed_titles = {
        subtheme.title.strip().lower()
        for theme in seed.themes
        for subtheme in theme.subthemes
    }
    themes = [theme.model_copy(deep=True) for theme in seed.themes]
    for theme in emergent.themes:
        subthemes = [
            s for s in theme.subthemes if s.title.strip().lower() not in seed_titles
        ]
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


def parse_markdown_table_row(row: str) -> list[str] | None:
    """Split one Markdown table row into trimmed cells."""
    stripped = row.strip().strip("|")
    if not stripped:
        return None
    cells: list[str] = []
    current: list[str] = []
    escape = False
    for char in stripped:
        if escape:
            current.append(char)
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    cells.append("".join(current).strip())
    return cells


def escape_markdown_table_cell(value: str) -> str:
    """Escape a value for safe rendering inside a Markdown table cell."""
    escaped = html.escape(value, quote=False)
    return escaped.replace("|", "&#124;").replace("\n", "<br>")


def parse_codebook_markdown(content: str) -> Codebook | None:
    """Parse a codebook from Markdown table or heading/list form."""
    table_lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip().startswith("|") and line.strip().endswith("|")
    ]
    if len(table_lines) >= 3:
        header_cells = parse_markdown_table_row(table_lines[0])
        if header_cells:
            headers = [cell.strip().lower() for cell in header_cells]
            required = [
                "theme id",
                "theme title",
                "code id",
                "code title",
                "definition",
                "include",
                "exclude",
            ]
            if all(header in headers for header in required):
                index = {header: headers.index(header) for header in required}
                themes: dict[str, Theme] = {}
                for raw_line in table_lines[2:]:
                    row = parse_markdown_table_row(raw_line)
                    if not row or len(row) < len(headers):
                        continue
                    theme_id = html.unescape(row[index["theme id"]].strip())
                    theme_title = html.unescape(row[index["theme title"]].strip())
                    code_id = html.unescape(row[index["code id"]].strip())
                    if not code_id:
                        continue
                    code_title = html.unescape(row[index["code title"]].strip())
                    definition = html.unescape(row[index["definition"]].strip())
                    include = [
                        html.unescape(item.strip())
                        for item in html.unescape(row[index["include"]]).split(";")
                        if item.strip()
                    ]
                    exclude = [
                        html.unescape(item.strip())
                        for item in html.unescape(row[index["exclude"]]).split(";")
                        if item.strip()
                    ]
                    theme = themes.get(theme_id)
                    if theme is None:
                        theme = Theme(theme_id=theme_id, title=theme_title)
                        themes[theme_id] = theme
                    theme.subthemes.append(
                        Subtheme(
                            code_id=code_id,
                            title=code_title,
                            definition=definition,
                            include=include,
                            exclude=exclude,
                        )
                    )
                if themes and any(theme.subthemes for theme in themes.values()):
                    return normalise_codebook_ids(
                        Codebook(themes=list(themes.values()))
                    )

    themes_list: list[Theme] = []
    current_theme: Theme | None = None
    theme_pattern = re.compile(r"^##\s+(?P<theme_id>T\d+):\s*(?P<title>.+?)\s*$")
    code_pattern = re.compile(
        r"^-\s+`(?P<code_id>[^`]+)`\s+\*\*(?P<title>[^*]+)\*\*:\s*(?P<definition>.*)$"
    )
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        theme_match = theme_pattern.match(line)
        if theme_match:
            current_theme = Theme(
                theme_id=theme_match.group("theme_id").strip(),
                title=theme_match.group("title").strip(),
            )
            themes_list.append(current_theme)
            continue
        code_match = code_pattern.match(line)
        if code_match and current_theme is not None:
            current_theme.subthemes.append(
                Subtheme(
                    code_id=code_match.group("code_id").strip(),
                    title=code_match.group("title").strip(),
                    definition=code_match.group("definition").strip(),
                )
            )

    if not themes_list or not any(theme.subthemes for theme in themes_list):
        return None
    return normalise_codebook_ids(Codebook(themes=themes_list))


def recover_exportable_codebook(
    state: State, keys: QualitativeResultKeys | None = None
) -> Codebook | None:
    """Return the draft codebook, recovering it from chat history if needed."""
    keys = keys or QualitativeResultKeys()
    codebook = get_draft_codebook(state, keys)
    if codebook is not None:
        return codebook
    for message_text in assistant_message_texts(state):
        recovered = parse_codebook_markdown(message_text)
        if recovered is not None:
            return recovered
    return None


def get_seed_codebook(
    config: RunnableConfig | None,
    state: State | None = None,
    keys: QualitativeResultKeys | None = None,
    *,
    config_key: str = "seed_codebook",
) -> Codebook | None:
    """Return a seed codebook from config or an uploaded file, if available."""
    keys = keys or QualitativeResultKeys()
    raw: Any = get_configurable(config).get(config_key)
    if raw is None and state is not None:
        raw = get_seed_codebook_from_file(state, keys)
    if raw is None:
        return None
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        return normalise_codebook_ids(Codebook.model_validate(payload))
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "code_to_theme_map",
    "escape_markdown_table_cell",
    "fallback_codebook",
    "get_seed_codebook",
    "make_code_id",
    "make_insight_id",
    "make_theme_id",
    "make_unit_id",
    "merge_codebooks",
    "normalise_codebook_ids",
    "normalise_label",
    "parse_codebook_csv",
    "parse_codebook_markdown",
    "parse_markdown_table_row",
    "recover_exportable_codebook",
    "render_codebook_for_prompt",
]
