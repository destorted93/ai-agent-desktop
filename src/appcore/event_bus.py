"""Lightweight in-process event bus (GUI-agnostic).

Design goals:
- No PyQt dependency.
- Thread-safe publish from any thread.
- Delivery happens on the consumer's thread via `pump()` (queue-based), which makes it
  safe for UIs (Qt/web/etc.) that require main-thread updates.

Usage:
    from src.runtime_context import Runtime

    unsub = Runtime.get_event_bus().subscribe("vectordb.collections.changed", handler)
    ...
    Runtime.get_event_bus().publish("vectordb.collections.changed", {"name": "foo"})

    # In your main loop / UI timer:
    Runtime.get_event_bus().pump(max_events=50)
"""

from __future__ import annotations

from dataclasses import dataclass
from queue import Queue, Empty
from threading import RLock
from typing import Any, Callable, DefaultDict, Dict, List
from collections import defaultdict


@dataclass(frozen=True)
class Event:
    topic: str
    payload: Any = None


Callback = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._lock = RLock()
        self._subs: DefaultDict[str, List[Callback]] = defaultdict(list)
        self._queue: Queue[Event] = Queue()

    def subscribe(self, topic: str, callback: Callback) -> Callable[[], None]:
        """Subscribe to a topic. Returns an unsubscribe function."""
        with self._lock:
            self._subs[topic].append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subs.get(topic, []):
                    self._subs[topic].remove(callback)

        return unsubscribe

    def publish(self, topic: str, payload: Any = None) -> None:
        """Publish an event from any thread (delivery occurs on pump())."""
        self._queue.put(Event(topic=topic, payload=payload))

    def pump(self, max_events: int = 100) -> int:
        """Deliver up to `max_events` queued events on the caller's thread."""
        delivered = 0
        for _ in range(max_events):
            try:
                event = self._queue.get_nowait()
            except Empty:
                break

            with self._lock:
                callbacks = list(self._subs.get(event.topic, []))

            for cb in callbacks:
                try:
                    cb(event)
                except Exception:
                    # Event handlers should never take down the bus.
                    # If you want logging, we can add it later.
                    pass

            delivered += 1

        return delivered

    def pending_count(self) -> int:
        try:
            return self._queue.qsize()
        except Exception:
            return 0
