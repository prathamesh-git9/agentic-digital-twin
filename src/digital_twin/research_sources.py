from __future__ import annotations

import asyncio
import re
import urllib.robotparser
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol
from urllib.parse import unquote, urljoin, urlparse

import httpx
from pydantic import BaseModel, Field
from selectolax.parser import HTMLParser
from trafilatura import extract

from .security import is_public_http_url, sanitize_external_text

USER_AGENT = "PrathameshDigitalTwin/2.0 (+public-source-research)"


class AttributedFact(BaseModel):
    value: str = Field(min_length=1, max_length=500)
    source_url: str
    confidence: Literal["high", "medium", "low"]
    why: str = Field(min_length=1, max_length=300)
    source_kind: str = "public_web"
    subject_name: str | None = None
    company_level: bool = False


class SourceReport(BaseModel):
    source: str
    status: Literal["ok", "empty", "blocked", "timeout", "failed"]
    url: str | None = None
    detail: str | None = None


class PublicDocument(BaseModel):
    url: str
    title: str = ""
    text: str = ""
    links: list[str] = []
    link_labels: dict[str, str] = {}
    email_addresses: list[str] = []
    image_url: str | None = None
    source: str = "public_web"


class PageFetcher(Protocol):
    async def fetch(self, url: str) -> tuple[PublicDocument | None, SourceReport]: ...


class RobotsTransport(Protocol):
    async def get_text(self, url: str, *, timeout_seconds: float) -> str: ...


class HttpxRobotsTransport:
    async def get_text(self, url: str, *, timeout_seconds: float) -> str:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout_seconds,
            )
        if 400 <= response.status_code < 500:
            return "User-agent: *\nAllow: /"
        response.raise_for_status()
        return response.text


class RobotsPolicy:
    """RFC 9309 policy check with a fail-closed network-error posture."""

    def __init__(
        self,
        *,
        timeout: float,
        transport: RobotsTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self.transport = transport or HttpxRobotsTransport()
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    async def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._cache:
            robots_url = f"{origin}/robots.txt"
            try:
                body = await asyncio.wait_for(
                    self.transport.get_text(robots_url, timeout_seconds=self.timeout),
                    timeout=self.timeout,
                )
            except Exception:  # noqa: BLE001 - an unavailable policy fails closed
                self._cache[origin] = None
            else:
                parser = urllib.robotparser.RobotFileParser(robots_url)
                parser.parse(body.splitlines())
                self._cache[origin] = parser
        parser = self._cache[origin]
        return bool(parser and parser.can_fetch(USER_AGENT, url))


class ScraplingPageFetcher:
    """Robots-aware public fetcher; Scrapling is imported only at the network edge."""

    def __init__(
        self,
        *,
        timeout: float = 5.0,
        robots: RobotsPolicy | None = None,
        fetch_html: Callable[[str, float], Awaitable[str]] | None = None,
    ) -> None:
        self.timeout = timeout
        self.robots = robots or RobotsPolicy(timeout=timeout)
        self.fetch_html = fetch_html or self._scrapling_fetch

    @staticmethod
    async def _scrapling_fetch(url: str, timeout_seconds: float) -> str:
        from scrapling.fetchers import AsyncFetcher

        page = await AsyncFetcher.get(
            url,
            timeout=timeout_seconds,
            stealthy_headers=False,
            follow_redirects=True,
        )
        status = int(getattr(page, "status", getattr(page, "status_code", 200)))
        if status >= 400:
            raise httpx.HTTPStatusError(
                f"public page returned {status}",
                request=httpx.Request("GET", url),
                response=httpx.Response(status),
            )
        raw = getattr(page, "html_content", None)
        if raw is None:
            raw = getattr(page, "body", None)
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw if raw is not None else page)

    async def fetch(self, url: str) -> tuple[PublicDocument | None, SourceReport]:
        if not is_public_http_url(url):
            return None, SourceReport(
                source="public_web", status="blocked", url=url, detail="non-public URL"
            )
        try:
            allowed = await self.robots.allowed(url)
        except Exception:  # noqa: BLE001 - policy errors are represented, never raised
            allowed = False
        if not allowed:
            return None, SourceReport(
                source="public_web", status="blocked", url=url, detail="robots policy"
            )
        try:
            html = await asyncio.wait_for(
                self.fetch_html(url, self.timeout), timeout=self.timeout
            )
        except TimeoutError:
            return None, SourceReport(source="public_web", status="timeout", url=url)
        except Exception:  # noqa: BLE001 - each source must degrade independently
            return None, SourceReport(source="public_web", status="failed", url=url)
        document = extract_public_document(html, url)
        if not document.text and not document.title and not document.email_addresses:
            return None, SourceReport(source="public_web", status="empty", url=url)
        return document, SourceReport(source="public_web", status="ok", url=url)


def extract_public_document(html: str, url: str) -> PublicDocument:
    tree = HTMLParser(html)
    title_node = tree.css_first("title")
    title = sanitize_external_text(
        title_node.text(separator=" ") if title_node else "", max_length=240
    )
    image_node = tree.css_first('meta[property="og:image"]')
    image_url = (
        urljoin(url, image_node.attributes.get("content", "")) if image_node else None
    )
    if image_url and not is_public_http_url(image_url):
        image_url = None
    main_text = (
        extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        or ""
    )
    # Sanitise paragraph-by-paragraph so one malicious fragment cannot poison the page.
    safe_parts = [
        safe
        for part in re.split(r"[\r\n]+", main_text)
        if (safe := sanitize_external_text(part, max_length=1_000))
    ]
    links: list[str] = []
    link_labels: dict[str, str] = {}
    email_addresses: list[str] = []
    for node in tree.css("a[href]")[:300]:
        href = node.attributes.get("href", "").strip()
        if href.casefold().startswith("mailto:"):
            mailbox = unquote(href[7:].split("?", 1)[0]).strip()
            if mailbox:
                email_addresses.append(mailbox)
            continue
        absolute = urljoin(url, href)
        if is_public_http_url(absolute):
            links.append(absolute)
            label = sanitize_external_text(node.text(separator=" "), max_length=240)
            if label:
                link_labels[absolute] = label
    return PublicDocument(
        url=url,
        title=title,
        text="\n".join(safe_parts)[:20_000],
        links=list(dict.fromkeys(links)),
        link_labels=link_labels,
        email_addresses=list(dict.fromkeys(email_addresses)),
        image_url=image_url,
    )
