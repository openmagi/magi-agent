from __future__ import annotations

import asyncio
from collections import deque


class _Subscription:
    """Async iterator over a subscriber queue. `aclose()` always unregisters
    the queue from the bus, even if the subscription was never iterated."""

    def __init__(self, bus: "ActivityBus", q: asyncio.Queue, channel: str) -> None:
        self._bus = bus
        self._q = q
        self._channel = channel
        self._closed = False

    def __aiter__(self) -> "_Subscription":
        return self

    async def __anext__(self) -> dict:
        while True:
            ev = await self._q.get()
            if self._channel == "*" or ev.get("session_id") == self._channel:
                return ev

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            self._bus._subscribers.discard(self._q)


class ActivityBus:
    """In-process pub/sub. New subscribers replay the recent ring buffer, then
    receive live events. Channel '*' = all; otherwise matches session_id.
    Assumes a single asyncio event loop; publish/subscribe are not safe to call across threads or loops."""

    def __init__(self, *, replay: int = 200, subscriber_queue: int = 1000) -> None:
        self._recent: deque[dict] = deque(maxlen=replay)
        self._subscribers: set[asyncio.Queue] = set()
        self._subscriber_queue = subscriber_queue
        self._lock = asyncio.Lock()

    async def publish(self, event: dict) -> None:
        self._recent.append(event)
        async with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def subscribe(self, *, channel: str = "*") -> _Subscription:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._subscriber_queue)
        for ev in list(self._recent):
            if channel == "*" or ev.get("session_id") == channel:
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    break
        self._subscribers.add(q)
        return _Subscription(self, q, channel)
