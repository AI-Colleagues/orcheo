"""Tests for the qualitative record segmentation node."""

import pytest
from orcheo.graph.state import State
from orcheo.nodes.qualitative import SegmentRecordsNode
from orcheo.nodes.qualitative.segment import strip_html_tags


def test_strip_html_tags_preserves_paragraph_breaks():
    text = "<p>First &amp; foremost</p><p>Second<br/>line</p>"
    assert strip_html_tags(text) == "First & foremost\n\nSecond\nline"


def test_strip_html_tags_collapses_inline_whitespace():
    assert strip_html_tags("a  <b>bold</b>\tc") == "a bold c"


@pytest.mark.asyncio
async def test_segment_records_paragraph_granularity():
    node = SegmentRecordsNode(
        name="segment",
        records=[
            {
                "_id": "abc123",
                "title": "Model release",
                "description": "<p>Paragraph one.</p><p>Paragraph two.</p>",
                "link": "https://example.com/a",
                "isoDate": "2026-07-01T00:00:00Z",
                "source": "https://feed.example.com/rss",
            }
        ],
        text_fields=["title", "description"],
        metadata_fields=["link", "isoDate"],
    )
    result = await node.run(State(), None)

    assert result["record_count"] == 1
    assert result["record_ids"] == ["abc123"]
    assert result["has_units"] is True
    assert result["unit_count"] == 3
    units = result["units"]
    assert [unit["text"] for unit in units] == [
        "Model release",
        "Paragraph one.",
        "Paragraph two.",
    ]
    for unit in units:
        assert unit["record_id"] == "abc123"
        assert unit["source"] == "https://feed.example.com/rss"
        assert unit["metadata"] == {
            "link": "https://example.com/a",
            "isoDate": "2026-07-01T00:00:00Z",
        }
    assert units[0]["unit_id"] == "U0001"
    assert units[2]["unit_id"] == "U0003"


@pytest.mark.asyncio
async def test_segment_records_record_granularity_and_defaults():
    node = SegmentRecordsNode(
        name="segment",
        records=[
            {"text": "One.\n\nTwo."},
            "not-a-mapping",
            {"text": "   "},
            {"text": None},
        ],
        granularity="record",
    )
    result = await node.run(State(), None)

    assert result["record_count"] == 3
    assert result["record_ids"] == []
    assert result["unit_count"] == 1
    unit = result["units"][0]
    assert unit["text"] == "One.\n\nTwo."
    assert unit["record_id"] == "R00001"
    assert unit["source"] == "records"


@pytest.mark.asyncio
async def test_segment_records_empty_input():
    node = SegmentRecordsNode(name="segment", records=None)
    result = await node.run(State(), None)

    assert result == {
        "units": [],
        "unit_count": 0,
        "record_count": 0,
        "record_ids": [],
        "has_units": False,
    }


@pytest.mark.asyncio
async def test_segment_records_without_source_field_and_html_kept():
    node = SegmentRecordsNode(
        name="segment",
        records=[{"_id": 7, "text": "<b>kept</b>"}],
        source_field=None,
        strip_html=False,
    )
    result = await node.run(State(), None)

    assert result["record_ids"] == [7]
    unit = result["units"][0]
    assert unit["text"] == "<b>kept</b>"
    assert unit["record_id"] == "7"
    assert unit["source"] == "records"
