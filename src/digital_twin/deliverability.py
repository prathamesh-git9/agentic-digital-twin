from __future__ import annotations

import asyncio
from typing import Protocol

import dns.asyncresolver
from pydantic import BaseModel


class TXTResolver(Protocol):
    async def records(self, name: str) -> list[str]: ...


class DnspythonTXTResolver:
    def __init__(self, *, timeout: float = 3.0) -> None:
        self.timeout = timeout

    async def records(self, name: str) -> list[str]:
        resolver = dns.asyncresolver.Resolver()
        resolver.lifetime = self.timeout
        answer = await asyncio.wait_for(
            resolver.resolve(name, "TXT"), timeout=self.timeout
        )
        values = []
        for record in answer:
            strings = getattr(record, "strings", ())
            if strings:
                values.append(b"".join(strings).decode("utf-8", errors="replace"))
            else:
                values.append(str(record).strip('"'))
        return values


class DeliverabilityReport(BaseModel):
    domain: str
    spf: bool
    dkim: bool
    dmarc: bool
    dkim_selector: str | None = None
    ready: bool
    reasons: list[str]


class DeliverabilityPreflight:
    def __init__(self, resolver: TXTResolver, *, selectors: tuple[str, ...]) -> None:
        self.resolver = resolver
        self.selectors = selectors

    async def check(self, from_email: str) -> DeliverabilityReport:
        _, separator, domain = from_email.strip().casefold().rpartition("@")
        if not separator or not domain:
            return DeliverabilityReport(
                domain=domain,
                spf=False,
                dkim=False,
                dmarc=False,
                ready=False,
                reasons=["The configured sender address is invalid."],
            )
        spf_records = await self._safe_records(domain)
        dmarc_records = await self._safe_records(f"_dmarc.{domain}")
        spf = any(record.casefold().startswith("v=spf1") for record in spf_records)
        dmarc = any(record.casefold().startswith("v=dmarc1") for record in dmarc_records)
        selector_match: str | None = None
        for selector in self.selectors:
            records = await self._safe_records(f"{selector}._domainkey.{domain}")
            if any("p=" in record.casefold() for record in records):
                selector_match = selector
                break
        dkim = selector_match is not None
        reasons = []
        if not spf:
            reasons.append("SPF record not observed for the sender domain.")
        if not dkim:
            reasons.append("DKIM public key not observed for configured selectors.")
        if not dmarc:
            reasons.append("DMARC policy not observed for the sender domain.")
        return DeliverabilityReport(
            domain=domain,
            spf=spf,
            dkim=dkim,
            dmarc=dmarc,
            dkim_selector=selector_match,
            ready=spf and dkim and dmarc,
            reasons=reasons,
        )

    async def _safe_records(self, name: str) -> list[str]:
        try:
            return await self.resolver.records(name)
        except Exception:  # noqa: BLE001 - missing DNS is a refusal, not an app failure
            return []
