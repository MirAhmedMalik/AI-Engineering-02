"""Common agent logic: one retry/timeout policy for every model.

An agent answers one question: "given this state, which move next?". Child
classes only build their request and parse the answer (``_call``). Everything
else - timeouts, retries, validation of the move, logging of bad answers,
request budget - is done here, so all models are treated in the same way.

Rules (same for every model):
  * every request has a timeout (``request_timeout_s``)
  * a failed or invalid answer is retried, up to ``max_attempts`` in total,
    with exponential back-off. Every real request counts against the budget.
  * an answer is only used if it is one of the allowed moves, exactly as
    written. We never "repair" an answer (for example 'r' -> 'R').
  * every failed attempt is logged (kind, detail, latency, live or cache)
  * if the request budget is used up, the model has no move: the trial ends
  * if all attempts fail, the model has no move: the trial ends as ERROR
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..store import BudgetExceeded
from .contract import build_state_payload


class TrialTimeout(Exception):
    """The whole trial ran out of time."""


class AgentError(Exception):
    """A single attempt failed. ``kind`` is one of: timeout, http_error, transport, malformed."""

    def __init__(
        self,
        kind: str,
        detail: str = "",
        status: Optional[int] = None,
        retryable: bool = True,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after
        self.source = "live"  # set to "cache" by the adapter if the bad answer came from the cache


@dataclass
class RetryPolicy:
    max_attempts: int = 2
    request_timeout_s: float = 90.0
    backoff_s: float = 1.0


@dataclass
class MoveRequest:
    facelets: str
    recent_moves: List[str]
    moves_made: int
    move_limit: int
    allowed_moves: List[str]

    def state_payload(self) -> dict:
        return build_state_payload(self.facelets, self.recent_moves, self.moves_made, self.move_limit)


@dataclass
class RawResponse:
    """What an adapter returns for one successful call (move not yet validated)."""

    move: Any
    confidence: Optional[float] = None
    probabilities: Optional[Dict[str, float]] = None
    cost_usd: Optional[float] = None  # money spent by this call (0 for a cache hit)
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    http_ms: Optional[float] = None  # LIVE latency (for a cache hit: the one measured when it was first made)
    notes: Optional[str] = None
    source: str = "live"  # "live" | "cache"
    cache_ms: Optional[float] = None  # time to answer from the cache


@dataclass
class AttemptLog:
    attempt: int
    ok: bool
    latency_ms: float
    kind: Optional[str] = None
    detail: str = ""
    http_status: Optional[int] = None
    source: str = "live"

    def to_dict(self) -> dict:
        return {
            "attempt": self.attempt,
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 1),
            "kind": self.kind,
            "detail": self.detail[:300],
            "http_status": self.http_status,
            "source": self.source,
        }


@dataclass
class MoveDecision:
    move: Optional[str]
    latency_ms: float  # live latency of the call that produced the accepted move
    decision_ms: float  # wall time for the whole decision (retries and back-off included)
    attempts: List[AttemptLog] = field(default_factory=list)
    confidence: Optional[float] = None
    probabilities: Optional[Dict[str, float]] = None
    cost_usd: Optional[float] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    error: Optional[str] = None
    source: str = "live"
    cache_ms: Optional[float] = None
    budget_exhausted: bool = False

    @property
    def failed_attempts(self) -> List[AttemptLog]:
        return [a for a in self.attempts if not a.ok]


class Agent:
    """Base class. ``spec`` describes the model."""

    mode = "live"

    def __init__(self, spec: Any, policy: RetryPolicy) -> None:
        self.spec = spec
        self.policy = policy

    async def prepare(self, puzzle: Any) -> None:  # hook, used by the simulated agents only
        return None

    async def _call(self, req: MoveRequest, timeout_s: float) -> RawResponse:  # pragma: no cover
        raise NotImplementedError

    async def aclose(self) -> None:
        return None

    async def decide(
        self,
        req: MoveRequest,
        deadline: Optional[float] = None,
        on_attempt: Optional[Callable[[AttemptLog], None]] = None,
    ) -> MoveDecision:
        pol = self.policy
        t_start = time.perf_counter()
        attempts: List[AttemptLog] = []
        last_error = "no attempt was made"

        for attempt in range(1, pol.max_attempts + 1):
            remaining = None if deadline is None else deadline - time.perf_counter()
            if remaining is not None and remaining <= 0:
                raise TrialTimeout()
            limit = pol.request_timeout_s if remaining is None else min(pol.request_timeout_s, remaining)
            deadline_bound = remaining is not None and remaining <= pol.request_timeout_s

            t0 = time.perf_counter()
            retryable = True
            retry_after: Optional[float] = None
            try:
                raw = await asyncio.wait_for(self._call(req, limit), timeout=limit)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                latency_ms = raw.http_ms if raw.http_ms is not None else elapsed_ms
                if isinstance(raw.move, str) and raw.move in req.allowed_moves:
                    log = AttemptLog(attempt, True, latency_ms, source=raw.source)
                    attempts.append(log)
                    if on_attempt:
                        on_attempt(log)
                    return MoveDecision(
                        move=raw.move,
                        latency_ms=latency_ms,
                        decision_ms=(time.perf_counter() - t_start) * 1000,
                        attempts=attempts,
                        confidence=raw.confidence,
                        probabilities=raw.probabilities,
                        cost_usd=raw.cost_usd,
                        tokens_in=raw.tokens_in,
                        tokens_out=raw.tokens_out,
                        source=raw.source,
                        cache_ms=raw.cache_ms,
                    )
                log = AttemptLog(
                    attempt, False, latency_ms, "invalid_move",
                    f"model returned {raw.move!r}, which is not an allowed move", source=raw.source,
                )
            except BudgetExceeded as exc:
                return MoveDecision(
                    move=None,
                    latency_ms=0.0,
                    decision_ms=(time.perf_counter() - t_start) * 1000,
                    attempts=attempts,
                    error=f"request budget: {exc.reason}",
                    budget_exhausted=True,
                )
            except asyncio.TimeoutError:
                if deadline_bound:
                    raise TrialTimeout()
                log = AttemptLog(attempt, False, (time.perf_counter() - t0) * 1000, "timeout",
                                 f"no answer within {limit:.0f} s")
            except AgentError as err:
                if err.kind == "timeout" and deadline_bound:
                    raise TrialTimeout()
                log = AttemptLog(attempt, False, (time.perf_counter() - t0) * 1000, err.kind, err.detail, err.status,
                                 source=err.source)
                retryable = err.retryable
                retry_after = err.retry_after

            attempts.append(log)
            last_error = f"{log.kind}: {log.detail}"
            if on_attempt:
                on_attempt(log)
            if not retryable or attempt == pol.max_attempts:
                break

            delay = max(retry_after or 0.0, pol.backoff_s * (2 ** (attempt - 1)))
            if deadline is not None:
                delay = min(delay, max(0.0, deadline - time.perf_counter()))
            if delay > 0:
                await asyncio.sleep(delay)

        return MoveDecision(
            move=None,
            latency_ms=attempts[-1].latency_ms if attempts else 0.0,
            decision_ms=(time.perf_counter() - t_start) * 1000,
            attempts=attempts,
            error=last_error,
        )
