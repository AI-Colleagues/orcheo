"""Two-track (Covered vs. Emergent) theme report rendering + node.

Renders a decision-support HTML report over quantified themes, split by
whether each theme is present in a curated "seed" codebook (**Covered**,
ranked by within-period salience) or was only discovered inductively this
period (**Emergent**, surfaced as unvetted candidates). Designed for scheduled
theme-monitoring colleagues (e.g. a news/market radar) that recode a rolling
corpus and need a compact report plus a delivery/mark-as-processed decision.

HTML (not Markdown) is used because the report is delivered as a document
attachment: chat clients such as Telegram show a ``.md`` file as unstyled
plain text, while a self-contained ``.html`` file opens with real formatting.

This is a plain :class:`~orcheo.nodes.base.TaskNode`, not a script-defined
``CodeNode``: composing a report over a whole run's coded corpus can process
more data than the sandboxed ``CodeNode`` runtime's fuel/memory budget allows,
so the logic lives here as trusted, reusable node code instead.
"""

from __future__ import annotations
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from html import escape
from typing import Any
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.qualitative.accessors import coerce_model, coerce_model_list
from orcheo.nodes.qualitative.models import (
    CandidateInsight,
    Codebook,
    CooccurrenceRow,
    QuantificationRow,
    Quote,
    Recommendation,
    Unit,
)
from orcheo.nodes.registry import NodeMetadata, registry


_STYLE = (
    "body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
    "max-width:720px;margin:2rem auto;padding:0 1rem;color:#1a1a1a;"
    "line-height:1.5}"
    "h1{font-size:1.5rem;margin-bottom:.25rem}"
    "h2{margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.25rem}"
    "h3{margin-bottom:.25rem}"
    "ul{margin:.25rem 0}"
    "blockquote{margin:.25rem 0 .75rem;padding-left:.75rem;"
    "border-left:3px solid #ccc;color:#333}"
    ".meta{color:#666;font-size:.9rem}"
    ".candidate{color:#8a6d00;font-style:italic;font-weight:normal;"
    "font-size:.85em}"
)


def _safe_href(url: str) -> str | None:
    """Return *url* if it is a plain http(s) link, else ``None``."""
    if url.startswith(("http://", "https://")):
        return url
    return None


def coerce_codebook_input(value: Any) -> Codebook | None:
    """Coerce a codebook value that may be a JSON-encoded textarea string.

    Config-driven codebook fields (e.g. a ``format: textarea`` configurable)
    resolve to a raw JSON string rather than a parsed object, unlike codebook
    values produced by upstream pipeline nodes.
    """
    codebook = coerce_model(value, Codebook)
    if codebook is not None:
        return codebook
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return None
        return coerce_model(payload, Codebook)
    return None


def _coerce_string_list_input(value: Any) -> list[str]:
    """Coerce a list value that may be a JSON-encoded textarea string."""
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, list):
            return [str(item) for item in payload]
    return []


def seed_theme_keys(codebook: Codebook) -> set[str]:
    """Return matching keys (theme ids + lowercase titles) for a codebook."""
    keys: set[str] = set()
    for theme in codebook.themes:
        theme_id = theme.theme_id.strip()
        title = theme.title.strip().lower()
        if theme_id:
            keys.add(theme_id)
        if title:
            keys.add(title)
    return keys


def split_covered_emergent(
    rows: list[QuantificationRow], seed_keys: set[str]
) -> tuple[list[QuantificationRow], list[QuantificationRow], list[QuantificationRow]]:
    """Split quantified themes into (covered, covered-but-unmentioned, emergent).

    Both non-empty groups are ranked by within-period salience: mention count
    first, then respondent (article) reach as a tie-break.
    """

    def is_covered(row: QuantificationRow) -> bool:
        return row.theme_id in seed_keys or row.title.strip().lower() in seed_keys

    def salience(row: QuantificationRow) -> tuple[int, int]:
        return (-row.mentions, -row.respondents)

    covered = sorted(
        (row for row in rows if is_covered(row) and row.mentions > 0), key=salience
    )
    covered_zero = [row for row in rows if is_covered(row) and row.mentions == 0]
    emergent = sorted(
        (row for row in rows if not is_covered(row) and row.mentions > 0),
        key=salience,
    )
    return covered, covered_zero, emergent


def corpus_summary(
    articles: list[Mapping[str, Any]],
) -> tuple[dict[str, int], str, str]:
    """Return (source counts, earliest published date, latest published date)."""
    source_counts: dict[str, int] = {}
    published: list[str] = []
    for article in articles:
        source = str(article.get("source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
        iso_date = str(article.get("isoDate") or "").strip()
        if iso_date:
            published.append(iso_date)
    period_start = min(published) if published else ""
    period_end = max(published) if published else ""
    return source_counts, period_start, period_end


def render_radar_report_html(  # noqa: C901, PLR0912, PLR0913, PLR0915
    *,
    title: str,
    report_date: str,
    record_count: int,
    unit_count: int,
    source_counts: dict[str, int],
    period_start: str,
    period_end: str,
    covered: list[QuantificationRow],
    covered_zero: list[QuantificationRow],
    emergent: list[QuantificationRow],
    codebook: Codebook,
    quotes: list[Quote],
    quotes_per_theme: int,
    unit_links: dict[str, str],
    cooccurrence: list[CooccurrenceRow],
    approved_insights: list[CandidateInsight],
    recommendations_by_id: dict[str, Recommendation],
) -> str:
    """Render the two-track report as a self-contained HTML document.

    All theme/quote/insight/article-derived text is untrusted (LLM output over
    public news text) and is HTML-escaped before interpolation; evidence links
    are restricted to plain ``http(s)`` URLs.
    """
    subthemes_by_theme = {theme.theme_id: theme.subthemes for theme in codebook.themes}
    quotes_by_theme: dict[str, list[Quote]] = {}
    for quote in quotes:
        bucket = quotes_by_theme.setdefault(quote.theme_id, [])
        if len(bucket) < quotes_per_theme:
            bucket.append(quote)

    def quote_blocks(theme_id: str) -> list[str]:
        blocks: list[str] = []
        for quote in quotes_by_theme.get(theme_id, []):
            text = quote.text.strip()
            if not text:
                continue
            href = _safe_href(unit_links.get(quote.unit_id, ""))
            suffix = f' (<a href="{escape(href)}">source</a>)' if href else ""
            blocks.append(f"<blockquote>{escape(text)}{suffix}</blockquote>")
        return blocks

    def theme_section(rank: int, row: QuantificationRow, *, candidate: bool) -> str:
        label = ' <span class="candidate">(candidate)</span>' if candidate else ""
        parts = [
            f"<h3>{rank}. {escape(row.title)}{label}</h3>",
            "<ul>",
            f"<li>Mentions: {row.mentions} across {row.respondents} article(s)</li>",
        ]
        sentiment = ", ".join(
            f"{key} {value}" for key, value in row.sentiment_counts.items() if value
        )
        if sentiment:
            parts.append(f"<li>Sentiment: {escape(sentiment)}</li>")
        if candidate:
            details = [
                f"{sub.title}: {sub.definition}".strip(": ")
                for sub in subthemes_by_theme.get(row.theme_id, [])
                if sub.title
            ]
            if details:
                parts.append(f"<li>Codes: {escape('; '.join(details))}</li>")
        parts.append("</ul>")
        parts.extend(quote_blocks(row.theme_id))
        return "\n".join(parts)

    body: list[str] = [
        f"<h1>{escape(title)} — {escape(report_date)}</h1>",
        '<p class="meta"><em>Salience is a within-period ranking of media '
        "coverage, not an absolute signal of importance.</em></p>",
        "<h2>Corpus</h2>",
        "<ul>",
        f"<li>Articles processed: {record_count} ({unit_count} coding units)</li>",
    ]
    if period_start or period_end:
        body.append(f"<li>Period: {escape(period_start)} → {escape(period_end)}</li>")
    if source_counts:
        mix = ", ".join(
            f"{escape(source)} ({count})"
            for source, count in sorted(
                source_counts.items(), key=lambda item: -item[1]
            )
        )
        body.append(f"<li>Source mix: {mix}</li>")
    body.append("</ul>")

    body.append("<h2>Covered themes (salience-ranked)</h2>")
    if covered:
        for rank, row in enumerate(covered, start=1):
            body.append(theme_section(rank, row, candidate=False))
    else:
        body.append("<p>(No seed-codebook theme was mentioned this period.)</p>")
    if covered_zero:
        titles = escape(", ".join(row.title for row in covered_zero))
        body.append(f"<p><strong>No coverage this period:</strong> {titles}</p>")

    body.append("<h2>Emergent candidates</h2>")
    body.append(
        "<p><em>Themes found outside the seed codebook this period. They are "
        "candidates to investigate, not graded opportunities.</em></p>"
    )
    if emergent:
        for rank, row in enumerate(emergent, start=1):
            body.append(theme_section(rank, row, candidate=True))
    else:
        body.append("<p>(No emergent theme surfaced this period.)</p>")

    if approved_insights:
        body.append("<h2>Insights</h2>")
        body.append("<ul>")
        for insight in approved_insights:
            body.append(f"<li><strong>{escape(insight.observation.strip())}</strong>")
            sub_items: list[str] = []
            if insight.implication.strip():
                sub_items.append(
                    f"<li>Implication: {escape(insight.implication.strip())}</li>"
                )
            rec = recommendations_by_id.get(insight.insight_id)
            if rec and rec.action:
                sub_items.append(f"<li>Suggested action: {escape(rec.action)}</li>")
            if sub_items:
                body.append("<ul>" + "".join(sub_items) + "</ul>")
            body.append("</li>")
        body.append("</ul>")

    if cooccurrence:
        body.append("<h2>Theme co-occurrence</h2>")
        titles_by_id = {
            row.theme_id: row.title for row in (*covered, *covered_zero, *emergent)
        }
        body.append("<ul>")
        for pair in cooccurrence:
            theme_a = titles_by_id.get(pair.theme_id_a, pair.theme_id_a)
            theme_b = titles_by_id.get(pair.theme_id_b, pair.theme_id_b)
            body.append(
                f"<li>{escape(theme_a)} + {escape(theme_b)}: "
                f"{pair.respondents} article(s)</li>"
            )
        body.append("</ul>")

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{escape(title)} — {escape(report_date)}</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        + "\n".join(body)
        + "\n</body>\n</html>\n"
    )


@registry.register(
    NodeMetadata(
        name="TwoTrackThemeReportNode",
        description="Render a Covered/Emergent salience-ranked theme report",
        category="workflow",
    )
)
class TwoTrackThemeReportNode(TaskNode):
    """Compose a two-track (Covered/Emergent) HTML theme report.

    Splits quantified themes by membership in a curated ``seed_codebook``:
    themes present there are **Covered** (ranked by within-period salience),
    everything else discovered this period is **Emergent** (an unvetted
    candidate, never graded). Also decides whether the corresponding source
    items should be marked processed (``should_mark_read``), gated on
    ``record_count`` and ``dry_run``.

    ``report_date`` labels the report (title + filename) and defaults to the
    current UTC date -- i.e. when the report was produced. It is deliberately
    *not* derived from the corpus's article publish dates (see ``period_start``
    / ``period_end`` in the rendered "Corpus" section for that): a backlog of
    unprocessed items would otherwise make the report look stale by showing a
    date well before the run actually happened.
    """

    title: str = "Market Radar Report"
    filename_prefix: str = "radar_report"
    report_date: str | None = None
    articles: Any | None = None
    record_count: int | str = 0
    unit_count: int | str = 0
    seed_codebook: Any | None = None
    codebook: Any | None = None
    quantification: Any | None = None
    cooccurrence: Any | None = None
    units: Any | None = None
    quotes: Any | None = None
    candidate_insights: Any | None = None
    approved_insight_ids: Any | None = None
    recommendations: Any | None = None
    quotes_per_theme: int | str = 3
    dry_run: bool | str = False

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Render the report and return delivery/mark-processed decisions."""
        del state, config
        record_count = int(self.record_count)
        unit_count = int(self.unit_count)
        quotes_per_theme = int(self.quotes_per_theme)
        dry_run = (
            self.dry_run
            if isinstance(self.dry_run, bool)
            else str(self.dry_run).strip().lower() in {"true", "1", "yes"}
        )
        articles = [a for a in (self.articles or []) if isinstance(a, Mapping)]
        source_counts, period_start, period_end = corpus_summary(articles)
        report_date = self.report_date or datetime.now(UTC).date().isoformat()

        codebook = coerce_codebook_input(self.codebook) or Codebook()
        seed_codebook = coerce_codebook_input(self.seed_codebook) or Codebook()
        seed_keys = seed_theme_keys(seed_codebook)
        quantification = coerce_model_list(self.quantification, QuantificationRow)
        covered, covered_zero, emergent = split_covered_emergent(
            quantification, seed_keys
        )

        units = coerce_model_list(self.units, Unit)
        unit_links = {
            unit.unit_id: str(unit.metadata.get("link") or "")
            for unit in units
            if unit.metadata.get("link")
        }
        quotes = coerce_model_list(self.quotes, Quote)
        cooccurrence = coerce_model_list(self.cooccurrence, CooccurrenceRow)

        candidates_by_id = {
            insight.insight_id: insight
            for insight in coerce_model_list(self.candidate_insights, CandidateInsight)
        }
        approved_ids = _coerce_string_list_input(self.approved_insight_ids)
        approved_insights = [
            candidates_by_id[insight_id]
            for insight_id in approved_ids
            if insight_id in candidates_by_id
        ]
        recommendations_by_id = {
            rec.insight_id: rec
            for rec in coerce_model_list(self.recommendations, Recommendation)
        }

        report = render_radar_report_html(
            title=self.title,
            report_date=report_date,
            record_count=record_count,
            unit_count=unit_count,
            source_counts=source_counts,
            period_start=period_start,
            period_end=period_end,
            covered=covered,
            covered_zero=covered_zero,
            emergent=emergent,
            codebook=codebook,
            quotes=quotes,
            quotes_per_theme=quotes_per_theme,
            unit_links=unit_links,
            cooccurrence=cooccurrence,
            approved_insights=approved_insights,
            recommendations_by_id=recommendations_by_id,
        )

        caption = (
            f"{self.title} {report_date}: {len(covered)} covered theme(s), "
            f"{len(emergent)} emergent candidate(s) from {record_count} article(s)."
        )
        return {
            "report_html": report,
            "report_filename": f"{self.filename_prefix}_{report_date}.html",
            "caption": caption,
            "should_mark_read": bool(record_count) and not dry_run,
        }


__all__ = [
    "TwoTrackThemeReportNode",
    "coerce_codebook_input",
    "corpus_summary",
    "render_radar_report_html",
    "seed_theme_keys",
    "split_covered_emergent",
]
