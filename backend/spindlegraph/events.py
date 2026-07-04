"""Project-scoped in-process event bus feeding the WebSocket channel."""
from __future__ import annotations

import asyncio


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue]] = {}

    def subscribe(self, project_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(project_id, set()).add(q)
        return q

    def unsubscribe(self, project_id: int, q: asyncio.Queue) -> None:
        self._subs.get(project_id, set()).discard(q)

    def publish(self, project_id: int, event: dict) -> None:
        for q in self._subs.get(project_id, set()):
            q.put_nowait(event)


bus = EventBus()
