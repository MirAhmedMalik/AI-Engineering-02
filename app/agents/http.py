"""Small HTTP helper shared by the live adapters."""
from __future__ import annotations

import time
from typing import Any, Dict, Tuple

import httpx

from .base import AgentError

RETRYABLE_STATUS = {408, 409, 425, 429}


def error_message(resp: httpx.Response) -> str:
    """Pull a readable message out of an OpenRouter error body."""
    try:
        data = resp.json()
        err = data.get("error", data) if isinstance(data, dict) else data
        if isinstance(err, dict):
            msg = err.get("message") or str(err)
            meta = err.get("metadata")
            if isinstance(meta, dict) and meta.get("raw"):
                msg = f"{msg} | {str(meta['raw'])[:200]}"
            return str(msg)[:400]
        return str(err)[:400]
    except Exception:
        return (resp.text or "")[:400]


def retry_after_seconds(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return min(float(raw), 30.0)
    except ValueError:
        return None


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
    timeout_s: float,
) -> Tuple[Any, float]:
    """POST JSON. Returns (parsed JSON, http latency in ms). Raises AgentError on any problem."""
    t0 = time.perf_counter()
    try:
        # httpx timeout is a little larger than ours: asyncio.wait_for in Agent.decide is the real limit
        resp = await client.post(url, json=body, headers=headers, timeout=timeout_s + 2)
    except httpx.TimeoutException as exc:
        raise AgentError("timeout", f"HTTP timeout ({exc.__class__.__name__})") from exc
    except httpx.HTTPError as exc:
        raise AgentError("transport", f"{exc.__class__.__name__}: {exc}"[:300]) from exc
    http_ms = (time.perf_counter() - t0) * 1000

    if resp.status_code != 200:
        retryable = resp.status_code in RETRYABLE_STATUS or resp.status_code >= 500
        raise AgentError(
            "http_error",
            f"HTTP {resp.status_code}: {error_message(resp)}",
            status=resp.status_code,
            retryable=retryable,
            retry_after=retry_after_seconds(resp),
        )
    try:
        return resp.json(), http_ms
    except ValueError as exc:
        raise AgentError("malformed", "response body is not valid JSON") from exc
