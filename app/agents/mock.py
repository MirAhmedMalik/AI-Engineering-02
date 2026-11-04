"""SIMULATED agents, only for practising without spending any real request.

These are NOT models and their numbers say nothing about the real models. They
let you check the game UI, the benchmark runner, the statistics, the report
generator AND the request rules (cache, per-model limits, total limit, restart
persistence) before you use a single real request.

A simulated agent sends its requests through the same Gateway as the real
adapters, into its own folder (``data/simulated``), with the same limits as the
real OpenRouter run (Claude 5, decision models 20, total 65). So you can see in
practice what happens when a limit is reached. The real counters are never touched.

Every trial made by a simulated agent is stored with ``"mode": "simulated"`` and
the report generator ignores such runs unless you force it.

A simulated agent follows a known solution with some probability, otherwise it
plays a random move and then has to undo it. It waits a random time before
answering, so the latency columns also show something.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..cube.engine import invert_move
from .base import Agent, MoveRequest, RawResponse, RetryPolicy
from .chat import parse_chat_response
from .decisions import QUESTION_KEY, parse_decisions_response


@dataclass(frozen=True)
class MockProfile:
    latency_min_s: float
    latency_max_s: float
    p_follow: float  # chance to play the next move of the known solution
    p_invalid: float  # chance to return something that is not a move


MOCK_PROFILES: Dict[str, MockProfile] = {
    "jev": MockProfile(0.12, 0.35, 0.72, 0.02),
    "luna": MockProfile(0.10, 0.30, 0.80, 0.00),
    "clef": MockProfile(0.06, 0.20, 0.62, 0.00),
    "opus": MockProfile(2.0, 6.0, 0.97, 0.00),
}
DEFAULT_PROFILE = MockProfile(0.1, 0.3, 0.7, 0.0)


class MockAgent(Agent):
    mode = "simulated"

    def __init__(self, spec: Any, policy: RetryPolicy, gateway: Any, latency_scale: float = 1.0, profile: Optional[MockProfile] = None) -> None:
        super().__init__(spec, policy)
        self.gateway = gateway
        self.profile = profile or MOCK_PROFILES.get(spec.key, DEFAULT_PROFILE)
        self.latency_scale = latency_scale
        self._plan: List[str] = []
        self._rng = random.Random(0)
        self._trial_counter = 0

    async def prepare(self, puzzle: Any) -> None:
        self._trial_counter += 1
        self._plan = list(puzzle.reference_solution)
        # the same puzzle always gives the same simulated model: a second run is a clean cache replay
        self._rng = random.Random(f"{puzzle.seed}:{self.spec.key}")

    def _fake_response(self, move: str, allowed: List[str]) -> Dict[str, Any]:
        if self.spec.interface == "chat":
            import json as _json
            return {"choices": [{"message": {"content": _json.dumps({"move": move})}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 900, "completion_tokens": 8}}
        confidence = round(self._rng.uniform(0.35, 0.95), 3)
        rest = (1.0 - confidence) / (len(allowed) - 1)
        probs = {m: (confidence if m == move else round(rest, 4)) for m in allowed}
        return {"answers": {QUESTION_KEY: {"type": "choice", "choice": move, "probabilities": probs, "confidence": confidence}}}

    async def _call(self, req: MoveRequest, timeout_s: float) -> RawResponse:
        p = self.profile
        body = {"simulated": True, "model": self.spec.model_id, "state": req.state_payload(), "allowed": req.allowed_moves}

        async def send():
            latency = self._rng.uniform(p.latency_min_s, p.latency_max_s) * self.latency_scale
            if latency > 0:
                await asyncio.sleep(latency)
            if self._rng.random() < p.p_invalid:
                move = "X9"
            elif self._plan and self._rng.random() < p.p_follow:
                move = self._plan[0]
            else:
                move = self._rng.choice(req.allowed_moves)
            return self._fake_response(move, req.allowed_moves), latency * 1000

        fetched = await self.gateway.fetch(self.spec.key, self.spec.model_id, body, send)
        parser = parse_chat_response if self.spec.interface == "chat" else parse_decisions_response
        raw = parser(fetched.data, fetched.live_ms)
        raw.source, raw.cache_ms = fetched.source, fetched.cache_ms
        raw.cost_usd = 0.0
        if isinstance(raw.move, str) and raw.move in req.allowed_moves:
            self.gateway.accept(fetched)
            if self._plan and raw.move == self._plan[0]:
                self._plan.pop(0)
            else:
                self._plan.insert(0, invert_move(raw.move))
        return raw
