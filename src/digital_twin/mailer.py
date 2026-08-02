from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

from pydantic import BaseModel

from .config import Settings


class MailSender(Protocol):
    async def send(self, *, recipient: str, subject: str, body: str) -> str: ...


class SMTPCheckResult(BaseModel):
    ok: bool
    host: str
    port: int
    starttls: bool
    authenticated_as: str | None = None
    detail: str


class GmailSMTPSender:
    """Gmail STARTTLS transport. Construction does not open a network connection."""

    def __init__(self, settings: Settings, *, timeout: float = 10.0) -> None:
        self.settings = settings
        self.timeout = timeout

    def _validate(self) -> None:
        if not self.settings.smtp_ready:
            raise RuntimeError(
                "Gmail SMTP requires smtp.gmail.com:587, STARTTLS, username, password, "
                "and a From address."
            )

    async def send(self, *, recipient: str, subject: str, body: str) -> str:
        self._validate()
        message = EmailMessage()
        message["From"] = formataddr((self.settings.from_name, self.settings.from_email))
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        await asyncio.to_thread(self._send_sync, message)
        return "smtp.gmail.com:587"

    def _send_sync(self, message: EmailMessage) -> None:
        with smtplib.SMTP(
            self.settings.smtp_host,
            self.settings.smtp_port,
            timeout=self.timeout,
        ) as client:
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
            client.login(self.settings.smtp_username, self.settings.smtp_password)
            client.send_message(message)

    async def self_test(self) -> SMTPCheckResult:
        """Authenticate only. This deliberately never creates or sends a message."""
        try:
            self._validate()
            await asyncio.to_thread(self._self_test_sync)
        except Exception as exc:  # noqa: BLE001 - operator command needs a safe result
            return SMTPCheckResult(
                ok=False,
                host=self.settings.smtp_host or "smtp.gmail.com",
                port=self.settings.smtp_port,
                starttls=self.settings.smtp_starttls,
                detail=f"Connectivity/authentication failed ({type(exc).__name__}).",
            )
        return SMTPCheckResult(
            ok=True,
            host=self.settings.smtp_host,
            port=self.settings.smtp_port,
            starttls=True,
            authenticated_as=None,
            detail=(
                "STARTTLS negotiation and authentication succeeded; no email was sent."
            ),
        )

    def _self_test_sync(self) -> None:
        with smtplib.SMTP(
            self.settings.smtp_host,
            self.settings.smtp_port,
            timeout=self.timeout,
        ) as client:
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
            client.login(self.settings.smtp_username, self.settings.smtp_password)
