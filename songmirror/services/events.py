"""EventBus — fan-out of engine log events to SSE subscribers.

The engine emits `logs.Event`s from *worker threads* (one-way passes run one
thread per target). `asyncio.Queue` is not thread-safe, so every publish is
marshalled onto the event loop with `call_soon_threadsafe`. A small ring buffer
lets a late/reconnecting browser backfill the current pass.

Services tier: it imports the leaf `logs` module (never the reverse).
"""

import asyncio
from collections import deque

from ..engine.logs import Event, set_sink


class EventBus:
    def __init__(self, ring: int = 500):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs: set[asyncio.Queue] = set()
        self._ring: deque[Event] = deque(maxlen=ring)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the running loop at app startup; publishes route through it."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def recent(self) -> list[Event]:
        return list(self._ring)

    def publish(self, e: Event) -> None:
        """Thread-safe: callable from any worker thread. No-op until a loop is
        bound (e.g. a pass running before the web app started)."""
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._deliver, e)

    def _deliver(self, e: Event) -> None:
        # Runs on the loop thread — the only place _subs/_ring are mutated.
        self._ring.append(e)
        for q in self._subs:
            try:
                q.put_nowait(e)
            except asyncio.QueueFull:
                pass  # a slow client drops events rather than blocking the sync

    def attach_to_logs(self) -> None:
        """Route every engine log event into this bus."""
        set_sink(self.publish)
