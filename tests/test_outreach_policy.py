from __future__ import annotations

from pathlib import Path

from digital_twin.config import Settings
from digital_twin.deliverability import DeliverabilityPreflight
from digital_twin.models import Database
from digital_twin.outreach import (
    ApprovalTokenService,
    OutreachComposer,
    OutreachService,
    decide_send,
)
from digital_twin.profile import ProfileCorpus
from digital_twin.research import Candidate, CandidateEmail
from digital_twin.roles import RoleMatch

ROOT = Path(__file__).parents[1]


class FakeTXT:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    async def records(self, name: str) -> list[str]:
        if not self.ready:
            return []
        if name == "gmail.com":
            return ["v=spf1 redirect=_spf.google.com"]
        if name == "_dmarc.gmail.com":
            return ["v=DMARC1; p=none"]
        if name.startswith("20230601._domainkey"):
            return ["v=DKIM1; p=public-key"]
        return []


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def send(self, *, recipient: str, subject: str, body: str) -> str:
        self.messages.append({"recipient": recipient, "subject": subject, "body": body})
        return "fake-smtp"


def make_candidate(
    number: int,
    *,
    confidence: int = 92,
    email_status: str = "verified",
) -> Candidate:
    return Candidate(
        id=f"candidate-{number}",
        name=f"Sarah Chen{number}",
        headline="Platform Engineer",
        company="Acme",
        initials="SC",
        source_link=f"https://example.com/sarah-{number}",
        source_label="example.com",
        confidence=confidence,
        why=["public name match"],
        email=CandidateEmail(
            address=f"sarah{number}@acme.io",
            status=email_status,
            confidence="high" if email_status == "verified" else "medium",
            source_url="https://acme.io/team",
            why="published" if email_status == "verified" else "pattern inferred",
        ),
    )


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": "sqlite://",
        "profile_path": ROOT / "data" / "profile.yaml",
        "hash_secret": "test-signing-secret",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_starttls": True,
        "smtp_username": "owner@gmail.com",
        "smtp_password": "unit-test-password",
        "from_email": "owner@gmail.com",
        "autosend": True,
        "fanout_unselected": True,
        "fanout_max": 3,
        "daily_send_cap": 20,
        "outreach_candidate_daily_cap": 5,
        "pushover_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def make_service(
    settings: Settings, database: Database, sender: FakeSender, *, dns_ready: bool = True
) -> tuple[OutreachService, OutreachComposer, ApprovalTokenService]:
    tokens = ApprovalTokenService(settings.hash_secret)
    service = OutreachService(
        settings=settings,
        database=database,
        sender=sender,
        preflight=DeliverabilityPreflight(FakeTXT(dns_ready), selectors=("20230601",)),
        tokens=tokens,
    )
    composer = OutreachComposer(
        ProfileCorpus(ROOT / "data" / "profile.yaml"),
        public_base_url="https://twin.example",
        token_service=tokens,
    )
    return service, composer, tokens


def test_count_based_send_decision_autos_small_sets_and_reviews_large_sets() -> None:
    single = decide_send(
        [make_candidate(1, email_status="inferred")],
        fanout_unselected=True,
        fanout_max=3,
    )
    pair = decide_send(
        [make_candidate(1), make_candidate(2)],
        fanout_unselected=True,
        fanout_max=3,
    )
    large = decide_send(
        [make_candidate(index) for index in range(4)],
        fanout_unselected=True,
        fanout_max=3,
    )
    disabled = decide_send(
        [make_candidate(1), make_candidate(2)],
        fanout_unselected=False,
        fanout_max=3,
    )

    assert single.decision == "auto"
    assert single.candidate_ids == ["candidate-1"]
    assert pair.decision == "auto"
    assert pair.fanout_candidate_ids == ["candidate-1", "candidate-2"]
    assert large.decision == "review"
    assert disabled.decision == "review"
    assert disabled.fanout_candidate_ids == []


def test_fanout_copy_never_claims_recipient_visited_and_referral_is_omitted() -> None:
    settings = make_settings()
    database = Database("sqlite://")
    database.create_schema()
    _, composer, _ = make_service(settings, database, FakeSender())
    candidate = make_candidate(1)

    variant = composer.variants(
        candidate,
        candidate.email,
        role=None,
        template="fanout",  # type: ignore[arg-type]
    )[0]

    lowered = variant.body.casefold()
    assert "may have looked at my profile" in lowered
    assert "if that wasn’t you" in lowered
    assert "you visited" not in lowered
    assert "you just talked" not in lowered
    assert "referral" not in lowered


def test_single_match_copy_can_be_confident_and_names_a_real_requisition() -> None:
    settings = make_settings()
    database = Database("sqlite://")
    database.create_schema()
    _, composer, _ = make_service(settings, database, FakeSender())
    candidate = make_candidate(1)
    role = RoleMatch(
        title="Backend Engineer",
        team="Platform",
        location="Dublin",
        canonical_apply_url="https://jobs.example/req-42",
        requisition_id="REQ-42",
        ats="greenhouse",
        fit_score=88,
        evidence=[],
        source_url="https://boards.greenhouse.io/example",
    )

    variant = composer.variants(
        candidate,
        candidate.email,  # type: ignore[arg-type]
        role=role,
        template="single_match",
    )[0]

    assert "You just talked to my digital twin" in variant.body
    assert "Backend Engineer (req REQ-42)" in variant.body
    assert "https://jobs.example/req-42" in variant.body


async def test_dns_failure_refuses_before_sender_is_called() -> None:
    settings = make_settings()
    database = Database("sqlite://")
    database.create_schema()
    visit = database.create_visit("ip")
    sender = FakeSender()
    service, composer, _ = make_service(settings, database, sender, dns_ready=False)
    candidate = make_candidate(1)
    variants = composer.variants(
        candidate,
        candidate.email,  # type: ignore[arg-type]
        role=None,
        template="single_match",
    )
    draft = service.create_draft(
        session_id=visit.id,
        candidate=candidate,
        email=candidate.email,  # type: ignore[arg-type]
        variants=variants,
        linkedin={},
    )
    decision = decide_send([candidate], fanout_unselected=True, fanout_max=3)

    result = await service.send(
        draft=draft,
        variant_id="warm",
        decision=decision,
        template="single_match",
        approver="policy",
        automatic=True,
    )

    assert result.status == "refused"
    assert result.preflight is not None and result.preflight.ready is False
    assert sender.messages == []


async def test_daily_cap_and_once_only_are_checked_before_every_send() -> None:
    settings = make_settings(daily_send_cap=10)
    database = Database("sqlite://")
    database.create_schema()
    visit = database.create_visit("ip")
    sender = FakeSender()
    service, composer, _ = make_service(settings, database, sender)
    candidate = make_candidate(1, email_status="inferred")
    variants = composer.variants(
        candidate,
        candidate.email,  # type: ignore[arg-type]
        role=None,
        template="single_match",
    )
    decision = decide_send([candidate], fanout_unselected=True, fanout_max=3)
    first = service.create_draft(
        session_id=visit.id,
        candidate=candidate,
        email=candidate.email,  # type: ignore[arg-type]
        variants=variants,
        linkedin={},
    )
    first_result = await service.send(
        draft=first,
        variant_id="warm",
        decision=decision,
        template="single_match",
        approver="policy",
        automatic=True,
    )
    second = service.create_draft(
        session_id=visit.id,
        candidate=candidate,
        email=candidate.email,  # type: ignore[arg-type]
        variants=variants,
        linkedin={},
    )
    second_result = await service.send(
        draft=second,
        variant_id="warm",
        decision=decision,
        template="single_match",
        approver="policy",
        automatic=True,
    )

    assert first_result.status == "sent"
    assert second_result.status == "capped"
    assert "Once-only" in second_result.reason
    assert len(sender.messages) == 1

    capped_settings = make_settings(daily_send_cap=1)
    another = make_candidate(2)
    capped_service, capped_composer, _ = make_service(capped_settings, database, sender)
    other_variants = capped_composer.variants(
        another,
        another.email,  # type: ignore[arg-type]
        role=None,
        template="single_match",
    )
    other_draft = capped_service.create_draft(
        session_id=visit.id,
        candidate=another,
        email=another.email,  # type: ignore[arg-type]
        variants=other_variants,
        linkedin={},
    )
    global_result = await capped_service.send(
        draft=other_draft,
        variant_id="warm",
        decision=decide_send([another], fanout_unselected=True, fanout_max=3),
        template="single_match",
        approver="policy",
        automatic=True,
    )
    assert global_result.status == "capped"
    assert "TWIN_DAILY_SEND_CAP" in global_result.reason


def test_approval_token_is_bound_to_recipient_variant_and_exact_body() -> None:
    tokens = ApprovalTokenService("test-secret", ttl_seconds=60)
    token = tokens.issue(
        draft_id="draft-123",
        recipient="sarah@acme.io",
        variant_id="warm",
        body="Exact body",
    )

    assert tokens.verify(
        token,
        draft_id="draft-123",
        recipient="sarah@acme.io",
        variant_id="warm",
        body="Exact body",
    )
    assert not tokens.verify(
        token,
        draft_id="draft-123",
        recipient="sarah@acme.io",
        variant_id="warm",
        body="Edited body",
    )


async def test_suppression_refuses_delivery_and_signed_opt_out_is_stable() -> None:
    settings = make_settings()
    database = Database("sqlite://")
    database.create_schema()
    visit = database.create_visit("ip")
    sender = FakeSender()
    service, composer, tokens = make_service(settings, database, sender)
    candidate = make_candidate(1)
    opt_out_token = tokens.issue_opt_out(candidate.email.address)  # type: ignore[union-attr]
    address = tokens.verify_opt_out(opt_out_token)
    assert address == candidate.email.address  # type: ignore[union-attr]
    database.suppress(address, "signed link")
    variants = composer.variants(
        candidate,
        candidate.email,  # type: ignore[arg-type]
        role=None,
        template="single_match",
    )
    draft = service.create_draft(
        session_id=visit.id,
        candidate=candidate,
        email=candidate.email,  # type: ignore[arg-type]
        variants=variants,
        linkedin={},
    )

    result = await service.send(
        draft=draft,
        variant_id="warm",
        decision=decide_send([candidate], fanout_unselected=True, fanout_max=3),
        template="single_match",
        approver="policy",
        automatic=True,
    )

    assert result.status == "suppressed"
    assert sender.messages == []
