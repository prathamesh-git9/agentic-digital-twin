from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .grounding import DraftAnswer, GenerationRequest
from .profile import EvidenceItem
from .providers import AnswerProvider, ProviderTurn, ScriptedProvider
from .security import approximate_tokens
from .tooling import ToolRegistry, ToolResult, ToolTrace

AgentEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class AgentOutcome:
    draft: DraftAnswer
    evidence: list[EvidenceItem]
    trace: list[ToolTrace]
    extra_token_usage: int
    model_turns: int


class AgentRunner:
    """Bounded OpenAI-compatible tool loop with a deterministic safe fallback."""

    def __init__(
        self,
        *,
        settings: Settings,
        provider: AnswerProvider,
        tools: ToolRegistry,
        fallback: ScriptedProvider | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.tools = tools
        self.fallback = fallback or ScriptedProvider()

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.tool_calling_enabled
            and self.tools.definitions
            and callable(getattr(self.provider, "generate_turn", None))
        )

    async def run(
        self,
        *,
        session_id: str,
        request: GenerationRequest,
        allowed_tools: tuple[str, ...],
        publish: AgentEventCallback | None = None,
    ) -> AgentOutcome:
        if not self.enabled:
            draft = await self.provider.generate(request)
            return AgentOutcome(draft, list(request.evidence), [], 0, 1)

        deadline = time.monotonic() + self.settings.tool_wall_clock_seconds
        definitions = self.tools.definitions_for(allowed_tools)
        continuation: list[dict[str, Any]] = []
        evidence = list(request.evidence)
        trace: list[ToolTrace] = []
        provider_turns = 0
        draft: DraftAnswer | None = None

        for _ in range(self.settings.tool_max_iterations):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                turn = await self._turn(
                    request,
                    continuation=continuation,
                    allow_tools=True,
                    definitions=definitions,
                    time_budget=remaining,
                )
                provider_turns += 1
            except Exception:  # noqa: BLE001 - provider failure falls back safely
                break
            if turn.draft is not None:
                draft = turn.draft
                break
            if not turn.tool_calls:
                break

            continuation.append(turn.assistant_message())
            for call in turn.tool_calls:
                sequence = len(trace) + 1
                _, _, redacted, phrase = self.tools.describe_call(call)
                await _emit(
                    publish,
                    {
                        "type": "tool.call",
                        "sequence": sequence,
                        "call_id": call.id,
                        "tool": call.name,
                        "arguments": redacted,
                        "phrase": phrase,
                    },
                )
                available = deadline - time.monotonic()
                if available <= 0:
                    result = ToolResult(
                        status="timeout",
                        summary="The total tool wall-clock budget was exhausted.",
                    )
                    duration_ms = 0
                else:
                    result, duration_ms = await self.tools.execute(
                        session_id,
                        call,
                        available_seconds=available,
                    )
                source_urls = list(
                    dict.fromkeys(
                        source.url for source in result.sources if source.url is not None
                    )
                )
                item = ToolTrace(
                    sequence=sequence,
                    call_id=call.id,
                    tool=call.name,
                    arguments=redacted,
                    phrase=phrase,
                    status=result.status,
                    duration_ms=duration_ms,
                    summary=result.summary,
                    source_urls=source_urls,
                    cached=result.cached,
                )
                trace.append(item)
                await _emit(
                    publish,
                    {
                        "type": "tool.result",
                        "sequence": sequence,
                        "call_id": call.id,
                        "tool": call.name,
                        "status": result.status,
                        "duration_ms": duration_ms,
                        "summary": result.summary,
                        "source_urls": source_urls,
                        "cached": result.cached,
                    },
                )
                continuation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": self.tools.model_content(result),
                    }
                )
                evidence.extend(result.evidence())

        if draft is None:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                try:
                    turn = await self._turn(
                        request,
                        continuation=continuation,
                        allow_tools=False,
                        definitions=definitions,
                        time_budget=remaining,
                    )
                    provider_turns += 1
                    draft = turn.draft
                except Exception:  # noqa: BLE001 - deterministic fallback is next
                    draft = None

        effective_request = GenerationRequest(
            question=request.question,
            context=request.context,
            evidence=evidence,
            confirmed_candidate=request.confirmed_candidate,
        )
        if draft is None:
            draft = await self.fallback.generate(effective_request)

        continuation_tokens = sum(
            approximate_tokens(json.dumps(message, ensure_ascii=False))
            for message in continuation
        )
        repeated_context = max(0, provider_turns - 1) * approximate_tokens(
            request.context
        )
        return AgentOutcome(
            draft,
            evidence,
            trace,
            continuation_tokens + repeated_context,
            provider_turns,
        )

    async def _turn(
        self,
        request: GenerationRequest,
        *,
        continuation: list[dict[str, Any]],
        allow_tools: bool,
        definitions: list[dict[str, Any]],
        time_budget: float,
    ) -> ProviderTurn:
        generate_turn = self.provider.generate_turn  # type: ignore[attr-defined]
        value = await asyncio.wait_for(
            generate_turn(
                request,
                tools=definitions if allow_tools else [],
                continuation=continuation,
                allow_tools=allow_tools,
            ),
            timeout=max(0.001, time_budget),
        )
        return ProviderTurn.model_validate(value)


async def _emit(callback: AgentEventCallback | None, event: dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        await callback(event)
    except Exception:  # noqa: BLE001 - a disconnected SSE client cannot break chat
        return
