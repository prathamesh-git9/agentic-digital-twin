from __future__ import annotations

from agentic_digital_twin.deliverability import DeliverabilityPreflight
from agentic_digital_twin.email_discovery import EmailDiscoveryService
from agentic_digital_twin.research import (
    Candidate,
    CandidateDossier,
    CompanyDossier,
    PersonDossier,
)
from agentic_digital_twin.research_sources import AttributedFact


class MXRecords:
    def __init__(self, values: list[str]) -> None:
        self.values = values
        self.queries: list[str] = []

    async def records(self, domain: str) -> list[str]:
        self.queries.append(domain)
        return self.values


class TXTRecords:
    def __init__(self, values: dict[str, list[str]]) -> None:
        self.values = values

    async def records(self, name: str) -> list[str]:
        return self.values.get(name, [])


def candidate() -> Candidate:
    return Candidate(
        id="candidate-1",
        name="Sarah Chen",
        headline="Platform Engineer",
        company="Acme",
        initials="SC",
        source_link="https://example.com/sarah",
        source_label="example.com",
        confidence=93,
        why=["name matched"],
    )


def dossier(*, person_email: str | None = None) -> CandidateDossier:
    public_emails = []
    if person_email:
        public_emails.append(
            AttributedFact(
                value=person_email,
                source_url="https://acme.example/team/sarah",
                confidence="high",
                why="published",
            )
        )
    return CandidateDossier(
        candidate_id="candidate-1",
        person=PersonDossier(candidate_id="candidate-1", public_emails=public_emails),
        company=CompanyDossier(
            name="Acme",
            domain=AttributedFact(
                value="acme.io",
                source_url="https://acme.io",
                confidence="high",
                why="official site",
            ),
            public_emails=[
                AttributedFact(
                    value="john.smith@acme.io",
                    source_url="https://acme.io/team/john",
                    confidence="high",
                    why="published pattern observation",
                )
            ],
        ),
    )


async def test_published_email_is_verified_with_its_own_source() -> None:
    result = await EmailDiscoveryService(mx_resolver=MXRecords([])).discover(
        candidate(), dossier(person_email="sarah@acme.io")
    )

    assert result.status == "verified"
    assert result.selected is not None
    assert result.selected.address == "sarah@acme.io"
    assert result.selected.source_url == "https://acme.example/team/sarah"


async def test_observed_pattern_and_mx_produce_an_honestly_inferred_address() -> None:
    resolver = MXRecords(["mx.acme.io"])
    result = await EmailDiscoveryService(mx_resolver=resolver).discover(
        candidate(), dossier()
    )

    assert result.status == "inferred"
    assert result.observed_pattern == "first.last"
    assert result.selected is not None
    assert result.selected.address == "sarah.chen@acme.io"
    assert result.selected.status == "inferred"
    assert result.selected.confidence == "high"
    assert resolver.queries == ["acme.io"]


async def test_missing_mx_refuses_to_offer_an_inferred_mailbox() -> None:
    result = await EmailDiscoveryService(mx_resolver=MXRecords([])).discover(
        candidate(), dossier()
    )

    assert result.status == "unavailable"
    assert result.selected is None
    assert "MX" in result.reason


async def test_sender_dns_preflight_requires_spf_dkim_and_dmarc() -> None:
    resolver = TXTRecords(
        {
            "gmail.com": ["v=spf1 redirect=_spf.google.com"],
            "_dmarc.gmail.com": ["v=DMARC1; p=none"],
            "20230601._domainkey.gmail.com": ["v=DKIM1; p=public-key"],
        }
    )
    report = await DeliverabilityPreflight(
        resolver, selectors=("20230601", "default")
    ).check("owner@gmail.com")

    assert report.ready is True
    assert report.spf and report.dkim and report.dmarc
    assert report.dkim_selector == "20230601"


async def test_sender_dns_preflight_refuses_when_dkim_is_missing() -> None:
    resolver = TXTRecords(
        {
            "gmail.com": ["v=spf1 redirect=_spf.google.com"],
            "_dmarc.gmail.com": ["v=DMARC1; p=none"],
        }
    )
    report = await DeliverabilityPreflight(resolver, selectors=("20230601",)).check(
        "owner@gmail.com"
    )

    assert report.ready is False
    assert report.dkim is False
    assert any("DKIM" in reason for reason in report.reasons)
