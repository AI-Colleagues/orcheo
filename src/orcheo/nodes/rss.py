"""RSS feed fetching and parsing node."""

from __future__ import annotations
import datetime
from collections.abc import Iterable
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree
import httpx
from langchain_core.runnables import RunnableConfig
from pydantic import Field, field_validator
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.security.ssrf import restricted_egress_client_kwargs


RSS_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OrcheoRSS/1.0; +https://orcheo.ai)",
    "Accept": (
        "application/rss+xml, application/atom+xml, application/xml, "
        "text/xml;q=0.9, */*;q=0.8"
    ),
}


@registry.register(
    NodeMetadata(
        name="RSSNode",
        description="Fetch and parse RSS/Atom feeds from multiple sources",
        category="rss",
    )
)
class RSSNode(TaskNode):
    """Fetch RSS/Atom feeds via HTTP and parse their entries.

    Supports both RSS 2.0 ``<item>`` and Atom ``<entry>`` elements.
    Each source is fetched independently; per-source errors are captured
    without aborting the remaining feeds.
    """

    sources: list[str] | str = Field(description="RSS/Atom feed URLs to fetch")
    timeout: float = Field(
        default=15.0,
        gt=0.0,
        description="HTTP timeout in seconds per feed",
    )

    @classmethod
    def _flatten_sources(cls, value: str | Iterable[Any]) -> list[str]:
        """Flatten sources into a list of URL strings.

        Handles a bare string, a flat list, nested lists, or other
        iterables (tuples, sets) that ``resolved_for_run`` may produce
        because ``model_copy`` bypasses Pydantic field validators.
        """
        if isinstance(value, str):
            return [value]
        flat: list[str] = []
        for item in value:
            if isinstance(item, str):
                flat.append(item)
            elif isinstance(item, Iterable):
                flat.extend(str(i) for i in item)
            else:
                flat.append(str(item))
        return flat

    @field_validator("sources", mode="before")
    @classmethod
    def _normalize_sources(cls, value: list[str | list[str]] | str) -> list[str]:
        """Normalize a single feed URL or nested lists into a flat list of sources."""
        return cls._flatten_sources(value)

    # ------------------------------------------------------------------
    # XML helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _local_name(tag: str) -> str:
        """Return an XML tag without its namespace prefix."""
        if not tag:
            return ""
        if "}" in tag:
            return tag.rsplit("}", 1)[1]
        return tag

    @staticmethod
    def _child_text(
        parent: ElementTree.Element,
        names: set[str],
    ) -> str:
        """Return first matching child element text by local-name."""
        lowered = {name.lower() for name in names}
        for child in list(parent):
            if RSSNode._local_name(child.tag).lower() not in lowered:
                continue
            text = "".join(child.itertext()).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _extract_link(parent: ElementTree.Element) -> str:
        """Extract RSS/Atom links from item or entry children."""
        fallback_href = ""
        for child in list(parent):
            if RSSNode._local_name(child.tag).lower() != "link":
                continue
            href = (child.attrib.get("href") or "").strip()
            rel = (child.attrib.get("rel") or "").strip().lower()
            if href:
                if rel == "alternate":
                    return href
                if not fallback_href:
                    fallback_href = href
                continue
            text = "".join(child.itertext()).strip()
            if text:
                return text
        return fallback_href

    @staticmethod
    def _normalize_date(raw: str) -> str:
        """Normalize an RSS/Atom date string to UTC ISO 8601.

        Handles RFC 2822 (RSS 2.0) and ISO 8601 (Atom) formats.
        Returns an empty string when the date cannot be parsed.
        """
        if not raw:
            return ""
        # RFC 2822 – used by RSS 2.0 <pubDate>
        try:
            dt = parsedate_to_datetime(raw)
            return dt.astimezone(datetime.UTC).isoformat()
        except (ValueError, TypeError):
            pass
        # ISO 8601 – used by Atom <published>/<updated>
        try:
            dt = datetime.datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.UTC)
            return dt.astimezone(datetime.UTC).isoformat()
        except ValueError:
            pass
        return ""

    @classmethod
    def _parse_items(cls, body: str) -> list[dict[str, str]]:
        """Parse RSS/Atom items from an XML body."""
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            msg = "Malformed XML feed response"
            raise ValueError(msg) from exc

        items: list[dict[str, str]] = []
        for element in root.iter():
            local_name = cls._local_name(element.tag).lower()
            if local_name not in {"item", "entry"}:
                continue

            title = cls._child_text(element, {"title"})
            link = cls._extract_link(element)
            description = cls._child_text(element, {"description", "summary"})
            pub_date = cls._child_text(element, {"pubDate", "published", "updated"})

            items.append(
                {
                    "title": title,
                    "link": link,
                    "description": description,
                    "pubDate": pub_date,
                    "isoDate": cls._normalize_date(pub_date),
                }
            )
        return items

    # ------------------------------------------------------------------
    # Node execution
    # ------------------------------------------------------------------

    async def _fetch_source(
        self,
        client: httpx.AsyncClient,
        url: str,
        now_iso: str,
    ) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
        """Fetch a single source, returning (documents, error_or_None)."""
        try:
            response = await client.get(url, timeout=self.timeout)
            response.raise_for_status()
            body = response.text
        except httpx.HTTPError as exc:
            return [], {"source": url, "error": str(exc)}

        if not body:
            return [], {"source": url, "error": "Empty feed response"}

        try:
            parsed_items = self._parse_items(body)
        except ValueError as exc:
            return [], {"source": url, "error": str(exc)}

        documents: list[dict[str, Any]] = []
        for item in parsed_items:
            documents.append(
                {
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "description": item.get("description", ""),
                    "pubDate": item.get("pubDate", ""),
                    "isoDate": item.get("isoDate", ""),
                    "source": url,
                    "read": False,
                    "fetched_at": now_iso,
                }
            )
        return documents, None

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Fetch all configured RSS sources and return parsed documents."""
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        documents: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        async with httpx.AsyncClient(
            follow_redirects=True,
            headers=RSS_REQUEST_HEADERS,
            **restricted_egress_client_kwargs(),
        ) as client:
            # Re-flatten because resolved_for_run() uses model_copy(update=…)
            # which bypasses Pydantic field validators, so self.sources may
            # be a bare string, tuple, set, or nested list after templating.
            flat_sources = self._flatten_sources(self.sources)
            for url in flat_sources:
                docs, error = await self._fetch_source(client, url, now_iso)
                documents.extend(docs)
                if error is not None:
                    errors.append(error)

        return {
            "documents": documents,
            "errors": errors,
            "fetched_count": len(documents),
            "failed_sources": len(errors),
        }
