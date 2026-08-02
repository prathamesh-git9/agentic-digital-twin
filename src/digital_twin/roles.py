from __future__ import annotations

import re
from typing import Literal
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from pydantic import BaseModel, Field
from selectolax.parser import HTMLParser

from .profile import ProfileCorpus, tokens
from .security import is_public_http_url, sanitize_external_text

ATSKind = Literal[
    "greenhouse",
    "lever",
    "ashby",
    "workable",
    "smartrecruiters",
    "recruitee",
    "careers_page",
]


class ATSBoard(BaseModel):
    kind: ATSKind
    token: str
    source_url: str


class RoleEvidence(BaseModel):
    signal: str
    evidence: str
    source: str


class RoleMatch(BaseModel):
    title: str
    team: str | None = None
    location: str | None = None
    canonical_apply_url: str
    requisition_id: str | None = None
    ats: ATSKind
    fit_score: int = Field(ge=0, le=100)
    evidence: list[RoleEvidence]
    source_url: str


class RoleDiscoveryResult(BaseModel):
    board: ATSBoard | None = None
    roles: list[RoleMatch] = []
    status: Literal["ok", "empty", "failed"]
    reason: str


class _RawRole(BaseModel):
    title: str
    team: str | None = None
    location: str | None = None
    apply_url: str
    requisition_id: str | None = None
    description: str = ""
    ats: ATSKind
    source_url: str


def detect_ats(
    careers_url: str, *, html: str = "", links: list[str] | None = None
) -> list[ATSBoard]:
    candidates = [careers_url, *(links or [])]
    candidates.extend(re.findall(r"https?://[^\s\"'<>]+", html))
    boards: list[ATSBoard] = []
    seen: set[tuple[str, str]] = set()
    for raw_url in candidates:
        url = raw_url.rstrip(".,);]")
        if not is_public_http_url(url):
            continue
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        parts = [part for part in parsed.path.split("/") if part]
        board: ATSBoard | None = None
        if host in {
            "boards.greenhouse.io",
            "job-boards.greenhouse.io",
            "boards-api.greenhouse.io",
        }:
            token = parse_qs(parsed.query).get("for", [parts[0] if parts else ""])[0]
            if token and token not in {"embed", "v1", "boards"}:
                board = ATSBoard(kind="greenhouse", token=token, source_url=url)
        elif host in {"jobs.lever.co", "api.lever.co", "jobs.eu.lever.co"}:
            offset = parts.index("postings") + 1 if "postings" in parts else 0
            if len(parts) > offset:
                board = ATSBoard(kind="lever", token=parts[offset], source_url=url)
        elif host == "jobs.ashbyhq.com" and parts:
            board = ATSBoard(kind="ashby", token=parts[0], source_url=url)
        elif host == "apply.workable.com" and parts:
            board = ATSBoard(kind="workable", token=parts[0], source_url=url)
        elif host == "careers.smartrecruiters.com" and parts:
            board = ATSBoard(kind="smartrecruiters", token=parts[0], source_url=url)
        elif host.endswith(".recruitee.com"):
            board = ATSBoard(
                kind="recruitee",
                token=host.removesuffix(".recruitee.com"),
                source_url=url,
            )
        if board and (board.kind, board.token.casefold()) not in seen:
            boards.append(board)
            seen.add((board.kind, board.token.casefold()))
    return boards


class PublicATSClient:
    def __init__(
        self, *, timeout: float = 5.0, client: httpx.AsyncClient | None = None
    ) -> None:
        self.timeout = timeout
        self.client = client

    async def fetch(self, board: ATSBoard) -> list[_RawRole]:
        method, url, kwargs = self._request(board)
        if self.client is not None:
            response = await self.client.request(
                method, url, timeout=self.timeout, **kwargs
            )
        else:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.request(
                    method, url, timeout=self.timeout, **kwargs
                )
        response.raise_for_status()
        return self._parse(board, response.json())

    @staticmethod
    def _request(board: ATSBoard) -> tuple[str, str, dict[str, object]]:
        token = board.token
        if board.kind == "greenhouse":
            return (
                "GET",
                f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                {"params": {"content": "true"}},
            )
        if board.kind == "lever":
            host = (
                "api.eu.lever.co" if "eu.lever.co" in board.source_url else "api.lever.co"
            )
            return (
                "GET",
                f"https://{host}/v0/postings/{token}",
                {"params": {"mode": "json", "limit": 200}},
            )
        if board.kind == "ashby":
            return (
                "GET",
                f"https://api.ashbyhq.com/posting-api/job-board/{token}",
                {},
            )
        if board.kind == "workable":
            return (
                "POST",
                f"https://apply.workable.com/api/v3/accounts/{token}/jobs",
                {"json": {"query": "", "location": [], "department": []}},
            )
        if board.kind == "smartrecruiters":
            return (
                "GET",
                f"https://api.smartrecruiters.com/v1/companies/{token}/postings",
                {"params": {"limit": 100}},
            )
        if board.kind == "recruitee":
            return "GET", f"https://{token}.recruitee.com/api/offers/", {}
        raise ValueError("unsupported ATS board")

    def _parse(self, board: ATSBoard, payload: object) -> list[_RawRole]:
        if board.kind == "greenhouse":
            return self._greenhouse(board, payload)
        if board.kind == "lever":
            return self._lever(board, payload)
        if board.kind == "ashby":
            return self._ashby(board, payload)
        if board.kind == "workable":
            return self._workable(board, payload)
        if board.kind == "smartrecruiters":
            return self._smartrecruiters(board, payload)
        if board.kind == "recruitee":
            return self._recruitee(board, payload)
        return []

    @staticmethod
    def _greenhouse(board: ATSBoard, payload: object) -> list[_RawRole]:
        data = payload if isinstance(payload, dict) else {}
        values = []
        for item in data.get("jobs", []):
            departments = item.get("departments") or []
            values.append(
                _make_role(
                    board,
                    title=item.get("title"),
                    team=departments[0].get("name") if departments else None,
                    location=(item.get("location") or {}).get("name"),
                    url=item.get("absolute_url"),
                    requisition=item.get("id"),
                    description=item.get("content"),
                )
            )
        return _valid_roles(values)

    @staticmethod
    def _lever(board: ATSBoard, payload: object) -> list[_RawRole]:
        items = payload if isinstance(payload, list) else []
        values = []
        for item in items:
            categories = item.get("categories") or {}
            values.append(
                _make_role(
                    board,
                    title=item.get("text"),
                    team=categories.get("team") or categories.get("department"),
                    location=categories.get("location"),
                    url=item.get("applyUrl") or item.get("hostedUrl"),
                    requisition=item.get("id"),
                    description=item.get("descriptionPlain") or item.get("openingPlain"),
                )
            )
        return _valid_roles(values)

    @staticmethod
    def _ashby(board: ATSBoard, payload: object) -> list[_RawRole]:
        data = payload if isinstance(payload, dict) else {}
        values = [
            _make_role(
                board,
                title=item.get("title"),
                team=item.get("department"),
                location=item.get("location"),
                url=item.get("applyUrl") or item.get("jobUrl"),
                requisition=item.get("id") or item.get("jobUrl"),
                description=item.get("descriptionPlain") or item.get("descriptionHtml"),
            )
            for item in data.get("jobs", [])
        ]
        return _valid_roles(values)

    @staticmethod
    def _workable(board: ATSBoard, payload: object) -> list[_RawRole]:
        data = payload if isinstance(payload, dict) else {}
        values = []
        for item in data.get("results", data.get("jobs", [])):
            location = item.get("location")
            if isinstance(location, dict):
                location = ", ".join(
                    str(location[key])
                    for key in ("city", "region", "country")
                    if location.get(key)
                )
            values.append(
                _make_role(
                    board,
                    title=item.get("title"),
                    team=item.get("department"),
                    location=location,
                    url=item.get("url") or item.get("application_url"),
                    requisition=item.get("shortcode") or item.get("id"),
                    description=item.get("description") or item.get("requirements"),
                )
            )
        return _valid_roles(values)

    @staticmethod
    def _smartrecruiters(board: ATSBoard, payload: object) -> list[_RawRole]:
        data = payload if isinstance(payload, dict) else {}
        values = []
        for item in data.get("content", []):
            location_data = item.get("location") or {}
            location = ", ".join(
                str(location_data[key])
                for key in ("city", "region", "country")
                if location_data.get(key)
            )
            department = item.get("department") or {}
            identifier = item.get("id") or item.get("uuid") or item.get("refNumber")
            url = item.get("postingUrl")
            if not url and identifier:
                url = f"https://jobs.smartrecruiters.com/{board.token}/{identifier}"
            values.append(
                _make_role(
                    board,
                    title=item.get("name"),
                    team=department.get("label")
                    if isinstance(department, dict)
                    else department,
                    location=location,
                    url=url,
                    requisition=item.get("refNumber") or identifier,
                    description=item.get("jobAd", {})
                    .get("sections", {})
                    .get("jobDescription", {})
                    .get("text", ""),
                )
            )
        return _valid_roles(values)

    @staticmethod
    def _recruitee(board: ATSBoard, payload: object) -> list[_RawRole]:
        data = payload if isinstance(payload, dict) else {}
        values = [
            _make_role(
                board,
                title=item.get("title"),
                team=item.get("department"),
                location=item.get("location"),
                url=item.get("careers_url") or item.get("url"),
                requisition=item.get("id") or item.get("slug"),
                description=item.get("description") or item.get("requirements"),
            )
            for item in data.get("offers", [])
        ]
        return _valid_roles(values)


class OpenRoleService:
    def __init__(self, corpus: ProfileCorpus, *, client: PublicATSClient) -> None:
        self.corpus = corpus
        self.client = client
        self.profile_text = " ".join(item.text for item in corpus.evidence)

    async def discover(
        self,
        careers_url: str,
        *,
        html: str = "",
        links: list[str] | None = None,
        link_labels: dict[str, str] | None = None,
        preferred_location: str = "Dublin, Ireland",
    ) -> RoleDiscoveryResult:
        boards = detect_ats(careers_url, html=html, links=links)
        raw_roles: list[_RawRole] = []
        selected_board = boards[0] if boards else None
        for board in boards:
            try:
                discovered = await self.client.fetch(board)
            except Exception:  # noqa: BLE001 - every ATS independently degrades
                discovered = []
            raw_roles.extend(discovered)
        if not raw_roles:
            raw_roles = _fallback_roles(careers_url, html)
        if not raw_roles and link_labels:
            raw_roles = _fallback_labelled_links(careers_url, link_labels)
        matches = [self._rank(role, preferred_location) for role in raw_roles]
        matches.sort(key=lambda role: (-role.fit_score, role.title.casefold()))
        if not matches:
            return RoleDiscoveryResult(
                board=selected_board,
                status="empty",
                reason="No attributable open role was observed.",
            )
        return RoleDiscoveryResult(
            board=selected_board,
            roles=matches[:20],
            status="ok",
            reason=f"Ranked {len(matches)} public open roles against supplied evidence.",
        )

    def _rank(self, role: _RawRole, preferred_location: str) -> RoleMatch:
        role_text = f"{role.title} {role.team or ''} {role.description}"
        role_tokens = tokens(role_text)
        profile_tokens = tokens(self.profile_text)
        technology_terms = {
            "python",
            "java",
            "spring",
            "fastapi",
            "sql",
            "llm",
            "agentic",
            "docker",
            "github",
            "rest",
            "microservices",
            "kafka",
            "observability",
        }
        overlaps = sorted(technology_terms & role_tokens & profile_tokens)
        evidence: list[RoleEvidence] = []
        score = min(60, len(overlaps) * 10)
        if overlaps:
            evidence.append(
                RoleEvidence(
                    signal="skills",
                    evidence=f"Shared evidenced terms: {', '.join(overlaps)}",
                    source="CV and public job description",
                )
            )
        if re.search(
            r"\b(?:back.?end|software|platform|ai|automation)\b", role.title, re.I
        ):
            score += 20
            evidence.append(
                RoleEvidence(
                    signal="role_family",
                    evidence=(
                        "Role family overlaps the CV's backend/AI engineering focus."
                    ),
                    source="CV › Summary",
                )
            )
        location = role.location or ""
        preferred = tokens(preferred_location)
        if re.search(r"\b(?:remote|emea|europe)\b", location, re.I) or preferred & tokens(
            location
        ):
            score += 15
            evidence.append(
                RoleEvidence(
                    signal="location",
                    evidence=f"Location appears compatible: {location}",
                    source=role.source_url,
                )
            )
        elif location:
            score -= 8
            evidence.append(
                RoleEvidence(
                    signal="location_sanity",
                    evidence=f"Location requires review: {location}",
                    source=role.source_url,
                )
            )
        if re.search(r"\b(?:principal|staff|director|head|vp)\b", role.title, re.I):
            score -= 25
            evidence.append(
                RoleEvidence(
                    signal="seniority_sanity",
                    evidence=(
                        "Title is above the 3.5+ years explicitly evidenced in the CV."
                    ),
                    source="CV › Summary",
                )
            )
        elif re.search(r"\bsenior\b", role.title, re.I):
            score -= 10
        else:
            score += 5
        return RoleMatch(
            title=role.title,
            team=role.team,
            location=role.location,
            canonical_apply_url=role.apply_url,
            requisition_id=role.requisition_id,
            ats=role.ats,
            fit_score=max(0, min(100, score)),
            evidence=evidence,
            source_url=role.source_url,
        )


def _make_role(
    board: ATSBoard,
    *,
    title: object,
    team: object,
    location: object,
    url: object,
    requisition: object,
    description: object,
) -> _RawRole | None:
    safe_title = sanitize_external_text(str(title or ""), max_length=240)
    safe_url = str(url or "")
    safe_id = sanitize_external_text(str(requisition or ""), max_length=120) or None
    if not safe_title or not is_public_http_url(safe_url):
        return None
    return _RawRole(
        title=safe_title,
        team=sanitize_external_text(str(team or ""), max_length=160) or None,
        location=sanitize_external_text(str(location or ""), max_length=200) or None,
        apply_url=safe_url,
        requisition_id=safe_id,
        description=sanitize_external_text(str(description or ""), max_length=8_000),
        ats=board.kind,
        source_url=board.source_url,
    )


def _valid_roles(values: list[_RawRole | None]) -> list[_RawRole]:
    return [value for value in values if value is not None]


def _fallback_roles(careers_url: str, html: str) -> list[_RawRole]:
    if not html:
        return []
    tree = HTMLParser(html)
    values: list[_RawRole] = []
    board = ATSBoard(
        kind="careers_page",
        token="-".join(("careers", "page")),
        source_url=careers_url,
    )
    for anchor in tree.css("a[href]"):
        title = sanitize_external_text(anchor.text(separator=" "), max_length=240)
        url = urljoin(careers_url, anchor.attributes.get("href", ""))
        if not re.search(
            r"\b(?:engineer|developer|architect|platform|software)\b", title, re.I
        ):
            continue
        role = _make_role(
            board,
            title=title,
            team=None,
            location=None,
            url=url,
            requisition=None,
            description="",
        )
        if role:
            values.append(role)
    return values


def _fallback_labelled_links(careers_url: str, labels: dict[str, str]) -> list[_RawRole]:
    board = ATSBoard(
        kind="careers_page",
        token="-".join(("careers", "links")),
        source_url=careers_url,
    )
    values: list[_RawRole] = []
    for url, title in labels.items():
        if not re.search(
            r"\b(?:engineer|developer|architect|platform|software)\b", title, re.I
        ):
            continue
        role = _make_role(
            board,
            title=title,
            team=None,
            location=None,
            url=url,
            requisition=None,
            description="",
        )
        if role:
            values.append(role)
    return values
