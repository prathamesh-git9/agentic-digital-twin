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

    app_name: str = "Prathamesh · Agentic Digital Twin"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./agentic-digital-twin.db"
    profile_path: Path = Path("data/profile.yaml")
    public_base_url: str = "http://localhost:8000"

    provider: Literal["scripted", "openai-compatible"] = "scripted"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: float = 25.0
    max_output_tokens: int = Field(default=700, ge=100, le=2_000)

    tool_calling_enabled: bool = True
    tool_max_iterations: int = Field(default=4, ge=1, le=8)
    tool_wall_clock_seconds: float = Field(default=18.0, ge=0.05, le=60)
    tool_timeout_seconds: float = Field(default=6.0, ge=0.01, le=30)
    tool_budget_per_session: int = Field(default=24, ge=0, le=500)
    tool_cache_ttl_seconds: int = Field(default=900, ge=30, le=86_400)
    tool_result_max_chars: int = Field(default=12_000, ge=1_000, le=50_000)

    tool_web_search_enabled: bool = True
    tool_fetch_page_enabled: bool = True
    tool_search_github_enabled: bool = True
    tool_repo_detail_enabled: bool = True
    tool_company_research_enabled: bool = True
    tool_open_roles_enabled: bool = True
    tool_job_fit_enabled: bool = True
    tool_cv_lookup_enabled: bool = True

    fetch_page_allow_hosts: str = ""
    fetch_page_deny_hosts: str = ""
    fetch_page_max_bytes: int = Field(default=262_144, ge=4_096, le=2_000_000)
    fetch_page_max_redirects: int = Field(default=3, ge=0, le=10)

    search_provider: Literal["duckduckgo", "tavily", "serper", "brave"] = "duckduckgo"
    search_api_key: str = ""
    search_timeout_seconds: float = 8.0
    research_source_timeout_seconds: float = Field(default=5.0, ge=0.1, le=30)
    research_page_limit: int = Field(default=5, ge=0, le=5)
    research_cache_ttl_seconds: int = Field(default=900, ge=30, le=86_400)

    email_mx_timeout_seconds: float = Field(default=3.0, ge=0.1, le=15)
    email_verification_provider: Literal["none", "hunter"] = "none"
    email_verification_api_key: str = ""
    email_verification_base_url: str = "https://api.hunter.io/v2"

    linkedin_url: str = "https://www.linkedin.com/in/prathamesh-kalamkar/"
    calendar_url: str = ""
    from_email: str = "prathameh7744yt@gmail.com"
    from_name: str = "Prathamesh Kalamkar"
    outreach_approval_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    follow_up_days: int = Field(default=5, ge=1, le=30)
    proof_pack_ttl_seconds: int = Field(default=604_800, ge=300, le=2_592_000)

    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65_535)
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    autosend: bool = False
    send_confidence_threshold: int = Field(default=85, ge=50, le=100)
    fanout_unselected: bool = False
    # Ceiling raised to 6 at the owner's request: common names routinely return
    # four or five public profiles, and a hard stop at 3 meant those sessions
    # always fell back to review and never sent. The default stays 3.
    fanout_max: int = Field(default=3, ge=1, le=6)
    inferred_send_max: int = Field(default=3, ge=0, le=10)
    daily_send_cap: int = Field(default=20, ge=1, le=100)
    outreach_candidate_daily_cap: int = Field(default=1, ge=1, le=5)
    dkim_selectors: str = "20230601,20161025,default,google,selector1,selector2"

    linkedin_enabled: bool = False
    linkedin_auto: bool = False
    linkedin_kill_switch: bool = False
    linkedin_user_data_dir: Path = Path(".twin-linkedin-profile")
    linkedin_daily_cap: int = Field(default=5, ge=1, le=20)
    linkedin_delay_min_seconds: float = Field(default=4.0, ge=0, le=60)
    linkedin_delay_max_seconds: float = Field(default=12.0, ge=0, le=120)

    handoff_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    pushover_enabled: bool = False
    pushover_user: str = ""
    pushover_token: str = ""
    notification_rate_limit_per_minute: int = Field(default=12, ge=1, le=60)

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

    @property
    def smtp_ready(self) -> bool:
        return bool(
            self.smtp_host.casefold() == "smtp.gmail.com"
            and self.smtp_port == 587
            and self.smtp_starttls
            and self.smtp_username
            and self.smtp_password
            and self.from_email
        )

    @property
    def parsed_dkim_selectors(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                selector.strip().casefold()
                for selector in self.dkim_selectors.split(",")
                if selector.strip()
            )
        )

    @property
    def parsed_fetch_page_allow_hosts(self) -> tuple[str, ...]:
        return _host_list(self.fetch_page_allow_hosts)

    @property
    def parsed_fetch_page_deny_hosts(self) -> tuple[str, ...]:
        return _host_list(self.fetch_page_deny_hosts)


def _host_list(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            host.strip().casefold().lstrip(".")
            for host in value.split(",")
            if host.strip()
        )
    )
