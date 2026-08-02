from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from pydantic import BaseModel, Field

from .security import (
    is_public_http_url,
    normalize_name,
    normalized_cache_key,
    sanitize_external_text,
)


@dataclass(frozen=True, slots=True)
class RawSearchResult:
    title: str
    url: str
    snippet: str = ""
    thumbnail: str | None = None


class SearchProvider(Protocol):
    async def search(
        self, name: str, company: str | None, limit: int
    ) -> list[RawSearchResult]:
        """Return public results without ever using social credentials."""


class Candidate(BaseModel):
    id: str
    name: str
    headline: str
    company: str | None = None
    photo_url: str | None = None
    initials: str
    source_link: str
    source_label: str
    confidence: int = Field(ge=0, le=100)
    why: list[str]


class SearchOutcome(BaseModel):
    status: str
    candidates: list[Candidate] = []
    message: str
    provider_failed: bool = False


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._capture: str | None = None
        self._text: list[str] = []
        self._href = ""
        self._tag = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if "result__a" in classes:
            self._capture, self._tag = "title", tag
            self._href = values.get("href") or ""
            self._text = []
        elif "result__snippet" in classes:
            self._capture, self._tag = "snippet", tag
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture or tag != self._tag:
            return
        text = " ".join(self._text).strip()
        if self._capture == "title":
            self.results.append({"title": text, "url": self._href, "snippet": ""})
        elif self.results:
            self.results[-1]["snippet"] = text
        self._capture = None
        self._text = []


def _unwrap_duckduckgo_url(value: str) -> str:
    if value.startswith("//"):
        value = "https:" + value
    parsed = urlparse(value)
    if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(target[0])
    return value


class DuckDuckGoSearchProvider:
    endpoint = "https://html.duckduckgo.com/html/"

    def __init__(self, *, timeout: float = 8.0, client: httpx.AsyncClient | None = None):
        self.timeout = timeout
        self.client = client

    async def search(
        self, name: str, company: str | None, limit: int
    ) -> list[RawSearchResult]:
        query = f'"{name}" {company or "professional profile"}'
        headers = {"User-Agent": "digital-twin/0.1 (+public-source-discovery)"}
        if self.client is not None:
            response = await self.client.post(
                self.endpoint, data={"q": query}, headers=headers, timeout=self.timeout
            )
        else:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.post(
                    self.endpoint,
                    data={"q": query},
                    headers=headers,
                    timeout=self.timeout,
                )
        response.raise_for_status()
        parser = _DuckDuckGoParser()
        parser.feed(response.text)
        return [
            RawSearchResult(
                title=result["title"],
                url=_unwrap_duckduckgo_url(result["url"]),
                snippet=result["snippet"],
            )
            for result in parser.results[:limit]
        ]


class TavilySearchProvider:
    endpoint = "https://api.tavily.com/search"

    def __init__(self, api_key: str, *, timeout: float = 8.0) -> None:
        self.api_key, self.timeout = api_key, timeout

    async def search(
        self, name: str, company: str | None, limit: int
    ) -> list[RawSearchResult]:
        payload = {
            "api_key": self.api_key,
            "query": f'"{name}" {company or "professional profile"}',
            "max_results": limit,
            "include_images": True,
            "search_depth": "basic",
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.endpoint, json=payload, timeout=self.timeout
            )
        response.raise_for_status()
        data = response.json()
        images = data.get("images") or []
        return [
            RawSearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("content", "")),
                thumbnail=images[index] if index < len(images) else None,
            )
            for index, item in enumerate(data.get("results", [])[:limit])
        ]


class SerperSearchProvider:
    endpoint = "https://google.serper.dev/search"

    def __init__(self, api_key: str, *, timeout: float = 8.0) -> None:
        self.api_key, self.timeout = api_key, timeout

    async def search(
        self, name: str, company: str | None, limit: int
    ) -> list[RawSearchResult]:
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        payload = {"q": f'"{name}" {company or "professional profile"}', "num": limit}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.endpoint, json=payload, headers=headers, timeout=self.timeout
            )
        response.raise_for_status()
        return [
            RawSearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("link", "")),
                snippet=str(item.get("snippet", "")),
                thumbnail=item.get("imageUrl"),
            )
            for item in response.json().get("organic", [])[:limit]
        ]


class BraveSearchProvider:
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, *, timeout: float = 8.0) -> None:
        self.api_key, self.timeout = api_key, timeout

    async def search(
        self, name: str, company: str | None, limit: int
    ) -> list[RawSearchResult]:
        headers = {"X-Subscription-Token": self.api_key, "Accept": "application/json"}
        params: dict[str, Any] = {
            "q": f'"{name}" {company or "professional profile"}',
            "count": limit,
        }
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.endpoint, params=params, headers=headers, timeout=self.timeout
            )
        response.raise_for_status()
        return [
            RawSearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("description", "")),
                thumbnail=(item.get("thumbnail") or {}).get("src"),
            )
            for item in response.json().get("web", {}).get("results", [])[:limit]
        ]


class ResearchCache:
    """TTL memory cache: useful across tabs, impossible to survive a process restart."""

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._values: dict[str, tuple[float, list[RawSearchResult]]] = {}

    def get(self, name: str) -> list[RawSearchResult] | None:
        key = normalized_cache_key(name)
        value = self._values.get(key)
        if not value:
            return None
        expires_at, results = value
        if time.monotonic() >= expires_at:
            self._values.pop(key, None)
            return None
        return list(results)

    def put(self, name: str, results: list[RawSearchResult]) -> None:
        self._values[normalized_cache_key(name)] = (
            time.monotonic() + self.ttl_seconds,
            list(results),
        )

    def purge(self, name: str) -> None:
        self._values.pop(normalized_cache_key(name), None)


def _words(value: str) -> set[str]:
    return {word.casefold() for word in re.findall(r"[\w]+", value)}


def _source_label(url: str) -> str:
    host = (urlparse(url).hostname or "public web").removeprefix("www.")
    return host


def score_candidate(
    query_name: str,
    result: RawSearchResult,
    *,
    rank: int,
    company: str | None = None,
    location: str | None = None,
) -> tuple[int, list[str]]:
    """Score observable signals only; no model is involved in this confidence value."""
    safe_title = sanitize_external_text(result.title)
    safe_snippet = sanitize_external_text(result.snippet)
    combined = f"{safe_title} {safe_snippet}"
    query_tokens = _words(query_name)
    observed_tokens = _words(safe_title)
    name_ratio = len(query_tokens & observed_tokens) / max(1, len(query_tokens))
    name_points = round(55 * name_ratio)
    score = name_points
    why = [
        f"name tokens matched {len(query_tokens & observed_tokens)}/{len(query_tokens)}"
    ]

    if company:
        company_tokens = _words(company)
        overlap = company_tokens & _words(combined)
        if overlap:
            score += 20
            why.append("stated company appears in the public result")
        else:
            why.append("stated company was not observed")

    if location:
        if _words(location) & _words(combined):
            score += 10
            why.append("stated location overlaps")
        else:
            why.append("stated location was not observed")

    rank_points = max(2, 10 - (rank - 1) * 2)
    score += rank_points
    why.append(f"search result rank {rank}")

    host = (urlparse(result.url).hostname or "").casefold()
    if host.endswith("linkedin.com") and "/in/" in urlparse(result.url).path:
        score += 10
        why.append("public LinkedIn profile result")
    elif any(token in host for token in _words(company or "")):
        score += 8
        why.append("source domain overlaps the stated company")
    else:
        score += 4
        why.append("publicly indexed source")
    return min(100, score), why


def _candidate_name(query_name: str, title: str) -> str:
    first = re.split(r"\s+(?:[-–—|·])\s+", title, maxsplit=1)[0].strip()
    if _words(query_name) <= _words(first):
        return first[:80]
    return normalize_name(query_name)


def _headline(title: str, snippet: str, candidate_name: str) -> str:
    value = title
    if value.casefold().startswith(candidate_name.casefold()):
        value = value[len(candidate_name) :].lstrip(" -–—|·")
    value = re.sub(r"\s*[|·-]\s*LinkedIn\s*$", "", value, flags=re.I)
    return (value or snippet or "Professional profile")[:180]


def _initials(name: str) -> str:
    parts = [part for part in name.split() if part]
    return "".join(part[0].upper() for part in parts[:2]) or "?"


class ResearchEngine:
    def __init__(self, provider: SearchProvider, *, cache_ttl_seconds: int = 900) -> None:
        self.provider = provider
        self.cache = ResearchCache(cache_ttl_seconds)

    async def find(
        self,
        name: str,
        *,
        company: str | None = None,
        location: str | None = None,
        limit: int = 6,
    ) -> SearchOutcome:
        safe_name = normalize_name(name)
        if not safe_name:
            return SearchOutcome(status="empty", message="No name was supplied.")
        results = self.cache.get(safe_name)
        failed = False
        if results is None:
            try:
                results = await self.provider.search(safe_name, company, limit)
                self.cache.put(safe_name, results)
            except Exception:  # noqa: BLE001 - every provider failure degrades to an empty result
                results, failed = [], True

        candidates: list[Candidate] = []
        for rank, raw in enumerate(results[:limit], start=1):
            title = sanitize_external_text(raw.title, max_length=180)
            snippet = sanitize_external_text(raw.snippet, max_length=240)
            url = _unwrap_duckduckgo_url(raw.url)
            if not title or not is_public_http_url(url):
                continue
            confidence, why = score_candidate(
                safe_name,
                RawSearchResult(title, url, snippet, raw.thumbnail),
                rank=rank,
                company=company,
                location=location,
            )
            if not _words(safe_name) & _words(title):
                continue
            candidate_name = _candidate_name(safe_name, title)
            observed_company = (
                company
                if company and _words(company) & _words(f"{title} {snippet}")
                else None
            )
            photo = (
                raw.thumbnail
                if raw.thumbnail and is_public_http_url(raw.thumbnail)
                else None
            )
            candidates.append(
                Candidate(
                    id=hashlib.sha256(f"{candidate_name}|{url}".encode()).hexdigest()[
                        :12
                    ],
                    name=candidate_name,
                    headline=_headline(title, snippet, candidate_name),
                    company=observed_company,
                    photo_url=photo,
                    initials=_initials(candidate_name),
                    source_link=url,
                    source_label=_source_label(url),
                    confidence=math.floor(confidence),
                    why=why,
                )
            )

        if not candidates:
            return SearchOutcome(
                status="empty",
                message=(
                    "Couldn't find anything useful in public sources. Chat is unaffected."
                ),
                provider_failed=failed,
            )
        return SearchOutcome(
            status="candidates",
            candidates=candidates,
            message=f"Found {len(candidates)} possible public match"
            f"{'es' if len(candidates) != 1 else ''}. Please confirm before I use one.",
        )
