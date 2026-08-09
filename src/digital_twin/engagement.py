from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel

from .config import Settings
from .models import Database, ProofPack, Visit
from .profile import ProfileCorpus, tokens
from .research import CompanyDossier
from .roles import RoleMatch
from .security import SlidingWindowLimiter


class FitSignal(BaseModel):
    topic: str
    profile_evidence: str
    profile_source: str
    company_evidence: str
    company_source_url: str


class CompanyFitResult(BaseModel):
    score: int
    signals: list[FitSignal]
    summary: str
    caveat: str


class Notifier(Protocol):
    async def notify(self, kind: str, payload: dict[str, Any]) -> bool: ...


class NotificationService:
    pushover_endpoint = "https://api.pushover.net/1/messages.json"

    def __init__(
        self,
        settings: Settings,
        *,
        timeout: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.timeout = timeout
        self.client = client
        self.limiter = SlidingWindowLimiter(settings.notification_rate_limit_per_minute)

    async def notify(self, kind: str, payload: dict[str, Any]) -> bool:
        configured = bool(
            self.settings.handoff_webhook_url
            or (self.settings.telegram_bot_token and self.settings.telegram_chat_id)
            or (
                self.settings.pushover_enabled
                and self.settings.pushover_user
                and self.settings.pushover_token
            )
        )
        if not configured:
            return False
        if not self.limiter.allow("owner-notifications"):
            return False
        delivered = False
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient()
        try:
            if self.settings.handoff_webhook_url:
                try:
                    response = await client.post(
                        self.settings.handoff_webhook_url,
                        json={"type": kind, **payload},
                        timeout=self.timeout,
                    )
                    response.raise_for_status()
                    delivered = True
                except Exception:  # noqa: BLE001 - notifications never affect the app
                    delivered = bool(delivered)
            if self.settings.telegram_bot_token and self.settings.telegram_chat_id:
                try:
                    response = await client.post(
                        "https://api.telegram.org/bot"
                        f"{self.settings.telegram_bot_token}/sendMessage",
                        json={
                            "chat_id": self.settings.telegram_chat_id,
                            "text": _notification_text(kind, payload),
                        },
                        timeout=self.timeout,
                    )
                    response.raise_for_status()
                    delivered = True
                except Exception:  # noqa: BLE001 - notifications never affect the app
                    delivered = bool(delivered)
            if (
                self.settings.pushover_enabled
                and self.settings.pushover_user
                and self.settings.pushover_token
            ):
                try:
                    response = await client.post(
                        self.pushover_endpoint,
                        data={
                            "token": self.settings.pushover_token,
                            "user": self.settings.pushover_user,
                            "title": (
                                f"Agentic digital twin · {kind.replace('_', ' ')[:40]}"
                            ),
                            "message": _notification_text(kind, payload)[:1_024],
                        },
                        timeout=self.timeout,
                    )
                    response.raise_for_status()
                    delivered = True
                except Exception:  # noqa: BLE001 - notifications never affect the app
                    delivered = bool(delivered)
        finally:
            if owns_client:
                await client.aclose()
        return delivered


class CompanyFitService:
    def __init__(self, corpus: ProfileCorpus) -> None:
        self.corpus = corpus

    def analyze(self, company: CompanyDossier) -> CompanyFitResult:
        signals: list[FitSignal] = []
        for fact in company.tech_stack:
            fact_tokens = tokens(fact.value)
            evidence = next(
                (
                    item
                    for item in self.corpus.evidence
                    if fact_tokens and fact_tokens <= tokens(item.text)
                ),
                None,
            )
            if evidence:
                signals.append(
                    FitSignal(
                        topic=fact.value,
                        profile_evidence=evidence.text,
                        profile_source=evidence.source,
                        company_evidence=fact.why,
                        company_source_url=fact.source_url,
                    )
                )
        score = min(100, len(signals) * 15)
        summary = (
            f"Found {len(signals)} attributed company signals with direct CV evidence."
            if signals
            else "No reliable company-to-profile overlap was observed yet."
        )
        return CompanyFitResult(
            score=score,
            signals=signals,
            summary=summary,
            caveat=(
                "Company research is relevance data only. It does not establish any new "
                "fact about Prathamesh."
            ),
        )


class ProofPackService:
    def __init__(
        self,
        *,
        database: Database,
        corpus: ProfileCorpus,
        ttl_seconds: int,
        public_base_url: str,
    ) -> None:
        self.database = database
        self.corpus = corpus
        self.ttl_seconds = ttl_seconds
        self.public_base_url = public_base_url.rstrip("/")

    def create(
        self,
        *,
        session_id: str,
        role: RoleMatch | None,
        company_fit: CompanyFitResult | None,
    ) -> tuple[ProofPack, str]:
        token = secrets.token_urlsafe(32)
        evidence = [
            {"source": item.source, "text": item.text, "url": item.url}
            for item in self.corpus.evidence
            if item.source.startswith(("CV › Summary", "CV › Project"))
        ][:12]
        payload: dict[str, Any] = {
            "person": self.corpus.data["person"]["name"],
            "evidence": evidence,
            "role": role.model_dump(mode="json") if role else None,
            "company_fit": company_fit.model_dump(mode="json") if company_fit else None,
            "created_at": datetime.now(UTC).isoformat(),
            "disclosure": "Every profile claim comes from the supplied CV evidence.",
        }
        row = self.database.create_proof_pack(
            token=token,
            session_id=session_id,
            payload=payload,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.ttl_seconds),
        )
        return row, f"{self.public_base_url}/api/proof-packs/{token}"


class RecruiterVerificationResult(BaseModel):
    verified: bool
    domain: str | None = None
    reason: str


def verify_corporate_recruiter(
    address: str, *, expected_domain: str | None
) -> RecruiterVerificationResult:
    _, separator, domain = address.strip().casefold().rpartition("@")
    if not separator or not domain:
        return RecruiterVerificationResult(
            verified=False, reason="A valid corporate email address is required."
        )
    if not expected_domain:
        return RecruiterVerificationResult(
            verified=False,
            domain=domain,
            reason="No attributed company domain is available for comparison.",
        )
    expected = expected_domain.casefold().strip(".")
    verified = domain == expected or domain.endswith(f".{expected}")
    return RecruiterVerificationResult(
        verified=verified,
        domain=domain,
        reason=(
            "Address domain matches the confirmed company domain."
            if verified
            else "Address domain does not match the confirmed company domain."
        ),
    )


def crm_row(visit: Visit, database: Database) -> dict[str, Any]:
    actions = database.outreach_actions_for(session_id=visit.id)
    variants = [
        action.metadata_value.get("variant")
        for action in actions
        if action.action == "email.sent"
    ]
    return {
        "session_id": visit.id,
        "stage": visit.crm_stage,
        "visitor_name": visit.visitor_name,
        "visitor_company": visit.visitor_company,
        "intent": visit.visitor_intent,
        "created_at": visit.created_at.isoformat(),
        "last_seen_at": visit.last_seen_at.isoformat(),
        "sent_variants": [variant for variant in variants if variant],
        "actions": [
            {
                "action": action.action,
                "created_at": action.created_at.isoformat(),
                "transport": action.transport,
                "metadata": action.metadata_value,
            }
            for action in reversed(actions)
        ],
    }


def valid_crm_stage(
    value: str,
) -> Literal["visited", "confirmed", "drafted", "sent", "replied"] | None:
    allowed = {"visited", "confirmed", "drafted", "sent", "replied"}
    return value if value in allowed else None  # type: ignore[return-value]


def _notification_text(kind: str, payload: dict[str, Any]) -> str:
    safe = {
        key: value for key, value in payload.items() if key not in {"token", "secret"}
    }
    parts = [kind.replace("_", " ")]
    for key in (
        "visitor_name",
        "candidate_count",
        "recipient",
        "role",
        "decision",
        "question",
        "error",
    ):
        value = safe.get(key)
        if value is not None and value != "":
            parts.append(f"{key.replace('_', ' ')}: {str(value)[:240]}")
    return " · ".join(parts)[:1_024]
