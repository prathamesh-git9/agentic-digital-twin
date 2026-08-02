from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from digital_twin.config import Settings
from digital_twin.main import create_app
from digital_twin.research import RawSearchResult

ROOT = Path(__file__).parents[1]


class EmptySearchProvider:
    async def search(
        self, name: str, company: str | None, limit: int
    ) -> list[RawSearchResult]:
        return []


@pytest.fixture
def app_factory() -> Callable[..., FastAPI]:
    def factory(**overrides: Any) -> FastAPI:
        search_provider = overrides.pop("search_provider", EmptySearchProvider())
        answer_provider = overrides.pop("answer_provider", None)
        github_service = overrides.pop("github_service", None)
        page_fetcher = overrides.pop("page_fetcher", None)
        mx_resolver = overrides.pop("mx_resolver", None)
        txt_resolver = overrides.pop("txt_resolver", None)
        mail_sender = overrides.pop("mail_sender", None)
        ats_client = overrides.pop("ats_client", None)
        linkedin_driver = overrides.pop("linkedin_driver", None)
        notifier_service = overrides.pop("notifier_service", None)
        values: dict[str, Any] = {
            "environment": "test",
            "database_url": "sqlite://",
            "profile_path": ROOT / "data" / "profile.yaml",
            "hash_secret": "test-hash-secret",
            "requests_per_minute": 50,
            "max_sessions_per_ip_hour": 50,
            "token_budget_per_session": 20_000,
            "max_output_tokens": 200,
            "autosend": False,
            "fanout_unselected": False,
            "smtp_host": "",
            "smtp_username": "",
            "smtp_password": "",
            "linkedin_auto": False,
            "pushover_enabled": False,
            "owner_password": "",
        }
        values.update(overrides)
        settings = Settings(
            **values,
        )
        return create_app(
            settings,
            search_provider=search_provider,
            answer_provider=answer_provider,
            github_service=github_service,
            page_fetcher=page_fetcher,
            mx_resolver=mx_resolver,
            txt_resolver=txt_resolver,
            mail_sender=mail_sender,
            ats_client=ats_client,
            linkedin_driver=linkedin_driver,
            notifier_service=notifier_service,
        )

    return factory


@pytest.fixture
def client(app_factory: Callable[..., FastAPI]) -> Iterator[TestClient]:
    with TestClient(app_factory()) as test_client:
        yield test_client


@pytest.fixture
def session_id(client: TestClient) -> str:
    response = client.post("/api/sessions")
    assert response.status_code == 201
    return str(response.json()["session_id"])
