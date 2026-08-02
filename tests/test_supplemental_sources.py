from __future__ import annotations

import json

import httpx

from digital_twin.supplemental import (
    GitHubOrganizationSource,
    HackerNewsAlgoliaSource,
    RSSAtomReader,
)


async def test_hacker_news_algolia_source_is_attributed_and_offline_mockable() -> None:
    payload = {
        "hits": [
            {
                "objectID": "42",
                "title": "Acme launches a developer platform",
                "url": "https://acme.io/blog/platform",
            }
        ]
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload), request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        results = await HackerNewsAlgoliaSource(client=client).discover("Acme")
    finally:
        await client.aclose()

    assert results[0].url == "https://acme.io/blog/platform"
    assert "42" in results[0].snippet


async def test_github_org_source_reports_public_languages_and_activity() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/users":
            payload = {
                "items": [{"login": "acme", "html_url": "https://github.com/acme"}]
            }
        else:
            payload = [
                {"language": "Python", "pushed_at": "2026-07-01T00:00:00Z"},
                {"language": "Go", "pushed_at": "2026-08-01T00:00:00Z"},
            ]
        return httpx.Response(200, content=json.dumps(payload), request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        results = await GitHubOrganizationSource(client=client).discover("Acme")
    finally:
        await client.aclose()

    assert results[0].url == "https://github.com/acme"
    assert "Go, Python" in results[0].snippet
    assert "2026-08-01" in results[0].snippet


async def test_rss_and_atom_reader_extracts_only_observed_entries() -> None:
    feed = """<?xml version="1.0"?>
    <rss><channel><item><title>Engineering update</title>
    <link>https://acme.io/blog/update</link>
    <description>Python platform release</description></item></channel></rss>"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=feed, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        results = await RSSAtomReader(client=client).read("https://acme.io/feed.xml")
    finally:
        await client.aclose()

    assert len(results) == 1
    assert results[0].title == "Engineering update"
    assert results[0].url == "https://acme.io/blog/update"
