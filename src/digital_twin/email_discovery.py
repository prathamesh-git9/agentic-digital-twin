from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urljoin

import dns.asyncresolver
import httpx
from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel

from .research import Candidate, CandidateDossier, CandidateEmail

ROLE_LOCALS = {
    "admin",
    "careers",
    "contact",
    "hello",
    "hr",
    "info",
    "jobs",
    "noreply",
    "press",
    "recruiting",
    "sales",
    "security",
    "support",
}
DISPOSABLE_DOMAINS = {
    "10minutemail.com",
    "guerrillamail.com",
    "mailinator.com",
    "temp-mail.org",
    "yopmail.com",
}


class MXResolver(Protocol):
    async def records(self, domain: str) -> list[str]: ...


class VerificationAdapter(Protocol):
    async def verify(self, address: str) -> tuple[bool, str]: ...


class DnspythonMXResolver:
    def __init__(self, *, timeout: float = 3.0) -> None:
        self.timeout = timeout

    async def records(self, domain: str) -> list[str]:
        resolver = dns.asyncresolver.Resolver()
        resolver.lifetime = self.timeout
        answer = await asyncio.wait_for(
            resolver.resolve(domain, "MX"), timeout=self.timeout
        )
        return [str(record.exchange).rstrip(".") for record in answer]


class HunterVerificationAdapter:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.hunter.io/v2",
        timeout: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.endpoint = urljoin(base_url.rstrip("/") + "/", "email-verifier")
        self.timeout = timeout
        self.client = client

    async def verify(self, address: str) -> tuple[bool, str]:
        params = {"email": address, "api_key": self.api_key}
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
        data = response.json().get("data", {})
        return data.get("status") == "valid", self.endpoint


class EmailDiscoveryResult(BaseModel):
    status: Literal["verified", "inferred", "unavailable"]
    selected: CandidateEmail | None = None
    candidates: list[CandidateEmail] = []
    mx_records: list[str] = []
    observed_pattern: str | None = None
    reason: str


@dataclass(frozen=True, slots=True)
class _Pattern:
    label: str
    local: str
    rank: int


class EmailDiscoveryService:
    """Honest discovery: publication/API can verify; DNS and patterns cannot."""

    def __init__(
        self,
        *,
        mx_resolver: MXResolver,
        verifier: VerificationAdapter | None = None,
    ) -> None:
        self.mx_resolver = mx_resolver
        self.verifier = verifier

    async def discover(
        self, candidate: Candidate, dossier: CandidateDossier
    ) -> EmailDiscoveryResult:
        if candidate.email and candidate.email.status == "verified":
            return EmailDiscoveryResult(
                status="verified",
                selected=candidate.email,
                candidates=[candidate.email],
                reason=(
                    "The address is explicitly published on an attributed public page."
                ),
            )
        published = (
            dossier.person.public_emails[0] if dossier.person.public_emails else None
        )
        if published and _syntax_valid(published.value):
            address = published.value.casefold()
            exact = CandidateEmail(
                address=address,
                status="verified",
                confidence="high",
                source_url=published.source_url,
                why="address is explicitly displayed in an attributed public source",
            )
            return EmailDiscoveryResult(
                status="verified",
                selected=exact,
                candidates=[exact],
                reason="A published address was observed.",
            )

        domain_fact = dossier.company.domain
        if domain_fact is None:
            return EmailDiscoveryResult(
                status="unavailable",
                reason="No attributable company domain was observed.",
            )
        domain = domain_fact.value.casefold().strip(".")
        if domain in DISPOSABLE_DOMAINS:
            return EmailDiscoveryResult(
                status="unavailable", reason="Disposable domains are never used."
            )
        try:
            mx_records = await self.mx_resolver.records(domain)
        except Exception:  # noqa: BLE001 - DNS failure is an honest unavailable result
            mx_records = []
        if not mx_records:
            return EmailDiscoveryResult(
                status="unavailable",
                reason="The observed domain has no confirmed MX records.",
            )

        patterns = _rank_patterns(candidate.name)
        observed = _observed_pattern(dossier, domain)
        if observed:
            patterns.sort(key=lambda item: (item.label != observed, item.rank))
        addresses: list[CandidateEmail] = []
        for pattern in patterns:
            if pattern.local in ROLE_LOCALS:
                continue
            address = f"{pattern.local}@{domain}"
            if not _syntax_valid(address):
                continue
            confidence: Literal["high", "medium", "low"] = (
                "high" if observed == pattern.label else "medium"
            )
            addresses.append(
                CandidateEmail(
                    address=address,
                    status="inferred",
                    confidence=confidence,
                    source_url=domain_fact.source_url,
                    why=(
                        f"inferred from observed {observed} company pattern and MX"
                        if observed == pattern.label
                        else f"standard {pattern.label} pattern on an MX-enabled domain"
                    ),
                )
            )
        if not addresses:
            return EmailDiscoveryResult(
                status="unavailable",
                mx_records=mx_records,
                reason="No safe candidate address could be formed.",
            )

        if self.verifier is not None:
            for index, inferred in enumerate(addresses[:3]):
                try:
                    valid, source_url = await self.verifier.verify(inferred.address)
                except Exception:  # noqa: BLE001 - optional verification degrades
                    valid, source_url = False, ""
                if valid:
                    verified = inferred.model_copy(
                        update={
                            "status": "verified",
                            "confidence": "high",
                            "source_url": source_url,
                            "why": (
                                "address returned valid by the configured "
                                "verification API"
                            ),
                        }
                    )
                    addresses[index] = verified
                    return EmailDiscoveryResult(
                        status="verified",
                        selected=verified,
                        candidates=addresses,
                        mx_records=mx_records,
                        observed_pattern=observed,
                        reason="The configured verification API validated the address.",
                    )
        return EmailDiscoveryResult(
            status="inferred",
            selected=addresses[0],
            candidates=addresses,
            mx_records=mx_records,
            observed_pattern=observed,
            reason="Pattern candidates remain inferred; MX does not verify a mailbox.",
        )


def _name_parts(name: str) -> tuple[str, str]:
    parts = [re.sub(r"[^a-z0-9]", "", part.casefold()) for part in name.split()]
    parts = [part for part in parts if part]
    if not parts:
        return "", ""
    return parts[0], parts[-1] if len(parts) > 1 else ""


def _rank_patterns(name: str) -> list[_Pattern]:
    first, last = _name_parts(name)
    values = [
        _Pattern("first.last", f"{first}.{last}", 1),
        _Pattern("first", first, 2),
        _Pattern("flast", f"{first[:1]}{last}", 3),
        _Pattern("firstl", f"{first}{last[:1]}", 4),
        _Pattern("first_last", f"{first}_{last}", 5),
        _Pattern("first-last", f"{first}-{last}", 6),
    ]
    return [value for value in values if value.local.strip("._-")]


def _observed_pattern(dossier: CandidateDossier, domain: str) -> str | None:
    for fact in dossier.company.public_emails:
        local, separator, observed_domain = fact.value.casefold().partition("@")
        if not separator or observed_domain != domain or local in ROLE_LOCALS:
            continue
        if "." in local:
            return "first.last"
        if "_" in local:
            return "first_last"
        if "-" in local:
            return "first-last"
    return None


def _syntax_valid(address: str) -> bool:
    try:
        validated = validate_email(address, check_deliverability=False)
    except EmailNotValidError:
        return False
    local = validated.local_part.casefold()
    return (
        local not in ROLE_LOCALS and validated.domain.casefold() not in DISPOSABLE_DOMAINS
    )
