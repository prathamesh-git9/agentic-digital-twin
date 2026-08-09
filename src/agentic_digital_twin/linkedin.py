from __future__ import annotations

import asyncio
import hashlib
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel

from .config import Settings
from .models import Database
from .outreach import ApprovalTokenService
from .security import is_public_http_url

LinkedInActionKind = Literal["follow", "connect", "message"]


class LinkedInChallenge(RuntimeError):
    pass


class LinkedInDriver(Protocol):
    async def perform(
        self, *, profile_url: str, action: LinkedInActionKind, message: str | None
    ) -> str: ...


class LinkedInActionResult(BaseModel):
    status: Literal[
        "completed",
        "unavailable",
        "duplicate",
        "capped",
        "killed",
        "challenge",
        "refused",
    ]
    action: LinkedInActionKind
    detail: str
    handoff_required: bool = False


class PlaywrightLinkedInDriver:
    """Visible local-profile automation without stealth, proxies, or credentials."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def perform(
        self, *, profile_url: str, action: LinkedInActionKind, message: str | None
    ) -> str:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                str(self.settings.linkedin_user_data_dir.resolve()),
                headless=False,
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(profile_url, wait_until="domcontentloaded")
                content = (await page.locator("body").inner_text()).casefold()
                if _looks_like_challenge(content):
                    raise LinkedInChallenge("LinkedIn requested account verification")
                if action == "follow":
                    button = page.get_by_role("button", name="Follow", exact=True)
                    if await button.count() == 0:
                        return "Follow action is not available on this profile."
                    await button.first.click()
                    return "Follow completed."
                if action == "connect":
                    button = page.get_by_role("button", name="Connect", exact=True)
                    if await button.count() == 0:
                        return "Connect action is not available on this profile."
                    await button.first.click()
                    if message:
                        add_note = page.get_by_role("button", name="Add a note")
                        if await add_note.count():
                            await add_note.first.click()
                            textarea = page.locator("textarea")
                            if await textarea.count():
                                await textarea.first.fill(message[:300])
                    send = page.get_by_role("button", name=re_compile("Send"))
                    if await send.count():
                        await send.first.click()
                    return "Connection request completed."
                button = page.get_by_role("button", name=re_compile("Message"))
                if await button.count() == 0:
                    return "Message action is not available on this profile."
                await button.first.click()
                editor = page.locator('[contenteditable="true"]')
                if await editor.count() == 0 or not message:
                    return "Message composer is unavailable."
                await editor.last.fill(message)
                send = page.get_by_role("button", name=re_compile("Send"))
                if await send.count():
                    await send.last.click()
                    return "Message completed."
                return "Message send control is unavailable."
            finally:
                await context.close()


def re_compile(value: str):
    import re

    return re.compile(value, re.I)


class LinkedInAutomationService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        driver: LinkedInDriver,
        tokens: ApprovalTokenService,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_source: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.driver = driver
        self.tokens = tokens
        self.sleep = sleep
        self.random = random_source or random.SystemRandom()
        self._lock = asyncio.Lock()

    def approval_token(
        self,
        *,
        candidate_id: str,
        profile_url: str,
        action: LinkedInActionKind,
        message: str | None,
    ) -> str:
        return self.tokens.issue(
            draft_id=f"linkedin:{candidate_id}",
            recipient=profile_url,
            variant_id=action,
            body=message or "",
        )

    async def perform(
        self,
        *,
        session_id: str,
        candidate_id: str,
        profile_url: str,
        action: LinkedInActionKind,
        message: str | None,
        approval_token: str | None,
        automatic: bool,
    ) -> LinkedInActionResult:
        if self.settings.linkedin_kill_switch:
            return LinkedInActionResult(
                status="killed", action=action, detail="LinkedIn kill switch is active."
            )
        if not is_public_http_url(profile_url) or "linkedin.com/in/" not in profile_url:
            return LinkedInActionResult(
                status="refused",
                action=action,
                detail="A public LinkedIn profile is required.",
            )
        if automatic and not self.settings.linkedin_auto:
            return LinkedInActionResult(
                status="refused",
                action=action,
                detail="TWIN_LINKEDIN_AUTO is off.",
            )
        if not automatic and not (
            approval_token
            and self.tokens.verify(
                approval_token,
                draft_id=f"linkedin:{candidate_id}",
                recipient=profile_url,
                variant_id=action,
                body=message or "",
            )
        ):
            return LinkedInActionResult(
                status="refused",
                action=action,
                detail="Per-action confirmation is required.",
            )
        async with self._lock:
            existing = self.database.outreach_actions_for(
                candidate_id=candidate_id, action_prefix=f"linkedin.{action}"
            )
            if any(row.action == f"linkedin.{action}.completed" for row in existing):
                return LinkedInActionResult(
                    status="duplicate",
                    action=action,
                    detail="This action has already completed once for this person.",
                )
            start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            completed_today = [
                row
                for row in self.database.outreach_actions_for(
                    action_prefix="linkedin.", since=start
                )
                if row.action.endswith(".completed")
            ]
            if len(completed_today) >= self.settings.linkedin_daily_cap:
                return LinkedInActionResult(
                    status="capped",
                    action=action,
                    detail="Conservative LinkedIn daily cap reached.",
                )
            delay = self.random.uniform(
                self.settings.linkedin_delay_min_seconds,
                max(
                    self.settings.linkedin_delay_min_seconds,
                    self.settings.linkedin_delay_max_seconds,
                ),
            )
            await self.sleep(delay)
            try:
                detail = await self.driver.perform(
                    profile_url=profile_url, action=action, message=message
                )
            except LinkedInChallenge:
                self._record(
                    session_id,
                    candidate_id,
                    profile_url,
                    action=f"linkedin.{action}.challenge",
                    message=message,
                )
                return LinkedInActionResult(
                    status="challenge",
                    action=action,
                    detail=(
                        "LinkedIn presented a challenge; automation stopped for handoff."
                    ),
                    handoff_required=True,
                )
            if "not available" in detail.casefold() or "unavailable" in detail.casefold():
                return LinkedInActionResult(
                    status="unavailable", action=action, detail=detail
                )
            send_key = hashlib.sha256(
                f"linkedin|{candidate_id}|{action}".encode()
            ).hexdigest()
            _, created = self.database.record_outreach_action(
                session_id=session_id,
                draft_id="linkedin",
                candidate_id=candidate_id,
                recipient=profile_url,
                body_hash=hashlib.sha256((message or "").encode()).hexdigest(),
                action=f"linkedin.{action}.completed",
                approver="owner:auto" if automatic else "owner",
                transport="playwright",
                send_key=send_key,
                metadata={"human_delay_seconds": round(delay, 3)},
            )
            if not created:
                return LinkedInActionResult(
                    status="duplicate",
                    action=action,
                    detail="Once-only guard won the race.",
                )
            return LinkedInActionResult(status="completed", action=action, detail=detail)

    def _record(
        self,
        session_id: str,
        candidate_id: str,
        profile_url: str,
        *,
        action: str,
        message: str | None,
    ) -> None:
        self.database.record_outreach_action(
            session_id=session_id,
            draft_id="linkedin",
            candidate_id=candidate_id,
            recipient=profile_url,
            body_hash=hashlib.sha256((message or "").encode()).hexdigest(),
            action=action,
            approver="owner",
            transport="playwright",
            metadata={"handoff_required": True},
        )


def _looks_like_challenge(content: str) -> bool:
    return any(
        marker in content
        for marker in (
            "security verification",
            "verify your identity",
            "unusual activity",
            "captcha",
            "checkpoint/challenge",
        )
    )
