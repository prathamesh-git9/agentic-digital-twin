from __future__ import annotations

import random
from pathlib import Path

from agentic_digital_twin.config import Settings
from agentic_digital_twin.linkedin import (
    LinkedInAutomationService,
    LinkedInChallenge,
)
from agentic_digital_twin.models import Database
from agentic_digital_twin.outreach import ApprovalTokenService

ROOT = Path(__file__).parents[1]


class FakeLinkedInPage:
    def __init__(self, *, challenge: bool = False) -> None:
        self.challenge = challenge
        self.actions: list[tuple[str, str, str | None]] = []

    async def perform(self, *, profile_url: str, action: str, message: str | None) -> str:
        if self.challenge:
            raise LinkedInChallenge("fake verification page")
        self.actions.append((profile_url, action, message))
        return f"{action.title()} completed."


async def no_delay(seconds: float) -> None:
    return None


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": "sqlite://",
        "profile_path": ROOT / "data" / "profile.yaml",
        "hash_secret": "linkedin-test-secret",
        "linkedin_auto": False,
        "linkedin_kill_switch": False,
        "linkedin_daily_cap": 1,
        "linkedin_delay_min_seconds": 0,
        "linkedin_delay_max_seconds": 0,
        "pushover_enabled": False,
        "autosend": False,
    }
    values.update(overrides)
    return Settings(**values)


def service(
    configuration: Settings, database: Database, driver: FakeLinkedInPage
) -> LinkedInAutomationService:
    return LinkedInAutomationService(
        settings=configuration,
        database=database,
        driver=driver,
        tokens=ApprovalTokenService(configuration.hash_secret),
        sleep=no_delay,
        random_source=random.Random(7),  # noqa: S311 - deterministic timing test
    )


async def test_linkedin_action_requires_confirmation_and_runs_once_per_person() -> None:
    database = Database("sqlite://")
    database.create_schema()
    visit = database.create_visit("ip")
    driver = FakeLinkedInPage()
    automation = service(settings(), database, driver)
    url = "https://www.linkedin.com/in/sarah-chen"
    token = automation.approval_token(
        candidate_id="candidate-1",
        profile_url=url,
        action="follow",
        message=None,
    )

    first = await automation.perform(
        session_id=visit.id,
        candidate_id="candidate-1",
        profile_url=url,
        action="follow",
        message=None,
        approval_token=token,
        automatic=False,
    )
    duplicate = await automation.perform(
        session_id=visit.id,
        candidate_id="candidate-1",
        profile_url=url,
        action="follow",
        message=None,
        approval_token=token,
        automatic=False,
    )

    assert first.status == "completed"
    assert duplicate.status == "duplicate"
    assert len(driver.actions) == 1


async def test_linkedin_daily_cap_blocks_a_second_person() -> None:
    database = Database("sqlite://")
    database.create_schema()
    visit = database.create_visit("ip")
    driver = FakeLinkedInPage()
    automation = service(settings(linkedin_daily_cap=1), database, driver)
    for candidate_id, handle in (
        ("candidate-1", "sarah-chen"),
        ("candidate-2", "sam-lee"),
    ):
        url = f"https://www.linkedin.com/in/{handle}"
        token = automation.approval_token(
            candidate_id=candidate_id,
            profile_url=url,
            action="message",
            message="Hello",
        )
        result = await automation.perform(
            session_id=visit.id,
            candidate_id=candidate_id,
            profile_url=url,
            action="message",
            message="Hello",
            approval_token=token,
            automatic=False,
        )
        if candidate_id == "candidate-1":
            assert result.status == "completed"
        else:
            assert result.status == "capped"
    assert len(driver.actions) == 1


async def test_linkedin_challenge_stops_and_requests_human_handoff() -> None:
    database = Database("sqlite://")
    database.create_schema()
    visit = database.create_visit("ip")
    driver = FakeLinkedInPage(challenge=True)
    automation = service(settings(linkedin_daily_cap=5), database, driver)
    url = "https://www.linkedin.com/in/sarah-chen"
    token = automation.approval_token(
        candidate_id="candidate-1",
        profile_url=url,
        action="connect",
        message="Hi Sarah",
    )

    result = await automation.perform(
        session_id=visit.id,
        candidate_id="candidate-1",
        profile_url=url,
        action="connect",
        message="Hi Sarah",
        approval_token=token,
        automatic=False,
    )

    assert result.status == "challenge"
    assert result.handoff_required is True
    assert database.outreach_actions_for(
        candidate_id="candidate-1", action_prefix="linkedin.connect.challenge"
    )


async def test_linkedin_kill_switch_prevents_driver_use() -> None:
    database = Database("sqlite://")
    database.create_schema()
    visit = database.create_visit("ip")
    driver = FakeLinkedInPage()
    automation = service(settings(linkedin_kill_switch=True), database, driver)

    result = await automation.perform(
        session_id=visit.id,
        candidate_id="candidate-1",
        profile_url="https://www.linkedin.com/in/sarah-chen",
        action="follow",
        message=None,
        approval_token=None,
        automatic=False,
    )

    assert result.status == "killed"
    assert driver.actions == []
