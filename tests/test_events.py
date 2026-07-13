"""EventBus bridges worker-thread publishes to async subscribers."""

import asyncio
import threading

from omni_sync.services.events import EventBus
from omni_sync.engine.logs import Event


def test_publish_from_worker_thread_reaches_subscriber():
    async def scenario():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        q = bus.subscribe()
        threading.Thread(
            target=lambda: bus.publish(Event(0.0, "add", "apple", "x"))
        ).start()
        e = await asyncio.wait_for(q.get(), 2.0)
        assert e.kind == "add" and e.tag == "apple"
        bus.unsubscribe(q)

    asyncio.run(scenario())


def test_recent_ring_backfills_late_subscriber():
    async def scenario():
        bus = EventBus(ring=3)
        bus.bind_loop(asyncio.get_running_loop())
        for i in range(5):
            bus.publish(Event(float(i), "note", "sync", str(i)))
        await asyncio.sleep(0.05)  # let the scheduled deliveries drain
        assert [e.message for e in bus.recent()] == ["2", "3", "4"]

    asyncio.run(scenario())


def test_publish_without_loop_is_noop():
    EventBus().publish(Event(0.0, "add", "x", "y"))  # must not raise
