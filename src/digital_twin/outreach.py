from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel

from .config import Settings
from .deliverability import DeliverabilityPreflight, DeliverabilityReport
from .email_utils import recipient_key
from .mailer import MailSender
from .models import Database, OutreachDraft
from .profile import ProfileCorpus
from .research import Candidate, CandidateEmail
from .roles import RoleMatch

TemplateKind = Literal["single_match", "selected", "fanout", "follow_up"]


class SendDecision(BaseModel):
    decision: Literal["auto", "review"]
    reason: str
    candidate_ids: list[str]
    fanout_candidate_ids: list[str] = []
    confidence_threshold: int


class OutreachVariant(BaseModel):
    id: str
    tone: Literal["warm", "direct", "technical", "fanout", "follow_up"]
    subject: str
    body: str
    template: TemplateKind


class SendResult(BaseModel):
    status: Literal["sent", "compose", "duplicate", "suppressed", "refused", "capped"]
    transport: str
    reason: str
    mailto_url: str | None = None
    preflight: DeliverabilityReport | None = None


def decide_send(
    candidates: list[Candidate],
    *,
    threshold: int = 85,
    fanout_unselected: bool = False,
    fanout_max: int = 3,
) -> SendDecision:
    if fanout_unselected and 0 < len(candidates) <= fanout_max:
        candidate_ids = [candidate.id for candidate in candidates]
        exact_single = len(candidates) == 1 and candidates[0].confidence >= threshold
        if exact_single:
            reason = (
                "One exact research candidate is within TWIN_FANOUT_MAX; the owner "
                "policy authorizes confident unattended outreach."
            )
        else:
            reason = (
                f"{len(candidates)} research candidates are within TWIN_FANOUT_MAX; "
                "the owner policy authorizes uncertainty-safe fanout to all."
            )
        return SendDecision(
            decision="auto",
            reason=reason,
            candidate_ids=candidate_ids if exact_single else [],
            fanout_candidate_ids=[] if exact_single else candidate_ids,
            confidence_threshold=threshold,
        )
    reason = (
        f"{len(candidates)} candidates exceed TWIN_FANOUT_MAX; identity review "
        "is required."
        if len(candidates) > fanout_max
        else "Unselected fanout is disabled, so identity review is required."
    )
    return SendDecision(
        decision="review",
        reason=reason,
        candidate_ids=[],
        fanout_candidate_ids=[],
        confidence_threshold=threshold,
    )


class ApprovalTokenService:
    def __init__(self, secret: str, *, ttl_seconds: int = 900) -> None:
        self.secret = secret.encode()
        self.ttl_seconds = ttl_seconds

    def issue(self, *, draft_id: str, recipient: str, variant_id: str, body: str) -> str:
        payload = {
            "purpose": "outreach-send",
            "draft_id": draft_id,
            "recipient": recipient.casefold(),
            "variant_id": variant_id,
            "body_hash": body_hash(body),
            "expires": int(time.time()) + self.ttl_seconds,
        }
        return self._encode(payload)

    def verify(
        self,
        token: str,
        *,
        draft_id: str,
        recipient: str,
        variant_id: str,
        body: str,
    ) -> bool:
        payload = self._decode(token)
        expected = {
            "purpose": "outreach-send",
            "draft_id": draft_id,
            "recipient": recipient.casefold(),
            "variant_id": variant_id,
            "body_hash": body_hash(body),
        }
        return bool(
            payload
            and payload.get("expires", 0) >= int(time.time())
            and all(payload.get(key) == value for key, value in expected.items())
        )

    def issue_opt_out(self, address: str) -> str:
        return self._encode(
            {
                "purpose": "outreach-opt-out",
                "recipient": address.casefold(),
                "expires": int(time.time()) + 31_536_000,
            }
        )

    def verify_opt_out(self, token: str) -> str | None:
        payload = self._decode(token)
        if (
            not payload
            or payload.get("purpose") != "outreach-opt-out"
            or payload.get("expires", 0) < int(time.time())
        ):
            return None
        recipient = payload.get("recipient")
        return recipient if isinstance(recipient, str) else None

    def _encode(self, payload: dict[str, object]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
        signature = hmac.new(self.secret, encoded, hashlib.sha256).digest()
        signature_text = base64.urlsafe_b64encode(signature).decode().rstrip("=")
        return f"{encoded.decode()}.{signature_text}"

    def _decode(self, token: str) -> dict[str, object] | None:
        try:
            encoded, supplied_signature = token.split(".", 1)
            expected = hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest()
            supplied = _b64decode(supplied_signature)
            if not hmac.compare_digest(expected, supplied):
                return None
            value = json.loads(_b64decode(encoded))
            return value if isinstance(value, dict) else None
        except (ValueError, TypeError, json.JSONDecodeError):
            return None


class OutreachComposer:
    def __init__(
        self,
        corpus: ProfileCorpus,
        *,
        public_base_url: str,
        token_service: ApprovalTokenService,
    ) -> None:
        self.corpus = corpus
        self.public_base_url = public_base_url.rstrip("/")
        self.token_service = token_service

    def variants(
        self,
        candidate: Candidate,
        email: CandidateEmail,
        *,
        role: RoleMatch | None,
        template: TemplateKind,
    ) -> list[OutreachVariant]:
        first_name = candidate.name.split()[0]
        opt_out = self._opt_out_line(email.address)
        referral = self._referral(role)
        proof = (
            "TL;DR: I’m a backend engineer with 3.5+ years across Java, Spring Boot, "
            "Python and production support, now building reliable agent systems."
        )
        if template == "fanout":
            body = (
                f"Hi {first_name} — I’m reaching out because you may have looked at my "
                "profile. If that wasn’t you, feel free to ignore this.\n\n"
                f"{proof}\n\n{referral}"
                "Happy to send a compact proof pack if useful.\n\n"
                f"Best,\nPrathamesh\n\n{opt_out}"
            )
            return [
                OutreachVariant(
                    id="fanout",
                    tone="fanout",
                    subject=self._subject(role, "Quick backend/AI intro"),
                    body=body,
                    template="fanout",
                )
            ]
        if template == "follow_up":
            return [
                OutreachVariant(
                    id="follow-up",
                    tone="follow_up",
                    subject=self._subject(role, "Quick follow-up"),
                    body=(
                        f"Hi {first_name} — one quick follow-up in case this got "
                        "buried.\n\n"
                        f"{proof}\n\n{referral}"
                        "No pressure — happy to leave it here.\n\n"
                        f"Best,\nPrathamesh\n\n{opt_out}"
                    ),
                    template="follow_up",
                )
            ]
        opening = (
            "You just talked to my digital twin — thanks for taking a look."
            if template == "single_match"
            else "Thanks for taking a look at my digital twin."
        )
        common_end = (
            f"\n\n{referral}"
            "If useful, I can send a compact proof pack or jump on a short call.\n\n"
            f"Best,\nPrathamesh\n\n{opt_out}"
        )
        return [
            OutreachVariant(
                id="warm",
                tone="warm",
                subject=self._subject(role, "Thanks for visiting my twin"),
                body=f"Hi {first_name} — {opening}\n\n{proof}{common_end}",
                template=template,
            ),
            OutreachVariant(
                id="direct",
                tone="direct",
                subject=self._subject(role, "Backend + agent systems"),
                body=(
                    f"Hi {first_name} — {opening}\n\n{proof}\n\n"
                    "I ship APIs, tool execution, validation, retries, tests and "
                    f"operational diagnostics — not prompt-only demos.{common_end}"
                ),
                template=template,
            ),
            OutreachVariant(
                id="technical",
                tone="technical",
                subject=self._subject(role, "Reliable AI/backend systems"),
                body=(
                    f"Hi {first_name} — {opening}\n\n"
                    "Recent work: FastAPI services, structured tool orchestration, "
                    "guardrails, retry semantics, GitHub automation and reproducible "
                    f"agent evaluations. All claims have a source trail.{common_end}"
                ),
                template=template,
            ),
        ]

    def _opt_out_line(self, address: str) -> str:
        token = self.token_service.issue_opt_out(address)
        return (
            "Prefer no follow-up? Reply ‘opt out’ or use "
            f"{self.public_base_url}/outreach/opt-out?token={quote(token)}"
        )

    @staticmethod
    def _referral(role: RoleMatch | None) -> str:
        if role is None or not role.requisition_id:
            return ""
        return (
            f"I’m looking at {role.title} (req {role.requisition_id}): "
            f"{role.canonical_apply_url}. Would you be open to a referral if the fit "
            "looks real?\n\n"
        )

    @staticmethod
    def _subject(role: RoleMatch | None, fallback: str) -> str:
        return (
            f"{role.title} — req {role.requisition_id}"
            if role and role.requisition_id
            else fallback
        )


class OutreachService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        sender: MailSender,
        preflight: DeliverabilityPreflight,
        tokens: ApprovalTokenService,
    ) -> None:
        self.settings = settings
        self.database = database
        self.sender = sender
        self.preflight = preflight
        self.tokens = tokens
        self._send_lock = asyncio.Lock()

    def create_draft(
        self,
        *,
        session_id: str,
        candidate: Candidate,
        email: CandidateEmail,
        variants: list[OutreachVariant],
        linkedin: dict[str, object],
        kind: str = "initial",
        parent_draft_id: str | None = None,
    ) -> OutreachDraft:
        draft = self.database.create_outreach_draft(
            session_id=session_id,
            candidate_id=candidate.id,
            recipient=email.address,
            recipient_status=email.status,
            recipient_pattern=email.pattern,
            recipient_score=email.score,
            recipient_why=email.why,
            recipient_source_url=email.source_url,
            recipient_source_kind=email.source_kind,
            recipient_company_level=email.company_level,
            subject=variants[0].subject,
            variants=[variant.model_dump(mode="json") for variant in variants],
            linkedin=linkedin,
            kind=kind,
            parent_draft_id=parent_draft_id,
        )
        self.database.update_visit(session_id, crm_stage="drafted")
        return draft

    def record_decision(
        self,
        *,
        session_id: str,
        candidate: Candidate,
        email: CandidateEmail,
        decision: SendDecision,
        template: TemplateKind,
    ) -> None:
        self.database.record_outreach_action(
            session_id=session_id,
            draft_id="decision",
            candidate_id=candidate.id,
            recipient=email.address,
            body_hash="",
            action="email.decision",
            approver="policy",
            transport="none",
            metadata={
                "decision": decision.decision,
                "reason": decision.reason,
                "template": template,
                "recipient_status": email.status,
                "pattern": email.pattern,
                "score": email.score,
                "why": email.why,
                "source_url": email.source_url,
                "source_kind": email.source_kind,
                "company_level": email.company_level,
            },
        )

    async def send(
        self,
        *,
        draft: OutreachDraft,
        variant_id: str,
        decision: SendDecision,
        template: TemplateKind,
        approver: str,
        approval_token: str | None = None,
        automatic: bool = False,
    ) -> SendResult:
        variant = next(
            (value for value in draft.variants if value.get("id") == variant_id), None
        )
        if variant is None:
            return SendResult(
                status="refused", transport="none", reason="Unknown variant."
            )
        body = str(variant.get("body", ""))
        subject = str(variant.get("subject", draft.subject))
        metadata = {
            "decision": decision.decision,
            "reason": decision.reason,
            "template": template,
            "variant": variant_id,
            "kind": draft.kind,
            "recipient_status": draft.recipient_status,
            "pattern": draft.recipient_pattern,
            "score": draft.recipient_score,
            "why": draft.recipient_why,
            "source_url": draft.recipient_source_url,
            "source_kind": draft.recipient_source_kind,
            "company_level": draft.recipient_company_level,
        }
        if automatic:
            single_allowed = (
                decision.decision == "auto"
                and draft.candidate_id in decision.candidate_ids
                and template == "single_match"
            )
            fanout_allowed = (
                self.settings.fanout_unselected
                and template == "fanout"
                and draft.candidate_id in decision.fanout_candidate_ids
            )
            if not (single_allowed or fanout_allowed):
                return SendResult(
                    status="refused",
                    transport="none",
                    reason="Policy did not authorize an automatic send.",
                )
        elif not approval_token or not self.tokens.verify(
            approval_token,
            draft_id=draft.id,
            recipient=draft.recipient,
            variant_id=variant_id,
            body=body,
        ):
            return SendResult(
                status="refused",
                transport="none",
                reason="A valid body-bound approval token is required.",
            )
        if self.database.is_suppressed(draft.recipient):
            return SendResult(
                status="suppressed",
                transport="none",
                reason="Recipient is on the suppression list.",
            )
        if not self.settings.autosend or not self.settings.smtp_ready:
            mailto = (
                f"mailto:{quote(draft.recipient)}?subject={quote(subject)}"
                f"&body={quote(body)}"
            )
            self._record_action(
                draft=draft,
                body=body,
                action="email.compose",
                approver=approver,
                transport="mailto",
                metadata=metadata,
            )
            return SendResult(
                status="compose",
                transport="mailto",
                reason=(
                    "SMTP automation is off; opening the user's mail client is the "
                    "default."
                ),
                mailto_url=mailto,
            )
        async with self._send_lock:
            cap_reason = self._cap_reason(draft)
            if cap_reason:
                return SendResult(status="capped", transport="none", reason=cap_reason)
            report = await self.preflight.check(self.settings.from_email)
            if not report.ready:
                return SendResult(
                    status="refused",
                    transport="none",
                    reason="Sender SPF/DKIM/DMARC preflight failed.",
                    preflight=report,
                )
            send_key = hashlib.sha256(
                f"{recipient_key(draft.recipient)}|{draft.kind}".encode()
            ).hexdigest()
            _, reserved = self.database.record_outreach_action(
                session_id=draft.session_id,
                draft_id=draft.id,
                candidate_id=draft.candidate_id,
                recipient=draft.recipient,
                body_hash=body_hash(body),
                action="email.reserved",
                approver=approver,
                transport="smtp",
                send_key=send_key,
                metadata=metadata,
            )
            if not reserved:
                return SendResult(
                    status="duplicate",
                    transport="none",
                    reason="Once-only delivery already reserved for this person.",
                )
            try:
                transport = await self.sender.send(
                    recipient=draft.recipient, subject=subject, body=body
                )
            except Exception as exc:
                self._record_action(
                    draft=draft,
                    body=body,
                    action="email.failed",
                    approver=approver,
                    transport="smtp",
                    metadata={**metadata, "error_type": type(exc).__name__},
                )
                raise
            self._record_action(
                draft=draft,
                body=body,
                action="email.sent",
                approver=approver,
                transport=transport,
                metadata=metadata,
            )
            self.database.update_visit(draft.session_id, crm_stage="sent")
            return SendResult(
                status="sent",
                transport=transport,
                reason=(
                    "Delivered once after approval, caps, suppression, and DNS checks."
                ),
                preflight=report,
            )

    def _cap_reason(self, draft: OutreachDraft) -> str | None:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        today = [
            action
            for action in self.database.outreach_actions_for(since=start)
            if action.action == "email.sent"
        ]
        if len(today) >= self.settings.daily_send_cap:
            return "The global TWIN_DAILY_SEND_CAP has been reached."
        candidate_today = [
            action for action in today if action.candidate_id == draft.candidate_id
        ]
        current_campaign = [
            action
            for action in candidate_today
            if action.session_id == draft.session_id
            and action.metadata_value.get("kind", draft.kind) == draft.kind
        ]
        campaign_ids = {
            (action.session_id, action.metadata_value.get("kind", "initial"))
            for action in candidate_today
        }
        if not current_campaign and (
            len(campaign_ids) >= self.settings.outreach_candidate_daily_cap
        ):
            return "The per-candidate daily send cap has been reached."
        ever_sent = [
            action
            for action in self.database.outreach_actions_for()
            if action.action == "email.sent"
            and recipient_key(action.recipient) == recipient_key(draft.recipient)
        ]
        if ever_sent and draft.kind == "initial":
            return "Once-only initial outreach has already been sent to this person."
        prior_person_campaign = [
            action
            for action in self.database.outreach_actions_for(
                candidate_id=draft.candidate_id
            )
            if action.action == "email.sent"
            and action.session_id != draft.session_id
            and action.metadata_value.get("kind", "initial") == "initial"
        ]
        if prior_person_campaign and draft.kind == "initial":
            return "Once-only initial outreach has already been sent to this person."
        return None

    def _record_action(
        self,
        *,
        draft: OutreachDraft,
        body: str,
        action: str,
        approver: str,
        transport: str,
        metadata: dict[str, object],
    ) -> None:
        self.database.record_outreach_action(
            session_id=draft.session_id,
            draft_id=draft.id,
            candidate_id=draft.candidate_id,
            recipient=draft.recipient,
            body_hash=body_hash(body),
            action=action,
            approver=approver,
            transport=transport,
            metadata=metadata,
        )


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
