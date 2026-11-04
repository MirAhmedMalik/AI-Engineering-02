"""Adapter for local models served by Ollama (Clef, Qwen, DeepSeek, Gemma, ...).

POST {base}/api/chat   (default http://127.0.0.1:11434)

It uses the same system prompt, the same state and the same 18 allowed moves as
the Claude adapter, and asks Ollama for structured output (a JSON schema with an
enum of the 18 moves) so the model can only answer with a valid move string.
The reply must be exactly {"move": "R'"}; anything else is counted as malformed.

Local requests are free, so they have no request budget. They still go through
the cache (a second run is instant) and they are counted and logged.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import httpx

from .base import Agent, AgentError, MoveRequest, RawResponse, RetryPolicy
from .contract import LLM_SYSTEM_PROMPT, MOVE_JSON_SCHEMA, build_llm_user_prompt
from .http import post_json


class OllamaAgent(Agent):
    mode = "live"

    def __init__(self, spec: Any, policy: RetryPolicy, client: httpx.AsyncClient, base_url: str, gateway: Any, think: str = "false") -> None:
        super().__init__(spec, policy)
        self.client = client
        self.gateway = gateway
        self.url = f"{base_url.rstrip('/')}/api/chat"
        self.think = think  # "false" | "true" | "" (do not send the field)
        self.downgrades: list[str] = []

    def build_body(self, req: MoveRequest) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": [
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": build_llm_user_prompt(req.state_payload(), req.allowed_moves)},
            ],
            "stream": False,
            "format": MOVE_JSON_SCHEMA,
            "options": {"temperature": 0, "seed": 0},
        }
        if self.think in ("true", "false"):
            body["think"] = self.think == "true"
        return body

    async def _call(self, req: MoveRequest, timeout_s: float) -> RawResponse:
        fetched = None
        for _ in range(3):
            body = self.build_body(req)

            async def send(body=body):
                return await post_json(self.client, self.url, {"Content-Type": "application/json"}, body, timeout_s)

            try:
                fetched = await self.gateway.fetch(self.spec.key, self.spec.model_id, body, send)
                break
            except AgentError as err:
                # some models do not know the "think" option: send the same request without it
                if err.status == 400 and "think" in err.detail.lower() and self.think:
                    self.think = ""
                    self.downgrades.append("think option removed")
                    continue
                raise
        if fetched is None:
            raise AgentError("http_error", "Ollama rejected the request", status=400)
        try:
            raw = parse_ollama_response(fetched.data, fetched.live_ms)
        except AgentError as err:
            err.source = fetched.source
            raise
        raw.source, raw.cache_ms = fetched.source, fetched.cache_ms
        raw.cost_usd = 0.0
        if self.downgrades:
            raw.notes = "; ".join(self.downgrades)
        if isinstance(raw.move, str) and raw.move in req.allowed_moves:
            self.gateway.accept(fetched)
        return raw


def parse_ollama_response(data: Any, http_ms: Optional[float]) -> RawResponse:
    if not isinstance(data, dict):
        raise AgentError("malformed", "response is not a JSON object")
    if data.get("error"):
        raise AgentError("http_error", f"Ollama error: {str(data['error'])[:200]}", retryable=False)
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise AgentError("malformed", "empty answer (no message content)")
    try:
        parsed = json.loads(content)
    except ValueError as exc:
        raise AgentError("malformed", f"answer is not valid JSON: {content[:120]!r}") from exc
    if not isinstance(parsed, dict) or "move" not in parsed:
        raise AgentError("malformed", f"JSON has no 'move' key: {content[:120]!r}")
    return RawResponse(
        move=parsed["move"],
        tokens_in=data.get("prompt_eval_count") if isinstance(data.get("prompt_eval_count"), int) else None,
        tokens_out=data.get("eval_count") if isinstance(data.get("eval_count"), int) else None,
        http_ms=http_ms,
    )
