"""Tests for RSS node implementation."""

from unittest.mock import AsyncMock, Mock, patch
from xml.etree import ElementTree
import httpx
import pytest
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError
from orcheo.nodes.connectors.rss import RSS_REQUEST_HEADERS, RSSNode


@pytest.mark.asyncio
async def test_rss_node_run():
    """Test RSS node run method."""
    sources = ["https://example.com/feed1.xml", "https://example.com/feed2.xml"]
    node = RSSNode(name="test_rss", sources=sources)
    state = {}
    config = RunnableConfig()

    feed1_xml = (
        "<rss><channel>"
        "<item><title>Test Entry 1</title><link>https://example.com/1</link></item>"
        "<item><title>Test Entry 2</title><link>https://example.com/2</link></item>"
        "</channel></rss>"
    )
    feed2_xml = (
        "<rss><channel>"
        "<item><title>Test Entry 3</title><link>https://example.com/3</link></item>"
        "</channel></rss>"
    )

    mock_resp1 = Mock()
    mock_resp1.text = feed1_xml
    mock_resp2 = Mock()
    mock_resp2.text = feed2_xml

    mock_client = AsyncMock()
    mock_client.get.side_effect = [mock_resp1, mock_resp2]

    with patch("orcheo.nodes.rss.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await node.run(state, config)

    assert result["fetched_count"] == 3
    assert len(result["documents"]) == 3
    assert result["documents"][0]["title"] == "Test Entry 1"
    assert result["documents"][0]["link"] == "https://example.com/1"
    assert result["documents"][1]["title"] == "Test Entry 2"
    assert result["documents"][2]["title"] == "Test Entry 3"
    assert mock_client.get.call_count == 2
    mock_client.get.assert_any_call("https://example.com/feed1.xml", timeout=15.0)
    mock_client.get.assert_any_call("https://example.com/feed2.xml", timeout=15.0)


@pytest.mark.asyncio
async def test_rss_node_normalizes_single_source_string() -> None:
    """Single source strings should be normalized into a one-item list."""
    node = RSSNode(name="test_rss", sources="https://example.com/feed.xml")

    mock_resp = Mock()
    mock_resp.text = "<rss><channel></channel></rss>"

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    with patch("orcheo.nodes.rss.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await node.run({}, RunnableConfig())

    assert node.sources == ["https://example.com/feed.xml"]
    assert result["failed_sources"] == 0
    mock_client.get.assert_called_once_with(
        "https://example.com/feed.xml", timeout=15.0
    )


def test_rss_node_rejects_non_positive_timeout() -> None:
    """Timeouts must be strictly positive."""
    with pytest.raises(ValidationError, match="greater than 0"):
        RSSNode(name="test_rss", sources=["https://example.com/feed.xml"], timeout=0.0)


@pytest.mark.asyncio
async def test_rss_node_parses_atom_links_with_href():
    """Test RSS node parsing for Atom link attributes."""
    sources = ["https://example.com/atom.xml"]
    node = RSSNode(name="test_rss", sources=sources)
    state = {}
    config = RunnableConfig()

    atom_xml = (
        "<feed xmlns='http://www.w3.org/2005/Atom'>"
        "<entry>"
        "<title>Atom Entry</title>"
        "<link rel='alternate' href='https://example.com/atom-entry'/>"
        "<summary>Atom summary</summary>"
        "<updated>2024-01-01T00:00:00Z</updated>"
        "</entry>"
        "</feed>"
    )

    mock_resp = Mock()
    mock_resp.text = atom_xml

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    with patch("orcheo.nodes.rss.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await node.run(state, config)

    assert result["fetched_count"] == 1
    assert result["errors"] == []
    assert result["documents"][0]["title"] == "Atom Entry"
    assert result["documents"][0]["link"] == "https://example.com/atom-entry"
    assert result["documents"][0]["description"] == "Atom summary"
    assert result["documents"][0]["pubDate"] == "2024-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_rss_node_run_empty_feeds():
    """Test RSS node run method with empty feeds."""
    sources = ["https://example.com/empty.xml"]
    node = RSSNode(name="test_rss", sources=sources)
    state = {}
    config = RunnableConfig()

    mock_resp = Mock()
    mock_resp.text = ""

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    with patch("orcheo.nodes.rss.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await node.run(state, config)

    assert result["documents"] == []
    assert result["fetched_count"] == 0
    assert len(result["errors"]) == 1
    mock_client.get.assert_called_once_with(
        "https://example.com/empty.xml", timeout=15.0
    )


@pytest.mark.asyncio
async def test_rss_node_run_malformed_xml_returns_structured_error():
    """Malformed XML should be captured as a per-source error."""
    sources = ["https://example.com/bad.xml"]
    node = RSSNode(name="test_rss", sources=sources)
    state = {}
    config = RunnableConfig()

    mock_resp = Mock()
    mock_resp.text = "<rss><channel><item><title>Broken"

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    with patch("orcheo.nodes.rss.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await node.run(state, config)

    assert result["documents"] == []
    assert result["fetched_count"] == 0
    assert result["failed_sources"] == 1
    assert result["errors"] == [
        {
            "source": "https://example.com/bad.xml",
            "error": "Malformed XML feed response",
        }
    ]


@pytest.mark.asyncio
async def test_rss_node_run_no_sources():
    """Test RSS node run method with no sources."""
    node = RSSNode(name="test_rss", sources=[])
    state = {}
    config = RunnableConfig()

    mock_client = AsyncMock()

    with patch("orcheo.nodes.rss.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await node.run(state, config)

    assert result["documents"] == []
    assert result["fetched_count"] == 0
    mock_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_rss_node_run_non_2xx_response_is_reported_as_error():
    """Test RSS node run reports non-2xx responses as source failures."""
    sources = ["https://example.com/protected.xml"]
    node = RSSNode(name="test_rss", sources=sources)
    state = {}
    config = RunnableConfig()

    mock_resp = Mock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Unauthorized",
        request=httpx.Request("GET", "https://example.com/protected.xml"),
        response=httpx.Response(401),
    )

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    with patch("orcheo.nodes.rss.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await node.run(state, config)

    assert result["documents"] == []
    assert result["fetched_count"] == 0
    assert len(result["errors"]) == 1
    assert result["failed_sources"] == 1
    assert result["errors"][0]["source"] == "https://example.com/protected.xml"
    mock_client.get.assert_called_once_with(
        "https://example.com/protected.xml", timeout=15.0
    )


@pytest.mark.asyncio
async def test_rss_node_configures_http_client_for_redirects_and_feed_headers():
    """RSS client should follow redirects and advertise feed-friendly headers."""
    sources = ["https://example.com/feed.xml"]
    node = RSSNode(name="test_rss", sources=sources)
    state = {}
    config = RunnableConfig()

    mock_resp = Mock()
    mock_resp.text = "<rss><channel></channel></rss>"

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    with patch("orcheo.nodes.rss.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await node.run(state, config)

    mock_cls.assert_called_once_with(
        follow_redirects=True,
        headers=RSS_REQUEST_HEADERS,
    )


# ---------------------------------------------------------------------------
# Unit tests for static helpers – targeting uncovered branches
# ---------------------------------------------------------------------------


def test_local_name_empty_tag() -> None:
    """_local_name returns empty string for an empty tag (line 45)."""
    assert RSSNode._local_name("") == ""


def test_child_text_matching_child_with_empty_text_continues() -> None:
    """_child_text skips a matching child with no text and returns the
    next (branch 61->57).
    """
    parent = ElementTree.fromstring(
        "<item><title></title><title>Second Title</title></item>"
    )
    result = RSSNode._child_text(parent, {"title"})
    assert result == "Second Title"


def test_extract_link_non_alternate_href_stored_and_returned() -> None:
    """Non-alternate href is stored as fallback and returned at loop end
    (lines 77-79, 83).
    """
    parent = ElementTree.fromstring(
        '<entry><link href="https://example.com/page" rel="self"/></entry>'
    )
    result = RSSNode._extract_link(parent)
    assert result == "https://example.com/page"


def test_extract_link_no_href_no_text_continues_loop() -> None:
    """Link with no href and no text is skipped; fallback href from next
    link is returned (branch 81->69).
    """
    parent = ElementTree.fromstring(
        '<entry><link/><link href="https://example.com/second" rel="self"/></entry>'
    )
    result = RSSNode._extract_link(parent)
    assert result == "https://example.com/second"


def test_extract_link_second_non_alternate_href_skips_overwrite() -> None:
    """When fallback_href already set, a second non-alternate href does
    not overwrite it (branch 77->79).
    """
    parent = ElementTree.fromstring(
        "<entry>"
        '<link href="https://example.com/first" rel="self"/>'
        '<link href="https://example.com/second" rel="self"/>'
        "</entry>"
    )
    result = RSSNode._extract_link(parent)
    assert result == "https://example.com/first"


# ---------------------------------------------------------------------------
# _normalize_date – targeting uncovered lines 98, 105, 107-109
# ---------------------------------------------------------------------------


def test_normalize_date_rfc2822_returns_utc_iso() -> None:
    """_normalize_date converts a valid RFC 2822 date to UTC ISO 8601 (line 98)."""
    raw = "Mon, 15 Jan 2024 10:00:00 +0000"
    result = RSSNode._normalize_date(raw)
    assert result == "2024-01-15T10:00:00+00:00"


def test_normalize_date_naive_iso_datetime_gains_utc() -> None:
    """_normalize_date adds UTC to a naive ISO 8601 datetime (line 105)."""
    raw = "2024-01-15T10:00:00"  # no timezone suffix
    result = RSSNode._normalize_date(raw)
    assert result == "2024-01-15T10:00:00+00:00"


def test_normalize_date_invalid_string_returns_empty() -> None:
    """_normalize_date returns empty string when both parsers fail (lines 107-109)."""
    result = RSSNode._normalize_date("not-a-date")
    assert result == ""


# ---------------------------------------------------------------------------
# _flatten_sources – targeting uncovered lines 62-65
# ---------------------------------------------------------------------------


def test_flatten_sources_nested_iterable_is_flattened() -> None:
    """A nested iterable (non-string) is expanded via the elif branch (lines 62-63)."""
    result = RSSNode._flatten_sources(
        ["https://a.com/feed.xml", ("https://b.com/feed.xml", "https://c.com/feed.xml")]
    )
    assert result == [
        "https://a.com/feed.xml",
        "https://b.com/feed.xml",
        "https://c.com/feed.xml",
    ]


def test_flatten_sources_non_iterable_item_is_stringified() -> None:
    """A non-string, non-iterable item falls through to the else branch (lines 64-65)."""  # noqa: E501
    result = RSSNode._flatten_sources(["https://a.com/feed.xml", 42])
    assert result == ["https://a.com/feed.xml", "42"]
