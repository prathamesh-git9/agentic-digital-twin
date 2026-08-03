from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .research import Candidate, CandidateDossier
from .roles import RoleMatch
from .tooling import ToolTrace


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
    dossier: CandidateDossier | None = None
    roles: list[RoleMatch] = []
    outreach: dict[str, Any] | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=50_000)


class ChatResponse(BaseModel):
    answer: str
    sources: list[str]
    grounded: bool
    refusal: bool = False
    tailored_for: str | None = None
    budget_remaining: int
    tool_budget_remaining: int
    trace: list[ToolTrace] = []


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


class IntentRequest(BaseModel):
    intent: Literal["hiring", "networking", "exploring"]


class OutreachApprovalRequest(BaseModel):
    draft_id: str = Field(min_length=10, max_length=64)
    variant_id: str = Field(min_length=1, max_length=40)


class OutreachSendRequest(OutreachApprovalRequest):
    approval_token: str = Field(min_length=20, max_length=2_000)


class SuppressionRequest(BaseModel):
    address: str = Field(min_length=3, max_length=320)
    reason: str = Field(default="requested", max_length=240)


class BounceRequest(BaseModel):
    address: str = Field(min_length=3, max_length=320)
    pattern: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="hard bounce", min_length=1, max_length=240)
    session_id: str | None = Field(default=None, max_length=64)
    candidate_id: str | None = Field(default=None, max_length=64)


class FollowUpRequest(BaseModel):
    draft_id: str = Field(min_length=10, max_length=64)


class RecruiterVerificationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class LinkedInApprovalRequest(BaseModel):
    candidate_id: str = Field(min_length=6, max_length=64)
    profile_url: str = Field(min_length=10, max_length=2_000)
    action: Literal["follow", "connect", "message"]
    message: str | None = Field(default=None, max_length=1_000)


class LinkedInActionRequest(LinkedInApprovalRequest):
    approval_token: str | None = Field(default=None, max_length=2_000)
    automatic: bool = False


class CRMStageRequest(BaseModel):
    stage: Literal["visited", "confirmed", "drafted", "sent", "replied"]
