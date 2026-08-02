from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; secrets are environment-only and never enter prompts."""

    model_config = SettingsConfigDict(
        env_prefix="TWIN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Prathamesh · Digital Twin"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./digital-twin.db"
    profile_path: Path = Path("data/profile.yaml")
    public_base_url: str = "http://localhost:8000"

    provider: Literal["scripted", "openai-compatible"] = "scripted"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: float = 25.0
    max_output_tokens: int = Field(default=700, ge=100, le=2_000)

    search_provider: Literal["duckduckgo", "tavily", "serper", "brave"] = "duckduckgo"
    search_api_key: str = ""
    search_timeout_seconds: float = 8.0
    research_cache_ttl_seconds: int = Field(default=900, ge=30, le=86_400)

    requests_per_minute: int = Field(default=30, ge=1, le=1_000)
    token_budget_per_session: int = Field(default=12_000, ge=500, le=1_000_000)
    max_input_chars: int = Field(default=8_000, ge=100, le=50_000)
    max_sessions_per_ip_hour: int = Field(default=20, ge=1, le=1_000)

    show_phone: bool = False
    owner_username: str = "owner"
    owner_password: str = ""
    hash_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    github_token: str = ""

    @property
    def owner_enabled(self) -> bool:
        return bool(self.owner_username and self.owner_password)
