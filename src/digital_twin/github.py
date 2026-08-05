from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

from .profile import EvidenceItem
from .security import sanitize_external_text

REPOSITORIES = (
    "effect-broker",
    "agent-runtime",
    "effect-browser",
    "agent-redteam",
    "answer-engine",
    "agent-mesh",
    "llm-gateway",
    "promise-ledger",
    "reachable",
    "trustdesk",
)
OWNER = "prathamesh-git9"


class CommitSummary(BaseModel):
    sha: str
    message: str
    url: str
    committed_at: datetime | None = None


class RepoMetadata(BaseModel):
    name: str
    url: str
    description: str | None = None
    stars: int | None = None
    forks: int | None = None
    open_issues: int | None = None
    language: str | None = None
    topics: list[str] = []
    default_branch: str | None = None
    updated_at: datetime | None = None
    commits: list[CommitSummary] = []
    live: bool = True


class RepoDetail(RepoMetadata):
    languages: dict[str, int] = {}
    latest_ci_conclusion: str | None = None
    last_commit: CommitSummary | None = None


class GitHubSearchHit(BaseModel):
    repository: str
    path: str
    permalink: str
    kind: str
    excerpt: str


class GitHubService:
    # Metadata for ten repositories costs twenty upstream calls, so a visitor
    # must never wait for it twice. Inside FRESH_SECONDS the cache is served
    # outright; up to STALE_SECONDS it is still served, with a refresh started
    # behind the response.
    FRESH_SECONDS = 300.0
    STALE_SECONDS = 3_600.0

    def __init__(
        self,
        *,
        token: str = "",
        timeout: float = 6.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.token, self.timeout, self.client = token, timeout, client
        self._cache: tuple[float, list[RepoMetadata]] | None = None
        self._refreshing: asyncio.Task[list[RepoMetadata]] | None = None

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "prathamesh-digital-twin",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def cached_repositories(self) -> list[RepoMetadata] | None:
        """Return warm metadata, or None rather than pay for a GitHub round trip."""
        if self._cache is None:
            return None
        cached_at, repos = self._cache
        if time.monotonic() - cached_at > self.STALE_SECONDS:
            return None
        return repos

    async def get_repositories(self, *, refresh: bool = False) -> list[RepoMetadata]:
        if self._cache is not None and not refresh:
            cached_at, repos = self._cache
            age = time.monotonic() - cached_at
            if age <= self.FRESH_SECONDS:
                return repos
            if age <= self.STALE_SECONDS:
                self._start_background_refresh()
                return repos
        return await self._refresh()

    def prime(self) -> None:
        """Warm a cold cache off the request path so the next visitor pays nothing."""
        if self.cached_repositories() is None:
            self._start_background_refresh()

    def _start_background_refresh(self) -> None:
        if self._refreshing is not None and not self._refreshing.done():
            return
        try:
            self._refreshing = asyncio.create_task(self._refresh())
        except RuntimeError:  # no running loop; the next request refreshes inline
            return
        # Nothing awaits this task, so consume its outcome: a GitHub outage must
        # not surface as an "exception was never retrieved" warning.
        self._refreshing.add_done_callback(
            lambda task: None if task.cancelled() else task.exception()
        )

    async def _refresh(self) -> list[RepoMetadata]:
        if self.client is not None:
            repos = await self._fetch_with(self.client)
        else:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                repos = await self._fetch_with(client)
        self._cache = (time.monotonic(), repos)
        return repos

    async def _fetch_with(self, client: httpx.AsyncClient) -> list[RepoMetadata]:
        values = await asyncio.gather(
            *(self._fetch_repo(client, name) for name in REPOSITORIES),
            return_exceptions=True,
        )
        by_name = {
            value.name: value for value in values if isinstance(value, RepoMetadata)
        }
        return [
            by_name.get(
                name,
                RepoMetadata(
                    name=name,
                    url=f"https://github.com/{OWNER}/{name}",
                    description="Live metadata is temporarily unavailable.",
                    live=False,
                ),
            )
            for name in REPOSITORIES
        ]

    async def _fetch_repo(self, client: httpx.AsyncClient, name: str) -> RepoMetadata:
        base = f"https://api.github.com/repos/{OWNER}/{name}"
        repo_response, commits_response = await asyncio.gather(
            client.get(base, headers=self.headers, timeout=self.timeout),
            client.get(
                f"{base}/commits",
                params={"per_page": 3},
                headers=self.headers,
                timeout=self.timeout,
            ),
        )
        repo_response.raise_for_status()
        data: dict[str, Any] = repo_response.json()
        commits: list[CommitSummary] = []
        if commits_response.is_success:
            for row in commits_response.json()[:3]:
                commit = row.get("commit") or {}
                message = sanitize_external_text(
                    str((commit.get("message") or "").splitlines()[0]), max_length=120
                )
                if message:
                    commits.append(
                        CommitSummary(
                            sha=str(row.get("sha", ""))[:7],
                            message=message,
                            url=str(row.get("html_url", "")),
                            committed_at=(commit.get("committer") or {}).get("date"),
                        )
                    )
        description = sanitize_external_text(
            str(data.get("description") or ""), max_length=360
        )
        return RepoMetadata(
            name=name,
            url=str(data["html_url"]),
            description=description or None,
            stars=int(data.get("stargazers_count", 0)),
            forks=int(data.get("forks_count", 0)),
            open_issues=int(data.get("open_issues_count", 0)),
            language=data.get("language"),
            topics=[str(topic)[:40] for topic in data.get("topics", [])[:12]],
            default_branch=str(data.get("default_branch") or "")[:100] or None,
            updated_at=data.get("updated_at"),
            commits=commits,
        )

    async def get_repo_detail(self, name: str) -> RepoDetail:
        if name not in REPOSITORIES:
            raise ValueError("repository is not in the public allowlist")
        if self.client is not None:
            return await self._repo_detail_with(self.client, name)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            return await self._repo_detail_with(client, name)

    async def _repo_detail_with(self, client: httpx.AsyncClient, name: str) -> RepoDetail:
        metadata = await self._fetch_repo(client, name)
        base = f"https://api.github.com/repos/{OWNER}/{name}"
        languages_response, runs_response = await asyncio.gather(
            client.get(f"{base}/languages", headers=self.headers, timeout=self.timeout),
            client.get(
                f"{base}/actions/runs",
                params={"per_page": 1},
                headers=self.headers,
                timeout=self.timeout,
            ),
            return_exceptions=True,
        )
        languages: dict[str, int] = {}
        if (
            isinstance(languages_response, httpx.Response)
            and languages_response.is_success
        ):
            payload = languages_response.json()
            if isinstance(payload, dict):
                languages = {
                    str(language)[:60]: int(value)
                    for language, value in payload.items()
                    if isinstance(value, int) and value >= 0
                }
        conclusion = None
        if isinstance(runs_response, httpx.Response) and runs_response.is_success:
            runs = runs_response.json().get("workflow_runs", [])
            if runs:
                observed = str(runs[0].get("conclusion") or runs[0].get("status") or "")
                if observed in {
                    "success",
                    "failure",
                    "cancelled",
                    "skipped",
                    "timed_out",
                    "action_required",
                    "neutral",
                    "stale",
                    "queued",
                    "in_progress",
                }:
                    conclusion = observed
        return RepoDetail(
            **metadata.model_dump(),
            languages=languages,
            latest_ci_conclusion=conclusion,
            last_commit=metadata.commits[0] if metadata.commits else None,
        )

    async def search(self, query: str, *, limit: int = 10) -> list[GitHubSearchHit]:
        safe_query = sanitize_external_text(query, max_length=160)
        terms = {value.casefold() for value in re.findall(r"[a-z0-9_.+#-]+", safe_query)}
        if not safe_query or not terms:
            return []
        repositories = await self.get_repositories()
        hits = self._metadata_hits(repositories, terms)
        if self.client is not None:
            remote = await self._search_with(self.client, safe_query, terms)
        else:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                remote = await self._search_with(client, safe_query, terms)
        seen = {(hit.repository, hit.path, hit.permalink) for hit in hits}
        for hit in remote:
            key = (hit.repository, hit.path, hit.permalink)
            if key not in seen:
                hits.append(hit)
                seen.add(key)
        return hits[:limit]

    def _metadata_hits(
        self, repositories: list[RepoMetadata], terms: set[str]
    ) -> list[GitHubSearchHit]:
        hits: list[GitHubSearchHit] = []
        for repo in repositories:
            if not repo.live:
                continue
            metadata = " ".join((repo.name, repo.description or "", *repo.topics))
            if terms & set(re.findall(r"[a-z0-9_.+#-]+", metadata.casefold())):
                hits.append(
                    GitHubSearchHit(
                        repository=repo.name,
                        path="README.md",
                        permalink=f"{repo.url}#readme",
                        kind="metadata",
                        excerpt=(repo.description or f"Topics: {', '.join(repo.topics)}"),
                    )
                )
            for commit in repo.commits:
                words = set(re.findall(r"[a-z0-9_.+#-]+", commit.message.casefold()))
                if terms & words:
                    hits.append(
                        GitHubSearchHit(
                            repository=repo.name,
                            path=f"commit/{commit.sha}",
                            permalink=commit.url,
                            kind="commit",
                            excerpt=commit.message,
                        )
                    )
        return hits

    async def _search_with(
        self,
        client: httpx.AsyncClient,
        query: str,
        terms: set[str],
    ) -> list[GitHubSearchHit]:
        readmes = await asyncio.gather(
            *(self._search_readme(client, name, terms) for name in REPOSITORIES),
            return_exceptions=True,
        )
        hits = [value for value in readmes if isinstance(value, GitHubSearchHit)]
        if not self.token:
            return hits
        try:
            response = await client.get(
                "https://api.github.com/search/code",
                params={"q": f"{query} user:{OWNER}", "per_page": 20},
                headers={
                    **self.headers,
                    "Accept": "application/vnd.github.text-match+json",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception:  # noqa: BLE001 - README/metadata search still succeeds
            return hits
        for item in response.json().get("items", []):
            repository = str((item.get("repository") or {}).get("name") or "")
            if repository not in REPOSITORIES:
                continue
            excerpt = " ".join(
                str(match.get("fragment") or "")
                for match in item.get("text_matches", [])[:2]
            )
            safe_excerpt = sanitize_external_text(excerpt, max_length=500)
            path = sanitize_external_text(str(item.get("path") or ""), max_length=300)
            permalink = str(item.get("html_url") or "")
            if path and safe_excerpt and permalink.startswith("https://github.com/"):
                hits.append(
                    GitHubSearchHit(
                        repository=repository,
                        path=path,
                        permalink=permalink,
                        kind="code",
                        excerpt=safe_excerpt,
                    )
                )
        return hits

    async def _search_readme(
        self, client: httpx.AsyncClient, name: str, terms: set[str]
    ) -> GitHubSearchHit | None:
        try:
            response = await client.get(
                f"https://api.github.com/repos/{OWNER}/{name}/readme",
                headers={**self.headers, "Accept": "application/vnd.github.raw+json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception:  # noqa: BLE001 - one repository is independent
            return None
        for raw_line in response.text.splitlines():
            words = set(re.findall(r"[a-z0-9_.+#-]+", raw_line.casefold()))
            if not terms & words:
                continue
            excerpt = sanitize_external_text(raw_line, max_length=500)
            if excerpt:
                return GitHubSearchHit(
                    repository=name,
                    path="README.md",
                    permalink=f"https://github.com/{OWNER}/{name}/blob/HEAD/README.md",
                    kind="readme",
                    excerpt=excerpt,
                )
        return None

    @staticmethod
    def evidence(repositories: list[RepoMetadata]) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        for repo in repositories:
            if not repo.live:
                continue
            details = [repo.description or "No repository description."]
            details.append(
                f"Live GitHub metadata: {repo.stars} stars, {repo.forks} forks, "
                f"primary language {repo.language or 'not reported'}."
            )
            if repo.topics:
                details.append(f"Topics: {', '.join(repo.topics)}.")
            items.append(
                EvidenceItem(
                    repo.url,
                    " ".join(details),
                    repo.url,
                    authority="github",
                )
            )
        return items
