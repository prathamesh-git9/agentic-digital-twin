from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any


class EventHub:
    """Session-scoped, intentionally ephemeral SSE fan-out."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(
            set
        )
        self._latest: dict[str, dict[str, Any]] = {}

    async def publish(
        self, session_id: str, event: dict[str, Any], *, retain: bool = True
    ) -> None:
        if retain:
            self._latest[session_id] = event
        for queue in tuple(self._subscribers.get(session_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A slow browser must not retain private research indefinitely.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def latest(self, session_id: str) -> dict[str, Any]:
        return self._latest.get(
            session_id,
            {
                "type": "research",
                "status": "idle",
                "disclosure": (
                    "Public-source research is optional and off until you share a name."
                ),
            },
        )

    async def stream(self, session_id: str) -> AsyncIterator[str]:
        # Eight bounded rounds can legally produce 128 call/result frames. Keep one
        # complete turn plus its plan/phase frames while retaining ephemeral purge
        # semantics for disconnected clients.
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=144)
        self._subscribers[session_id].add(queue)
        try:
            yield self._encode(self.latest(session_id))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield self._encode(event)
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            self._subscribers[session_id].discard(queue)
            if not self._subscribers[session_id]:
                self._subscribers.pop(session_id, None)

    def purge(self, session_id: str) -> None:
        self._latest.pop(session_id, None)
        self._subscribers.pop(session_id, None)

    @staticmethod
    def _encode(event: dict[str, Any]) -> str:
        return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event)}\n\n"
