from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_digital_twin.email_harvesting import EmailHarvestResult
from agentic_digital_twin.research import RawSearchResult
from agentic_digital_twin.research_sources import AttributedFact


class OneCandidateProvider:
    async def search(
        self, name: str, company: str | None, limit: int
    ) -> list[RawSearchResult]:
        return [
            RawSearchResult(
                "Sarah Chen - Platform Engineer at Acme",
                "https://profiles.example/sarah-chen",
                "Sarah Chen works at Acme in Dublin.",
            )
        ]

    async def search_query(self, query: str, limit: int) -> list[RawSearchResult]:
        if "official company" in query:
            return [
                RawSearchResult(
                    "Acme official site",
                    "https://acme.io/about",
                    "Acme engineering company",
                )
            ]
        return []


class MX:
    async def records(self, domain: str) -> list[str]:
        return ["mx.acme.io"] if domain == "acme.io" else []


class TXT:
    async def records(self, name: str) -> list[str]:
        values = {
            "gmail.com": ["v=spf1 redirect=_spf.google.com"],
            "_dmarc.gmail.com": ["v=DMARC1; p=none"],
            "20230601._domainkey.gmail.com": ["v=DKIM1; p=key"],
        }
        return values.get(name, [])


class Mailbox:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def send(self, *, recipient: str, subject: str, body: str) -> str:
        self.messages.append({"recipient": recipient, "subject": subject, "body": body})
        return "offline-test-smtp"


class PublishedHarvester:
    async def harvest(self, candidate, dossier) -> EmailHarvestResult:  # noqa: ANN001
        return EmailHarvestResult(
            addresses=[
                AttributedFact(
                    value="sarah@acme.io",
                    source_url="https://github.com/sarah",
                    confidence="high",
                    why="public GitHub profile email",
                    source_kind="github_profile",
                    subject_name="Sarah Chen",
                ),
                AttributedFact(
                    value="schen@acme.io",
                    source_url="https://github.com/acme/repo/commit/abc",
                    confidence="high",
                    why="public commit author email",
                    source_kind="github_commit",
                    subject_name="Sarah Chen",
                ),
            ]
        )


class EmptyHarvester:
    async def harvest(self, candidate, dossier) -> EmailHarvestResult:  # noqa: ANN001
        return EmailHarvestResult()


def _wait_for_sent(app: FastAPI, expected: int) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        sent = [
            action
            for action in app.state.database.outreach_actions()
            if action.action == "email.sent"
        ]
        if len(sent) >= expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"expected {expected} sent audit rows")


def _app(
    app_factory: Callable[..., FastAPI],
    mailbox: Mailbox,
    *,
    harvester,
    inferred_send_max: int = 3,
) -> FastAPI:
    return app_factory(
        search_provider=OneCandidateProvider(),
        email_harvester=harvester,
        mx_resolver=MX(),
        txt_resolver=TXT(),
        mail_sender=mailbox,
        autosend=True,
        fanout_unselected=True,
        fanout_max=3,
        inferred_send_max=inferred_send_max,
        daily_send_cap=10,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_starttls=True,
        smtp_username="owner@gmail.com",
        smtp_password="offline-test-password",
        from_email="owner@gmail.com",
        dkim_selectors="20230601",
    )


def test_every_verified_address_is_sent_and_audited(
    app_factory: Callable[..., FastAPI],
) -> None:
    mailbox = Mailbox()
    app = _app(app_factory, mailbox, harvester=PublishedHarvester())
    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        client.post(
            f"/api/sessions/{session_id}/identity",
            json={"name": "Sarah", "company": "Acme"},
        )
        _wait_for_sent(app, 2)

    assert {message["recipient"] for message in mailbox.messages} == {
        "sarah@acme.io",
        "schen@acme.io",
    }
    sent = [
        action
        for action in app.state.database.outreach_actions()
        if action.action == "email.sent"
    ]
    assert all(action.metadata_value["recipient_status"] == "verified" for action in sent)
    assert {action.metadata_value["source_kind"] for action in sent} == {
        "github_profile",
        "github_commit",
    }


def test_inferred_send_max_is_enforced_in_the_real_autosend_path(
    app_factory: Callable[..., FastAPI],
) -> None:
    mailbox = Mailbox()
    app = _app(
        app_factory,
        mailbox,
        harvester=EmptyHarvester(),
        inferred_send_max=2,
    )
    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        client.post(
            f"/api/sessions/{session_id}/identity",
            json={"name": "Sarah", "company": "Acme"},
        )
        _wait_for_sent(app, 2)
        outreach = client.get(f"/api/sessions/{session_id}/outreach").json()

    assert len(mailbox.messages) == 2
    assert len(outreach["drafts"]) == 2
    assert all(draft["recipient_status"] == "inferred" for draft in outreach["drafts"])
    assert all(draft["recipient_pattern"] for draft in outreach["drafts"])
