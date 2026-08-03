from __future__ import annotations

import asyncio
import hashlib
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urljoin

import dns.asyncresolver
import httpx
from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, Field

from .email_harvesting import EmailHarvestResult
from .email_utils import normalize_address, recipient_key
from .identity import (
    PublicNameEvidence,
    nickname_variants,
    parse_name_parts,
    resolve_public_name,
)
from .research import Candidate, CandidateAvatar, CandidateDossier, CandidateEmail
from .research_sources import AttributedFact

ROLE_LOCALS = {
    "abuse",
    "admin",
    "careers",
    "contact",
    "hello",
    "hr",
    "info",
    "jobs",
    "noreply",
    "press",
    "privacy",
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
GENERAL_PATTERN_PREVALENCE: dict[str, int] = {
    "first.last": 78,
    "firstlast": 73,
    "flast": 69,
    "first": 65,
    "first_last": 61,
    "f.last": 58,
    "first-last": 55,
    "firstl": 53,
    "last": 49,
    "last.first": 46,
    "lastf": 43,
    "first.middle.last": 42,
    "first.m.last": 40,
    "firstmiddlelast": 38,
    "firstmlast": 36,
    "first.mlast": 34,
    "fmlast": 32,
    "fm.last": 30,
    "initials": 26,
    "f": 20,
}


class MXResolver(Protocol):
    async def records(self, domain: str) -> list[str]: ...


class VerificationAdapter(Protocol):
    async def verify(self, address: str) -> tuple[bool, str]: ...


class EmailHarvester(Protocol):
    async def harvest(
        self, candidate: Candidate, dossier: CandidateDossier
    ) -> EmailHarvestResult: ...


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
        base = base_url.rstrip("/") + "/"
        self.endpoint = urljoin(base, "email-verifier")
        self.domain_endpoint = urljoin(base, "domain-search")
        self.timeout = timeout
        self.client = client

    async def verify(self, address: str) -> tuple[bool, str]:
        payload = await self._get(self.endpoint, {"email": address})
        data = payload.get("data", {})
        return data.get("status") == "valid", self.endpoint

    async def domain_pattern(self, domain: str) -> tuple[str | None, str]:
        payload = await self._get(self.domain_endpoint, {"domain": domain})
        raw_pattern = str(payload.get("data", {}).get("pattern") or "")
        return _provider_pattern(raw_pattern), self.domain_endpoint

    async def _get(self, url: str, params: dict[str, str]) -> dict:
        params = {**params, "api_key": self.api_key}
        if self.client is not None:
            response = await self.client.get(url, params=params, timeout=self.timeout)
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


class EmailPermutation(BaseModel):
    local: str
    pattern: str
    prevalence: int
    name_variant: str


class EmailDiscoveryResult(BaseModel):
    status: Literal["verified", "inferred", "unavailable"]
    resolved_name: str
    surname_resolved: bool
    name_source_url: str | None = None
    selected: CandidateEmail | None = None
    candidates: list[CandidateEmail] = Field(default_factory=list)
    mx_records: list[str] = Field(default_factory=list)
    observed_pattern: str | None = None
    published_count: int = 0
    inferred_count: int = 0
    reason: str


@dataclass(frozen=True, slots=True)
class _Pattern:
    label: str
    local: str
    rank: int


class EmailDiscoveryService:
    """Publication/API can verify; DNS and naming patterns never can."""

    def __init__(
        self,
        *,
        mx_resolver: MXResolver,
        verifier: VerificationAdapter | None = None,
        harvester: EmailHarvester | None = None,
        bounce_counts: Callable[[str], dict[str, int]] | None = None,
    ) -> None:
        self.mx_resolver = mx_resolver
        self.verifier = verifier
        self.harvester = harvester
        self.bounce_counts = bounce_counts
        self._pattern_cache: dict[str, str] = {}
        self._bounce_penalties: defaultdict[str, Counter[str]] = defaultdict(Counter)

    async def discover(
        self, candidate: Candidate, dossier: CandidateDossier
    ) -> EmailDiscoveryResult:
        harvest = await self._harvest(candidate, dossier)
        resolution = self._resolve_name(candidate, harvest)
        domain_fact = dossier.company.domain
        domain = (
            domain_fact.value.casefold().strip(".") if domain_fact is not None else None
        )
        observed = self._derive_observed_pattern(dossier, domain) if domain else None
        published = self._published_candidates(harvest, resolution.display_name, domain)
        if published:
            return EmailDiscoveryResult(
                status="verified",
                resolved_name=resolution.display_name,
                surname_resolved=resolution.surname_resolved,
                name_source_url=resolution.source_url,
                selected=published[0],
                candidates=published,
                observed_pattern=observed,
                published_count=len(published),
                reason=(
                    f"Found {len(published)} explicitly published address"
                    f"{'es' if len(published) != 1 else ''}."
                ),
            )

        if domain_fact is None or domain is None:
            return EmailDiscoveryResult(
                status="unavailable",
                resolved_name=resolution.display_name,
                surname_resolved=resolution.surname_resolved,
                name_source_url=resolution.source_url,
                reason="No attributable company domain was observed.",
            )
        if domain in DISPOSABLE_DOMAINS:
            return EmailDiscoveryResult(
                status="unavailable",
                resolved_name=resolution.display_name,
                surname_resolved=resolution.surname_resolved,
                name_source_url=resolution.source_url,
                reason="Disposable domains are never used.",
            )

        adapter_pattern = await self._verification_domain_pattern(domain)
        if adapter_pattern:
            observed = adapter_pattern
            self._pattern_cache[domain] = adapter_pattern
        try:
            mx_records = await self.mx_resolver.records(domain)
        except Exception:  # noqa: BLE001 - DNS failure is an honest unavailable result
            mx_records = []
        if not mx_records:
            return EmailDiscoveryResult(
                status="unavailable",
                resolved_name=resolution.display_name,
                surname_resolved=resolution.surname_resolved,
                name_source_url=resolution.source_url,
                observed_pattern=observed,
                reason="The observed domain has no confirmed MX records.",
            )

        permutations = generate_email_permutations(resolution.display_name)
        addresses = self._rank_inferred(
            permutations,
            domain=domain,
            source_url=domain_fact.source_url,
            observed_pattern=observed,
        )
        if not addresses:
            return EmailDiscoveryResult(
                status="unavailable",
                resolved_name=resolution.display_name,
                surname_resolved=resolution.surname_resolved,
                name_source_url=resolution.source_url,
                mx_records=mx_records,
                observed_pattern=observed,
                reason="No safe candidate address could be formed.",
            )

        verified = await self._verify_top(addresses)
        if verified is not None:
            addresses.sort(key=lambda value: (value.status != "verified", -value.score))
            return EmailDiscoveryResult(
                status="verified",
                resolved_name=resolution.display_name,
                surname_resolved=resolution.surname_resolved,
                name_source_url=resolution.source_url,
                selected=verified,
                candidates=addresses,
                mx_records=mx_records,
                observed_pattern=observed,
                published_count=0,
                inferred_count=len(addresses) - 1,
                reason="The configured verification API validated the address.",
            )
        return EmailDiscoveryResult(
            status="inferred",
            resolved_name=resolution.display_name,
            surname_resolved=resolution.surname_resolved,
            name_source_url=resolution.source_url,
            selected=addresses[0],
            candidates=addresses,
            mx_records=mx_records,
            observed_pattern=observed,
            inferred_count=len(addresses),
            reason="Pattern candidates remain inferred; MX does not verify a mailbox.",
        )

    def record_bounce(self, domain: str, pattern: str | None) -> None:
        normalized_domain = domain.casefold().strip(".")
        if normalized_domain and pattern and self.bounce_counts is None:
            self._bounce_penalties[normalized_domain][pattern] += 1

    def cached_pattern(self, domain: str) -> str | None:
        return self._pattern_cache.get(domain.casefold().strip("."))

    async def _harvest(
        self, candidate: Candidate, dossier: CandidateDossier
    ) -> EmailHarvestResult:
        fallback = _observed_harvest(candidate, dossier)
        if self.harvester is None:
            return fallback
        try:
            harvested = await self.harvester.harvest(candidate, dossier)
        except Exception:  # noqa: BLE001 - harvesting cannot break research
            return fallback
        return EmailHarvestResult(
            addresses=_dedupe_facts([*fallback.addresses, *harvested.addresses]),
            names=[*fallback.names, *harvested.names],
        )

    @staticmethod
    def _resolve_name(candidate: Candidate, harvest: EmailHarvestResult):
        submitted = candidate.submitted_name or candidate.name
        evidence: list[PublicNameEvidence] = []
        if candidate.name_detail is not None:
            evidence.append(
                PublicNameEvidence(
                    name=candidate.name_detail.value,
                    source_url=candidate.name_detail.source_url,
                    source_kind=candidate.name_detail.source_kind,
                    confidence=_confidence_score(candidate.name_detail.confidence),
                )
            )
        elif candidate.source_link:
            evidence.append(
                PublicNameEvidence(
                    name=candidate.name,
                    source_url=candidate.source_link,
                    source_kind="search_result",
                    confidence=80,
                )
            )
        evidence.extend(
            PublicNameEvidence(
                name=fact.value,
                source_url=fact.source_url,
                source_kind=fact.source_kind,
                confidence=_confidence_score(fact.confidence),
            )
            for fact in harvest.names
        )
        return resolve_public_name(submitted, evidence)

    def _derive_observed_pattern(
        self, dossier: CandidateDossier, domain: str
    ) -> str | None:
        if domain in self._pattern_cache:
            return self._pattern_cache[domain]
        patterns = [
            pattern
            for fact in dossier.company.public_emails
            if (pattern := _pattern_from_fact(fact, domain)) is not None
        ]
        if not patterns:
            return None
        selected = Counter(patterns).most_common(1)[0][0]
        self._pattern_cache[domain] = selected
        return selected

    async def _verification_domain_pattern(self, domain: str) -> str | None:
        if domain in self._pattern_cache:
            return self._pattern_cache[domain]
        domain_pattern = getattr(self.verifier, "domain_pattern", None)
        if not callable(domain_pattern):
            return None
        try:
            pattern, _ = await domain_pattern(domain)
        except Exception:  # noqa: BLE001 - optional verification degrades
            return None
        return pattern

    def _published_candidates(
        self,
        harvest: EmailHarvestResult,
        resolved_name: str,
        company_domain: str | None,
    ) -> list[CandidateEmail]:
        values: list[CandidateEmail] = []
        seen: set[str] = set()
        for fact in harvest.addresses:
            address = normalize_address(fact.value)
            if not _syntax_valid(address, allow_role=True):
                continue
            key = recipient_key(address)
            if key in seen:
                continue
            seen.add(key)
            _, _, domain = address.rpartition("@")
            pattern = (
                derive_email_pattern(address, fact.subject_name or resolved_name)
                if domain == company_domain and not fact.company_level
                else None
            )
            values.append(
                CandidateEmail(
                    address=address,
                    status="verified",
                    confidence="high",
                    source_url=fact.source_url,
                    why=fact.why,
                    pattern=pattern,
                    score=100,
                    mx_valid=None,
                    source_kind=fact.source_kind,
                    company_level=fact.company_level,
                )
            )
        return values

    def _rank_inferred(
        self,
        permutations: list[EmailPermutation],
        *,
        domain: str,
        source_url: str,
        observed_pattern: str | None,
    ) -> list[CandidateEmail]:
        penalties = Counter(self.bounce_counts(domain) if self.bounce_counts else {})
        penalties.update(self._bounce_penalties.get(domain, {}))
        values: list[CandidateEmail] = []
        seen: set[str] = set()
        for permutation in permutations:
            address = f"{permutation.local}@{domain}"
            if not _syntax_valid(address) or recipient_key(address) in seen:
                continue
            seen.add(recipient_key(address))
            observed_match = permutation.pattern == observed_pattern
            score = 100 if observed_match else permutation.prevalence
            if permutation.name_variant != "resolved name":
                score -= 3
            bounce_count = penalties.get(permutation.pattern, 0)
            score = max(0, score - min(75, bounce_count * 35))
            why = [
                (
                    f"matches the observed {observed_pattern} pattern for {domain}"
                    if observed_match
                    else f"general {permutation.pattern} pattern prevalence"
                ),
                f"{domain} has MX records",
            ]
            if permutation.name_variant != "resolved name":
                why.append(permutation.name_variant)
            if bounce_count:
                why.append(
                    f"demoted after {bounce_count} recorded bounce"
                    f"{'s' if bounce_count != 1 else ''} for this domain pattern"
                )
            values.append(
                CandidateEmail(
                    address=address,
                    status="inferred",
                    confidence="high"
                    if observed_match and not bounce_count
                    else "medium",
                    source_url=source_url,
                    why="; ".join(why),
                    pattern=permutation.pattern,
                    score=score,
                    mx_valid=True,
                    source_kind="inference",
                )
            )
        values.sort(
            key=lambda value: (
                -value.score,
                -GENERAL_PATTERN_PREVALENCE.get(value.pattern or "", 0),
                value.address,
            )
        )
        return values

    async def _verify_top(self, addresses: list[CandidateEmail]) -> CandidateEmail | None:
        if self.verifier is None:
            return None
        for index, inferred in enumerate(addresses[:3]):
            try:
                valid, source_url = await self.verifier.verify(inferred.address)
            except Exception:  # noqa: BLE001, S112 - optional adapter degrades
                continue
            if not valid:
                continue
            verified = inferred.model_copy(
                update={
                    "status": "verified",
                    "confidence": "high",
                    "source_url": source_url,
                    "source_kind": "verification_api",
                    "score": max(100, inferred.score),
                    "why": (
                        "address returned valid by the configured verification API; "
                        f"original rank: {inferred.why}"
                    ),
                }
            )
            addresses[index] = verified
            return verified
        return None


def generate_email_permutations(name: str) -> list[EmailPermutation]:
    parts = parse_name_parts(name)
    if not parts.first:
        return []
    first_values = nickname_variants(parts.first) or (parts.first,)
    surname_values = parts.surname_variants or ((parts.last,) if parts.last else ())
    values: list[EmailPermutation] = []

    def add(local: str, pattern: str, variant: str) -> None:
        local = local.strip("._-")
        if local:
            values.append(
                EmailPermutation(
                    local=local,
                    pattern=pattern,
                    prevalence=GENERAL_PATTERN_PREVALENCE.get(pattern, 25),
                    name_variant=variant,
                )
            )

    for first in first_values:
        variant = "resolved name" if first == parts.first else f"nickname variant {first}"
        add(first, "first", variant)
        add(first[:1], "f", variant)
        for last_index, last in enumerate(surname_values):
            surname_variant = variant
            if last_index:
                surname_variant = f"{variant}; alternate compound surname"
            for local, pattern in (
                (f"{first}.{last}", "first.last"),
                (f"{first}{last}", "firstlast"),
                (f"{first}_{last}", "first_last"),
                (f"{first[:1]}{last}", "flast"),
                (f"{first[:1]}.{last}", "f.last"),
                (f"{last}", "last"),
                (f"{last}{first[:1]}", "lastf"),
                (f"{last}.{first}", "last.first"),
                (f"{first}-{last}", "first-last"),
                (f"{first}{last[:1]}", "firstl"),
                (f"{first[:1]}{last[:1]}", "initials"),
            ):
                add(local, pattern, surname_variant)
            if parts.middle:
                middle = parts.middle[0]
                middle_initial = middle[:1]
                middle_patterns = [
                    (f"{first}{middle_initial}{last}", "firstmlast"),
                    (f"{first}.{middle_initial}{last}", "first.mlast"),
                    (f"{first[:1]}{middle_initial}{last}", "fmlast"),
                    (f"{first[:1]}{middle_initial}.{last}", "fm.last"),
                    (
                        f"{first[:1]}{''.join(value[:1] for value in parts.middle)}"
                        f"{last[:1]}",
                        "initials",
                    ),
                    (f"{first}.{middle_initial}.{last}", "first.m.last"),
                ]
                if len(middle) > 1:
                    middle_patterns.extend(
                        (
                            (f"{first}.{middle}.{last}", "first.middle.last"),
                            (f"{first}{middle}{last}", "firstmiddlelast"),
                        )
                    )
                for local, pattern in middle_patterns:
                    add(local, pattern, surname_variant)

    if parts.last and not parts.family_first:
        # Some sources display family name first without punctuation. Generate inverse
        # initial forms as lower-ranked alternatives without changing display text.
        add(
            f"{parts.last[:1]}{parts.first}",
            "alternate.flast",
            "alternate name order",
        )
        add(
            f"{parts.last[:1]}.{parts.first}",
            "alternate.f.last",
            "alternate name order",
        )

    result: list[EmailPermutation] = []
    seen: set[str] = set()
    for value in values:
        if value.local not in seen and value.local not in ROLE_LOCALS:
            seen.add(value.local)
            result.append(value)
    return result


def derive_email_pattern(address: str, full_name: str) -> str | None:
    local, separator, _ = normalize_address(address).partition("@")
    if not separator or local in ROLE_LOCALS:
        return None
    matches = [
        value for value in generate_email_permutations(full_name) if value.local == local
    ]
    if not matches:
        return None
    return max(matches, key=lambda value: value.prevalence).pattern


def select_send_targets(
    candidates: list[CandidateEmail], *, inferred_send_max: int = 3
) -> list[CandidateEmail]:
    """Verified wins; otherwise return a hard-capped, recipient-deduped inferred set."""

    unique: list[CandidateEmail] = []
    seen: set[str] = set()
    for candidate in sorted(
        candidates,
        key=lambda value: (value.status != "verified", -value.score, value.address),
    ):
        key = recipient_key(candidate.address)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    verified = [value for value in unique if value.status == "verified"]
    if verified:
        return verified
    return [value for value in unique if value.status == "inferred"][:inferred_send_max]


def attach_email_discovery(
    candidate: Candidate, discovery: EmailDiscoveryResult
) -> Candidate:
    primary = discovery.selected
    avatar = candidate.avatar
    if candidate.photo is None and primary is not None and primary.status == "verified":
        digest = hashlib.md5(  # noqa: S324 - Gravatar's non-security identifier
            primary.address.strip().casefold().encode(), usedforsecurity=False
        ).hexdigest()
        avatar = CandidateAvatar(
            kind="gravatar",
            url=f"https://www.gravatar.com/avatar/{digest}?d=404&s=160",
            initials=_initials(discovery.resolved_name),
            source_url=primary.source_url,
        )
    name_detail = candidate.name_detail
    if discovery.name_source_url:
        name_detail = AttributedFact(
            value=discovery.resolved_name,
            source_url=discovery.name_source_url,
            confidence="high",
            why="highest-confidence public display name used before email inference",
            source_kind=(name_detail.source_kind if name_detail else "public_profile"),
            subject_name=discovery.resolved_name,
        )
    return candidate.model_copy(
        update={
            "name": discovery.resolved_name,
            "initials": _initials(discovery.resolved_name),
            "name_detail": name_detail,
            "surname_resolved": discovery.surname_resolved,
            "name_variants": list(
                resolve_public_name(
                    discovery.resolved_name,
                    [
                        PublicNameEvidence(
                            discovery.resolved_name,
                            discovery.name_source_url or candidate.source_link,
                        )
                    ],
                ).variants
            ),
            "email": primary,
            "emails": discovery.candidates,
            "avatar": avatar,
        }
    )


def _name_parts(name: str) -> tuple[str, str]:
    parts = parse_name_parts(name)
    return parts.first, parts.last


def _rank_patterns(name: str) -> list[_Pattern]:
    return [
        _Pattern(value.pattern, value.local, index)
        for index, value in enumerate(generate_email_permutations(name), start=1)
    ]


def _observed_pattern(dossier: CandidateDossier, domain: str) -> str | None:
    values = [
        pattern
        for fact in dossier.company.public_emails
        if (pattern := _pattern_from_fact(fact, domain)) is not None
    ]
    return Counter(values).most_common(1)[0][0] if values else None


def _pattern_from_fact(fact: AttributedFact, domain: str) -> str | None:
    local, separator, observed_domain = normalize_address(fact.value).partition("@")
    if (
        not separator
        or observed_domain != domain
        or local in ROLE_LOCALS
        or fact.company_level
    ):
        return None
    if fact.subject_name:
        derived = derive_email_pattern(fact.value, fact.subject_name)
        if derived:
            return derived
    if "." in local:
        return "first.last"
    if "_" in local:
        return "first_last"
    if "-" in local:
        return "first-last"
    return None


def _observed_harvest(
    candidate: Candidate, dossier: CandidateDossier
) -> EmailHarvestResult:
    addresses = list(dossier.person.public_emails)
    for email in (*candidate.emails, *((candidate.email,) if candidate.email else ())):
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
    addresses.extend(fact for fact in dossier.company.public_emails if fact.company_level)
    names = [candidate.name_detail] if candidate.name_detail else []
    return EmailHarvestResult(
        addresses=_dedupe_facts(addresses), names=[value for value in names if value]
    )


def _dedupe_facts(values: list[AttributedFact]) -> list[AttributedFact]:
    result: list[AttributedFact] = []
    seen: set[str] = set()
    for fact in values:
        key = recipient_key(fact.value)
        if key not in seen:
            seen.add(key)
            result.append(fact)
    return result


def _confidence_score(value: str) -> int:
    return {"high": 90, "medium": 72, "low": 55}.get(value, 60)


def _provider_pattern(value: str) -> str | None:
    normalized = value.casefold().replace("{", "").replace("}", "")
    normalized = normalized.replace(" ", "")
    aliases = {
        "first.last": "first.last",
        "firstlast": "firstlast",
        "first_last": "first_last",
        "first-last": "first-last",
        "first": "first",
        "last": "last",
        "f.last": "f.last",
        "flast": "flast",
        "last.first": "last.first",
        "lastf": "lastf",
    }
    return aliases.get(normalized)


def _syntax_valid(address: str, *, allow_role: bool = False) -> bool:
    try:
        validated = validate_email(address, check_deliverability=False)
    except EmailNotValidError:
        return False
    local = validated.local_part.casefold()
    return (
        allow_role or local not in ROLE_LOCALS
    ) and validated.domain.casefold() not in DISPOSABLE_DOMAINS


def _initials(name: str) -> str:
    parts = [value for value in name.replace(",", " ").split() if value]
    return "".join(value[0].upper() for value in parts[:2]) or "?"
