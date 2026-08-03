from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel

from .email_utils import (
    EMAIL_PATTERN,
    is_github_noreply,
    normalize_address,
    recipient_key,
)
from .identity import names_compatible
from .research import Candidate, CandidateDossier
from .research_sources import AttributedFact
from .security import sanitize_external_text


class EmailHarvestResult(BaseModel):
    addresses: list[AttributedFact] = []
    names: list[AttributedFact] = []


class PublicEmailHarvester:
    """Collect explicitly published addresses without mailbox probing."""

    github_api = "https://api.github.com"
    rdap_api = "https://rdap.org/domain"

    def __init__(
        self,
        *,
        timeout: float = 5.0,
        github_token: str = "",
        client: httpx.AsyncClient | None = None,
        github_repo_limit: int = 8,
        github_commit_limit: int = 20,
    ) -> None:
        self.timeout = timeout
        self.github_token = github_token
        self.client = client
        self.github_repo_limit = github_repo_limit
        self.github_commit_limit = github_commit_limit

    @property
    def github_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "prathamesh-digital-twin",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    async def harvest(
        self, candidate: Candidate, dossier: CandidateDossier
    ) -> EmailHarvestResult:
        result = self._from_observed_content(candidate, dossier)
        if self.client is not None:
            await self._harvest_network(self.client, candidate, dossier, result)
        else:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                await self._harvest_network(client, candidate, dossier, result)
        result.addresses = _dedupe_facts(result.addresses)
        result.names = _dedupe_names(result.names)
        return result

    def _from_observed_content(
        self, candidate: Candidate, dossier: CandidateDossier
    ) -> EmailHarvestResult:
        addresses: list[AttributedFact] = []
        names: list[AttributedFact] = []
        if candidate.name_detail is not None:
            names.append(candidate.name_detail)
        for email in (
            *candidate.emails,
            *((candidate.email,) if candidate.email else ()),
        ):
            if email.status == "verified":
                addresses.append(
                    AttributedFact(
                        value=email.address,
                        source_url=email.source_url,
                        confidence="high",
                        why=email.why,
                        source_kind=email.source_kind,
                        subject_name=candidate.name,
                        company_level=email.company_level,
                    )
                )
        addresses.extend(dossier.person.public_emails)
        for fact in dossier.company.public_emails:
            if fact.company_level or (
                fact.subject_name and names_compatible(candidate.name, fact.subject_name)
            ):
                addresses.append(fact)

        candidate_words = {
            value.casefold()
            for value in re.findall(r"[^\W\d_]+", candidate.name, re.UNICODE)
        }
        for document in dossier.documents:
            observed_words = {
                value.casefold()
                for value in re.findall(
                    r"[^\W\d_]+", f"{document.title} {document.text}", re.UNICODE
                )
            }
            if candidate.surname_resolved:
                matches_person = candidate_words <= observed_words
            else:
                matches_person = bool(candidate_words & observed_words)
            if matches_person and document.title:
                names.append(
                    AttributedFact(
                        value=document.title,
                        source_url=document.url,
                        confidence="medium",
                        why="public page title associated with this candidate",
                        source_kind=_page_source_kind(document.url),
                        subject_name=candidate.name,
                    )
                )
            if not matches_person:
                continue
            for address in (
                *document.email_addresses,
                *EMAIL_PATTERN.findall(document.text),
            ):
                company_level = _company_level_address(address, document.url)
                addresses.append(
                    AttributedFact(
                        value=normalize_address(address),
                        source_url=document.url,
                        confidence="high",
                        why="email address explicitly published on a candidate page",
                        source_kind=(
                            "mailto"
                            if address in document.email_addresses
                            else _page_source_kind(document.url)
                        ),
                        subject_name=None if company_level else candidate.name,
                        company_level=company_level,
                    )
                )
        return EmailHarvestResult(addresses=addresses, names=names)

    async def _harvest_network(
        self,
        client: httpx.AsyncClient,
        candidate: Candidate,
        dossier: CandidateDossier,
        result: EmailHarvestResult,
    ) -> None:
        github_logins = list(
            dict.fromkeys(
                login
                for profile in candidate.profiles
                if profile.kind == "github"
                and (login := _github_login(profile.url)) is not None
            )
        )
        tasks = [
            asyncio.create_task(self._github(client, login))
            for login in github_logins[:3]
        ]
        domain = dossier.company.domain.value if dossier.company.domain else None
        if domain and _valid_domain(domain):
            tasks.extend(
                (
                    asyncio.create_task(self._security_txt(client, domain)),
                    asyncio.create_task(self._rdap(client, domain)),
                )
            )
        if not tasks:
            return
        values = await asyncio.gather(*tasks, return_exceptions=True)
        for value in values:
            if isinstance(value, EmailHarvestResult):
                result.addresses.extend(value.addresses)
                result.names.extend(value.names)

    async def _github(self, client: httpx.AsyncClient, login: str) -> EmailHarvestResult:
        result = EmailHarvestResult()
        profile_url = f"{self.github_api}/users/{quote(login, safe='')}"
        try:
            response = await client.get(
                profile_url, headers=self.github_headers, timeout=self.timeout
            )
            response.raise_for_status()
            profile: dict[str, Any] = response.json()
        except Exception:  # noqa: BLE001 - every public source degrades independently
            return result
        public_url = str(profile.get("html_url") or f"https://github.com/{login}")
        display_name = sanitize_external_text(
            str(profile.get("name") or ""), max_length=100
        )
        if display_name:
            result.names.append(
                AttributedFact(
                    value=display_name,
                    source_url=public_url,
                    confidence="high",
                    why="display name returned by the public GitHub profile API",
                    source_kind="github_profile",
                    subject_name=display_name,
                )
            )
        profile_email = normalize_address(str(profile.get("email") or ""))
        if (
            profile_email
            and EMAIL_PATTERN.fullmatch(profile_email)
            and not is_github_noreply(profile_email)
        ):
            result.addresses.append(
                AttributedFact(
                    value=profile_email,
                    source_url=public_url,
                    confidence="high",
                    why="email returned by the public GitHub profile API",
                    source_kind="github_profile",
                    subject_name=display_name or None,
                )
            )

        repos_url = f"{self.github_api}/users/{quote(login, safe='')}/repos"
        try:
            response = await client.get(
                repos_url,
                params={
                    "type": "owner",
                    "sort": "pushed",
                    "per_page": self.github_repo_limit,
                },
                headers=self.github_headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            repos = response.json()
        except Exception:  # noqa: BLE001 - a profile email remains useful on repo failure
            return result
        commit_tasks = []
        for repo in repos[: self.github_repo_limit] if isinstance(repos, list) else []:
            full_name = str(repo.get("full_name") or "")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
                continue
            commit_tasks.append(
                asyncio.create_task(self._github_commits(client, full_name, login))
            )
        if commit_tasks:
            values = await asyncio.gather(*commit_tasks, return_exceptions=True)
            for value in values:
                if isinstance(value, EmailHarvestResult):
                    result.addresses.extend(value.addresses)
                    result.names.extend(value.names)
        return result

    async def _github_commits(
        self, client: httpx.AsyncClient, full_name: str, login: str
    ) -> EmailHarvestResult:
        endpoint = f"{self.github_api}/repos/{full_name}/commits"
        result = EmailHarvestResult()
        try:
            response = await client.get(
                endpoint,
                params={"author": login, "per_page": self.github_commit_limit},
                headers=self.github_headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            rows = response.json()
        except Exception:  # noqa: BLE001 - one repository cannot fail discovery
            return result
        for row in rows[: self.github_commit_limit] if isinstance(rows, list) else []:
            commit = row.get("commit") or {}
            source_url = str(row.get("html_url") or endpoint)
            for author_key in ("author", "committer"):
                author = commit.get(author_key) or {}
                address = normalize_address(str(author.get("email") or ""))
                name = sanitize_external_text(
                    str(author.get("name") or ""), max_length=100
                )
                if (
                    address
                    and EMAIL_PATTERN.fullmatch(address)
                    and not is_github_noreply(address)
                ):
                    result.addresses.append(
                        AttributedFact(
                            value=address,
                            source_url=source_url,
                            confidence="high",
                            why="author email published in public GitHub commit metadata",
                            source_kind="github_commit",
                            subject_name=name or None,
                        )
                    )
                if name:
                    result.names.append(
                        AttributedFact(
                            value=name,
                            source_url=source_url,
                            confidence="medium",
                            why="author name published in public GitHub commit metadata",
                            source_kind="github_commit",
                            subject_name=name,
                        )
                    )
        return result

    async def _security_txt(
        self, client: httpx.AsyncClient, domain: str
    ) -> EmailHarvestResult:
        for path in ("/.well-known/security.txt", "/security.txt"):
            source_url = f"https://{domain}{path}"
            try:
                response = await client.get(source_url, timeout=self.timeout)
                if response.status_code == 404:
                    continue
                response.raise_for_status()
            except Exception:  # noqa: BLE001, S112 - optional public endpoint
                continue
            addresses = []
            for line in response.text.splitlines():
                if not line.casefold().startswith("contact:"):
                    continue
                addresses.extend(EMAIL_PATTERN.findall(line))
            return EmailHarvestResult(
                addresses=[
                    AttributedFact(
                        value=normalize_address(address),
                        source_url=source_url,
                        confidence="high",
                        why="contact address explicitly published in security.txt",
                        source_kind="security_txt",
                        company_level=True,
                    )
                    for address in addresses
                ]
            )
        return EmailHarvestResult()

    async def _rdap(self, client: httpx.AsyncClient, domain: str) -> EmailHarvestResult:
        source_url = f"{self.rdap_api}/{quote(domain, safe='')}"
        try:
            response = await client.get(
                source_url,
                headers={"Accept": "application/rdap+json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001 - WHOIS/RDAP is optional
            return EmailHarvestResult()
        addresses = EMAIL_PATTERN.findall(" ".join(_string_values(payload)))
        return EmailHarvestResult(
            addresses=[
                AttributedFact(
                    value=normalize_address(address),
                    source_url=source_url,
                    confidence="high",
                    why="abuse/admin contact published in public domain RDAP data",
                    source_kind="whois_rdap",
                    company_level=True,
                )
                for address in addresses
            ]
        )


def _github_login(url: str) -> str | None:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if (parsed.hostname or "").casefold() != "github.com" or len(parts) != 1:
        return None
    return parts[0] if re.fullmatch(r"[A-Za-z0-9-]{1,39}", parts[0]) else None


def _page_source_kind(url: str) -> str:
    path = urlparse(url).path.casefold()
    if any(value in path for value in ("speaker", "conference", "/talk", "cfp")):
        return "conference_speaker"
    if any(value in path for value in ("team", "people", "leadership", "about")):
        return "company_team"
    if "press" in path:
        return "press_release"
    return "public_page"


def _company_level_address(address: str, source_url: str) -> bool:
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


def _valid_domain(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
            r"[a-z]{2,63}",
            value.casefold().strip("."),
        )
    )


def _string_values(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item)


def _dedupe_facts(values: list[AttributedFact]) -> list[AttributedFact]:
    result: list[AttributedFact] = []
    seen: set[str] = set()
    for fact in values:
        address = normalize_address(fact.value)
        if (
            not EMAIL_PATTERN.fullmatch(address)
            or is_github_noreply(address)
            or recipient_key(address) in seen
        ):
            continue
        seen.add(recipient_key(address))
        result.append(fact.model_copy(update={"value": address}))
    return result


def _dedupe_names(values: list[AttributedFact]) -> list[AttributedFact]:
    result: list[AttributedFact] = []
    seen: set[tuple[str, str]] = set()
    for fact in values:
        key = (fact.value.casefold(), fact.source_url.casefold())
        if key not in seen:
            seen.add(key)
            result.append(fact)
    return result
