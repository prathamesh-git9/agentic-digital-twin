from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from digital_twin.research import RawSearchResult


class TwoCandidateProvider:
    async def search(
        self, name: str, company: str | None, limit: int
    ) -> list[RawSearchResult]:
        return [
            RawSearchResult(
                "Sarah Chen One - Platform Engineer at Acme",
                "https://profiles.example/sarah-one",
                "Sarah Chen works at Acme in Dublin.",
            ),
            RawSearchResult(
                "Sarah Chen Two - Backend Engineer at Acme",
                "https://profiles.example/sarah-two",
                "Sarah Chen works at Acme in Dublin.",
            ),
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


class OwnerNotifications:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def notify(self, kind: str, payload: dict) -> bool:
        self.events.append((kind, payload))
        return True


def test_small_candidate_set_fans_out_without_waiting_for_selection(
    app_factory: Callable[..., FastAPI],
) -> None:
    mailbox = Mailbox()
    notifications = OwnerNotifications()
    app = app_factory(
        search_provider=TwoCandidateProvider(),
        mx_resolver=MX(),
        txt_resolver=TXT(),
        mail_sender=mailbox,
        notifier_service=notifications,
        autosend=True,
        fanout_unselected=True,
        fanout_max=3,
        daily_send_cap=10,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_starttls=True,
        smtp_username="owner@gmail.com",
        smtp_password="offline-test-password",
        from_email="owner@gmail.com",
        dkim_selectors="20230601",
    )
    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        response = client.post(
            f"/api/sessions/{session_id}/identity",
            json={"name": "Sarah Chen", "company": "Acme", "location": "Dublin"},
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and len(mailbox.messages) < 2:
            time.sleep(0.01)
        outreach = client.get(f"/api/sessions/{session_id}/outreach").json()

    assert response.status_code == 202
    assert outreach["decision"]["decision"] == "auto"
    assert len(outreach["decision"]["fanout_candidate_ids"]) == 2
    assert {message["recipient"] for message in mailbox.messages} == {
        "sarah.one@acme.io",
        "sarah.two@acme.io",
    }
    assert all(
        "may have looked at my profile" in message["body"] for message in mailbox.messages
    )
    assert all(
        "you just talked" not in message["body"].casefold()
        for message in mailbox.messages
    )
    assert any(kind == "research_completed" for kind, _ in notifications.events)
    assert sum(kind == "outreach_email_sent" for kind, _ in notifications.events) == 2
