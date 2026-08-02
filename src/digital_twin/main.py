from __future__ import annotations

import asyncio
import csv
import hmac
import io
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings
from .events import EventHub
from .github import GitHubService
from .models import Database, Visit
from .profile import ProfileCorpus
from .providers import AnswerProvider, OpenAICompatibleProvider, ScriptedProvider
from .research import (
    BraveSearchProvider,
    DuckDuckGoSearchProvider,
    ResearchEngine,
    SearchOutcome,
    SearchProvider,
    SerperSearchProvider,
    TavilySearchProvider,
)
from .schemas import (
    ChatRequest,
    ChatResponse,
    ConfirmCandidateRequest,
    ConfirmCandidateResponse,
    IdentityRequest,
    IdentityResponse,
    JobDescriptionRequest,
    JobFitResponse,
    ResearchStateResponse,
    SessionResponse,
)
from .security import (
    SlidingWindowLimiter,
    approximate_tokens,
    hash_ip,
    normalize_name,
    sanitize_external_text,
)
from .services import ChatService, JobFitAnalyzer

STATIC_DIR = Path(__file__).parent / "static"
GREETING = (
    "Hi — I’m Prathamesh’s evidence-grounded digital twin. Ask me about his work, "
    "projects, skills, or fit for a role. If you’d like, share your name so I can look "
    "for public professional context; it is completely optional, and Skip gives you the "
    "same full chat."
)


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
) -> FastAPI:
    settings = settings or Settings()
    database = Database(settings.database_url)
    database.create_schema()
    corpus = ProfileCorpus(settings.profile_path, show_phone=settings.show_phone)
    configured_provider, effective_provider = _answer_provider(settings)
    provider = answer_provider or configured_provider
    if answer_provider is not None:
        effective_provider = type(answer_provider).__name__
    github = github_service or GitHubService(token=settings.github_token)
    research = ResearchEngine(
        search_provider or _search_provider(settings),
        cache_ttl_seconds=settings.research_cache_ttl_seconds,
    )
    events = EventHub()
    chat = ChatService(
        settings=settings,
        database=database,
        corpus=corpus,
        github=github,
        provider=provider,
    )
    fit = JobFitAnalyzer(corpus)
    limiter = SlidingWindowLimiter(settings.requests_per_minute)
    session_limiter = SlidingWindowLimiter(settings.max_sessions_per_ip_hour, 3_600)
    research_results: dict[str, SearchOutcome] = {}
    identities: dict[str, IdentityRequest] = {}
    tasks: dict[str, asyncio.Task[None]] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        for task in tuple(tasks.values()):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        research_results.clear()
        identities.clear()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="A source-grounded recruiter-facing digital twin.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.state.settings = settings
    app.state.database = database
    app.state.corpus = corpus
    app.state.research = research
    app.state.research_results = research_results
    app.state.events = events
    app.state.chat_service = chat

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
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
        outcome = await research.find(
            identity.name or "",
            company=identity.company,
            location=identity.location,
        )
        latest = database.get_visit(session_id)
        if latest is None or latest.research_opted_out:
            return
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

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/embed", include_in_schema=False)
    async def embed() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

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
        initial = events.latest(visit.id)
        return SessionResponse(
            session_id=visit.id,
            greeting=GREETING,
            research=initial,
        )

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
        )
        await events.publish(
            session_id,
            {
                "type": "research",
                "status": "researching",
                "query": name,
                "message": f'Researching "{name}"…',
                "disclosure": ("Checking public results. Chat remains fully available."),
            },
        )
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
        database.update_visit(
            session_id,
            visitor_name=candidate.name,
            research_status="confirmed",
            confirmed_candidate_json=json.dumps(candidate.model_dump(mode="json")),
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
        return ConfirmCandidateResponse(
            status="confirmed",
            candidate=candidate,
            message="Confirmed context is now available to the twin.",
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
        research_results.pop(session_id, None)
        database.update_visit(
            session_id,
            research_status="opted_out",
            research_opted_out=True,
            match_count=0,
            confirmed_candidate_json=None,
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
        estimated += settings.max_output_tokens
        if visit.token_usage + estimated > settings.token_budget_per_session:
            raise HTTPException(
                status_code=429,
                detail="This session has reached its hard token budget.",
            )
        verified, tailored_for, used = await chat.answer(visit, message)
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
                    "message_count": visit.message_count,
                    "token_usage": visit.token_usage,
                    "questions": database.questions_for(visit.id),
                }
            )
        return rows

    @app.get("/owner", include_in_schema=False)
    async def owner_page(_: str = Depends(require_owner)) -> FileResponse:
        return FileResponse(STATIC_DIR / "owner.html")

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
