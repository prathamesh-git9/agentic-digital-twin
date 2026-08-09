from __future__ import annotations

import json
from pathlib import Path

import httpx

from agentic_digital_twin.profile import ProfileCorpus
from agentic_digital_twin.roles import OpenRoleService, PublicATSClient, detect_ats

ROOT = Path(__file__).parents[1]


def test_detects_all_supported_public_ats_boards_from_observed_links() -> None:
    links = [
        "https://boards.greenhouse.io/acme",
        "https://jobs.lever.co/acme",
        "https://jobs.ashbyhq.com/acme",
        "https://apply.workable.com/acme/",
        "https://careers.smartrecruiters.com/Acme",
        "https://acme.recruitee.com/o/backend-engineer",
    ]

    boards = detect_ats("https://acme.example/careers", links=links)

    assert [(board.kind, board.token) for board in boards] == [
        ("greenhouse", "acme"),
        ("lever", "acme"),
        ("ashby", "acme"),
        ("workable", "acme"),
        ("smartrecruiters", "Acme"),
        ("recruitee", "acme"),
    ]


async def test_greenhouse_roles_are_real_urls_and_ranked_against_profile_evidence() -> (
    None
):
    payload = {
        "jobs": [
            {
                "id": 4242,
                "title": "Backend Python Engineer",
                "location": {"name": "Dublin, Ireland"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/4242",
                "content": "Build Python REST APIs with Docker and SQL.",
                "departments": [{"name": "Platform"}],
            },
            {
                "id": 9999,
                "title": "Principal Sales Director",
                "location": {"name": "Tokyo"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/9999",
                "content": "Lead enterprise sales.",
                "departments": [{"name": "Sales"}],
            },
        ]
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload), request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        service = OpenRoleService(
            ProfileCorpus(ROOT / "data" / "profile.yaml"),
            client=PublicATSClient(client=client),
        )
        result = await service.discover(
            "https://boards.greenhouse.io/acme",
            preferred_location="Dublin, Ireland",
        )
    finally:
        await client.aclose()

    assert result.status == "ok"
    assert result.roles[0].title == "Backend Python Engineer"
    assert result.roles[0].requisition_id == "4242"
    assert result.roles[0].canonical_apply_url.endswith("/4242")
    assert result.roles[0].fit_score > result.roles[1].fit_score
    assert any(item.signal == "skills" for item in result.roles[0].evidence)


async def test_direct_company_probe_survives_missing_search_results() -> None:
    payload = {
        "jobs": [
            {
                "id": 4242,
                "title": "Backend Python Engineer",
                "location": {"name": "Dublin, Ireland"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/4242",
                "content": "Build Python REST APIs with Docker and SQL.",
                "departments": [{"name": "Platform"}],
            }
        ]
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.url.host == "boards-api.greenhouse.io"
            and "/acme/" in request.url.path
        ):
            return httpx.Response(200, content=json.dumps(payload), request=request)
        return httpx.Response(404, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        service = OpenRoleService(
            ProfileCorpus(ROOT / "data" / "profile.yaml"),
            client=PublicATSClient(client=client),
        )
        result = await service.discover_company("Acme Ltd")
    finally:
        await client.aclose()

    assert result.status == "ok"
    assert result.board is not None
    assert result.board.kind == "greenhouse"
    assert [role.title for role in result.roles] == ["Backend Python Engineer"]


async def test_careers_page_fallback_omits_non_job_links() -> None:
    async def not_found(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(not_found))
    service = OpenRoleService(
        ProfileCorpus(ROOT / "data" / "profile.yaml"),
        client=PublicATSClient(client=client),
    )
    html = """
    <a href="/jobs/backend">Backend Software Engineer</a>
    <a href="/about">About our company</a>
    """
    try:
        result = await service.discover("https://acme.example/careers", html=html)
    finally:
        await client.aclose()

    assert [role.title for role in result.roles] == ["Backend Software Engineer"]
    assert result.roles[0].ats == "careers_page"
    assert result.roles[0].requisition_id is None


async def test_extracted_link_labels_power_the_fallback_without_inventing_a_req() -> None:
    service = OpenRoleService(
        ProfileCorpus(ROOT / "data" / "profile.yaml"),
        client=PublicATSClient(),
    )

    result = await service.discover(
        "https://acme.example/careers",
        links=["https://acme.example/jobs/backend"],
        link_labels={
            "https://acme.example/jobs/backend": "Backend Platform Engineer",
            "https://acme.example/about": "About Acme",
        },
    )

    assert [role.title for role in result.roles] == ["Backend Platform Engineer"]
    assert result.roles[0].requisition_id is None
