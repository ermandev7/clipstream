"""Bus de eventos: los hilos de trabajo publican, los WebSocket reciben."""
from __future__ import annotations

import asyncio
import logging
import threading
import time

from app.core import db

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, type_: str, **data) -> None:
        msg = {"type": type_, "ts": time.time(), **data}
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            loop.call_soon_threadsafe(_put_nowait, q, msg)


def _put_nowait(q: asyncio.Queue, msg: dict) -> None:
    try:
        q.put_nowait(msg)
    except asyncio.QueueFull:
        pass  # cliente lento: se pierde el mensaje, recargará estado al reconectar


bus = EventBus()


# ---- atajos usados por el pipeline ----------------------------------------

def log_event(source_id: int | None, message: str, level: str = "info") -> None:
    db.insert("events", source_id=source_id, ts=time.time(), level=level, message=message)
    getattr(log, "warning" if level == "warn" else level if level in ("info", "error") else "info")("[%s] %s", source_id, message)
    bus.publish("log", source_id=source_id, level=level, message=message)


def source_changed(source_id: int) -> None:
    bus.publish("source", source=db.get("sources", source_id))


def clip_changed(clip_id: int) -> None:
    bus.publish("clip", clip=db.get("clips", clip_id))
