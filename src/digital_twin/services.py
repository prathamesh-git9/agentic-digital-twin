from __future__ import annotations

import re

import httpx

from .config import Settings
from .github import GitHubService
from .grounding import (
    ContextAssembler,
    GenerationRequest,
    GroundingVerifier,
    VerifiedAnswer,
)
from .models import Database, Visit
from .profile import EvidenceItem, ProfileCorpus, tokens
from .providers import AnswerProvider, ScriptedProvider
from .schemas import FitEvidence, JobFitResponse
from .security import (
    approximate_tokens,
    contains_prompt_injection,
    sanitize_external_text,
)

GENERIC_OVERVIEW = re.compile(
    r"\b(?:overview|background|introduce|about|summary)\b", re.I
)
GITHUB_QUERY = re.compile(
    r"\b(?:github|repo|repository|effect-broker|agent-runtime|effect-browser|"
    r"agent-redteam|answer-engine|agent-mesh|llm-gateway|promise-ledger|"
    r"reachable|trustdesk)\b",
    re.I,
)
CONTRACTUAL_QUERY = re.compile(
    r"\b(?:salary|compensation|pay|offer|accept|contract|start date|joining date|"
    r"notice period|negotiate)\b",
    re.I,
)


class ChatService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        corpus: ProfileCorpus,
        github: GitHubService,
        provider: AnswerProvider,
    ) -> None:
        self.settings = settings
        self.database = database
        self.corpus = corpus
        self.github = github
        self.provider = provider
        self.scripted_fallback = ScriptedProvider()
        self.assembler = ContextAssembler()
        self.verifier = GroundingVerifier()

    async def answer(
        self, visit: Visit, question: str
    ) -> tuple[VerifiedAnswer, str | None, int]:
        if CONTRACTUAL_QUERY.search(question):
            answer = VerifiedAnswer(
                (
                    "I can't negotiate salary, accept an offer, commit to a start date, "
                    "or make a contractual promise for Prathamesh. Please contact him "
                    f"directly at {self.corpus.email}."
                ),
                ["Policy › Representation boundary", "CV › Contact"],
                True,
                True,
            )
            return answer, None, approximate_tokens(question + answer.text)
        if contains_prompt_injection(question):
            answer = VerifiedAnswer(
                (
                    "I treat pasted instructions as untrusted data. I can only answer "
                    "with evidence from Prathamesh's CV and allow-listed GitHub "
                    "repositories."
                ),
                ["Policy › Grounding boundary"],
                True,
                True,
            )
            return answer, None, approximate_tokens(question + answer.text)
        evidence = self.corpus.retrieve(question)
        if GENERIC_OVERVIEW.search(question):
            summaries = [
                item for item in self.corpus.evidence if item.source == "CV › Summary"
            ]
            evidence = list(dict.fromkeys([*summaries, *evidence]))[:8]
        if GITHUB_QUERY.search(question):
            try:
                repos = await self.github.get_repositories()
                evidence = [*self.github.evidence(repos), *evidence][:10]
            except (httpx.HTTPError, TimeoutError, ValueError, KeyError):
                pass
        if not evidence:
            evidence = [
                item
                for item in self.corpus.evidence
                if item.source == "Policy › Grounding boundary"
            ]

        messages = [
            {"role": message.role, "content": message.content}
            for message in self.database.recent_messages(visit.id)
        ]
        confirmed = visit.confirmed_candidate
        context = self.assembler.assemble(
            evidence=evidence,
            messages=messages,
            user_message=question,
            research_status=visit.research_status,
            confirmed_candidate=confirmed,
            confirmed_person_dossier=visit.confirmed_person_dossier,
            confirmed_company_dossier=visit.confirmed_company_dossier,
        )
        request = GenerationRequest(question, context, evidence, confirmed)
        try:
            draft = await self.provider.generate(request)
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError):
            draft = await self.scripted_fallback.generate(request)
        verified = self.verifier.verify(draft, evidence)

        tailored_for = None
        if visit.research_status == "confirmed" and confirmed:
            label = (
                confirmed.get("company")
                or confirmed.get("headline")
                or confirmed.get("name")
            )
            tailored_for = sanitize_external_text(str(label), max_length=100) or None
            if tailored_for and verified.grounded:
                verified = VerifiedAnswer(
                    f"Given the confirmed {tailored_for} context, I’d foreground "
                    "this:\n\n"
                    f"{verified.text}",
                    verified.sources,
                    verified.grounded,
                    verified.refusal,
                )

        used = approximate_tokens(context) + approximate_tokens(verified.text)
        return verified, tailored_for, used


COMMON_REQUIREMENTS = (
    "Python",
    "Java",
    "SQL",
    "Bash",
    "JavaScript",
    "Node.js",
    "GraphQL",
    "C++",
    "Spring Boot",
    "REST APIs",
    "microservices",
    "Kafka",
    "SQS",
    "FastAPI",
    "Docker",
    "GitHub Actions",
    "CI/CD",
    "OpenTelemetry",
    "Datadog",
    "RAG",
    "LLM",
    "MCP",
    "AWS",
    "TypeScript",
    "React",
    "Go",
    "Kubernetes",
    "Terraform",
    "Azure",
    "GCP",
    "Rust",
    "C#",
    ".NET",
    "Django",
    "Flask",
    "PostgreSQL",
    "Redis",
)


class JobFitAnalyzer:
    def __init__(self, corpus: ProfileCorpus) -> None:
        self.corpus = corpus
        self._corpus_text = " ".join(item.text for item in corpus.evidence).casefold()

    def analyze(self, description: str) -> JobFitResponse:
        requested = [
            requirement
            for requirement in COMMON_REQUIREMENTS
            if re.search(rf"(?<!\w){re.escape(requirement)}(?!\w)", description, re.I)
        ]
        matched: list[FitEvidence] = []
        not_evidenced: list[str] = []
        for requirement in requested:
            evidence = self._evidence_for(requirement)
            if evidence:
                matched.append(
                    FitEvidence(
                        requirement=requirement,
                        evidence=evidence.text,
                        source=evidence.source,
                    )
                )
            else:
                not_evidenced.append(requirement)
        total = len(matched) + len(not_evidenced)
        coverage = round(100 * len(matched) / total) if total else 0
        if not requested:
            summary = (
                "I could not identify enough explicit technical requirements to "
                "a useful evidence coverage figure."
            )
        else:
            summary = (
                f"The CV directly evidences {len(matched)} of {total} recognised "
                "requirements in this description."
            )
        return JobFitResponse(
            coverage_percent=coverage,
            matched=matched,
            not_evidenced=not_evidenced,
            summary=summary,
            caveat=(
                "Not evidenced means the supplied CV does not support the claim; it "
                "does not prove Prathamesh lacks the skill. No requirement was "
                "inferred from the job title."
            ),
        )

    def _evidence_for(self, requirement: str) -> EvidenceItem | None:
        aliases = {
            "Node.js": {"node.js"},
            "REST APIs": {"rest", "apis"},
            "RAG": {"rag"},
            "LLM": {"llm"},
            "AWS": {"aws"},
        }
        required = aliases.get(requirement, tokens(requirement))
        for item in self.corpus.evidence:
            if required <= tokens(item.text):
                return item
        return None
