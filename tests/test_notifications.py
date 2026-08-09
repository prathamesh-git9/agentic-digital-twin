from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

import httpx

from agentic_digital_twin.config import Settings
from agentic_digital_twin.engagement import NotificationService

ROOT = Path(__file__).parents[1]


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": "sqlite://",
        "profile_path": ROOT / "data" / "profile.yaml",
        "hash_secret": "notification-test-secret",
        "pushover_enabled": True,
        "pushover_user": "test-user-id",
        "pushover_token": "test-app-token",
        "notification_rate_limit_per_minute": 2,
        "autosend": False,
    }
    values.update(overrides)
    return Settings(**values)


async def test_pushover_payload_is_short_mockable_and_contains_known_context() -> None:
    captured: dict[str, list[str]] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"status": 1}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        delivered = await NotificationService(settings(), client=client).notify(
            "outreach_email_sent",
            {
                "visitor_name": "Sarah Chen",
                "recipient": "sarah@acme.io",
                "role": "Backend Engineer",
                "decision": "small candidate set",
                "question": "Does he know Python?",
            },
        )
    finally:
        await client.aclose()

    assert delivered is True
    assert captured["token"] == ["test-app-token"]
    assert captured["user"] == ["test-user-id"]
    message = captured["message"][0]
    assert "Sarah Chen" in message
    assert "Does he know Python?" in message
    assert len(message) <= 1_024


async def test_notification_failures_are_nonfatal_and_burst_is_rate_limited() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = NotificationService(
        settings(notification_rate_limit_per_minute=1), client=client
    )
    try:
        first = await notifier.notify("error", {"error": "fake failure"})
        second = await notifier.notify("error", {"error": "burst failure"})
    finally:
        await client.aclose()

    assert first is False
    assert second is False
    assert calls == 1
