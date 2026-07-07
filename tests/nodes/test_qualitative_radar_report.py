"""Tests for the two-track (Covered/Emergent) theme report node."""

import json
from datetime import UTC, datetime
import pytest
from orcheo.graph.state import State
from orcheo.nodes.qualitative import TwoTrackThemeReportNode
from orcheo.nodes.qualitative.models import Codebook, QuantificationRow
from orcheo.nodes.qualitative.radar_report import (
    coerce_codebook_input,
    corpus_summary,
    seed_theme_keys,
    split_covered_emergent,
)


SEED_CODEBOOK = {
    "themes": [
        {
            "theme_id": "T01",
            "title": "Foundation models",
            "subthemes": [{"code_id": "C001", "title": "model releases"}],
        }
    ]
}


def test_coerce_codebook_input_accepts_dict_json_string_and_rejects_garbage():
    assert coerce_codebook_input(SEED_CODEBOOK) == Codebook.model_validate(
        SEED_CODEBOOK
    )
    assert coerce_codebook_input(json.dumps(SEED_CODEBOOK)) == Codebook.model_validate(
        SEED_CODEBOOK
    )
    assert coerce_codebook_input("not json") is None
    assert coerce_codebook_input(None) is None


def test_seed_theme_keys():
    codebook = Codebook.model_validate(SEED_CODEBOOK)
    assert seed_theme_keys(codebook) == {"T01", "foundation models"}


def test_seed_theme_keys_skips_blank_id_or_title():
    codebook = Codebook.model_validate(
        {
            "themes": [
                {"theme_id": "", "title": "Untitled id"},
                {"theme_id": "T02", "title": "  "},
            ]
        }
    )
    assert seed_theme_keys(codebook) == {"untitled id", "T02"}


def test_split_covered_emergent_ranks_by_salience_and_excludes_zero_mentions():
    rows = [
        QuantificationRow(
            theme_id="T01",
            title="Foundation models",
            mentions=4,
            respondents=2,
            pct_respondents=100.0,
        ),
        QuantificationRow(
            theme_id="T02",
            title="AI agents",
            mentions=0,
            respondents=0,
            pct_respondents=0.0,
        ),
        QuantificationRow(
            theme_id="T90",
            title="Robotics embodiment",
            mentions=1,
            respondents=1,
            pct_respondents=50.0,
        ),
    ]
    covered, covered_zero, emergent = split_covered_emergent(rows, {"T01", "T02"})

    assert [row.theme_id for row in covered] == ["T01"]
    assert [row.theme_id for row in covered_zero] == ["T02"]
    assert [row.theme_id for row in emergent] == ["T90"]


def test_corpus_summary():
    articles = [
        {"source": "a", "isoDate": "2026-07-01T08:00:00Z"},
        {"source": "b", "isoDate": "2026-07-03T09:00:00Z"},
        {"source": "a", "isoDate": ""},
    ]
    counts, start, end = corpus_summary(articles)
    assert counts == {"a": 2, "b": 1}
    assert start == "2026-07-01T08:00:00Z"
    assert end == "2026-07-03T09:00:00Z"


def test_corpus_summary_empty():
    assert corpus_summary([]) == ({}, "", "")


@pytest.mark.asyncio
async def test_two_track_theme_report_node_accepts_json_string_seed_codebook():
    """A textarea-configured seed_codebook resolves to a raw JSON string."""
    node = TwoTrackThemeReportNode(
        name="compose_report",
        seed_codebook=json.dumps(SEED_CODEBOOK),
        quantification=[
            {
                "theme_id": "T01",
                "title": "Foundation models",
                "mentions": 3,
                "respondents": 2,
                "pct_respondents": 100.0,
            },
            {
                "theme_id": "T90",
                "title": "Robotics embodiment",
                "mentions": 1,
                "respondents": 1,
                "pct_respondents": 50.0,
            },
        ],
    )
    out = await node.run(State(), None)
    report = out["report_html"]
    covered_section, _, rest = report.partition("<h2>Emergent candidates</h2>")
    assert "<h3>1. Foundation models</h3>" in covered_section
    assert "<h3>1. Robotics embodiment" in rest


@pytest.mark.asyncio
async def test_two_track_theme_report_node_renders_full_report():
    node = TwoTrackThemeReportNode(
        name="compose_report",
        title="Market Radar Report",
        filename_prefix="radar_report",
        report_date="2026-07-03",
        articles=[
            {
                "_id": "a1",
                "source": "https://feed.a/rss",
                "isoDate": "2026-07-01T08:00:00Z",
            },
            {
                "_id": "a2",
                "source": "https://feed.b/rss",
                "isoDate": "2026-07-03T09:00:00Z",
            },
        ],
        record_count=2,
        unit_count=3,
        seed_codebook=SEED_CODEBOOK,
        codebook={
            "themes": SEED_CODEBOOK["themes"]
            + [
                {
                    "theme_id": "T90",
                    "title": "Robotics embodiment",
                    "subthemes": [
                        {
                            "code_id": "C900",
                            "title": "humanoid robots",
                            "definition": "News about humanoid robot demos.",
                        }
                    ],
                }
            ]
        },
        quantification=[
            {
                "theme_id": "T01",
                "title": "Foundation models",
                "mentions": 4,
                "respondents": 2,
                "pct_respondents": 100.0,
                "sentiment_counts": {"positive": 3, "neutral": 1},
            },
            {
                "theme_id": "T90",
                "title": "Robotics embodiment",
                "mentions": 1,
                "respondents": 1,
                "pct_respondents": 50.0,
                "sentiment_counts": {"neutral": 1},
            },
        ],
        cooccurrence=[
            {"theme_id_a": "T01", "theme_id_b": "T90", "respondents": 1, "mentions": 1}
        ],
        units=[
            {
                "unit_id": "U0001",
                "record_id": "a1",
                "source": "s",
                "text": "t",
                "original_text": "t",
                "metadata": {"link": "https://ex.com/a"},
            }
        ],
        quotes=[
            {"theme_id": "T01", "unit_id": "U0001", "text": "GPT-6 tops benchmark"},
            {"theme_id": "T90", "unit_id": "U0001", "text": "robot folds laundry"},
        ],
        candidate_insights=[
            {
                "insight_id": "I01",
                "observation": "Model releases dominate coverage",
                "implication": "Expect rapid capability shifts",
                "supporting_codes": ["C001"],
                "supporting_units": ["U0001"],
            }
        ],
        approved_insight_ids=json.dumps(["I01"]),
        recommendations=[
            {
                "insight_id": "I01",
                "finding": "f",
                "action": "Review release notes weekly",
                "expected_impact": "e",
            }
        ],
        quotes_per_theme=3,
        dry_run=False,
    )

    out = await node.run(State(), None)

    assert out["report_filename"] == "radar_report_2026-07-03.html"
    assert out["should_mark_read"] is True
    assert (
        out["caption"] == "Market Radar Report 2026-07-03: 1 covered theme(s), "
        "1 emergent candidate(s) from 2 article(s)."
    )
    report = out["report_html"]
    assert report.startswith("<!DOCTYPE html>")
    assert "<title>Market Radar Report — 2026-07-03</title>" in report
    assert "<h1>Market Radar Report — 2026-07-03</h1>" in report
    assert "<h2>Covered themes (salience-ranked)</h2>" in report
    assert "<h3>1. Foundation models</h3>" in report
    assert (
        '<blockquote>GPT-6 tops benchmark (<a href="https://ex.com/a">source'
        "</a>)</blockquote>" in report
    )
    assert "<h2>Emergent candidates</h2>" in report
    assert '<h3>1. Robotics embodiment <span class="candidate">' in report
    assert "humanoid robots: News about humanoid robot demos." in report
    assert "<h2>Insights</h2>" in report
    assert "Model releases dominate coverage" in report
    assert "Review release notes weekly" in report
    assert "<h2>Theme co-occurrence</h2>" in report
    assert "Foundation models + Robotics embodiment: 1 article(s)" in report


@pytest.mark.asyncio
async def test_two_track_theme_report_node_dry_run_skips_mark_read():
    node = TwoTrackThemeReportNode(name="compose_report", record_count=5, dry_run=True)
    out = await node.run(State(), None)
    assert out["should_mark_read"] is False


@pytest.mark.asyncio
async def test_two_track_theme_report_node_zero_records_skips_mark_read():
    node = TwoTrackThemeReportNode(name="compose_report", record_count=0, dry_run=False)
    out = await node.run(State(), None)
    assert out["should_mark_read"] is False


@pytest.mark.asyncio
async def test_two_track_theme_report_node_empty_input_renders_placeholders():
    node = TwoTrackThemeReportNode(name="compose_report", report_date="2026-07-01")
    out = await node.run(State(), None)

    assert out["report_filename"] == "radar_report_2026-07-01.html"
    report = out["report_html"]
    assert "(No seed-codebook theme was mentioned this period.)" in report
    assert "(No emergent theme surfaced this period.)" in report
    assert "<h2>Insights</h2>" not in report
    assert "<h2>Theme co-occurrence</h2>" not in report


@pytest.mark.asyncio
async def test_two_track_theme_report_node_report_date_defaults_to_today():
    """The report date is when the run happens, not the latest article's

    publish date -- a backlog of old unread articles must not make a
    freshly-generated report look stale.
    """
    node = TwoTrackThemeReportNode(
        name="compose_report",
        articles=[{"_id": "a1", "source": "s", "isoDate": "2020-01-01T00:00:00+00:00"}],
    )
    out = await node.run(State(), None)

    today = datetime.now(UTC).date().isoformat()
    assert out["report_filename"] == f"radar_report_{today}.html"
    assert f"<h1>Market Radar Report — {today}</h1>" in out["report_html"]
    assert "Period: 2020-01-01T00:00:00+00:00" in out["report_html"]


@pytest.mark.asyncio
async def test_two_track_theme_report_node_skip_branches():
    """Cover the no-op branches: capped quotes, blank quote, no sentiment/subtheme
    details, and an approved insight with no implication/recommendation."""
    node = TwoTrackThemeReportNode(
        name="compose_report",
        seed_codebook=SEED_CODEBOOK,
        codebook={"themes": []},
        quantification=[
            {
                "theme_id": "T01",
                "title": "Foundation models",
                "mentions": 2,
                "respondents": 1,
                "pct_respondents": 100.0,
            },
            {
                "theme_id": "T90",
                "title": "Robotics embodiment",
                "mentions": 1,
                "respondents": 1,
                "pct_respondents": 100.0,
            },
        ],
        quotes=[
            {"theme_id": "T01", "unit_id": "U0001", "text": "   "},
            {"theme_id": "T01", "unit_id": "U0002", "text": "first quote"},
            {"theme_id": "T01", "unit_id": "U0003", "text": "second quote"},
        ],
        quotes_per_theme=2,
        candidate_insights=[
            {
                "insight_id": "I01",
                "observation": "obs",
                "supporting_codes": ["C001"],
                "supporting_units": ["U0001"],
            }
        ],
        approved_insight_ids=["I01"],
    )

    out = await node.run(State(), None)
    report = out["report_html"]
    assert report.count("<blockquote>first quote</blockquote>") == 1
    assert "second quote" not in report
    assert "<strong>obs</strong>" in report
    assert "Implication" not in report
    assert "Suggested action" not in report
    assert "Sentiment:" not in report
    assert "Codes:" not in report


@pytest.mark.asyncio
async def test_two_track_theme_report_node_covered_zero_mentions_section():
    node = TwoTrackThemeReportNode(
        name="compose_report",
        seed_codebook=SEED_CODEBOOK,
        quantification=[
            {
                "theme_id": "T01",
                "title": "Foundation models",
                "mentions": 0,
                "respondents": 0,
                "pct_respondents": 0.0,
            }
        ],
    )
    out = await node.run(State(), None)
    assert (
        "<strong>No coverage this period:</strong> Foundation models"
        in out["report_html"]
    )


@pytest.mark.asyncio
async def test_two_track_theme_report_node_escapes_untrusted_text():
    """LLM/article-derived text is HTML-escaped; non-http(s) links are dropped."""
    node = TwoTrackThemeReportNode(
        name="compose_report",
        seed_codebook={
            "themes": [
                {
                    "theme_id": "T01",
                    "title": "<script>alert(1)</script>",
                    "subthemes": [],
                }
            ]
        },
        quantification=[
            {
                "theme_id": "T01",
                "title": "<script>alert(1)</script>",
                "mentions": 1,
                "respondents": 1,
                "pct_respondents": 100.0,
            }
        ],
        units=[
            {
                "unit_id": "U0001",
                "record_id": "a1",
                "source": "s",
                "text": "t",
                "original_text": "t",
                "metadata": {"link": "javascript:alert(1)"},
            }
        ],
        quotes=[
            {
                "theme_id": "T01",
                "unit_id": "U0001",
                "text": '<img src=x onerror="alert(1)">',
            }
        ],
    )

    out = await node.run(State(), None)
    report = out["report_html"]
    assert "<script>alert(1)</script>" not in report
    assert "&lt;script&gt;" in report
    assert "<img src=x" not in report
    assert "javascript:" not in report
