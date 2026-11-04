"""A tiny event bus: the race runner emits events, the browser reads them with SSE.

All events are kept in a list, so a browser that connects late (or reloads the
page) can replay everything from the start.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict, List, Optional


class EventBus:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self.finished = False
        self._waiter: Optional[asyncio.Event] = None

    def emit(self, type_: str, **data: Any) -> Dict[str, Any]:
        event = {"seq": len(self.events), "type": type_, "ts": time.time(), **data}
        self.events.append(event)
        if self._waiter is not None:
            old, self._waiter = self._waiter, None
            old.set()
        return event

    def close(self) -> None:
        self.finished = True
        if self._waiter is not None:
            old, self._waiter = self._waiter, None
            old.set()

    async def stream(self, start: int = 0, keepalive_s: float = 15.0) -> AsyncIterator[Optional[Dict[str, Any]]]:
        """Yield events from index ``start``. Yields None as a keep-alive when nothing happens."""
        index = start
        while True:
            while index < len(self.events):
                yield self.events[index]
                index += 1
            if self.finished:
                return
            if self._waiter is None:
                self._waiter = asyncio.Event()
            waiter = self._waiter
            try:
                await asyncio.wait_for(waiter.wait(), timeout=keepalive_s)
            except asyncio.TimeoutError:
                yield None
