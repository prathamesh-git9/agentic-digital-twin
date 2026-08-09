from __future__ import annotations

import asyncio
import csv
import hmac
import io
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings
from .deliverability import DeliverabilityPreflight, DnspythonTXTResolver, TXTResolver
from .email_discovery import (
    DnspythonMXResolver,
    EmailDiscoveryService,
    EmailHarvester,
    HunterVerificationAdapter,
    MXResolver,
    attach_email_discovery,
    select_send_targets,
)
from .email_harvesting import PublicEmailHarvester
from .email_utils import normalize_address, recipient_key
from .engagement import (
    CompanyFitService,
    NotificationService,
    Notifier,
    ProofPackService,
    crm_row,
    verify_corporate_recruiter,
)
from .events import EventHub
from .github import GitHubService
from .linkedin import (
    LinkedInAutomationService,
    LinkedInDriver,
    PlaywrightLinkedInDriver,
)
from .mailer import GmailSMTPSender, MailSender
from .models import Database, Visit
from .outreach import (
    ApprovalTokenService,
    OutreachComposer,
    OutreachService,
    SendDecision,
    body_hash,
    decide_send,
)
from .profile import ProfileCorpus
from .providers import AnswerProvider, OpenAICompatibleProvider, ScriptedProvider
from .research import (
    BraveSearchProvider,
    CandidateDossier,
    DuckDuckGoSearchProvider,
    ResearchEngine,
    SearchOutcome,
    SearchProvider,
    SerperSearchProvider,
    TavilySearchProvider,
)
from .research_sources import PageFetcher, ScraplingPageFetcher
from .roles import OpenRoleService, PublicATSClient, RoleDiscoveryResult
from .schemas import (
    BounceRequest,
    ChatRequest,
    ChatResponse,
    CompanyOpportunityRequest,
    ConfirmCandidateRequest,
    ConfirmCandidateResponse,
    CRMStageRequest,
    FollowUpRequest,
    IdentityRequest,
    IdentityResponse,
    IntentRequest,
    JobDescriptionRequest,
    JobFitResponse,
    LinkedInActionRequest,
    LinkedInApprovalRequest,
    OutreachApprovalRequest,
    OutreachSendRequest,
    RecruiterVerificationRequest,
    ResearchStateResponse,
    SessionResponse,
    SuppressionRequest,
)
from .security import (
    SlidingWindowLimiter,
    approximate_tokens,
    hash_ip,
    normalize_name,
    sanitize_external_text,
)
from .services import ChatService, JobFitAnalyzer
from .supplemental import GitHubOrganizationSource, HackerNewsAlgoliaSource, RSSAtomReader
from .tooling import ToolCall, ToolRegistry

STATIC_DIR = Path(__file__).parent / "static"
GREETING = (
    "Hi — I’m Prathamesh’s evidence-grounded agentic digital twin. "
    "Ask me about his work, "
    "projects, skills, or fit for a role. If you’d like, share your name so I can look "
    "for public professional context; it is completely optional, and Skip gives you the "
    "same full chat."
)


def _draft_payload(draft: Any) -> dict[str, Any]:
    return {
        "id": draft.id,
        "session_id": draft.session_id,
        "candidate_id": draft.candidate_id,
        "recipient": draft.recipient,
        "recipient_status": draft.recipient_status,
        "recipient_pattern": draft.recipient_pattern,
        "recipient_score": draft.recipient_score,
        "recipient_why": draft.recipient_why,
        "recipient_source_url": draft.recipient_source_url,
        "recipient_source_kind": draft.recipient_source_kind,
        "recipient_company_level": draft.recipient_company_level,
        "subject": draft.subject,
        "variants": draft.variants,
        "linkedin": draft.linkedin,
        "kind": draft.kind,
        "parent_draft_id": draft.parent_draft_id,
        "created_at": draft.created_at.isoformat(),
    }


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _mcp_manifest(profile_path: Path) -> dict[str, Any] | None:
    """The MCP server manifest vendored beside the profile, when one is present.

    `scripts/build_static.py` copies it out of the sibling mcp-servers checkout,
    so a deployed container reads it from `data/` rather than needing that repo.
    """
    candidate = profile_path.parent / "mcp-manifest.json"
    if not candidate.is_file():
        return None
    try:
        loaded = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


class SelectiveGZipMiddleware(GZipMiddleware):
    """Compress every response except the event stream.

    Starlette's gzip responder carries one deflate stream across chunks and only
    flushes when a block fills, so compressing SSE would hold events back until
    enough bytes accumulated -- research progress would arrive in bursts, or not
    at all on a quiet session.
    """

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"].endswith("/events"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


def _answer_provider(settings: Settings) -> tuple[AnswerProvider, str]:
    if settings.provider == "openai-compatible" and settings.llm_api_key:
        return (
            OpenAICompatibleProvider(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                timeout=settings.llm_timeout_seconds,
                max_output_tokens=settings.max_output_tokens,
            ),
            "openai-compatible",
        )
    return ScriptedProvider(), "scripted"


def _search_provider(settings: Settings) -> SearchProvider:
    if settings.search_provider == "tavily" and settings.search_api_key:
        return TavilySearchProvider(
            settings.search_api_key, timeout=settings.search_timeout_seconds
        )
    if settings.search_provider == "serper" and settings.search_api_key:
        return SerperSearchProvider(
            settings.search_api_key, timeout=settings.search_timeout_seconds
        )
    if settings.search_provider == "brave" and settings.search_api_key:
        return BraveSearchProvider(
            settings.search_api_key, timeout=settings.search_timeout_seconds
        )
    return DuckDuckGoSearchProvider(timeout=settings.search_timeout_seconds)


def create_app(
    settings: Settings | None = None,
    *,
    search_provider: SearchProvider | None = None,
    answer_provider: AnswerProvider | None = None,
    github_service: GitHubService | None = None,
    page_fetcher: PageFetcher | None = None,
    mx_resolver: MXResolver | None = None,
    email_harvester: EmailHarvester | None = None,
    txt_resolver: TXTResolver | None = None,
    mail_sender: MailSender | None = None,
    ats_client: PublicATSClient | None = None,
    linkedin_driver: LinkedInDriver | None = None,
    notifier_service: Notifier | None = None,
) -> FastAPI:
    settings = settings or Settings()
    database = Database(settings.database_url)
    database.create_schema()
    corpus = ProfileCorpus(settings.profile_path, show_phone=settings.show_phone)
    mcp_manifest = _mcp_manifest(settings.profile_path)
    configured_provider, effective_provider = _answer_provider(settings)
    provider = answer_provider or configured_provider
    if answer_provider is not None:
        effective_provider = type(answer_provider).__name__
    github = github_service or GitHubService(token=settings.github_token)
    production_research = search_provider is None
    effective_search = search_provider or _search_provider(settings)
    effective_page_fetcher = page_fetcher
    if effective_page_fetcher is None and production_research:
        effective_page_fetcher = ScraplingPageFetcher(
            timeout=settings.research_source_timeout_seconds,
            allow_hosts=settings.parsed_fetch_page_allow_hosts,
            deny_hosts=settings.parsed_fetch_page_deny_hosts,
            max_bytes=settings.fetch_page_max_bytes,
            max_redirects=settings.fetch_page_max_redirects,
        )
    supplemental = (
        (
            HackerNewsAlgoliaSource(timeout=settings.research_source_timeout_seconds),
            GitHubOrganizationSource(
                timeout=settings.research_source_timeout_seconds,
                token=settings.github_token,
            ),
        )
        if production_research
        else ()
    )
    research = ResearchEngine(
        effective_search,
        cache_ttl_seconds=settings.research_cache_ttl_seconds,
        page_fetcher=effective_page_fetcher,
        source_timeout_seconds=settings.research_source_timeout_seconds,
        page_limit=settings.research_page_limit,
        supplemental_sources=supplemental,
        feed_reader=(
            RSSAtomReader(timeout=settings.research_source_timeout_seconds)
            if production_research
            else None
        ),
    )
    events = EventHub()
    fit = JobFitAnalyzer(corpus)
    email_verifier = (
        HunterVerificationAdapter(
            settings.email_verification_api_key,
            base_url=settings.email_verification_base_url,
            timeout=settings.research_source_timeout_seconds,
        )
        if settings.email_verification_provider == "hunter"
        and settings.email_verification_api_key
        else None
    )
    email_discovery = EmailDiscoveryService(
        mx_resolver=mx_resolver
        or DnspythonMXResolver(timeout=settings.email_mx_timeout_seconds),
        verifier=email_verifier,
        harvester=(
            email_harvester
            if email_harvester is not None
            else PublicEmailHarvester(
                timeout=settings.research_source_timeout_seconds,
                github_token=settings.github_token,
            )
            if production_research
            else None
        ),
        bounce_counts=database.pattern_bounce_counts,
    )
    role_service = OpenRoleService(
        corpus,
        client=ats_client
        or PublicATSClient(timeout=settings.research_source_timeout_seconds),
    )
    tools = ToolRegistry(
        settings=settings,
        database=database,
        search=effective_search,
        pages=effective_page_fetcher,
        github=github,
        research=research,
        roles=role_service,
        fit=fit,
        corpus=corpus,
    )
    chat = ChatService(
        settings=settings,
        database=database,
        corpus=corpus,
        github=github,
        provider=provider,
        tools=tools,
    )
    tokens = ApprovalTokenService(
        settings.hash_secret, ttl_seconds=settings.outreach_approval_ttl_seconds
    )
    composer = OutreachComposer(
        corpus,
        public_base_url=settings.public_base_url,
        token_service=tokens,
    )
    preflight = DeliverabilityPreflight(
        txt_resolver or DnspythonTXTResolver(timeout=settings.email_mx_timeout_seconds),
        selectors=settings.parsed_dkim_selectors,
    )
    outreach = OutreachService(
        settings=settings,
        database=database,
        sender=mail_sender or GmailSMTPSender(settings),
        preflight=preflight,
        tokens=tokens,
    )
    company_fit = CompanyFitService(corpus)
    proof_packs = ProofPackService(
        database=database,
        corpus=corpus,
        ttl_seconds=settings.proof_pack_ttl_seconds,
        public_base_url=settings.public_base_url,
    )
    notifier = notifier_service or NotificationService(settings)
    linkedin = LinkedInAutomationService(
        settings=settings,
        database=database,
        driver=linkedin_driver or PlaywrightLinkedInDriver(settings),
        tokens=tokens,
    )
    limiter = SlidingWindowLimiter(settings.requests_per_minute)
    session_limiter = SlidingWindowLimiter(settings.max_sessions_per_ip_hour, 3_600)
    research_results: dict[str, SearchOutcome] = {}
    role_results: dict[str, dict[str, RoleDiscoveryResult]] = {}
    send_decisions: dict[str, SendDecision] = {}
    identities: dict[str, IdentityRequest] = {}
    tasks: dict[str, asyncio.Task[None]] = {}
    notification_tasks: set[asyncio.Task[bool]] = set()

    def notify_later(kind: str, payload: dict[str, Any]) -> None:
        task = asyncio.create_task(notifier.notify(kind, payload))
        notification_tasks.add(task)
        task.add_done_callback(notification_tasks.discard)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        for task in tuple(tasks.values()):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        if notification_tasks:
            await asyncio.gather(*notification_tasks, return_exceptions=True)
        research_results.clear()
        role_results.clear()
        send_decisions.clear()
        identities.clear()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="A source-grounded recruiter-facing agentic digital twin.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )
    # The stylesheet and script are ~67 KB of highly repetitive text; served raw
    # they dominate first paint on a mobile connection.
    app.add_middleware(SelectiveGZipMiddleware, minimum_size=700)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.state.settings = settings
    app.state.database = database
    app.state.corpus = corpus
    app.state.research = research
    app.state.research_results = research_results
    app.state.events = events
    app.state.chat_service = chat
    app.state.tools = tools
    app.state.role_results = role_results
    app.state.send_decisions = send_decisions
    app.state.outreach = outreach
    app.state.email_discovery = email_discovery
    app.state.linkedin = linkedin

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        try:
            response = await call_next(request)
        except Exception as exc:
            notify_later(
                "error",
                {"error": f"{request.url.path}: {type(exc).__name__}"},
            )
            raise
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' "
            "data: https:; connect-src 'self'; frame-ancestors *; base-uri 'none'; "
            "form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/"):
            # The shell bumps ?v= whenever an asset changes, so a stamped URL can
            # never name stale bytes and is safe to keep forever. The photo and
            # favicon carry no stamp and must stay replaceable.
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable"
                if "v" in request.query_params
                else "public, max-age=3600, must-revalidate"
            )
        return response

    def client_hash(request: Request) -> str:
        ip = request.client.host if request.client else "unknown"
        return hash_ip(ip, settings.hash_secret)

    def require_visit(session_id: str) -> Visit:
        visit = database.get_visit(session_id)
        if visit is None:
            raise HTTPException(status_code=404, detail="Session not found or expired")
        return visit

    def enforce_rate(request: Request, session_id: str) -> None:
        key = f"{client_hash(request)}:{session_id}"
        if not limiter.allow(key):
            raise HTTPException(
                status_code=429,
                detail="Rate limit reached. Please wait a moment before trying again.",
                headers={"Retry-After": "60"},
            )

    async def run_research(session_id: str, identity: IdentityRequest) -> None:
        async def publish_progress(event: dict[str, Any]) -> None:
            visit = database.get_visit(session_id)
            if visit is not None and not visit.research_opted_out:
                await events.publish(session_id, event, retain=False)

        outcome = await research.find(
            identity.name or "",
            company=identity.company,
            location=identity.location,
            progress=publish_progress,
        )
        latest = database.get_visit(session_id)
        if latest is None or latest.research_opted_out:
            return
        enriched_candidates = []
        for candidate in outcome.candidates:
            dossier = next(
                (
                    value
                    for value in outcome.dossiers
                    if value.candidate_id == candidate.id
                ),
                None,
            )
            if dossier is None:
                enriched_candidates.append(candidate)
                continue
            discovery = await email_discovery.discover(candidate, dossier)
            enriched_candidates.append(attach_email_discovery(candidate, discovery))
        outcome = outcome.model_copy(update={"candidates": enriched_candidates})
        per_candidate_roles: dict[str, RoleDiscoveryResult] = {}
        for candidate in outcome.candidates:
            dossier = next(
                (
                    value
                    for value in outcome.dossiers
                    if value.candidate_id == candidate.id
                ),
                None,
            )
            careers = dossier.company.careers_page if dossier else None
            if careers is None:
                continue
            links = [link for document in dossier.documents for link in document.links]
            link_labels = {
                url: label
                for document in dossier.documents
                for url, label in document.link_labels.items()
            }
            per_candidate_roles[candidate.id] = await role_service.discover(
                careers.value,
                links=links,
                link_labels=link_labels,
                preferred_location=corpus.data["person"]["location"],
            )
        role_results[session_id] = per_candidate_roles
        decision = decide_send(
            outcome.candidates,
            threshold=settings.send_confidence_threshold,
            fanout_unselected=settings.fanout_unselected,
            fanout_max=settings.fanout_max,
        )
        send_decisions[session_id] = decision
        outreach_cards: list[dict[str, Any]] = []
        auto_work: list[tuple[Any, str, str, str | None]] = []
        for candidate in outcome.candidates:
            targets = select_send_targets(
                candidate.emails
                or ([candidate.email] if candidate.email is not None else []),
                inferred_send_max=settings.inferred_send_max,
            )
            if not targets:
                if candidate.id in {
                    *decision.candidate_ids,
                    *decision.fanout_candidate_ids,
                }:
                    notify_later(
                        "send_refusal",
                        {
                            "visitor_name": identity.name,
                            "decision": decision.reason,
                            "error": "No publishable or safely inferred email address.",
                        },
                    )
                continue
            role_result = per_candidate_roles.get(candidate.id)
            best_role = (
                role_result.roles[0] if role_result and role_result.roles else None
            )
            if candidate.id in decision.candidate_ids:
                template = "single_match"
            elif candidate.id in decision.fanout_candidate_ids:
                template = "fanout"
            else:
                template = "selected"
            linkedin_profile = next(
                (profile for profile in candidate.profiles if profile.kind == "linkedin"),
                None,
            )
            linkedin_draft = {
                "profile_url": linkedin_profile.url if linkedin_profile else None,
                "connect_note": (
                    "Hi — I build reliable Java/Python backend and agent systems. "
                    "Happy to connect and share a compact proof pack."
                ),
                "message": (
                    "Thanks for connecting. I’m a backend engineer with 3.5+ years "
                    "across Java, Spring Boot, Python and production support, with "
                    "recent agent-systems work. Happy to share evidence relevant to "
                    "your team."
                ),
            }
            for target_index, email in enumerate(targets):
                variants = composer.variants(
                    candidate,
                    email,
                    role=best_role,
                    template=template,
                )
                draft = outreach.create_draft(
                    session_id=session_id,
                    candidate=candidate,
                    email=email,
                    variants=variants,
                    linkedin=linkedin_draft,
                )
                outreach.record_decision(
                    session_id=session_id,
                    candidate=candidate,
                    email=email,
                    decision=decision,
                    template=template,
                )
                outreach_cards.append(
                    {
                        "candidate_id": candidate.id,
                        "draft_id": draft.id,
                        "recipient": email.model_dump(mode="json"),
                        "recipient_rank": target_index + 1,
                        "recipient_count": len(targets),
                        "variants": [
                            variant.model_dump(mode="json") for variant in variants[0:3]
                        ],
                        "linkedin": linkedin_draft,
                        "template": template,
                    }
                )
                if settings.autosend and (
                    candidate.id in decision.candidate_ids
                    or candidate.id in decision.fanout_candidate_ids
                ):
                    auto_work.append(
                        (
                            draft,
                            variants[0].id,
                            template,
                            best_role.title if best_role else None,
                        )
                    )
        research_results[session_id] = outcome
        database.update_visit(
            session_id,
            research_status=outcome.status,
            match_count=len(outcome.candidates),
        )
        event = {
            "type": "research",
            **outcome.model_dump(mode="json"),
            "disclosure": (
                "Checked public results only. Nothing enters chat until you confirm."
            ),
        }
        await events.publish(session_id, event)
        if outcome.dossiers:
            await events.publish(
                session_id,
                {
                    "type": "research.dossier",
                    "candidates": [
                        candidate.model_dump(mode="json")
                        for candidate in outcome.candidates
                    ],
                    "dossiers": [
                        dossier.model_dump(mode="json") for dossier in outcome.dossiers
                    ],
                },
                retain=False,
            )
        notify_later(
            "research_completed",
            {
                "visitor_name": identity.name,
                "candidate_count": len(outcome.candidates),
            },
        )
        degraded = [
            report.source
            for report in outcome.source_reports
            if report.status in {"failed", "timeout"}
        ]
        if degraded:
            notify_later(
                "error",
                {
                    "visitor_name": identity.name,
                    "error": "Research source degradation: " + ", ".join(degraded[:5]),
                },
            )
        if per_candidate_roles:
            await events.publish(
                session_id,
                {
                    "type": "roles.ready",
                    "roles": {
                        candidate_id: result.model_dump(mode="json")
                        for candidate_id, result in per_candidate_roles.items()
                    },
                },
                retain=False,
            )
        await events.publish(
            session_id,
            {
                "type": "outreach.ready",
                "decision": decision.model_dump(mode="json"),
                "drafts": outreach_cards,
            },
            retain=False,
        )
        for draft, variant_id, template, role_title in auto_work:
            try:
                result = await outreach.send(
                    draft=draft,
                    variant_id=variant_id,
                    decision=decision,
                    template=template,
                    approver="owner:auto-policy",
                    automatic=True,
                )
            except Exception as exc:  # noqa: BLE001 - delivery cannot disrupt research
                notify_later(
                    "error",
                    {
                        "visitor_name": identity.name,
                        "recipient": draft.recipient,
                        "role": role_title,
                        "decision": decision.reason,
                        "error": type(exc).__name__,
                    },
                )
                continue
            recent_questions = database.questions_for(session_id, limit=1)
            notify_later(
                "outreach_email_sent" if result.status == "sent" else "send_refusal",
                {
                    "visitor_name": identity.name,
                    "recipient": draft.recipient,
                    "role": role_title,
                    "decision": decision.reason,
                    "question": recent_questions[0] if recent_questions else None,
                    "error": result.reason if result.status != "sent" else None,
                },
            )
            await events.publish(
                session_id,
                {
                    "type": "outreach.action",
                    "candidate_id": draft.candidate_id,
                    **result.model_dump(mode="json"),
                },
                retain=False,
            )

    # The shell must never be cached. It carries the version stamps for the
    # stylesheet and script, so a stale copy pins the browser to old assets --
    # including builds whose JavaScript failed to boot, which presents to the
    # visitor as a chat that silently does nothing.
    NO_STORE = {"Cache-Control": "no-store, must-revalidate"}

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers=NO_STORE)

    @app.get("/embed", include_in_schema=False)
    async def embed() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers=NO_STORE)

    @app.get("/widget.js", include_in_schema=False)
    async def widget() -> FileResponse:
        return FileResponse(STATIC_DIR / "widget.js", media_type="text/javascript")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "provider": effective_provider,
            "model": settings.llm_model
            if effective_provider == "openai-compatible"
            else None,
            "grounding": "authority-gated",
            "tool_calling": bool(chat.agent and chat.agent.enabled),
            "tools": tools.names if chat.agent and chat.agent.enabled else [],
        }

    @app.get("/api/corpus")
    async def corpus_snapshot() -> dict[str, Any]:
        """The evidence corpus, in the shape the static build ships beside itself.

        This is what the page's retrieval explorer indexes. It is the same
        already-public CV text the twin cites, so exposing it adds no disclosure
        the answers do not; it just lets a reader check the retrieval themselves.
        """
        return {
            "person": {
                "name": corpus.data["person"]["name"],
                "email": corpus.email,
                "location": corpus.data["person"]["location"],
            },
            "items": [
                {"source": item.source, "text": item.text, "authority": item.authority}
                for item in corpus.evidence
            ],
            "repositories": [
                repo.model_dump(mode="json")
                for repo in (github.cached_repositories() or [])
            ],
            "mcp": mcp_manifest,
        }

    @app.get("/api/contact")
    async def contact() -> dict[str, str]:
        value = {"email": corpus.email, "location": corpus.data["person"]["location"]}
        if settings.show_phone:
            value["phone"] = corpus.data["person"]["phone"]
        return value

    @app.post("/api/sessions", response_model=SessionResponse, status_code=201)
    async def create_session(request: Request) -> SessionResponse:
        ip_hash = client_hash(request)
        if not session_limiter.allow(ip_hash):
            raise HTTPException(
                status_code=429,
                detail="Session creation limit reached. Please try again later.",
                headers={"Retry-After": "3600"},
            )
        visit = database.create_visit(ip_hash)
        notify_later("new_visitor_session", {"session_id": visit.id})
        initial = events.latest(visit.id)
        return SessionResponse(
            session_id=visit.id,
            greeting=GREETING,
            research=initial,
        )

    @app.post("/api/bootstrap", status_code=201)
    async def bootstrap(request: Request) -> dict[str, Any]:
        """Everything the shell needs to become interactive, in one round trip.

        The page used to await health, then contact, then GitHub, and only then
        ask for a session -- four sequential trips during which the composer was
        dead. Repository metadata rides along only when it is already warm,
        because the work section is below the fold and must never delay chat.
        """
        session = await create_session(request)
        repositories = github.cached_repositories()
        if repositories is None:
            github.prime()
        return {
            "session": session.model_dump(mode="json"),
            "health": await health(),
            "contact": await contact(),
            "repositories": (
                None
                if repositories is None
                else [repo.model_dump(mode="json") for repo in repositories]
            ),
        }

    @app.post(
        "/api/sessions/{session_id}/identity",
        response_model=IdentityResponse,
        status_code=202,
    )
    async def set_identity(
        session_id: str, payload: IdentityRequest, request: Request
    ) -> IdentityResponse:
        require_visit(session_id)
        enforce_rate(request, session_id)
        name = normalize_name(payload.name or "")
        if not name:
            database.update_visit(
                session_id,
                research_status="skipped",
                research_opted_out=True,
            )
            await events.publish(
                session_id,
                {
                    "type": "research",
                    "status": "skipped",
                    "message": "Research skipped. You still have the full conversation.",
                    "disclosure": "No public-source lookup is running.",
                },
            )
            return IdentityResponse(
                status="skipped",
                message="Skipped — ask anything. No chat feature is withheld.",
            )

        safe_identity = IdentityRequest(
            name=name,
            company=sanitize_external_text(payload.company or "", max_length=120) or None,
            location=sanitize_external_text(payload.location or "", max_length=120)
            or None,
        )
        prior = tasks.pop(session_id, None)
        if prior:
            prior.cancel()
        identities[session_id] = safe_identity
        database.update_visit(
            session_id,
            visitor_name=name,
            visitor_company=safe_identity.company,
            research_status="researching",
            research_opted_out=False,
            match_count=0,
            confirmed_candidate_json=None,
            confirmed_person_dossier_json=None,
            confirmed_company_dossier_json=None,
            confirmed_email_json=None,
            verified_corporate_domain=None,
            crm_stage="visited",
        )
        research_results.pop(session_id, None)
        role_results.pop(session_id, None)
        send_decisions.pop(session_id, None)
        database.purge_research_artifacts(session_id)
        await events.publish(
            session_id,
            {
                "type": "research",
                "status": "researching",
                "query": name,
                "message": f'Researching "{name}"…',
                "disclosure": (
                    "Checking public results. Nothing enters chat before confirmation. "
                    "If owner-enabled delivery authorizes this candidate set, its "
                    "separate outreach policy may prepare and send once; opt-out is "
                    "always "
                    "available. Chat remains fully available."
                ),
            },
        )
        notify_later("research_started", {"visitor_name": name})
        task = asyncio.create_task(run_research(session_id, safe_identity))
        tasks[session_id] = task
        task.add_done_callback(
            lambda done: (
                tasks.pop(session_id, None) if tasks.get(session_id) is done else None
            )
        )
        return IdentityResponse(
            status="researching",
            message="Public-source research started in the background. Keep chatting.",
        )

    @app.post(
        "/api/sessions/{session_id}/skip",
        response_model=IdentityResponse,
        status_code=200,
    )
    async def skip_identity(session_id: str, request: Request) -> IdentityResponse:
        return await set_identity(session_id, IdentityRequest(), request)

    @app.get(
        "/api/sessions/{session_id}/research",
        response_model=ResearchStateResponse,
    )
    async def research_state(session_id: str) -> ResearchStateResponse:
        require_visit(session_id)
        return ResearchStateResponse(state=events.latest(session_id))

    @app.get("/api/sessions/{session_id}/events")
    async def event_stream(session_id: str) -> StreamingResponse:
        require_visit(session_id)
        return StreamingResponse(
            events.stream(session_id),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    @app.post("/api/sessions/{session_id}/intent")
    async def set_intent(
        session_id: str, payload: IntentRequest, request: Request
    ) -> dict[str, str]:
        visit = require_visit(session_id)
        enforce_rate(request, session_id)
        database.update_visit(session_id, visitor_intent=payload.intent)
        await events.publish(
            session_id,
            {"type": "intent", "intent": payload.intent, "status": "recorded"},
            retain=False,
        )
        notify_later(
            "visitor_intent",
            {"visitor_name": visit.visitor_name, "decision": payload.intent},
        )
        return {"status": "recorded", "intent": payload.intent}

    @app.get("/api/sessions/{session_id}/dossier")
    async def session_dossier(session_id: str) -> dict[str, Any]:
        require_visit(session_id)
        outcome = research_results.get(session_id)
        if outcome is None:
            raise HTTPException(status_code=404, detail="Research result is unavailable")
        return {
            "status": outcome.status,
            "candidates": [
                candidate.model_dump(mode="json") for candidate in outcome.candidates
            ],
            "dossiers": [dossier.model_dump(mode="json") for dossier in outcome.dossiers],
            "sources": [
                report.model_dump(mode="json") for report in outcome.source_reports
            ],
            "authority": (
                "Card and outreach data only; absent from chat until candidate "
                "confirmation."
            ),
        }

    @app.get("/api/sessions/{session_id}/roles")
    async def session_roles(session_id: str) -> dict[str, Any]:
        require_visit(session_id)
        return {
            "by_candidate": {
                candidate_id: result.model_dump(mode="json")
                for candidate_id, result in role_results.get(session_id, {}).items()
            }
        }

    @app.post("/api/sessions/{session_id}/opportunities")
    async def company_opportunities(
        session_id: str,
        payload: CompanyOpportunityRequest,
        request: Request,
    ) -> dict[str, Any]:
        """Rank attributable public roles without researching the visitor.

        Company-only onboarding must remain useful and private: this calls the
        bounded open_roles tool directly, never turns the visitor's company into
        evidence about Prathamesh, and never applies to a role.
        """
        visit = require_visit(session_id)
        enforce_rate(request, session_id)
        company = sanitize_external_text(payload.company, max_length=160).strip()
        if not company:
            raise HTTPException(status_code=422, detail="Enter a company name")
        database.update_visit(session_id, visitor_company=company)
        result, duration_ms = await tools.execute(
            session_id,
            ToolCall(
                id=f"opportunities-{session_id[:12]}",
                name="open_roles",
                arguments={"company": company},
            ),
            available_seconds=settings.tool_wall_clock_seconds,
        )
        response = {
            "company": company,
            "status": result.status,
            "summary": result.summary,
            "roles": result.data.get("roles", []),
            "sources": [row.model_dump(mode="json") for row in result.sources],
            "duration_ms": duration_ms,
            "public_sources_only": True,
            "automatic_application": False,
        }
        await events.publish(
            session_id,
            {"type": "opportunities.ready", **response},
            retain=False,
        )
        notify_later(
            "opportunities_scanned",
            {
                "visitor_name": visit.visitor_name,
                "company": company,
                "role_count": len(response["roles"]),
            },
        )
        return response

    @app.get("/api/sessions/{session_id}/outreach")
    async def session_outreach(session_id: str) -> dict[str, Any]:
        require_visit(session_id)
        drafts = database.outreach_drafts_for(session_id)
        decision = send_decisions.get(session_id)
        return {
            "decision": decision.model_dump(mode="json") if decision else None,
            "drafts": [_draft_payload(draft) for draft in drafts],
            "smtp_automation_configured": settings.autosend and settings.smtp_ready,
        }

    @app.get("/api/sessions/{session_id}/company-fit")
    async def session_company_fit(session_id: str) -> dict[str, Any]:
        visit = require_visit(session_id)
        if visit.research_status != "confirmed" or not visit.confirmed_company_dossier:
            raise HTTPException(
                status_code=409, detail="Confirm a candidate before company-fit analysis"
            )
        dossier = CandidateDossier.model_validate(
            {
                "candidate_id": (visit.confirmed_candidate or {}).get("id", "confirmed"),
                "person": visit.confirmed_person_dossier or {"candidate_id": "confirmed"},
                "company": visit.confirmed_company_dossier,
                "documents": [],
            }
        )
        result = company_fit.analyze(dossier.company)
        await events.publish(
            session_id,
            {"type": "company_fit.ready", **result.model_dump(mode="json")},
            retain=False,
        )
        return result.model_dump(mode="json")

    @app.get("/api/sessions/{session_id}/calendar")
    async def calendar(session_id: str) -> dict[str, Any]:
        require_visit(session_id)
        return {
            "configured": bool(settings.calendar_url),
            "url": settings.calendar_url or None,
            "cta": "Book a short conversation with Prathamesh"
            if settings.calendar_url
            else None,
        }

    @app.post("/api/sessions/{session_id}/proof-pack", status_code=201)
    async def create_proof_pack(session_id: str) -> dict[str, Any]:
        visit = require_visit(session_id)
        if visit.research_status != "confirmed":
            raise HTTPException(status_code=409, detail="Candidate confirmation required")
        candidate_id = str((visit.confirmed_candidate or {}).get("id", ""))
        role_result = role_results.get(session_id, {}).get(candidate_id)
        role = role_result.roles[0] if role_result and role_result.roles else None
        fit_result = None
        if visit.confirmed_company_dossier:
            dossier = CandidateDossier.model_validate(
                {
                    "candidate_id": candidate_id or "confirmed",
                    "person": visit.confirmed_person_dossier
                    or {"candidate_id": candidate_id or "confirmed"},
                    "company": visit.confirmed_company_dossier,
                    "documents": [],
                }
            )
            fit_result = company_fit.analyze(dossier.company)
        pack, url = proof_packs.create(
            session_id=session_id, role=role, company_fit=fit_result
        )
        await events.publish(
            session_id,
            {
                "type": "proof_pack.ready",
                "url": url,
                "expires_at": pack.expires_at.isoformat(),
            },
            retain=False,
        )
        return {"url": url, "expires_at": pack.expires_at.isoformat()}

    @app.get("/api/proof-packs/{token}")
    async def get_proof_pack(token: str) -> dict[str, Any]:
        pack = database.get_proof_pack(token)
        if pack is None:
            raise HTTPException(status_code=404, detail="Proof pack not found or expired")
        return pack.payload

    @app.post("/api/sessions/{session_id}/recruiter/verify")
    async def recruiter_verify(
        session_id: str, payload: RecruiterVerificationRequest
    ) -> dict[str, Any]:
        visit = require_visit(session_id)
        result = verify_corporate_recruiter(
            payload.email, expected_domain=visit.verified_corporate_domain
        )
        if result.verified:
            database.update_visit(session_id, verified_corporate_domain=result.domain)
        return result.model_dump(mode="json")

    @app.get("/outreach/opt-out")
    async def outreach_opt_out(token: str) -> dict[str, str]:
        address = tokens.verify_opt_out(token)
        if address is None:
            raise HTTPException(status_code=400, detail="Invalid or expired opt-out link")
        database.suppress(address, "recipient opt-out link")
        return {
            "status": "suppressed",
            "message": "You will not receive further automated outreach.",
        }

    @app.post(
        "/api/sessions/{session_id}/confirm",
        response_model=ConfirmCandidateResponse,
    )
    async def confirm_candidate(
        session_id: str, payload: ConfirmCandidateRequest, request: Request
    ) -> ConfirmCandidateResponse:
        require_visit(session_id)
        enforce_rate(request, session_id)
        outcome = research_results.get(session_id)
        candidate = next(
            (
                item
                for item in (outcome.candidates if outcome else [])
                if item.id == payload.candidate_id
            ),
            None,
        )
        if candidate is None:
            raise HTTPException(
                status_code=404, detail="Candidate is unavailable or expired"
            )
        dossier = next(
            (
                value
                for value in (outcome.dossiers if outcome else [])
                if value.candidate_id == candidate.id
            ),
            None,
        )
        company_domain = (
            dossier.company.domain.value
            if dossier and dossier.company.domain is not None
            else None
        )
        database.update_visit(
            session_id,
            visitor_name=candidate.name,
            research_status="confirmed",
            confirmed_candidate_json=json.dumps(candidate.model_dump(mode="json")),
            confirmed_person_dossier_json=(
                json.dumps(dossier.person.model_dump(mode="json")) if dossier else None
            ),
            confirmed_company_dossier_json=(
                json.dumps(dossier.company.model_dump(mode="json")) if dossier else None
            ),
            confirmed_email_json=(
                json.dumps(candidate.email.model_dump(mode="json"))
                if candidate.email
                else None
            ),
            verified_corporate_domain=company_domain,
            crm_stage="confirmed",
        )
        await events.publish(
            session_id,
            {
                "type": "research",
                "status": "confirmed",
                "candidate": candidate.model_dump(mode="json"),
                "message": (
                    f"Confirmed {candidate.name}. This context can now tailor answers."
                ),
                "disclosure": (
                    "You explicitly authorised this public context for the chat."
                ),
            },
        )
        await events.publish(
            session_id,
            {
                "type": "handoff",
                "status": "ready",
                "candidate_id": candidate.id,
                "calendar_url": settings.calendar_url or None,
                "message": "Confirmed visitor context is ready for owner handoff.",
            },
            retain=False,
        )
        notify_later(
            "confirmed_visitor",
            {
                "session_id": session_id,
                "name": candidate.name,
                "company": candidate.company,
                "intent": require_visit(session_id).visitor_intent,
            },
        )
        database.update_visit(session_id, handoff_notified=True)
        roles = role_results.get(session_id, {}).get(candidate.id)
        candidate_drafts = [
            value
            for value in database.outreach_drafts_for(session_id)
            if value.candidate_id == candidate.id
        ]
        draft = candidate_drafts[0] if candidate_drafts else None
        return ConfirmCandidateResponse(
            status="confirmed",
            candidate=candidate,
            message="Confirmed context is now available to the agentic digital twin.",
            dossier=dossier,
            roles=roles.roles if roles else [],
            outreach=(
                {
                    "draft_id": draft.id,
                    "recipient": draft.recipient,
                    "recipient_status": draft.recipient_status,
                    "variants": draft.variants,
                    "linkedin": draft.linkedin,
                    "drafts": [_draft_payload(value) for value in candidate_drafts],
                    "consent": "Confirmation authorises preparation, not manual sending.",
                }
                if draft
                else None
            ),
        )

    @app.post("/api/sessions/{session_id}/research/opt-out")
    async def opt_out(session_id: str, request: Request) -> dict[str, str]:
        require_visit(session_id)
        enforce_rate(request, session_id)
        task = tasks.pop(session_id, None)
        if task:
            task.cancel()
        identity = identities.pop(session_id, None)
        if identity and identity.name:
            research.cache.purge(identity.name)
        prior_outcome = research_results.pop(session_id, None)
        for candidate in prior_outcome.candidates if prior_outcome else []:
            for email in candidate.emails or (
                [candidate.email] if candidate.email is not None else []
            ):
                database.suppress(email.address, "session research opt-out")
        role_results.pop(session_id, None)
        send_decisions.pop(session_id, None)
        database.purge_research_artifacts(session_id)
        database.update_visit(
            session_id,
            research_status="opted_out",
            research_opted_out=True,
            match_count=0,
            confirmed_candidate_json=None,
            confirmed_person_dossier_json=None,
            confirmed_company_dossier_json=None,
            confirmed_email_json=None,
            verified_corporate_domain=None,
        )
        await events.publish(
            session_id,
            {
                "type": "research",
                "status": "opted_out",
                "message": "Research stopped and session research was purged.",
                "disclosure": (
                    "No public-source research context is available to the chat."
                ),
            },
        )
        return {"status": "opted_out"}

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def end_session(session_id: str) -> Response:
        require_visit(session_id)
        task = tasks.pop(session_id, None)
        if task:
            task.cancel()
        identity = identities.pop(session_id, None)
        if identity and identity.name:
            research.cache.purge(identity.name)
        research_results.pop(session_id, None)
        role_results.pop(session_id, None)
        send_decisions.pop(session_id, None)
        events.purge(session_id)
        database.delete_visit(session_id)
        return Response(status_code=204)

    @app.post("/api/sessions/{session_id}/chat", response_model=ChatResponse)
    async def send_chat(
        session_id: str, payload: ChatRequest, request: Request
    ) -> ChatResponse:
        visit = require_visit(session_id)
        enforce_rate(request, session_id)
        message = payload.message.strip()
        if not message or len(message) > settings.max_input_chars:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Message must be between 1 and {settings.max_input_chars} "
                    "characters."
                ),
            )
        history = database.recent_messages(session_id, limit=8)
        estimated = approximate_tokens(message) + sum(
            approximate_tokens(item.content) for item in history
        )
        provider_turns = (
            settings.tool_max_iterations + 1 if chat.uses_agent_tools(message) else 1
        )
        estimated += settings.max_output_tokens * provider_turns
        if visit.token_usage + estimated > settings.token_budget_per_session:
            raise HTTPException(
                status_code=429,
                detail="This session has reached its hard token budget.",
            )

        async def publish_tool_event(event: dict[str, Any]) -> None:
            await events.publish(session_id, event, retain=False)

        verified, tailored_for, used, trace, tool_remaining = await chat.answer(
            visit,
            message,
            publish=publish_tool_event,
        )
        database.add_message(session_id, "user", message)
        database.add_message(session_id, "assistant", verified.text, verified.sources)
        database.update_visit(
            session_id,
            token_usage=min(settings.token_budget_per_session, visit.token_usage + used),
        )
        remaining = max(0, settings.token_budget_per_session - visit.token_usage - used)
        return ChatResponse(
            answer=verified.text,
            sources=verified.sources,
            grounded=verified.grounded,
            refusal=verified.refusal,
            tailored_for=tailored_for,
            budget_remaining=remaining,
            tool_budget_remaining=tool_remaining,
            trace=trace,
        )

    @app.post("/api/sessions/{session_id}/jd-fit", response_model=JobFitResponse)
    async def job_fit(
        session_id: str, payload: JobDescriptionRequest, request: Request
    ) -> JobFitResponse:
        visit = require_visit(session_id)
        enforce_rate(request, session_id)
        description = payload.description.strip()
        cost = approximate_tokens(description)
        if visit.token_usage + cost > settings.token_budget_per_session:
            raise HTTPException(status_code=429, detail="Session token budget reached.")
        result = fit.analyze(description)
        database.update_visit(session_id, token_usage=visit.token_usage + cost)
        database.add_message(
            session_id, "user", "[Job description fit analysis requested]"
        )
        return result

    @app.get("/api/github")
    async def github_repositories(request: Request) -> dict[str, Any]:
        key = f"github:{client_hash(request)}"
        if not limiter.allow(key):
            raise HTTPException(status_code=429, detail="Rate limit reached.")
        repositories = await github.get_repositories()
        return {
            "source": "GitHub API",
            "owner": "prathamesh-git9",
            "repositories": [repo.model_dump(mode="json") for repo in repositories],
        }

    security = HTTPBasic(auto_error=False)

    def require_owner(
        credentials: HTTPBasicCredentials | None = Depends(security),  # noqa: B008
    ) -> str:
        if not settings.owner_enabled:
            raise HTTPException(
                status_code=503, detail="Owner dashboard is not configured"
            )
        username_ok = bool(credentials) and hmac.compare_digest(
            credentials.username.encode(), settings.owner_username.encode()
        )
        password_ok = bool(credentials) and hmac.compare_digest(
            credentials.password.encode(), settings.owner_password.encode()
        )
        if not (username_ok and password_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Owner authentication required",
                headers={"WWW-Authenticate": "Basic"},
            )
        return settings.owner_username

    def owner_rows() -> list[dict[str, Any]]:
        rows = []
        for visit in database.list_visits():
            candidate = visit.confirmed_candidate
            rows.append(
                {
                    "session_id": visit.id,
                    "created_at": visit.created_at.isoformat(),
                    "last_seen_at": visit.last_seen_at.isoformat(),
                    "visitor_name": visit.visitor_name,
                    "visitor_company": visit.visitor_company,
                    "research_status": visit.research_status,
                    "match_count": visit.match_count,
                    "confirmed_candidate": candidate,
                    "crm_stage": visit.crm_stage,
                    "visitor_intent": visit.visitor_intent,
                    "send_decision": (
                        send_decisions[visit.id].model_dump(mode="json")
                        if visit.id in send_decisions
                        else None
                    ),
                    "message_count": visit.message_count,
                    "token_usage": visit.token_usage,
                    "questions": database.questions_for(visit.id),
                }
            )
        return rows

    @app.get("/owner", include_in_schema=False)
    async def owner_page(_: str = Depends(require_owner)) -> FileResponse:
        return FileResponse(STATIC_DIR / "owner.html")

    @app.post("/outreach/approve")
    async def approve_outreach(
        payload: OutreachApprovalRequest,
        _: str = Depends(require_owner),
    ) -> dict[str, Any]:
        draft = database.get_outreach_draft(payload.draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Outreach draft not found")
        variant = next(
            (value for value in draft.variants if value.get("id") == payload.variant_id),
            None,
        )
        if variant is None:
            raise HTTPException(status_code=404, detail="Draft variant not found")
        body = str(variant.get("body", ""))
        token = tokens.issue(
            draft_id=draft.id,
            recipient=draft.recipient,
            variant_id=payload.variant_id,
            body=body,
        )
        return {
            "approval_token": token,
            "draft_id": draft.id,
            "variant_id": payload.variant_id,
            "recipient": draft.recipient,
            "body_hash": body_hash(body),
            "expires_in_seconds": settings.outreach_approval_ttl_seconds,
        }

    @app.post("/outreach/send")
    async def send_outreach(
        payload: OutreachSendRequest,
        owner: str = Depends(require_owner),
    ) -> dict[str, Any]:
        draft = database.get_outreach_draft(payload.draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Outreach draft not found")
        decision = send_decisions.get(draft.session_id) or SendDecision(
            decision="review",
            reason="Owner explicitly approved a reviewed draft.",
            candidate_ids=[],
            confidence_threshold=settings.send_confidence_threshold,
        )
        variant = next(
            (value for value in draft.variants if value.get("id") == payload.variant_id),
            None,
        )
        template = str((variant or {}).get("template", "selected"))
        if template not in {"single_match", "selected", "fanout", "follow_up"}:
            raise HTTPException(status_code=422, detail="Unsupported outreach template")
        try:
            result = await outreach.send(
                draft=draft,
                variant_id=payload.variant_id,
                decision=decision,
                template=template,
                approver=owner,
                approval_token=payload.approval_token,
                automatic=False,
            )
        except Exception as exc:
            notify_later(
                "error",
                {
                    "visitor_name": require_visit(draft.session_id).visitor_name,
                    "recipient": draft.recipient,
                    "decision": decision.reason,
                    "error": type(exc).__name__,
                },
            )
            raise HTTPException(
                status_code=502, detail="SMTP delivery failed safely"
            ) from exc
        roles = role_results.get(draft.session_id, {}).get(draft.candidate_id)
        role_title = roles.roles[0].title if roles and roles.roles else None
        notify_later(
            "outreach_email_sent" if result.status == "sent" else "send_refusal",
            {
                "visitor_name": require_visit(draft.session_id).visitor_name,
                "recipient": draft.recipient,
                "role": role_title,
                "decision": decision.reason,
                "error": result.reason if result.status != "sent" else None,
            },
        )
        await events.publish(
            draft.session_id,
            {"type": "outreach.action", **result.model_dump(mode="json")},
            retain=False,
        )
        return result.model_dump(mode="json")

    @app.post("/outreach/suppress")
    async def suppress_outreach(
        payload: SuppressionRequest,
        _: str = Depends(require_owner),
    ) -> dict[str, str]:
        database.suppress(payload.address, payload.reason)
        return {"status": "suppressed"}

    @app.post("/api/owner/outreach/bounces")
    async def record_outreach_bounce(
        payload: BounceRequest,
        owner: str = Depends(require_owner),
    ) -> dict[str, Any]:
        address = normalize_address(payload.address)
        _, separator, domain = address.rpartition("@")
        if not separator or not domain:
            raise HTTPException(
                status_code=422, detail="A valid email address is required"
            )
        prior = next(
            (
                action
                for action in database.outreach_actions()
                if recipient_key(action.recipient) == recipient_key(address)
            ),
            None,
        )
        prior_metadata = prior.metadata_value if prior else {}
        pattern = payload.pattern or (
            str(prior_metadata.get("pattern")) if prior_metadata.get("pattern") else None
        )
        database.suppress(address, payload.reason)
        bounce_count = None
        if pattern:
            bounce_count = database.record_pattern_bounce(domain, pattern)
            email_discovery.record_bounce(domain, pattern)
        database.record_outreach_action(
            session_id=payload.session_id or (prior.session_id if prior else "owner"),
            draft_id=prior.draft_id if prior else "bounce",
            candidate_id=payload.candidate_id
            or (prior.candidate_id if prior else "unknown"),
            recipient=address,
            body_hash=prior.body_hash if prior else "",
            action="email.bounced",
            approver=owner,
            transport=prior.transport if prior else "provider-report",
            metadata={
                "recipient_status": "bounced",
                "pattern": pattern,
                "why": payload.reason,
                "domain": domain,
                "pattern_bounce_count": bounce_count,
            },
        )
        return {
            "status": "bounced",
            "address": address,
            "suppressed": True,
            "domain": domain,
            "pattern": pattern,
            "pattern_bounce_count": bounce_count,
        }

    @app.post("/api/sessions/{session_id}/outreach/follow-up", status_code=201)
    async def draft_follow_up(
        session_id: str,
        payload: FollowUpRequest,
        _: str = Depends(require_owner),
    ) -> dict[str, Any]:
        require_visit(session_id)
        parent = database.get_outreach_draft(payload.draft_id)
        if parent is None or parent.session_id != session_id:
            raise HTTPException(status_code=404, detail="Parent outreach draft not found")
        sent = any(
            action.action == "email.sent" and action.draft_id == parent.id
            for action in database.outreach_actions_for(session_id=session_id)
        )
        if not sent:
            raise HTTPException(status_code=409, detail="Initial email has not been sent")
        available_at = _utc(parent.created_at) + timedelta(days=settings.follow_up_days)
        if datetime.now(UTC) < available_at:
            raise HTTPException(
                status_code=409,
                detail=f"Follow-up is available after {available_at.isoformat()}",
            )
        outcome = research_results.get(session_id)
        candidate = next(
            (
                value
                for value in (outcome.candidates if outcome else [])
                if value.id == parent.candidate_id
            ),
            None,
        )
        if candidate is None or candidate.email is None:
            raise HTTPException(status_code=404, detail="Candidate context was purged")
        role_result = role_results.get(session_id, {}).get(candidate.id)
        role = role_result.roles[0] if role_result and role_result.roles else None
        variants = composer.variants(
            candidate, candidate.email, role=role, template="follow_up"
        )
        draft = outreach.create_draft(
            session_id=session_id,
            candidate=candidate,
            email=candidate.email,
            variants=variants,
            linkedin=parent.linkedin,
            kind="follow_up",
            parent_draft_id=parent.id,
        )
        return _draft_payload(draft)

    def _observed_linkedin_profile(
        session_id: str, candidate_id: str, profile_url: str
    ) -> Any:
        outcome = research_results.get(session_id)
        candidate = next(
            (
                value
                for value in (outcome.candidates if outcome else [])
                if value.id == candidate_id
            ),
            None,
        )
        if candidate is None or not any(
            profile.kind == "linkedin" and profile.url == profile_url
            for profile in candidate.profiles
        ):
            raise HTTPException(
                status_code=404,
                detail="LinkedIn profile was not observed for this research candidate",
            )
        return candidate

    @app.post("/api/sessions/{session_id}/linkedin/approve")
    async def approve_linkedin(
        session_id: str,
        payload: LinkedInApprovalRequest,
        _: str = Depends(require_owner),
    ) -> dict[str, str]:
        require_visit(session_id)
        _observed_linkedin_profile(session_id, payload.candidate_id, payload.profile_url)
        token = linkedin.approval_token(
            candidate_id=payload.candidate_id,
            profile_url=payload.profile_url,
            action=payload.action,
            message=payload.message,
        )
        return {"approval_token": token}

    @app.post("/api/sessions/{session_id}/linkedin/action")
    async def linkedin_action(
        session_id: str,
        payload: LinkedInActionRequest,
        _: str = Depends(require_owner),
    ) -> dict[str, Any]:
        require_visit(session_id)
        candidate = _observed_linkedin_profile(
            session_id, payload.candidate_id, payload.profile_url
        )
        try:
            result = await linkedin.perform(
                session_id=session_id,
                candidate_id=payload.candidate_id,
                profile_url=payload.profile_url,
                action=payload.action,
                message=payload.message,
                approval_token=payload.approval_token,
                automatic=payload.automatic,
            )
        except Exception as exc:
            notify_later(
                "error",
                {"visitor_name": candidate.name, "error": type(exc).__name__},
            )
            raise HTTPException(
                status_code=502, detail="LinkedIn automation failed safely"
            ) from exc
        notify_later(
            "linkedin_action_taken",
            {
                "visitor_name": candidate.name,
                "decision": f"{payload.action}: {result.status}",
                "error": result.detail if result.status != "completed" else None,
            },
        )
        await events.publish(
            session_id,
            {"type": "linkedin.action", **result.model_dump(mode="json")},
            retain=False,
        )
        return result.model_dump(mode="json")

    @app.get("/api/owner/contacts")
    async def owner_contacts(_: str = Depends(require_owner)) -> dict[str, Any]:
        return {
            "contacts": [crm_row(visit, database) for visit in database.list_visits()]
        }

    @app.get("/api/owner/sessions/{session_id}/replay")
    async def owner_replay(
        session_id: str, _: str = Depends(require_owner)
    ) -> dict[str, Any]:
        visit = require_visit(session_id)
        return {
            "contact": crm_row(visit, database),
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    "sources": message.sources,
                    "created_at": message.created_at.isoformat(),
                }
                for message in database.messages_for(session_id)
            ],
            "research": (
                research_results[session_id].model_dump(mode="json")
                if session_id in research_results
                else None
            ),
        }

    @app.post("/api/owner/contacts/{session_id}/stage")
    async def owner_stage(
        session_id: str,
        payload: CRMStageRequest,
        _: str = Depends(require_owner),
    ) -> dict[str, str]:
        require_visit(session_id)
        database.update_visit(session_id, crm_stage=payload.stage)
        return {"status": "updated", "stage": payload.stage}

    @app.get("/api/owner/outreach")
    async def owner_outreach(_: str = Depends(require_owner)) -> dict[str, Any]:
        actions = database.outreach_actions()
        performance: dict[str, dict[str, int]] = {}
        for action in actions:
            variant = str(action.metadata_value.get("variant") or "unassigned")
            counts = performance.setdefault(
                variant, {"sent": 0, "compose": 0, "failed": 0, "bounced": 0}
            )
            if action.action == "email.sent":
                counts["sent"] += 1
            elif action.action == "email.compose":
                counts["compose"] += 1
            elif action.action == "email.failed":
                counts["failed"] += 1
            elif action.action == "email.bounced":
                counts["bounced"] += 1
        return {
            "actions": [
                {
                    "id": action.id,
                    "session_id": action.session_id,
                    "candidate_id": action.candidate_id,
                    "recipient": action.recipient,
                    "action": action.action,
                    "transport": action.transport,
                    "approver": action.approver,
                    "metadata": action.metadata_value,
                    "created_at": action.created_at.isoformat(),
                }
                for action in actions
            ],
            "variant_performance": performance,
            "tracking_pixels": False,
        }

    @app.get("/api/owner/visits")
    async def owner_visits(_: str = Depends(require_owner)) -> JSONResponse:
        return JSONResponse({"visits": owner_rows()})

    @app.get("/api/owner/export.csv")
    async def owner_export(_: str = Depends(require_owner)) -> StreamingResponse:
        stream = io.StringIO()
        fieldnames = [
            "session_id",
            "created_at",
            "visitor_name",
            "visitor_company",
            "research_status",
            "match_count",
            "message_count",
            "token_usage",
            "questions",
        ]
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in owner_rows():
            writer.writerow({key: row.get(key) for key in fieldnames})
        return StreamingResponse(
            iter([stream.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=digital-twin-visits.csv"
            },
        )

    return app


app = create_app()


def run() -> None:
    uvicorn.run(
        "digital_twin.main:app",
        host="0.0.0.0",  # noqa: S104 - container entrypoint must listen externally
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    run()
