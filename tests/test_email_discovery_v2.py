from __future__ import annotations

from collections.abc import Callable

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_digital_twin.email_discovery import (
    EmailDiscoveryService,
    generate_email_permutations,
    select_send_targets,
)
from agentic_digital_twin.email_harvesting import EmailHarvestResult, PublicEmailHarvester
from agentic_digital_twin.research import (
    Candidate,
    CandidateDossier,
    CandidateEmail,
    CompanyDossier,
    PersonDossier,
    ProfileLink,
    RawSearchResult,
    ResearchEngine,
)
from agentic_digital_twin.research_sources import (
    AttributedFact,
    PublicDocument,
    extract_public_document,
)


class MXRecords:
    def __init__(self, values: list[str]) -> None:
        self.values = values

    async def records(self, domain: str) -> list[str]:
        return self.values


class Results:
    def __init__(self, title: str) -> None:
        self.title = title

    async def search(
        self, name: str, company: str | None, limit: int
    ) -> list[RawSearchResult]:
        return [
            RawSearchResult(
                self.title,
                "https://profiles.example/michael-stone",
                "Platform engineering at Acme",
            )
        ]


class GitHubNameHarvester:
    async def harvest(self, candidate, dossier) -> EmailHarvestResult:  # noqa: ANN001
        return EmailHarvestResult(
            names=[
                AttributedFact(
                    value="Michael Stone",
                    source_url="https://github.com/mstone",
                    confidence="high",
                    why="public GitHub display name",
                    source_kind="github_profile",
                    subject_name="Michael Stone",
                )
            ]
        )


class PatternVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def domain_pattern(self, domain: str) -> tuple[str, str]:
        self.calls.append(("domain", domain))
        return "flast", "https://verifier.example/domain"

    async def verify(self, address: str) -> tuple[bool, str]:
        self.calls.append(("verify", address))
        return address == "schen@acme.io", "https://verifier.example/email"


def fact(
    value: str,
    *,
    source_url: str = "https://acme.io/team",
    subject_name: str | None = None,
    company_level: bool = False,
) -> AttributedFact:
    return AttributedFact(
        value=value,
        source_url=source_url,
        confidence="high",
        why="published",
        subject_name=subject_name,
        company_level=company_level,
    )


def candidate(
    name: str = "Sarah Chen", *, submitted_name: str | None = None
) -> Candidate:
    return Candidate(
        id="candidate-1",
        name=name,
        submitted_name=submitted_name,
        surname_resolved=len(name.replace(",", " ").split()) > 1,
        headline="Platform Engineer",
        company="Acme",
        initials="SC",
        source_link="https://profiles.example/sarah",
        source_label="profiles.example",
        confidence=92,
        why=["public result"],
    )


def dossier(
    *,
    person_emails: list[AttributedFact] | None = None,
    company_emails: list[AttributedFact] | None = None,
    documents: list[PublicDocument] | None = None,
) -> CandidateDossier:
    return CandidateDossier(
        candidate_id="candidate-1",
        person=PersonDossier(
            candidate_id="candidate-1", public_emails=person_emails or []
        ),
        company=CompanyDossier(
            name="Acme",
            domain=fact("acme.io", source_url="https://acme.io"),
            public_emails=company_emails or [],
        ),
        documents=documents or [],
    )


async def test_first_name_nickname_resolves_to_public_full_name_and_source() -> None:
    outcome = await ResearchEngine(Results("Michael Stone - Platform Engineer")).find(
        "Mike", company="Acme"
    )

    resolved = outcome.candidates[0]
    assert resolved.name == "Michael Stone"
    assert resolved.submitted_name == "Mike"
    assert resolved.surname_resolved is True
    assert resolved.name_detail is not None
    assert resolved.name_detail.source_url == "https://profiles.example/michael-stone"
    assert "Mike Stone" in resolved.name_variants


async def test_single_token_name_never_fabricates_a_surname() -> None:
    result = await EmailDiscoveryService(mx_resolver=MXRecords(["mx.acme.io"])).discover(
        candidate("Sasha", submitted_name="Sasha"), dossier()
    )

    assert result.surname_resolved is False
    assert result.status == "inferred"
    assert {value.address for value in result.candidates} >= {
        "sasha@acme.io",
        "alexander@acme.io",
    }
    assert not any(value.pattern == "first.last" for value in result.candidates)


async def test_github_profile_name_replaces_first_name_before_inference() -> None:
    result = await EmailDiscoveryService(
        mx_resolver=MXRecords(["mx.acme.io"]), harvester=GitHubNameHarvester()
    ).discover(candidate("Mike", submitted_name="Mike"), dossier())

    assert result.resolved_name == "Michael Stone"
    assert result.surname_resolved is True
    assert result.name_source_url == "https://github.com/mstone"
    assert result.name_source_kind == "github_profile"
    assert result.selected is not None
    assert result.selected.address == "michael.stone@acme.io"


def test_diacritics_middle_initials_compound_surnames_and_name_order() -> None:
    addresses = {
        value.local: value.pattern
        for value in generate_email_permutations("José M. García-Márquez")
    }
    reversed_addresses = {
        value.local for value in generate_email_permutations("Chen, Sarah M.")
    }

    assert addresses["jose.garciamarquez"] == "first.last"
    assert addresses["jose.m.garciamarquez"] == "first.m.last"
    assert "jmgarciamarquez" in addresses
    assert "sarah.chen" in reversed_addresses
    assert "sarah.m.chen" in reversed_addresses


def test_full_required_permutation_set_is_generated() -> None:
    patterns = {
        value.pattern: value.local for value in generate_email_permutations("Sarah Chen")
    }

    assert (
        patterns.items()
        >= {
            "first.last": "sarah.chen",
            "firstlast": "sarahchen",
            "first_last": "sarah_chen",
            "flast": "schen",
            "f.last": "s.chen",
            "first": "sarah",
            "last": "chen",
            "lastf": "chens",
            "last.first": "chen.sarah",
            "first-last": "sarah-chen",
            "initials": "sc",
        }.items()
    )


def test_mailto_links_are_preserved_as_attributed_document_addresses() -> None:
    document = extract_public_document(
        '<html><title>Sarah Chen</title><a href="mailto:Sarah@Acme.io?subject=Hi">'
        "Contact Sarah</a></html>",
        "https://acme.io/team/sarah",
    )

    assert document.email_addresses == ["Sarah@Acme.io"]


async def test_github_profile_commits_and_mailto_are_harvested_without_noreply() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/sarah":
            return httpx.Response(
                200,
                json={
                    "name": "Sarah Chen",
                    "email": "Sarah@Acme.io",
                    "html_url": "https://github.com/sarah",
                },
                request=request,
            )
        if request.url.path == "/users/sarah/repos":
            return httpx.Response(
                200,
                json=[{"full_name": "sarah/platform"}],
                request=request,
            )
        if request.url.path == "/repos/sarah/platform/commits":
            return httpx.Response(
                200,
                json=[
                    {
                        "html_url": "https://github.com/sarah/platform/commit/abc",
                        "commit": {
                            "author": {
                                "name": "Sarah Chen",
                                "email": "schen@acme.io",
                            },
                            "committer": {
                                "name": "Sarah Chen",
                                "email": "123+sarah@users.noreply.github.com",
                            },
                        },
                    }
                ],
                request=request,
            )
        if request.url.host == "acme.io":
            return httpx.Response(404, request=request)
        if request.url.host == "rdap.org":
            return httpx.Response(200, json={}, request=request)
        raise AssertionError(f"unexpected request {request.url}")

    github_candidate = candidate()
    github_candidate.profiles = [
        ProfileLink(
            kind="github",
            url="https://github.com/sarah",
            handle="sarah",
            source_url="https://search.example/sarah",
            verified=True,
        )
    ]
    public_document = PublicDocument(
        url="https://sarah.example/contact",
        title="Sarah Chen contact",
        text="Sarah Chen",
        email_addresses=["hello@sarah.example"],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await PublicEmailHarvester(client=client).harvest(
            github_candidate, dossier(documents=[public_document])
        )

    addresses = {value.value: value for value in result.addresses}
    assert set(addresses) == {
        "hello@sarah.example",
        "sarah@acme.io",
        "schen@acme.io",
    }
    assert addresses["schen@acme.io"].source_url.endswith("/commit/abc")
    assert all("users.noreply.github.com" not in value for value in addresses)
    assert any(value.value == "Sarah Chen" for value in result.names)


async def test_security_txt_and_public_rdap_contacts_are_company_level() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "acme.io":
            return httpx.Response(
                200,
                text="Contact: mailto:security@acme.io\nExpires: 2027-01-01",
                request=request,
            )
        if request.url.host == "rdap.org":
            return httpx.Response(
                200,
                json={"entities": [{"vcardArray": ["vcard", ["admin@acme.io"]]}]},
                request=request,
            )
        raise AssertionError(f"unexpected request {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await PublicEmailHarvester(client=client).harvest(candidate(), dossier())

    addresses = {value.value: value for value in result.addresses}
    assert set(addresses) == {"security@acme.io", "admin@acme.io"}
    assert all(value.company_level for value in addresses.values())
    assert addresses["security@acme.io"].source_kind == "security_txt"
    assert addresses["admin@acme.io"].source_kind == "whois_rdap"


async def test_observed_employee_pattern_wins_and_is_cached_per_domain() -> None:
    service = EmailDiscoveryService(mx_resolver=MXRecords(["mx.acme.io"]))
    observed = dossier(
        company_emails=[fact("j.smith@acme.io", subject_name="John Smith")]
    )

    first = await service.discover(candidate(), observed)
    cached = await service.discover(candidate(), dossier())

    assert first.observed_pattern == "f.last"
    assert first.selected is not None
    assert first.selected.address == "s.chen@acme.io"
    assert first.selected.score == 100
    assert cached.observed_pattern == "f.last"
    assert cached.selected is not None
    assert cached.selected.address == "s.chen@acme.io"


async def test_mx_dead_domain_kills_every_inferred_candidate() -> None:
    result = await EmailDiscoveryService(mx_resolver=MXRecords([])).discover(
        candidate(), dossier()
    )

    assert result.status == "unavailable"
    assert result.candidates == []
    assert "MX" in result.reason


async def test_verifier_prefers_domain_pattern_and_promotes_top_result() -> None:
    verifier = PatternVerifier()
    result = await EmailDiscoveryService(
        mx_resolver=MXRecords(["mx.acme.io"]), verifier=verifier
    ).discover(candidate(), dossier())

    assert verifier.calls[:2] == [
        ("domain", "acme.io"),
        ("verify", "schen@acme.io"),
    ]
    assert result.status == "verified"
    assert result.observed_pattern == "flast"
    assert result.selected is not None
    assert result.selected.address == "schen@acme.io"
    assert result.selected.source_kind == "verification_api"


def test_verified_targets_beat_inferred_cap_and_gmail_dedupe_is_provider_scoped() -> None:
    candidates = [
        CandidateEmail(
            address="first.last@acme.io",
            status="inferred",
            confidence="high",
            source_url="https://acme.io",
            why="observed pattern",
            pattern="first.last",
            score=100,
        ),
        CandidateEmail(
            address="Sarah.Chen+work@gmail.com",
            status="verified",
            confidence="high",
            source_url="https://github.com/sarah",
            why="published",
            score=100,
        ),
        CandidateEmail(
            address="sarahchen@gmail.com",
            status="verified",
            confidence="high",
            source_url="https://speaker.example/sarah",
            why="published twice",
            score=100,
        ),
    ]

    targets = select_send_targets(candidates, inferred_send_max=3)

    assert [value.address for value in targets] == ["Sarah.Chen+work@gmail.com"]


def test_inferred_target_cap_is_hard_and_rank_ordered() -> None:
    candidates = [
        CandidateEmail(
            address=f"candidate{index}@acme.io",
            status="inferred",
            confidence="medium",
            source_url="https://acme.io",
            why="inferred",
            pattern="firstlast",
            score=score,
        )
        for index, score in enumerate((30, 90, 70, 50), start=1)
    ]

    targets = select_send_targets(candidates, inferred_send_max=2)

    assert [value.score for value in targets] == [90, 70]


async def test_recorded_bounce_demotes_the_domain_pattern() -> None:
    service = EmailDiscoveryService(mx_resolver=MXRecords(["mx.acme.io"]))
    observed = dossier(
        company_emails=[fact("john.smith@acme.io", subject_name="John Smith")]
    )
    before = await service.discover(candidate(), observed)
    service.record_bounce("acme.io", "first.last")
    after = await service.discover(candidate(), observed)

    assert before.selected is not None and before.selected.pattern == "first.last"
    assert after.selected is not None and after.selected.pattern != "first.last"
    bounced = next(value for value in after.candidates if value.pattern == "first.last")
    assert "recorded bounce" in bounced.why


def test_owner_bounce_endpoint_suppresses_and_persists_pattern_learning(
    app_factory: Callable[..., FastAPI],
) -> None:
    app = app_factory(owner_password="owner-test-password")
    with TestClient(app) as client:
        response = client.post(
            "/api/owner/outreach/bounces",
            json={
                "address": "Sarah.Chen@Acme.io",
                "pattern": "first.last",
                "reason": "provider hard bounce",
            },
            auth=("owner", "owner-test-password"),
        )

    assert response.status_code == 200
    assert response.json()["pattern_bounce_count"] == 1
    assert app.state.database.is_suppressed("sarah.chen@acme.io")
    assert app.state.database.pattern_bounce_counts("acme.io") == {"first.last": 1}
    bounced = [
        action
        for action in app.state.database.outreach_actions()
        if action.action == "email.bounced"
    ]
    assert len(bounced) == 1
    assert bounced[0].metadata_value["why"] == "provider hard bounce"
