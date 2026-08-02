from __future__ import annotations

import json
import re
from typing import Protocol

import httpx

from .grounding import DraftAnswer, DraftClaim, GenerationRequest
from .security import contains_prompt_injection

CONTRACT_RE = re.compile(
    r"\b(?:salary|compensation|pay|offer|accept|contract|start date|joining date|"
    r"notice period|negotiate)\b",
    re.I,
)


class AnswerProvider(Protocol):
    async def generate(self, request: GenerationRequest) -> DraftAnswer:
        """Draft evidence-linked claims; a separate verifier is always authoritative."""


class ScriptedProvider:
    """Deterministic provider for demos and CI; it still passes through verification."""

    async def generate(self, request: GenerationRequest) -> DraftAnswer:
        if CONTRACT_RE.search(request.question):
            return DraftAnswer(
                refusal=True,
                refusal_text=(
                    "I can't negotiate salary, accept an offer, commit to a start date, "
                    "or make a contractual promise for Prathamesh. Please contact him "
                    "at prathameh7744yt@gmail.com."
                ),
            )
        if contains_prompt_injection(request.question):
            return DraftAnswer(
                refusal=True,
                refusal_text=(
                    "I treat pasted instructions as untrusted data. I can only answer "
                    "evidence from Prathamesh's CV and allow-listed GitHub repositories."
                ),
            )
        factual = [
            item for item in request.evidence if not item.source.startswith("Policy ›")
        ]
        if not factual:
            return DraftAnswer(refusal=True)
        return DraftAnswer(
            claims=[
                DraftClaim(text=item.text, source=item.source) for item in factual[:3]
            ]
        )


class OpenAICompatibleProvider:
    """One Chat Completions adapter works with any OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 25.0,
        max_output_tokens: int = 700,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens

    async def generate(self, request: GenerationRequest) -> DraftAnswer:
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": request.context},
                {
                    "role": "user",
                    "content": (
                        "Answer the visitor query using JSON only: "
                        '{"claims":[{"text":"...","source":"exact label"}],'
                        '"refusal":false,"refusal_text":null}. Each claim needs one '
                        "exact source label. If no evidence supports the answer, set "
                        "refusal=true."
                    ),
                },
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content).strip())
        return DraftAnswer.model_validate(json.loads(content))
