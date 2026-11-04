"""Adapter for a frontier LLM through OpenRouter chat completions (Claude Haiku 5.5).

POST {base}/api/v1/chat/completions

The model must answer with exactly one JSON object: {"move": "R'"}.
We ask for a JSON schema (strict structured output) so only the 18 allowed
strings can come back. If the route rejects a feature we asked for (structured
output, reasoning, temperature), the adapter switches that one feature off and
sends the same request again. That second request is a REAL request and counts
against the request budget (Claude has only 5), so it is logged like any other.
The answer itself is parsed strictly: if it is not exactly the expected JSON,
it is counted as malformed (no repair).
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import httpx

from .base import Agent, AgentError, MoveRequest, RawResponse, RetryPolicy
from .contract import LLM_SYSTEM_PROMPT, MOVE_JSON_SCHEMA, build_llm_user_prompt
from .http import post_json


MAX_TOKENS_CEILING = 8000  # the answer limit is doubled after a cut-off answer, never above this


class ChatAgent(Agent):
    mode = "live"

    def __init__(
        self,
        spec: Any,
        policy: RetryPolicy,
        client: httpx.AsyncClient,
        api_key: str,
        base_url: str,
        gateway: Any,
        reasoning_effort: str = "",
        max_tokens: int = 0,
        structured: str = "json_schema",
    ) -> None:
        super().__init__(spec, policy)
        self.client = client
        self.api_key = api_key
        self.gateway = gateway
        self.url = f"{base_url.rstrip('/')}/api/v1/chat/completions"
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens
        # features that can be switched off after the route rejects them
        self.use_json_schema = structured == "json_schema"
        self.use_json_object = structured in ("json_schema", "json_object")
        self.use_reasoning = bool(reasoning_effort)
        self.downgrades: list[str] = []

    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "Rubik's Cube Race (Assignment 02)",
        }

    def build_body(self, req: MoveRequest) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": [
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": build_llm_user_prompt(req.state_payload(), req.allowed_moves)},
            ],
            "stream": False,
            "usage": {"include": True},
        }
        if self.use_json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "cube_move", "strict": True, "schema": MOVE_JSON_SCHEMA},
            }
        elif self.use_json_object:
            body["response_format"] = {"type": "json_object"}
        if self.use_reasoning:
            body["reasoning"] = {"effort": self.reasoning_effort}
        else:
            body["temperature"] = 0  # temperature is only sent when reasoning is off
        if self.max_tokens > 0:
            body["max_tokens"] = self.max_tokens
        return body

    def _downgrade(self, detail: str) -> bool:
        """Switch off one unsupported feature. Returns True if something was changed."""
        text = detail.lower()
        if any(w in text for w in ("response_format", "json_schema", "structured")):
            if self.use_json_schema:
                self.use_json_schema = False
                self.downgrades.append("json_schema -> json_object")
                return True
            if self.use_json_object:
                self.use_json_object = False
                self.downgrades.append("json_object -> plain text")
                return True
        if any(w in text for w in ("reasoning", "thinking", "temperature")) and self.use_reasoning:
            self.use_reasoning = False
            self.downgrades.append("reasoning off")
            return True
        return False

    async def _call(self, req: MoveRequest, timeout_s: float) -> RawResponse:
        fetched = None
        for _ in range(4):
            body = self.build_body(req)

            async def send(body=body):
                return await post_json(self.client, self.url, self.headers(), body, timeout_s)

            try:
                fetched = await self.gateway.fetch(self.spec.key, self.spec.model_id, body, send)
                break
            except AgentError as err:
                if err.status == 400 and self._downgrade(err.detail):
                    continue  # same request, one unsupported feature removed (a new, counted request)
                raise
        if fetched is None:
            raise AgentError("http_error", "route rejected the request after removing optional features", status=400)
        try:
            raw = parse_chat_response(fetched.data, fetched.live_ms)
        except AgentError as err:
            err.source = fetched.source
            # Cut off by the token limit: the same request would be cut off again (temperature 0),
            # so the retry gets a bigger limit instead of wasting one of the few Claude requests.
            if "finish_reason=length" in err.detail and self.max_tokens > 0 and self.max_tokens < MAX_TOKENS_CEILING:
                self.max_tokens = min(self.max_tokens * 2, MAX_TOKENS_CEILING)
                self.downgrades.append(f"max_tokens -> {self.max_tokens}")
            raise
        raw.source, raw.cache_ms = fetched.source, fetched.cache_ms
        raw.cost_usd = fetched.cost_usd
        if self.downgrades:
            raw.notes = "; ".join(self.downgrades)
        if isinstance(raw.move, str) and raw.move in req.allowed_moves:
            self.gateway.accept(fetched)
        return raw


def parse_chat_response(data: Any, http_ms: Optional[float]) -> RawResponse:
    if not isinstance(data, dict):
        raise AgentError("malformed", "response is not a JSON object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        err = data.get("error")
        raise AgentError("malformed", f"no choices in response{': ' + str(err)[:200] if err else ''}")
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message.get("content")
    if choice.get("finish_reason") == "length":
        raise AgentError("malformed", "answer was cut off (finish_reason=length)")
    if not isinstance(content, str) or not content.strip():
        raise AgentError("malformed", "empty answer (no message content)")
    try:
        parsed = json.loads(content)
    except ValueError as exc:
        raise AgentError("malformed", f"answer is not valid JSON: {content[:120]!r}") from exc
    if not isinstance(parsed, dict) or "move" not in parsed:
        raise AgentError("malformed", f"JSON has no 'move' key: {content[:120]!r}")

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    cost = usage.get("cost")
    return RawResponse(
        move=parsed["move"],
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        tokens_in=usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else None,
        tokens_out=usage.get("completion_tokens") if isinstance(usage.get("completion_tokens"), int) else None,
        http_ms=http_ms,
    )
