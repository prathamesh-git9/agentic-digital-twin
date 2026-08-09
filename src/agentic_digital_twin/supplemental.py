from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import httpx

from .research import RawSearchResult
from .security import is_public_http_url, sanitize_external_text

USER_AGENT = "PrathameshDigitalTwin/2.0 (+public-source-research)"


class HackerNewsAlgoliaSource:
    name = "hacker_news"
    endpoint = "https://hn.algolia.com/api/v1/search_by_date"

    def __init__(self, *, timeout: float = 5.0, client: httpx.AsyncClient | None = None):
        self.timeout = timeout
        self.client = client

    async def discover(self, company: str) -> list[RawSearchResult]:
        params = {"query": company, "tags": "story", "hitsPerPage": 5}
        if self.client is not None:
            response = await self.client.get(
                self.endpoint, params=params, timeout=self.timeout
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.endpoint, params=params, timeout=self.timeout
                )
        response.raise_for_status()
        values: list[RawSearchResult] = []
        for hit in response.json().get("hits", []):
            object_id = str(hit.get("objectID", ""))
            url = str(
                hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
            )
            title = sanitize_external_text(str(hit.get("title", "")), max_length=300)
            if title and is_public_http_url(url):
                values.append(
                    RawSearchResult(
                        title=title,
                        url=url,
                        snippet=f"Hacker News discussion {object_id}",
                    )
                )
        return values


class GitHubOrganizationSource:
    name = "github_activity"
    search_endpoint = "https://api.github.com/search/users"

    def __init__(
        self,
        *,
        timeout: float = 5.0,
        token: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.timeout = timeout
        self.client = client
        self.headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def discover(self, company: str) -> list[RawSearchResult]:
        if self.client is not None:
            return await self._discover(self.client, company)
        async with httpx.AsyncClient() as client:
            return await self._discover(client, company)

    async def _discover(
        self, client: httpx.AsyncClient, company: str
    ) -> list[RawSearchResult]:
        search = await client.get(
            self.search_endpoint,
            params={"q": f'"{company}" type:org', "per_page": 3},
            headers=self.headers,
            timeout=self.timeout,
        )
        search.raise_for_status()
        organizations = search.json().get("items", [])[:3]
        results: list[RawSearchResult] = []
        for organization in organizations:
            login = str(organization.get("login", ""))
            url = str(organization.get("html_url", ""))
            if not login or not is_public_http_url(url):
                continue
            repos = await client.get(
                f"https://api.github.com/orgs/{login}/repos",
                params={"sort": "pushed", "per_page": 20},
                headers=self.headers,
                timeout=self.timeout,
            )
            repos.raise_for_status()
            repo_values: list[dict[str, Any]] = repos.json()
            languages = sorted(
                {
                    str(repo.get("language"))
                    for repo in repo_values
                    if repo.get("language")
                }
            )
            latest = max(
                (str(repo.get("pushed_at", "")) for repo in repo_values),
                default="unknown",
            )
            snippet = (
                f"{len(repo_values)} recent public repositories; languages: "
                f"{', '.join(languages[:8]) or 'not reported'}; latest push: {latest}"
            )
            results.append(RawSearchResult(f"{login} on GitHub", url, snippet))
        return results


class RSSAtomReader:
    def __init__(
        self, *, timeout: float = 5.0, client: httpx.AsyncClient | None = None
    ) -> None:
        self.timeout = timeout
        self.client = client

    async def read(self, url: str) -> list[RawSearchResult]:
        if not is_public_http_url(url):
            return []
        if self.client is not None:
            response = await self.client.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=self.timeout
            )
        else:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(
                    url, headers={"User-Agent": USER_AGENT}, timeout=self.timeout
                )
        response.raise_for_status()
        root = ET.fromstring(response.content)  # noqa: S314 - no entity support in stdlib
        values: list[RawSearchResult] = []
        for item in list(root.iter("item"))[:5]:
            title = sanitize_external_text(item.findtext("title") or "", max_length=300)
            link = (item.findtext("link") or "").strip()
            summary = sanitize_external_text(
                item.findtext("description") or "", max_length=600
            )
            if title and is_public_http_url(link):
                values.append(RawSearchResult(title, link, summary))
        atom_namespace = "{http://www.w3.org/2005/Atom}"
        for entry in list(root.iter(f"{atom_namespace}entry"))[:5]:
            title = sanitize_external_text(
                entry.findtext(f"{atom_namespace}title") or "", max_length=300
            )
            link_node = entry.find(f"{atom_namespace}link")
            link = link_node.attrib.get("href", "") if link_node is not None else ""
            summary = sanitize_external_text(
                entry.findtext(f"{atom_namespace}summary") or "", max_length=600
            )
            if title and is_public_http_url(link):
                values.append(RawSearchResult(title, link, summary))
        return values[:5]
