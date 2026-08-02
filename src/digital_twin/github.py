from __future__ import annotations

import asyncio
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
    updated_at: datetime | None = None
    commits: list[CommitSummary] = []
    live: bool = True


class GitHubService:
    def __init__(
        self,
        *,
        token: str = "",
        timeout: float = 6.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.token, self.timeout, self.client = token, timeout, client
        self._cache: tuple[float, list[RepoMetadata]] | None = None

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

    async def get_repositories(self, *, refresh: bool = False) -> list[RepoMetadata]:
        if self._cache and not refresh and self._cache[0] > time.monotonic():
            return self._cache[1]
        if self.client is not None:
            repos = await self._fetch_with(self.client)
        else:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                repos = await self._fetch_with(client)
        self._cache = (time.monotonic() + 300, repos)
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
            updated_at=data.get("updated_at"),
            commits=commits,
        )

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
            items.append(EvidenceItem(repo.url, " ".join(details), repo.url))
        return items
