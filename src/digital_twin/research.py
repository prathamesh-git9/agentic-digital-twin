from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import tldextract
from pydantic import BaseModel, Field

from .email_utils import EMAIL_PATTERN, normalize_address, recipient_key
from .identity import (
    PublicNameEvidence,
    extract_public_name,
    name_tokens,
    names_compatible,
    resolve_public_name,
    token_equivalent,
)
from .research_sources import (
    AttributedFact,
    PageFetcher,
    PublicDocument,
    SourceReport,
)
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


class SupplementalResearchSource(Protocol):
    name: str

    async def discover(self, company: str) -> list[RawSearchResult]: ...


class FeedReader(Protocol):
    async def read(self, url: str) -> list[RawSearchResult]: ...


class ProfileLink(BaseModel):
    kind: str
    url: str
    handle: str | None = None
    source_url: str
    verified: bool


class CandidateEmail(BaseModel):
    address: str
    status: Literal["verified", "inferred"]
    confidence: Literal["high", "medium", "low"]
    source_url: str
    why: str
    pattern: str | None = None
    score: int = Field(default=0, ge=0)
    mx_valid: bool | None = None
    source_kind: str = "public_web"
    company_level: bool = False


class CandidateAvatar(BaseModel):
    kind: Literal["photo", "gravatar", "initials"]
    url: str | None = None
    initials: str | None = None
    source_url: str


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
    name_detail: AttributedFact | None = None
    role: AttributedFact | None = None
    company_detail: AttributedFact | None = None
    location: AttributedFact | None = None
    bio: AttributedFact | None = None
    photo: AttributedFact | None = None
    avatar: CandidateAvatar | None = None
    profiles: list[ProfileLink] = []
    email: CandidateEmail | None = None
    emails: list[CandidateEmail] = []
    submitted_name: str | None = None
    surname_resolved: bool = False
    name_variants: list[str] = []


class PersonDossier(BaseModel):
    candidate_id: str
    headline: AttributedFact | None = None
    company: AttributedFact | None = None
    public_profiles: list[AttributedFact] = []
    public_emails: list[AttributedFact] = []
    talks: list[AttributedFact] = []
    recent_mentions: list[AttributedFact] = []


class CompanyDossier(BaseModel):
    name: str | None = None
    domain: AttributedFact | None = None
    website: AttributedFact | None = None
    careers_page: AttributedFact | None = None
    engineering_blog: AttributedFact | None = None
    github_org: AttributedFact | None = None
    tech_stack: list[AttributedFact] = []
    recent_news: list[AttributedFact] = []
    funding: list[AttributedFact] = []
    feeds: list[AttributedFact] = []
    public_emails: list[AttributedFact] = []


class CandidateDossier(BaseModel):
    candidate_id: str
    person: PersonDossier
    company: CompanyDossier
    documents: list[PublicDocument] = []


class SearchOutcome(BaseModel):
    status: str
    candidates: list[Candidate] = []
    message: str
    provider_failed: bool = False
    dossiers: list[CandidateDossier] = []
    source_reports: list[SourceReport] = []


class CompanyResearchOutcome(BaseModel):
    status: Literal["ok", "empty", "failed"]
    dossier: CompanyDossier
    documents: list[PublicDocument] = []
    source_reports: list[SourceReport] = []


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


_TLDEXTRACT = tldextract.TLDExtract(suffix_list_urls=())


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
        return await self.search_query(query, limit)

    async def search_query(self, query: str, limit: int) -> list[RawSearchResult]:
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
        return await self.search_query(
            f'"{name}" {company or "professional profile"}', limit
        )

    async def search_query(self, query: str, limit: int) -> list[RawSearchResult]:
        payload = {
            "api_key": self.api_key,
            "query": query,
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
        return await self.search_query(
            f'"{name}" {company or "professional profile"}', limit
        )

    async def search_query(self, query: str, limit: int) -> list[RawSearchResult]:
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        payload = {"q": query, "num": limit}
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
        return await self.search_query(
            f'"{name}" {company or "professional profile"}', limit
        )

    async def search_query(self, query: str, limit: int) -> list[RawSearchResult]:
        headers = {"X-Subscription-Token": self.api_key, "Accept": "application/json"}
        params: dict[str, Any] = {
            "q": query,
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


def _name_match_count(query_name: str, observed: str) -> int:
    observed_tokens = name_tokens(observed)
    return sum(
        any(token_equivalent(token, value) for value in observed_tokens)
        for token in name_tokens(query_name)
    )


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
    query_tokens = name_tokens(query_name)
    name_matches = _name_match_count(query_name, safe_title)
    name_ratio = name_matches / max(1, len(query_tokens))
    name_points = round(55 * name_ratio)
    score = name_points
    why = [f"name tokens matched {name_matches}/{len(query_tokens)}"]

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
    observed = extract_public_name(query_name, title)
    if observed:
        return observed[:100]
    return normalize_name(query_name)


def _name_source_kind(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    if host == "github.com" and len([part for part in path.split("/") if part]) == 1:
        return "github_profile"
    if any(value in path for value in ("/team", "/people", "/leadership")):
        return "company_team"
    if any(value in path for value in ("speaker", "conference", "/talk", "cfp")):
        return "conference_speaker"
    if host.endswith("linkedin.com"):
        return "public_profile"
    return "search_result"


def _resolve_research_name(
    submitted_name: str,
    result: RawSearchResult,
    documents: list[PublicDocument],
    groups: dict[str, list[RawSearchResult]],
    *,
    rank: int,
    allow_shared_evidence: bool,
):
    evidence = [
        PublicNameEvidence(
            name=result.title,
            source_url=result.url,
            source_kind=_name_source_kind(result.url),
            confidence=max(65, 96 - (rank - 1) * 5),
        )
    ]
    for document in documents:
        if document.url == result.url or allow_shared_evidence:
            evidence.append(
                PublicNameEvidence(
                    name=document.title,
                    source_url=document.url,
                    source_kind=(
                        _name_source_kind(document.url)
                        if _name_source_kind(document.url) != "search_result"
                        else "page_title"
                    ),
                    confidence=90 if document.url == result.url else 75,
                )
            )
    if allow_shared_evidence:
        for source, values in groups.items():
            if source in {"identity", "company_website", "careers"}:
                continue
            for value in _safe_results(values)[:2]:
                evidence.append(
                    PublicNameEvidence(
                        name=value.title,
                        source_url=value.url,
                        source_kind=_name_source_kind(value.url),
                        confidence=78,
                    )
                )
    return resolve_public_name(submitted_name, evidence)


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
    def __init__(
        self,
        provider: SearchProvider,
        *,
        cache_ttl_seconds: int = 900,
        page_fetcher: PageFetcher | None = None,
        source_timeout_seconds: float = 5.0,
        page_limit: int = 0,
        supplemental_sources: tuple[SupplementalResearchSource, ...] = (),
        feed_reader: FeedReader | None = None,
    ) -> None:
        self.provider = provider
        self.cache = ResearchCache(cache_ttl_seconds)
        self.page_fetcher = page_fetcher
        self.source_timeout_seconds = source_timeout_seconds
        self.page_limit = page_limit
        self.supplemental_sources = supplemental_sources
        self.feed_reader = feed_reader

    async def find(
        self,
        name: str,
        *,
        company: str | None = None,
        location: str | None = None,
        limit: int = 6,
        progress: ProgressCallback | None = None,
    ) -> SearchOutcome:
        safe_name = normalize_name(name)
        if not safe_name:
            return SearchOutcome(status="empty", message="No name was supplied.")
        await _emit(progress, "search", "running", f"Searching for {safe_name}")
        results = self.cache.get(safe_name)
        failed = False
        reports: list[SourceReport] = []
        if results is None:
            try:
                results = await asyncio.wait_for(
                    self.provider.search(safe_name, company, limit),
                    timeout=self.source_timeout_seconds,
                )
                self.cache.put(safe_name, results)
                reports.append(SourceReport(source="identity_search", status="ok"))
            except TimeoutError:
                results, failed = [], True
                reports.append(SourceReport(source="identity_search", status="timeout"))
            except Exception:  # noqa: BLE001 - every provider failure degrades to empty
                results, failed = [], True
                reports.append(SourceReport(source="identity_search", status="failed"))
        else:
            reports.append(
                SourceReport(source="identity_search", status="ok", detail="cache")
            )

        groups: dict[str, list[RawSearchResult]] = {"identity": results}
        query_method = getattr(self.provider, "search_query", None)
        if callable(query_method):
            query_specs = _research_queries(safe_name, company)
            tasks = {
                source: asyncio.create_task(
                    _bounded_query(
                        query_method,
                        query,
                        min(limit, 4),
                        self.source_timeout_seconds,
                    )
                )
                for source, query in query_specs.items()
            }
            try:
                for source, task in tasks.items():
                    try:
                        groups[source] = await task
                        status = "ok" if groups[source] else "empty"
                        reports.append(SourceReport(source=source, status=status))
                    except TimeoutError:
                        groups[source] = []
                        reports.append(SourceReport(source=source, status="timeout"))
                    except Exception:  # noqa: BLE001 - independent source degradation
                        groups[source] = []
                        reports.append(SourceReport(source=source, status="failed"))
                    await _emit(
                        progress,
                        source,
                        reports[-1].status,
                        f"{source.replace('_', ' ').title()} checked",
                    )
            finally:
                await _cancel_tasks(tasks.values())

        if company and self.supplemental_sources:
            tasks = {
                source.name: asyncio.create_task(source.discover(company))
                for source in self.supplemental_sources
            }
            try:
                for source, task in tasks.items():
                    try:
                        groups[source] = await asyncio.wait_for(
                            task, timeout=self.source_timeout_seconds
                        )
                        source_status = "ok" if groups[source] else "empty"
                    except TimeoutError:
                        groups[source], source_status = [], "timeout"
                    except Exception:  # noqa: BLE001 - optional public source
                        groups[source], source_status = [], "failed"
                    reports.append(SourceReport(source=source, status=source_status))
                    await _emit(progress, source, source_status, f"{source} checked")
            finally:
                await _cancel_tasks(tasks.values())

        documents = await self._fetch_documents(groups, reports, progress)
        await self._read_feeds(documents, groups, reports, progress)

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
            if not names_compatible(safe_name, title):
                continue
            name_resolution = _resolve_research_name(
                safe_name,
                RawSearchResult(title, url, snippet, raw.thumbnail),
                documents,
                groups,
                rank=rank,
                allow_shared_evidence=len(results) == 1,
            )
            candidate_name = name_resolution.display_name or _candidate_name(
                safe_name, title
            )
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
                    name_detail=AttributedFact(
                        value=candidate_name,
                        source_url=name_resolution.source_url or url,
                        confidence="high",
                        why=name_resolution.why,
                        source_kind=name_resolution.source_kind,
                        subject_name=candidate_name,
                    ),
                    role=AttributedFact(
                        value=_headline(title, snippet, candidate_name),
                        source_url=url,
                        confidence="medium",
                        why="role line observed in the public result title",
                    ),
                    company_detail=(
                        AttributedFact(
                            value=observed_company,
                            source_url=url,
                            confidence="medium",
                            why="company appears in the public identity result",
                        )
                        if observed_company
                        else None
                    ),
                    location=(
                        AttributedFact(
                            value=location,
                            source_url=url,
                            confidence="medium",
                            why="stated location is also observed in the public result",
                        )
                        if location and _words(location) & _words(f"{title} {snippet}")
                        else None
                    ),
                    bio=(
                        AttributedFact(
                            value=snippet,
                            source_url=url,
                            confidence="medium",
                            why="short public search-result description",
                        )
                        if snippet
                        else None
                    ),
                    photo=(
                        AttributedFact(
                            value=photo,
                            source_url=url,
                            confidence="medium",
                            why=(
                                "public search thumbnail attached to this identity result"
                            ),
                        )
                        if photo
                        else None
                    ),
                    avatar=CandidateAvatar(
                        kind="photo" if photo else "initials",
                        url=photo,
                        initials=_initials(candidate_name),
                        source_url=url,
                    ),
                    profiles=_profiles_from_result(
                        RawSearchResult(title, url, snippet, raw.thumbnail),
                        candidate_name,
                    ),
                    submitted_name=safe_name,
                    surname_resolved=name_resolution.surname_resolved,
                    name_variants=list(name_resolution.variants),
                )
            )

        if not candidates:
            return SearchOutcome(
                status="empty",
                message=(
                    "Couldn't find anything useful in public sources. Chat is unaffected."
                ),
                provider_failed=failed,
                source_reports=reports,
            )
        enriched = [
            _enrich_candidate(candidate, groups, documents) for candidate in candidates
        ]
        dossiers = [
            _build_dossier(candidate, groups, documents, company)
            for candidate in enriched
        ]
        await _emit(progress, "dossier", "ok", "Attributed dossier ready")
        return SearchOutcome(
            status="candidates",
            candidates=enriched,
            message=f"Found {len(candidates)} possible public match"
            f"{'es' if len(candidates) != 1 else ''}. Please confirm before I use one.",
            dossiers=dossiers,
            source_reports=reports,
        )

    async def research_company(
        self,
        name: str,
        *,
        limit: int = 4,
        progress: ProgressCallback | None = None,
    ) -> CompanyResearchOutcome:
        """Build the existing attributed company dossier without inventing a person."""
        safe_name = normalize_name(name)
        empty = CompanyDossier(name=safe_name or None)
        if not safe_name:
            return CompanyResearchOutcome(status="empty", dossier=empty)

        reports: list[SourceReport] = []
        groups: dict[str, list[RawSearchResult]] = {}
        query_method = getattr(self.provider, "search_query", None)
        if callable(query_method):
            queries = _research_queries(safe_name, safe_name)
            selected = {
                source: queries[source]
                for source in (
                    "company_website",
                    "careers",
                    "engineering_blog",
                    "github",
                    "news",
                    "funding",
                )
            }
            tasks = {
                source: asyncio.create_task(
                    _bounded_query(
                        query_method, query, limit, self.source_timeout_seconds
                    )
                )
                for source, query in selected.items()
            }
            try:
                for source, task in tasks.items():
                    try:
                        groups[source] = await task
                        source_status = "ok" if groups[source] else "empty"
                    except TimeoutError:
                        groups[source], source_status = [], "timeout"
                    except Exception:  # noqa: BLE001 - independent public source
                        groups[source], source_status = [], "failed"
                    reports.append(SourceReport(source=source, status=source_status))
                    await _emit(
                        progress,
                        source,
                        source_status,
                        f"{source.replace('_', ' ').title()} checked",
                    )
            finally:
                await _cancel_tasks(tasks.values())
        else:
            try:
                groups["company_website"] = await asyncio.wait_for(
                    self.provider.search(safe_name, safe_name, limit),
                    timeout=self.source_timeout_seconds,
                )
                source_status = "ok" if groups["company_website"] else "empty"
            except TimeoutError:
                groups["company_website"], source_status = [], "timeout"
            except Exception:  # noqa: BLE001 - provider failure is represented
                groups["company_website"], source_status = [], "failed"
            reports.append(SourceReport(source="company_website", status=source_status))

        documents = await self._fetch_documents(groups, reports, progress)
        observed = _safe_results([item for values in groups.values() for item in values])
        if not observed and not documents:
            status = (
                "failed" if any(row.status == "failed" for row in reports) else "empty"
            )
            return CompanyResearchOutcome(
                status=status,
                dossier=empty,
                source_reports=reports,
            )

        primary = (
            observed[0]
            if observed
            else RawSearchResult(
                title=documents[0].title or safe_name,
                url=documents[0].url,
                snippet=documents[0].text[:500],
            )
        )
        synthetic = Candidate(
            id=hashlib.sha256(f"company|{safe_name}".encode()).hexdigest()[:12],
            name=safe_name,
            headline=f"Attributed public company research for {safe_name}",
            company=safe_name,
            initials=_initials(safe_name),
            source_link=primary.url,
            source_label=_source_label(primary.url),
            confidence=0,
            why=["Synthetic carrier used only for the existing company dossier builder."],
            submitted_name=safe_name,
            surname_resolved=False,
        )
        company = _build_dossier(synthetic, groups, documents, safe_name).company
        return CompanyResearchOutcome(
            status="ok",
            dossier=company,
            documents=documents,
            source_reports=reports,
        )

    async def _fetch_documents(
        self,
        groups: dict[str, list[RawSearchResult]],
        reports: list[SourceReport],
        progress: ProgressCallback | None,
    ) -> list[PublicDocument]:
        if self.page_fetcher is None or self.page_limit <= 0:
            return []
        seed_urls: list[str] = []
        for source in (
            "company_website",
            "identity",
            "personal_profiles",
            "team_pages",
            "press",
            "speaker_pages",
            "talks",
            "cfp",
            "careers",
            "engineering_blog",
            "news",
            "funding",
        ):
            for result in groups.get(source, [])[:3]:
                host = (urlparse(result.url).hostname or "").casefold()
                if not any(
                    value in host for value in ("linkedin.com", "github.com")
                ) and (is_public_http_url(result.url)):
                    seed_urls.append(result.url)
                    break
        seed_urls = list(dict.fromkeys(seed_urls))
        documents: list[PublicDocument] = []
        fetched: set[str] = set()

        async def fetch_urls(urls: list[str]) -> None:
            tasks = [asyncio.create_task(self.page_fetcher.fetch(url)) for url in urls]
            try:
                for url, task in zip(urls, tasks, strict=True):
                    fetched.add(url)
                    try:
                        document, report = await asyncio.wait_for(
                            task, timeout=self.source_timeout_seconds + 0.1
                        )
                    except TimeoutError:
                        document = None
                        report = SourceReport(
                            source="public_web", status="timeout", url=url
                        )
                    reports.append(report)
                    if document is not None:
                        documents.append(document)
                    await _emit(
                        progress, "page", report.status, f"Public page checked: {url}"
                    )
            finally:
                await _cancel_tasks(tasks)

        reserved_follow_slots = min(2, max(0, self.page_limit - 1))
        initial_limit = max(1, self.page_limit - reserved_follow_slots)
        await fetch_urls(seed_urls[:initial_limit])

        relevant_links = [
            link
            for document in documents
            for link in document.links
            if any(
                token in urlparse(link).path.casefold()
                for token in (
                    "contact",
                    "about",
                    "team",
                    "people",
                    "leadership",
                    "press",
                    "speaker",
                    "conference",
                    "cfp",
                )
            )
            and link not in fetched
        ]
        remaining = self.page_limit - len(fetched)
        follow = list(dict.fromkeys(relevant_links))[:remaining]
        if follow:
            await fetch_urls(follow)
        remaining = self.page_limit - len(fetched)
        if remaining > 0:
            trailing = [url for url in seed_urls if url not in fetched][:remaining]
            if trailing:
                await fetch_urls(trailing)
        return documents

    async def _read_feeds(
        self,
        documents: list[PublicDocument],
        groups: dict[str, list[RawSearchResult]],
        reports: list[SourceReport],
        progress: ProgressCallback | None,
    ) -> None:
        if self.feed_reader is None:
            return
        feed_urls = [
            link
            for document in documents
            for link in document.links
            if any(token in link.casefold() for token in ("/feed", "rss", "atom.xml"))
        ][:2]
        for feed_url in feed_urls:
            try:
                items = await asyncio.wait_for(
                    self.feed_reader.read(feed_url), timeout=self.source_timeout_seconds
                )
                groups.setdefault("feeds", []).extend(items)
                feed_status = "ok" if items else "empty"
            except TimeoutError:
                feed_status = "timeout"
            except Exception:  # noqa: BLE001 - optional feed degradation
                feed_status = "failed"
            reports.append(
                SourceReport(source="rss_atom", status=feed_status, url=feed_url)
            )
            await _emit(progress, "rss_atom", feed_status, "Public feed checked")


async def _bounded_query(
    query_method: Callable[[str, int], Awaitable[list[RawSearchResult]]],
    query: str,
    limit: int,
    timeout_seconds: float,
) -> list[RawSearchResult]:
    return await asyncio.wait_for(query_method(query, limit), timeout=timeout_seconds)


async def _cancel_tasks(tasks: Any) -> None:
    values = tuple(tasks)
    for task in values:
        if not task.done():
            task.cancel()
    if values:
        await asyncio.gather(*values, return_exceptions=True)


async def _emit(
    callback: ProgressCallback | None,
    source: str,
    status: str,
    message: str,
) -> None:
    if callback is not None:
        await callback(
            {
                "type": "research.progress",
                "source": source,
                "status": status,
                "message": message,
            }
        )


def _research_queries(name: str, company: str | None) -> dict[str, str]:
    employer = company or name
    return {
        "company_website": f'"{employer}" official company website',
        "careers": f'"{employer}" careers jobs engineering',
        "engineering_blog": f'"{employer}" engineering blog technology stack',
        "github": f'"{employer}" site:github.com organization',
        "talks": f'"{name}" conference talk podcast',
        "team_pages": f'"{employer}" team about leadership "{name}"',
        "press": f'"{employer}" press release "{name}"',
        "speaker_pages": f'"{name}" speaker conference bio',
        "cfp": f'"{name}" CFP speaker',
        "personal_profiles": (f'"{name}" GitHub X Twitter Instagram personal website'),
        "news": f'"{employer}" recent news',
        "funding": f'"{employer}" funding investors round',
        "hacker_news": f'"{employer}" site:news.ycombinator.com',
    }


def _safe_results(results: list[RawSearchResult]) -> list[RawSearchResult]:
    safe: list[RawSearchResult] = []
    for result in results:
        url = _unwrap_duckduckgo_url(result.url)
        title = sanitize_external_text(result.title, max_length=300)
        snippet = sanitize_external_text(result.snippet, max_length=700)
        if title and is_public_http_url(url):
            safe.append(RawSearchResult(title, url, snippet, result.thumbnail))
    return safe


def _fact(
    value: str,
    result: RawSearchResult,
    confidence: str,
    why: str,
    *,
    source_kind: str = "public_web",
    subject_name: str | None = None,
    company_level: bool = False,
) -> AttributedFact:
    return AttributedFact(
        value=value[:500],
        source_url=result.url,
        confidence=confidence,
        why=why,
        source_kind=source_kind,
        subject_name=subject_name,
        company_level=company_level,
    )


def _profile_kind(url: str, title: str = "") -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = parsed.path.casefold()
    path_parts = [part for part in path.split("/") if part]
    if host.endswith("linkedin.com") and path.startswith("/in/"):
        return "linkedin"
    if host == "github.com" and len(path_parts) == 1:
        return "github"
    if host in {"x.com", "twitter.com"} and len(path_parts) == 1:
        return "x"
    if host == "instagram.com" and len(path_parts) == 1:
        return "instagram"
    if any(token in path for token in ("speaker", "talk", "podcast", "event")):
        return "speaker"
    if any(token in path for token in ("/team/", "/people/", "/about/")):
        return "company_bio"
    name_tokens = _words(title)
    host_tokens = set(re.split(r"[.-]", host))
    compact_name = "".join(re.findall(r"[a-z0-9]+", title.casefold()))
    if (
        name_tokens & host_tokens or compact_name in host.replace("-", "")
    ) and host not in {
        "medium.com",
        "youtube.com",
        "news.ycombinator.com",
    }:
        return "personal_site"
    return None


def _profile_handle(url: str) -> str | None:
    parts = [part for part in urlparse(url).path.split("/") if part]
    return parts[-1][:100] if parts else None


def _profiles_from_result(
    result: RawSearchResult, candidate_name: str
) -> list[ProfileLink]:
    if not _words(candidate_name) <= _words(f"{result.title} {result.snippet}"):
        return []
    kind = _profile_kind(result.url, candidate_name)
    if kind is None:
        return []
    return [
        ProfileLink(
            kind=kind,
            url=result.url,
            handle=_profile_handle(result.url),
            source_url=result.url,
            verified=True,
        )
    ]


def _enrich_candidate(
    candidate: Candidate,
    groups: dict[str, list[RawSearchResult]],
    documents: list[PublicDocument],
) -> Candidate:
    profiles = list(candidate.profiles)
    seen = {profile.url.casefold() for profile in profiles}
    name_tokens = _words(candidate.name)
    for result in _safe_results([item for values in groups.values() for item in values]):
        if not name_tokens <= _words(f"{result.title} {result.snippet}"):
            continue
        for profile in _profiles_from_result(result, candidate.name):
            if profile.url.casefold() not in seen:
                profiles.append(profile)
                seen.add(profile.url.casefold())

    matching_documents = [
        document
        for document in documents
        if name_tokens <= _words(f"{document.title} {document.text}")
    ]
    for document in matching_documents:
        for link in document.links:
            kind = _profile_kind(link, candidate.name)
            if (
                kind is None
                or link.casefold() in seen
                or not _profile_handle_matches(candidate.name, link)
            ):
                continue
            profiles.append(
                ProfileLink(
                    kind=kind,
                    url=link,
                    handle=_profile_handle(link),
                    source_url=document.url,
                    verified=False,
                )
            )
            seen.add(link.casefold())

    photo = candidate.photo
    if photo is None:
        document_with_image = next(
            (document for document in matching_documents if document.image_url), None
        )
        if document_with_image and document_with_image.image_url:
            photo = AttributedFact(
                value=document_with_image.image_url,
                source_url=document_with_image.url,
                confidence="medium",
                why="og:image observed on a public page naming this candidate",
            )

    emails = list(candidate.emails)
    if candidate.email is not None:
        emails.insert(0, candidate.email)
    for document in matching_documents:
        source_kind = (
            "conference_speaker"
            if _name_source_kind(document.url) == "conference_speaker"
            else "mailto"
            if document.email_addresses
            else "public_page"
        )
        for address in (
            *document.email_addresses,
            *EMAIL_PATTERN.findall(document.text),
        ):
            normalized = normalize_address(address)
            if recipient_key(normalized) in {
                recipient_key(value.address) for value in emails
            }:
                continue
            emails.append(
                CandidateEmail(
                    address=normalized,
                    status="verified",
                    confidence="high",
                    source_url=document.url,
                    why="address is explicitly published on an attributed public page",
                    score=100,
                    source_kind=source_kind,
                    company_level=_is_company_level_address(normalized, document.url),
                )
            )
    email = emails[0] if emails else None

    avatar: CandidateAvatar
    if photo:
        avatar = CandidateAvatar(
            kind="photo",
            url=photo.value,
            initials=candidate.initials,
            source_url=photo.source_url,
        )
    elif email and email.status == "verified":
        digest = hashlib.md5(  # noqa: S324 - Gravatar's non-security identifier
            email.address.strip().casefold().encode(), usedforsecurity=False
        ).hexdigest()
        avatar = CandidateAvatar(
            kind="gravatar",
            url=f"https://www.gravatar.com/avatar/{digest}?d=404&s=160",
            initials=candidate.initials,
            source_url=email.source_url,
        )
    else:
        avatar = CandidateAvatar(
            kind="initials",
            initials=candidate.initials,
            source_url=candidate.source_link,
        )
    return candidate.model_copy(
        update={
            "profiles": profiles,
            "photo": photo,
            "photo_url": photo.value if photo else None,
            "avatar": avatar,
            "email": email,
            "emails": emails,
        }
    )


def _profile_handle_matches(candidate_name: str, url: str) -> bool:
    first, *rest = [
        re.sub(r"[^a-z0-9]", "", part.casefold()) for part in candidate_name.split()
    ]
    last = rest[-1] if rest else ""
    handle = re.sub(r"[^a-z0-9]", "", _profile_handle(url) or "")
    if not handle or not first:
        return False
    return first in handle and (not last or last in handle)


def _first_result(
    groups: dict[str, list[RawSearchResult]], source: str
) -> RawSearchResult | None:
    values = _safe_results(groups.get(source, []))
    return values[0] if values else None


def _registered_domain(url: str) -> str | None:
    host = (urlparse(url).hostname or "").casefold()
    extracted = _TLDEXTRACT(host)
    if not extracted.domain or not extracted.suffix:
        return None
    return f"{extracted.domain}.{extracted.suffix}"


def _is_company_level_address(address: str, source_url: str) -> bool:
    local = normalize_address(address).partition("@")[0]
    return local in {
        "abuse",
        "admin",
        "contact",
        "hello",
        "info",
        "press",
        "privacy",
        "security",
        "support",
    } or any(
        value in source_url.casefold() for value in ("security.txt", "rdap", "whois")
    )


def _possible_subject_name(title: str) -> str | None:
    segment = re.split(r"\s+(?:[-–—|·])\s+", title, maxsplit=1)[0].strip()
    tokens = name_tokens(segment)
    if not 2 <= len(tokens) <= 5:
        return None
    blocked = {"official", "company", "team", "about", "careers", "press"}
    if {local_token.casefold() for local_token in tokens} & blocked:
        return None
    return segment[:100]


def _build_dossier(
    candidate: Candidate,
    groups: dict[str, list[RawSearchResult]],
    documents: list[PublicDocument],
    stated_company: str | None,
) -> CandidateDossier:
    identity_result = RawSearchResult(
        title=candidate.headline,
        url=candidate.source_link,
        snippet=candidate.company or "",
    )
    person = PersonDossier(
        candidate_id=candidate.id,
        headline=_fact(
            candidate.headline,
            identity_result,
            "high",
            "observed in the selected public identity result",
        ),
        company=(
            _fact(
                candidate.company,
                identity_result,
                "medium",
                "stated company overlaps the identity result",
            )
            if candidate.company
            else None
        ),
        public_profiles=[
            _fact(
                candidate.source_link,
                identity_result,
                "high",
                "canonical public result used for identity matching",
            )
        ],
    )
    for result in _safe_results(groups.get("talks", []))[:4]:
        person.talks.append(_fact(result.title, result, "medium", "public talk result"))
    for result in _safe_results(groups.get("news", []))[:4]:
        if _words(candidate.name) & _words(f"{result.title} {result.snippet}"):
            person.recent_mentions.append(
                _fact(result.title, result, "medium", "name appears in public news")
            )

    combined_sources = [
        *_safe_results([item for values in groups.values() for item in values]),
        *[
            RawSearchResult(
                document.title or document.url,
                document.url,
                f"{document.text} {' '.join(document.email_addresses)}",
            )
            for document in documents
        ],
    ]
    for result in combined_sources:
        if not _words(candidate.name) <= _words(f"{result.title} {result.snippet}"):
            continue
        for email in EMAIL_PATTERN.findall(f"{result.title} {result.snippet}"):
            if email.casefold() not in {
                fact.value.casefold() for fact in person.public_emails
            }:
                company_level = _is_company_level_address(email, result.url)
                person.public_emails.append(
                    _fact(
                        normalize_address(email),
                        result,
                        "high",
                        "email address publicly displayed",
                        source_kind="public_page",
                        subject_name=None if company_level else candidate.name,
                        company_level=company_level,
                    )
                )

    website_result = _first_result(groups, "company_website")
    domain = _registered_domain(website_result.url) if website_result else None
    company = CompanyDossier(name=stated_company or candidate.company)
    for result in combined_sources:
        for address in EMAIL_PATTERN.findall(f"{result.title} {result.snippet}"):
            if address.casefold() not in {
                fact.value.casefold() for fact in company.public_emails
            }:
                company_level = _is_company_level_address(address, result.url)
                company.public_emails.append(
                    _fact(
                        normalize_address(address),
                        result,
                        "high",
                        "email address publicly displayed in company-related content",
                        source_kind=(
                            "security_txt"
                            if "security.txt" in result.url.casefold()
                            else "company_page"
                        ),
                        subject_name=(
                            None
                            if company_level
                            else _possible_subject_name(result.title)
                        ),
                        company_level=company_level,
                    )
                )
    if website_result and domain:
        company.website = _fact(
            website_result.url,
            website_result,
            "medium",
            "top official-company search result",
        )
        company.domain = _fact(
            domain,
            website_result,
            "medium",
            "registered domain of the attributed company result",
        )
    careers = _first_result(groups, "careers")
    if careers:
        company.careers_page = _fact(
            careers.url, careers, "medium", "public careers search result"
        )
    engineering = _first_result(groups, "engineering_blog")
    if engineering:
        company.engineering_blog = _fact(
            engineering.url,
            engineering,
            "medium",
            "public engineering-blog search result",
        )
    github = next(
        (
            result
            for result in _safe_results(groups.get("github", []))
            if (urlparse(result.url).hostname or "").endswith("github.com")
        ),
        None,
    )
    if github:
        company.github_org = _fact(
            github.url, github, "medium", "public GitHub organisation result"
        )
    for result in _safe_results(groups.get("news", []))[:5]:
        company.recent_news.append(
            _fact(result.title, result, "medium", "public news result")
        )
    for result in _safe_results(groups.get("funding", []))[:4]:
        company.funding.append(
            _fact(result.title, result, "medium", "public funding result")
        )

    technology_terms = (
        "Python",
        "Java",
        "Go",
        "Rust",
        "TypeScript",
        "React",
        "Kubernetes",
        "AWS",
        "GCP",
        "Azure",
        "Kafka",
        "PostgreSQL",
        "Docker",
        "Terraform",
    )
    for term in technology_terms:
        match = next(
            (
                result
                for result in combined_sources
                if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", result.snippet, re.I)
            ),
            None,
        )
        if match:
            company.tech_stack.append(
                _fact(term, match, "low", "technology named in attributed public content")
            )
    for document in documents:
        for link in document.links:
            lowered = link.casefold()
            if any(token in lowered for token in ("/feed", "rss", "atom.xml")):
                result = RawSearchResult(document.title, document.url, "")
                company.feeds.append(
                    _fact(link, result, "medium", "feed link observed on public page")
                )
    return CandidateDossier(
        candidate_id=candidate.id,
        person=person,
        company=company,
        documents=documents,
    )


def attach_candidate_email(candidate: Candidate, email: CandidateEmail) -> Candidate:
    avatar = candidate.avatar
    if candidate.photo is None and email.status == "verified":
        digest = hashlib.md5(  # noqa: S324 - Gravatar's non-security identifier
            email.address.strip().casefold().encode(), usedforsecurity=False
        ).hexdigest()
        avatar = CandidateAvatar(
            kind="gravatar",
            url=f"https://www.gravatar.com/avatar/{digest}?d=404&s=160",
            initials=candidate.initials,
            source_url=email.source_url,
        )
    emails = [email, *candidate.emails]
    deduped: list[CandidateEmail] = []
    seen: set[str] = set()
    for value in emails:
        key = recipient_key(value.address)
        if key not in seen:
            seen.add(key)
            deduped.append(value)
    return candidate.model_copy(
        update={"email": email, "emails": deduped, "avatar": avatar}
    )
