from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from .research import Candidate


class SessionResponse(BaseModel):
    session_id: str
    greeting: str
    name_optional: bool = True
    research: dict[str, Any]


class IdentityRequest(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    company: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=120)

    @field_validator("name", "company", "location")
    @classmethod
    def trim(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None


class IdentityResponse(BaseModel):
    status: str
    message: str
    chat_ready: bool = True


class ConfirmCandidateRequest(BaseModel):
    candidate_id: str = Field(min_length=6, max_length=64)


class ConfirmCandidateResponse(BaseModel):
    status: str
    candidate: Candidate
    message: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=50_000)


class ChatResponse(BaseModel):
    answer: str
    sources: list[str]
    grounded: bool
    refusal: bool = False
    tailored_for: str | None = None
    budget_remaining: int


class JobDescriptionRequest(BaseModel):
    description: str = Field(min_length=20, max_length=50_000)


class FitEvidence(BaseModel):
    requirement: str
    evidence: str
    source: str


class JobFitResponse(BaseModel):
    coverage_percent: int
    matched: list[FitEvidence]
    not_evidenced: list[str]
    summary: str
    caveat: str


class ResearchStateResponse(BaseModel):
    state: dict[str, Any]
