"""The one door to a model API: cache first, then the request budget, then the network.

Every live adapter (OpenRouter decisions, OpenRouter chat, Ollama) and the
simulated adapter send their requests through ``Gateway.fetch``. That is how the
rules are enforced in one place:

    1. look in the cache            -> hit: return it, no request, no budget used
    2. reserve one request (counted and saved to disk BEFORE sending)
                                    -> limit used up: BudgetExceeded, nothing is sent
    3. log "request_start"
    4. send the request, measure the LIVE latency
    5. log "request_end"; the response is cached only after the adapter accepted it
       (``accept``), so invalid answers are never cached and a retry is a real retry
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from .agents.base import AgentError
from .store import BudgetExceeded, UsageStore, cache_key

Send = Callable[[], Awaitable[Tuple[Any, float]]]


@dataclass
class Fetched:
    data: Any
    live_ms: float  # latency of the real request (for a cache hit: the one measured when it was first made)
    source: str  # "live" | "cache"
    cache_ms: Optional[float]  # time to answer from the cache (None for live)
    cost_usd: float  # money spent by THIS call (0 for a cache hit)
    key: str
    model_key: str
    model_id: str
    raw_cost: Optional[float] = None


def extract_cost(data: Any) -> Optional[float]:
    if isinstance(data, dict) and isinstance(data.get("usage"), dict):
        c = data["usage"].get("cost")
        if isinstance(c, (int, float)):
            return float(c)
    return None


class Gateway:
    def __init__(self, store: UsageStore, backend: str, on_usage: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self.store = store
        self.backend = backend
        self.on_usage = on_usage

    def _notify(self) -> None:
        if self.on_usage:
            try:
                self.on_usage(self.store.snapshot())
            except Exception:  # a display problem must never break a trial
                pass

    async def fetch(self, model_key: str, model_id: str, body: Any, send: Send) -> Fetched:
        t0 = time.perf_counter()
        key = cache_key(self.backend, model_id, body)

        entry = self.store.cache_get(key)  # 1. cache first
        if entry is not None:
            self.store.note_cache_hit(model_key, model_id)
            cache_ms = (time.perf_counter() - t0) * 1000
            self.store.log("cache_hit", model_key=model_key, model_id=model_id, cache_ms=round(cache_ms, 3), original_live_ms=entry.get("live_ms"))
            self._notify()
            return Fetched(entry["data"], float(entry.get("live_ms") or 0.0), "cache", cache_ms, 0.0, key, model_key, model_id, entry.get("cost_usd"))

        number = self.store.reserve(model_key, model_id)  # 2 + 3. counted and logged before the request leaves
        self._notify()
        t_send = time.perf_counter()
        try:
            data, live_ms = await send()  # 4. the real request
        except AgentError as err:
            err.source = "live"
            self.store.finish(model_key, number, False, (time.perf_counter() - t_send) * 1000, None, err.kind, err.detail)
            self._notify()
            raise
        except asyncio.CancelledError:  # timeout of the attempt: the request was sent and cannot be called back
            self.store.finish(model_key, number, False, (time.perf_counter() - t_send) * 1000, None, "timeout", "attempt cancelled")
            self._notify()
            raise
        except Exception as exc:  # any bug in an adapter still leaves a correct count
            self.store.finish(model_key, number, False, (time.perf_counter() - t_send) * 1000, None, "error", repr(exc))
            self._notify()
            raise

        cost = extract_cost(data)
        self.store.finish(model_key, number, True, live_ms, cost)  # 5.
        self._notify()
        return Fetched(data, live_ms, "live", None, cost or 0.0, key, model_key, model_id, cost)

    def accept(self, fetched: Fetched) -> None:
        """Called by the adapter when the answer was valid: only then it is cached."""
        if fetched.source == "live":
            self.store.cache_put(fetched.key, fetched.model_key, fetched.model_id, fetched.data, fetched.live_ms, fetched.raw_cost)
