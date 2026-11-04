"""Adapter for the OpenRouter Decisions API (Jev, GPT-6 Luna Decisions, Cloudflare Clef).

POST {base}/api/alpha/decisions
{
  "model": "<model id>",
  "state": { ... },                       # same payload for every model
  "questions": {
    "next_move": {"type": "choice", "instructions": "...", "criteria": {"U": "...", ...}}
  }
}

Response (choice question):
{"answers": {"next_move": {"type": "choice", "choice": "R'", "probabilities": {...}, "confidence": 0.8}}}

The same adapter also talks to a LOCAL Ollama decision model (Clef): Ollama serves decision models on
POST http://127.0.0.1:11434/v1/systemone with the same body and the same answer format (no key needed).

The request goes through the Gateway: cache first, then the request budget, then the network.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from .base import Agent, AgentError, MoveRequest, RawResponse, RetryPolicy
from .contract import build_decision_question
from .http import post_json

QUESTION_KEY = "next_move"


class DecisionsAgent(Agent):
    mode = "live"

    def __init__(self, spec: Any, policy: RetryPolicy, client: httpx.AsyncClient, api_key: str, base_url: str, gateway: Any,
                 path: str = "/api/alpha/decisions", auth: bool = True) -> None:
        super().__init__(spec, policy)
        self.client = client
        self.api_key = api_key
        self.gateway = gateway
        self.auth = auth  # False for local Ollama: no Authorization header at all
        self.url = f"{base_url.rstrip('/')}{path}"

    def build_body(self, req: MoveRequest) -> Dict[str, Any]:
        return {
            "model": self.spec.model_id,
            "state": req.state_payload(),
            "questions": {QUESTION_KEY: build_decision_question(req.allowed_moves)},
        }

    def headers(self) -> Dict[str, str]:
        if not self.auth:
            return {"Content-Type": "application/json"}
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "Rubik's Cube Race (Assignment 02)",
        }

    async def _call(self, req: MoveRequest, timeout_s: float) -> RawResponse:
        body = self.build_body(req)

        async def send():
            return await post_json(self.client, self.url, self.headers(), body, timeout_s)

        fetched = await self.gateway.fetch(self.spec.key, self.spec.model_id, body, send)
        try:
            raw = parse_decisions_response(fetched.data, fetched.live_ms)
        except AgentError as err:
            err.source = fetched.source
            raise
        raw.source, raw.cache_ms = fetched.source, fetched.cache_ms
        raw.cost_usd = fetched.cost_usd
        if isinstance(raw.move, str) and raw.move in req.allowed_moves:
            self.gateway.accept(fetched)  # only valid answers are cached
        return raw


def parse_decisions_response(data: Any, http_ms: Optional[float]) -> RawResponse:
    """Strict parser. Anything unexpected is reported as 'malformed' and counted."""
    if not isinstance(data, dict):
        raise AgentError("malformed", "response is not a JSON object")
    answers = data.get("answers")
    answer = answers.get(QUESTION_KEY) if isinstance(answers, dict) else None
    if not isinstance(answer, dict):
        raise AgentError("malformed", f"no answers.{QUESTION_KEY} object in response")
    if answer.get("type") not in (None, "choice"):
        raise AgentError("malformed", f"answer type is {answer.get('type')!r}, expected 'choice'")
    choice = answer.get("choice")
    if not isinstance(choice, str):
        raise AgentError("malformed", "answer has no 'choice' string")

    probs = answer.get("probabilities")
    if isinstance(probs, dict):
        probs = {str(k): float(v) for k, v in probs.items() if isinstance(v, (int, float))}
    else:
        probs = None
    confidence = answer.get("confidence")
    confidence = float(confidence) if isinstance(confidence, (int, float)) else None
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    cost = usage.get("cost")
    return RawResponse(
        move=choice,
        confidence=confidence,
        probabilities=probs,
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        tokens_in=usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else None,
        tokens_out=usage.get("completion_tokens") if isinstance(usage.get("completion_tokens"), int) else None,
        http_ms=http_ms,
    )
