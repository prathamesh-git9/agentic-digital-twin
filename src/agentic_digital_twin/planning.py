from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

AgentIntent = Literal[
    "profile",
    "repository",
    "company",
    "roles",
    "job_fit",
    "web_research",
    "policy",
]
AgentMode = Literal["grounded-retrieval", "tool-assisted", "policy"]
StepStatus = Literal["pending", "running", "completed", "skipped", "blocked"]


class AgentStep(BaseModel):
    key: Literal["route", "retrieve", "tools", "verify", "answer"]
    label: str = Field(min_length=1, max_length=100)
    status: StepStatus = "pending"
    detail: str | None = Field(default=None, max_length=240)


class AgentRun(BaseModel):
    """Public, secret-free account of how one answer was produced."""

    intent: AgentIntent
    mode: AgentMode
    goal: str = Field(min_length=1, max_length=180)
    steps: list[AgentStep]
    tools_considered: list[str] = []
    tool_calls: int = Field(default=0, ge=0)
    model_turns: int = Field(default=1, ge=0)
    evidence_count: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    outcome: Literal["grounded", "refused"]


@dataclass(frozen=True, slots=True)
class AgentPlan:
    intent: AgentIntent
    mode: AgentMode
    goal: str
    allowed_tools: tuple[str, ...]

    @property
    def uses_tools(self) -> bool:
        return self.mode == "tool-assisted" and bool(self.allowed_tools)

    def steps(self) -> list[AgentStep]:
        return [
            AgentStep(
                key="route",
                label="Understand the hiring goal",
                status="completed",
                detail=f"Routed as {self.intent.replace('_', ' ')}.",
            ),
            AgentStep(key="retrieve", label="Retrieve verified profile evidence"),
            AgentStep(
                key="tools",
                label="Choose and run bounded tools",
                status="pending" if self.uses_tools else "skipped",
                detail=(
                    None
                    if self.uses_tools
                    else "The profile index can answer this without external calls."
                ),
            ),
            AgentStep(key="verify", label="Verify every claim against its source"),
            AgentStep(key="answer", label="Return a concise cited answer"),
        ]


REPOSITORY_QUERY = re.compile(
    r"\b(?:github|repo|repositor(?:y|ies)|commit|pull request|source code|readme|ci)\b"
    r"|\b(?:effect-broker|agent-runtime|effect-browser|agent-redteam|"
    r"answer-engine|agent-mesh|llm-gateway|promise-ledger|reachable|trustdesk)\b",
    re.I,
)
JOB_FIT_QUERY = re.compile(
    r"\b(?:job description|requirements? below|compare (?:this|the) role|"
    r"analyse (?:this|the) role|analyze (?:this|the) role|\bjd\b)\b",
    re.I,
)
ROLE_QUERY = re.compile(
    r"\b(?:jobs?|open roles?|openings?|vacanc(?:y|ies)|careers?|hiring for)\b",
    re.I,
)
COMPANY_QUERY = re.compile(
    r"\b(?:company|employer|engineering blog|tech stack|funding|organisation|"
    r"organization)\b",
    re.I,
)
WEB_QUERY = re.compile(
    r"\b(?:search the web|web search|public web|website|latest|recent news|"
    r"research online|look online)\b",
    re.I,
)


class AgentPlanner:
    """Deterministic first-hop planner before bounded model tool selection.

    The model still decides whether and how to call a tool. The planner constrains
    which tools it may see for this intent, reducing prompt size, latency and
    accidental calls to unrelated capabilities.
    """

    def plan(self, question: str) -> AgentPlan:
        if JOB_FIT_QUERY.search(question):
            return AgentPlan(
                intent="job_fit",
                mode="tool-assisted",
                goal="Compare role requirements with verified experience",
                allowed_tools=("job_fit", "cv_lookup"),
            )
        if ROLE_QUERY.search(question):
            return AgentPlan(
                intent="roles",
                mode="tool-assisted",
                goal="Find public roles and evaluate evidence-backed fit",
                allowed_tools=(
                    "open_roles",
                    "company_research",
                    "job_fit",
                    "cv_lookup",
                    "web_search",
                    "fetch_page",
                ),
            )
        if REPOSITORY_QUERY.search(question):
            return AgentPlan(
                intent="repository",
                mode="tool-assisted",
                goal="Inspect public code evidence and answer from what is found",
                allowed_tools=("search_github", "repo_detail", "cv_lookup"),
            )
        if COMPANY_QUERY.search(question):
            return AgentPlan(
                intent="company",
                mode="tool-assisted",
                goal=(
                    "Research public company context and connect it to verified "
                    "experience"
                ),
                allowed_tools=(
                    "company_research",
                    "open_roles",
                    "web_search",
                    "fetch_page",
                    "cv_lookup",
                ),
            )
        if WEB_QUERY.search(question):
            return AgentPlan(
                intent="web_research",
                mode="tool-assisted",
                goal="Research public sources and answer with attribution",
                allowed_tools=("web_search", "fetch_page", "cv_lookup"),
            )
        return AgentPlan(
            intent="profile",
            mode="grounded-retrieval",
            goal="Answer from verified profile evidence",
            allowed_tools=(),
        )

    @staticmethod
    def policy(goal: str) -> AgentPlan:
        return AgentPlan(
            intent="policy",
            mode="policy",
            goal=goal,
            allowed_tools=(),
        )


def final_steps(
    plan: AgentPlan,
    *,
    evidence_count: int,
    tool_statuses: list[str],
    grounded: bool,
) -> list[AgentStep]:
    steps = plan.steps()
    by_key = {step.key: step for step in steps}
    retrieve = by_key["retrieve"]
    tools = by_key["tools"]
    verify = by_key["verify"]
    answer = by_key["answer"]

    if plan.mode == "policy":
        retrieve.status = "skipped"
        retrieve.detail = "No personal or external research was needed."
    else:
        retrieve.status = "completed"
        retrieve.detail = f"Selected {evidence_count} relevant evidence chunk(s)."

    if plan.uses_tools:
        usable_calls = sum(status in {"ok", "empty"} for status in tool_statuses)
        if usable_calls:
            tools.status = "completed"
            tools.detail = (
                f"{usable_calls} of {len(tool_statuses)} screened tool call(s) "
                "completed safely."
            )
        elif tool_statuses:
            tools.status = "blocked"
            tools.detail = (
                "External calls failed or were blocked; verification continued "
                "with profile evidence only."
            )
        else:
            tools.status = "skipped"
            tools.detail = "The model found the retrieved evidence sufficient."

    verify.status = "completed" if grounded else "blocked"
    verify.detail = (
        "Every returned claim passed source verification."
        if grounded
        else "No unsupported claim was allowed into the answer."
    )
    answer.status = "completed"
    answer.detail = "Returned with citations." if grounded else "Returned a safe refusal."
    return steps
