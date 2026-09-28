"""In-process publish/subscribe bus for alert lifecycle events.

The frontend has no SSE/WebSocket support today, so this is deliberately
minimal: a bounded fan-out of alert events to any number of connected
`/api/alerts/stream` clients. Events are transient (not replayed) -- the
durable record lives in `alert_store` and the frontend reconciles with
`GET /api/alerts` on connect, so a dropped frame only costs a refresh.
"""

import asyncio
import threading
from collections import deque
from datetime import datetime, timezone

MAX_QUEUE = 100

_lock = threading.Lock()
_subscribers: set[asyncio.Queue] = set()
_recent: deque = deque(maxlen=20)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def publish(event_type: str, payload: dict) -> dict:
    """Fan an event out to all connected stream clients."""
    event = {"type": event_type, "timestamp": _now(), "data": payload}

    with _lock:
        _recent.append(event)
        targets = list(_subscribers)

    for queue in targets:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # Slow consumer: drop rather than block the alert webhook.
            pass

    return event


def subscribe() -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
    with _lock:
        _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    with _lock:
        _subscribers.discard(queue)


def recent() -> list:
    """Recent events, used to prime a newly connected client."""
    with _lock:
        return list(_recent)
