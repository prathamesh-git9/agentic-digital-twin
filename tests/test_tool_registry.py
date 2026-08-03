from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

from digital_twin.research import RawSearchResult, ResearchEngine
from digital_twin.research_sources import (
    PublicDocument,
    RobotsPolicy,
    ScraplingPageFetcher,
    SourceReport,
)
from digital_twin.tooling import ToolCall


class QueryProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def search(
        self, name: str, company: str | None, limit: int
    ) -> list[RawSearchResult]:
        return await self.search_query(f"{name} {company or ''}", limit)

    async def search_query(self, query: str, limit: int) -> list[RawSearchResult]:
        self.calls += 1
        return [
            RawSearchResult(
                title="Acme engineering uses Python",
                url="https://acme.com/engineering",
                snippet="Acme publishes details about its Python platform.",
            )
        ][:limit]


class EmptyPageFetcher:
    async def fetch(self, url: str) -> tuple[PublicDocument | None, SourceReport]:
        return None, SourceReport(source="public_web", status="empty", url=url)


class RobotsText:
    async def get_text(self, url: str, *, timeout_seconds: float) -> str:
        return "User-agent: *\nDisallow: /private"


def test_all_tools_have_schemas_and_are_individually_switchable(
    app_factory: Callable[..., FastAPI],
) -> None:
    app = app_factory(page_fetcher=EmptyPageFetcher())
    disabled = app_factory(
        page_fetcher=EmptyPageFetcher(),
        tool_open_roles_enabled=False,
    )

    assert app.state.tools.names == [
        "web_search",
        "fetch_page",
        "search_github",
        "repo_detail",
        "company_research",
        "open_roles",
        "job_fit",
        "cv_lookup",
    ]
    assert "open_roles" not in disabled.state.tools.names
    for definition in app.state.tools.definitions:
        function = definition["function"]
        assert definition["type"] == "function"
        assert function["description"]
        assert function["parameters"]["type"] == "object"
        assert function["parameters"]["additionalProperties"] is False


async def test_tool_cache_prevents_rescraping_and_session_budget_is_separate(
    app_factory: Callable[..., FastAPI],
) -> None:
    provider = QueryProvider()
    app = app_factory(
        search_provider=provider,
        tool_budget_per_session=2,
    )
    visit = app.state.database.create_visit("test-ip")
    call = ToolCall(
        id="call_web",
        name="web_search",
        arguments={"query": "Acme Python"},
    )

    first, _ = await app.state.tools.execute(visit.id, call, available_seconds=1)
    second, _ = await app.state.tools.execute(visit.id, call, available_seconds=1)
    blocked, _ = await app.state.tools.execute(visit.id, call, available_seconds=1)

    assert first.status == second.status == "ok"
    assert first.cached is False
    assert second.cached is True
    assert blocked.status == "blocked"
    assert provider.calls == 1
    assert app.state.tools.remaining(visit.id) == 0


async def test_web_tool_blocks_sensitive_trait_research_before_network(
    app_factory: Callable[..., FastAPI],
) -> None:
    provider = QueryProvider()
    app = app_factory(search_provider=provider)
    visit = app.state.database.create_visit("test-ip")

    result, _ = await app.state.tools.execute(
        visit.id,
        ToolCall(
            id="call_sensitive",
            name="web_search",
            arguments={"query": "candidate religious affiliation"},
        ),
        available_seconds=1,
    )

    assert result.status == "blocked"
    assert result.summary == "Sensitive-trait research is not permitted."
    assert provider.calls == 0


async def test_fetch_page_tool_honours_robots_denial_before_fetching(
    app_factory: Callable[..., FastAPI],
) -> None:
    html_calls = 0

    async def fetch_html(url: str, timeout_seconds: float) -> str:
        nonlocal html_calls
        html_calls += 1
        return "<title>Private page</title>"

    fetcher = ScraplingPageFetcher(
        timeout=0.1,
        robots=RobotsPolicy(timeout=0.1, transport=RobotsText()),
        fetch_html=fetch_html,
    )
    app = app_factory(page_fetcher=fetcher)
    visit = app.state.database.create_visit("test-ip")

    result, _ = await app.state.tools.execute(
        visit.id,
        ToolCall(
            id="call_page",
            name="fetch_page",
            arguments={"url": "https://example.com/private/profile"},
        ),
        available_seconds=1,
    )

    assert result.status == "blocked"
    assert "robots policy" in result.summary
    assert html_calls == 0


async def test_company_tool_uses_the_attributed_company_dossier_pipeline() -> None:
    outcome = await ResearchEngine(QueryProvider()).research_company("Acme")

    assert outcome.status == "ok"
    assert outcome.dossier.name == "Acme"
    assert outcome.dossier.website is not None
    assert outcome.dossier.website.source_url == "https://acme.com/engineering"
    assert [fact.value for fact in outcome.dossier.tech_stack] == ["Python"]


async def test_fetch_page_host_policy_and_size_cap_are_typed() -> None:
    async def fetch_html(url: str, timeout_seconds: float) -> str:
        return "<title>Large</title><p>" + ("x" * 200) + "</p>"

    fetcher = ScraplingPageFetcher(
        timeout=0.1,
        robots=RobotsPolicy(timeout=0.1, transport=RobotsTextAllow()),
        fetch_html=fetch_html,
        allow_hosts=("example.com",),
        deny_hosts=("denied.example.com",),
        max_bytes=50,
    )

    denied, denied_report = await fetcher.fetch("https://denied.example.com/page")
    large, large_report = await fetcher.fetch("https://example.com/page")

    assert denied is None and denied_report.status == "blocked"
    assert large is None and large_report.status == "blocked"
    assert large_report.detail == "response exceeded the configured size cap"


class RobotsTextAllow:
    async def get_text(self, url: str, *, timeout_seconds: float) -> str:
        return "User-agent: *\nAllow: /"
