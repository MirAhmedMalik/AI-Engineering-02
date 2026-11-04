"""Persistent response cache, request counters and request log.

Everything lives in one folder per backend (``data/openrouter``, ``data/ollama``,
``data/simulated``):

    cache.jsonl       one JSON object per line: a cached model response
    usage.json        request counters (total, per model), cache hits, cost
    request_log.jsonl a line BEFORE every real request is sent ("request_start")
                      and a line when it ends ("request_end")

Rules (from the teacher's e-mail):
  * the cache is checked before every request
  * counters and cache are NOT reset when the app restarts: they are read from
    disk at start-up and written to disk before a request is sent
  * hard limits are enforced here, in the backend: a request that would break a
    limit is refused (``BudgetExceeded``) and never sent
  * live API latency is stored apart from cached response time
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


class BudgetExceeded(Exception):
    """A request was refused because a request limit is used up (or usage could not be saved)."""

    def __init__(self, reason: str, model_key: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.model_key = model_key


class StoreError(Exception):
    """The usage file is damaged. We stop instead of guessing, so counters can never silently reset."""


@dataclass
class Limits:
    per_model: Dict[str, int] = field(default_factory=dict)
    total: Optional[int] = None
    max_cost_usd: Optional[float] = None  # hard money cap (None = no cap)
    cost_estimate: Dict[str, float] = field(default_factory=dict)  # worst-case cost of ONE request, per model


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="milliseconds")


def cache_key(backend: str, model_id: str, body: Any) -> str:
    """Same backend + same model + same request body = same key."""
    raw = json.dumps({"backend": backend, "model": model_id, "body": body}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _model_defaults(model_id: str = "") -> Dict[str, Any]:
    return {"model_id": model_id, "requests": 0, "failed": 0, "cache_hits": 0, "cost_usd": 0.0, "live_ms_total": 0.0, "last_request_at": None}


class UsageStore:
    def __init__(self, directory: Path, backend: str, limits: Optional[Limits] = None) -> None:
        self.dir = Path(directory)
        self.backend = backend
        self.limits = limits or Limits()
        self._lock = threading.RLock()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.dir / "cache.jsonl"
        self.usage_path = self.dir / "usage.json"
        self.log_path = self.dir / "request_log.jsonl"
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.usage: Dict[str, Any] = {}
        self._inflight: Dict[int, float] = {}  # request_no -> estimated cost of a request that is still running
        self._load()

    # ------------------------------------------------------------ loading
    def _load(self) -> None:
        if self.usage_path.exists():
            try:
                data = json.loads(self.usage_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or "total_requests" not in data:
                    raise ValueError("missing fields")
            except ValueError as exc:
                raise StoreError(
                    f"{self.usage_path} is damaged ({exc}). Fix or restore the file: the app will not start with fresh counters "
                    "because that could break the request limits."
                ) from exc
            data.setdefault("models", {})
            data.setdefault("cache_hits", 0)
            data.setdefault("total_cost_usd", 0.0)
            data.setdefault("assumed_cost_usd", 0.0)
            self.usage = data
        else:
            self.usage = {"version": 1, "backend": self.backend, "total_requests": 0, "total_failed": 0, "cache_hits": 0,
                          "total_cost_usd": 0.0, "assumed_cost_usd": 0.0, "created_at": now_iso(), "updated_at": now_iso(), "models": {}}
            self._save_usage()
        if self.cache_path.exists():
            with open(self.cache_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        self.cache[entry["key"]] = entry
                    except (ValueError, KeyError):
                        continue  # a half-written last line is ignored

    def _save_usage(self) -> None:
        self.usage["updated_at"] = now_iso()
        tmp = self.usage_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.usage, indent=2), encoding="utf-8")
        # On Windows, antivirus, OneDrive or an editor can hold the file for a moment
        # ([WinError 5] Access is denied). Try again a few times before giving up.
        for attempt in range(10):
            try:
                os.replace(tmp, self.usage_path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05 * (attempt + 1))

    def _model(self, model_key: str, model_id: str = "") -> Dict[str, Any]:
        m = self.usage["models"].setdefault(model_key, _model_defaults(model_id))
        if model_id and not m.get("model_id"):
            m["model_id"] = model_id
        return m

    # ------------------------------------------------------------ log
    def log(self, event: str, **data: Any) -> None:
        line = json.dumps({"ts": now_iso(), "backend": self.backend, "event": event, **data}, ensure_ascii=True)
        with self._lock, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ------------------------------------------------------------ cache
    def cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.cache.get(key)

    def cache_put(self, key: str, model_key: str, model_id: str, data: Any, live_ms: float, cost_usd: Optional[float]) -> None:
        entry = {"key": key, "backend": self.backend, "model_key": model_key, "model_id": model_id, "created_at": now_iso(),
                 "live_ms": round(live_ms, 2), "cost_usd": cost_usd, "data": data}
        with self._lock:
            self.cache[key] = entry
            with open(self.cache_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=True) + "\n")

    def note_cache_hit(self, model_key: str, model_id: str = "") -> None:
        with self._lock:
            self._model(model_key, model_id)["cache_hits"] += 1
            self.usage["cache_hits"] += 1
            self._save_usage()

    # ------------------------------------------------------------ budget
    def limit_for(self, model_key: str) -> Optional[int]:
        return self.limits.per_model.get(model_key)

    def _estimate(self, model_key: str) -> float:
        """Worst-case cost of the next request of this model: the dearest one seen so far, or the configured guess."""
        seen = self.usage["models"].get(model_key, {}).get("max_cost_usd", 0.0) or 0.0
        return max(seen, self.limits.cost_estimate.get(model_key, 0.0))

    def spent_usd(self) -> float:
        """Money spent so far: real costs reported by the API plus a guess for requests that reported no cost."""
        return self.usage["total_cost_usd"] + self.usage.get("assumed_cost_usd", 0.0)

    def reserve(self, model_key: str, model_id: str = "") -> int:
        """Count one real request BEFORE it is sent. Raises BudgetExceeded if a limit would be broken."""
        with self._lock:
            m = self._model(model_key, model_id)
            per = self.limit_for(model_key)
            if per is not None and m["requests"] >= per:
                raise BudgetExceeded(f"request limit of {per} for this model is used up ({m['requests']}/{per})", model_key)
            if self.limits.total is not None and self.usage["total_requests"] >= self.limits.total:
                raise BudgetExceeded(f"total request limit of {self.limits.total} is used up ({self.usage['total_requests']}/{self.limits.total})", model_key)
            est = 0.0
            cap = self.limits.max_cost_usd
            if cap is not None:
                est = self._estimate(model_key)
                projected = self.spent_usd() + sum(self._inflight.values()) + est
                if projected > cap:
                    raise BudgetExceeded(
                        f"cost cap of ${cap:.2f} would be exceeded (spent ${self.spent_usd():.4f}, "
                        f"next request may cost up to ${est:.4f})", model_key)
            m["requests"] += 1
            m["last_request_at"] = now_iso()
            self.usage["total_requests"] += 1
            try:
                self._save_usage()
            except OSError as exc:  # never send a request that we could not count
                m["requests"] -= 1
                self.usage["total_requests"] -= 1
                raise BudgetExceeded(f"could not save the usage counters ({exc})", model_key) from exc
            number = self.usage["total_requests"]
            self._inflight[number] = est
            self.log("request_start", model_key=model_key, model_id=model_id, request_no=number,
                     model_requests=m["requests"], model_limit=per, total_limit=self.limits.total)
            return number

    def finish(self, model_key: str, request_no: int, ok: bool, live_ms: float, cost_usd: Optional[float],
               kind: Optional[str] = None, detail: str = "") -> None:
        with self._lock:
            m = self._model(model_key)
            est = self._inflight.pop(request_no, 0.0)
            if self.limits.max_cost_usd is not None:
                if cost_usd:
                    m["max_cost_usd"] = max(m.get("max_cost_usd", 0.0), cost_usd)
                elif ok or kind == "timeout":  # the API did not tell the price: count the worst-case guess
                    self.usage["assumed_cost_usd"] = round(self.usage.get("assumed_cost_usd", 0.0) + est, 8)
            m["live_ms_total"] = round(m["live_ms_total"] + live_ms, 2)
            if not ok:
                m["failed"] += 1
                self.usage["total_failed"] = self.usage.get("total_failed", 0) + 1
            if cost_usd:
                m["cost_usd"] = round(m["cost_usd"] + cost_usd, 8)
                self.usage["total_cost_usd"] = round(self.usage["total_cost_usd"] + cost_usd, 8)
            self._save_usage()
            self.log("request_end", model_key=model_key, request_no=request_no, ok=ok, live_ms=round(live_ms, 1),
                     cost_usd=cost_usd, kind=kind, detail=(detail or "")[:200])

    # ------------------------------------------------------------ views
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            models = {}
            for key, m in self.usage["models"].items():
                models[key] = {"model_id": m.get("model_id"), "requests": m["requests"], "limit": self.limit_for(key),
                               "failed": m["failed"], "cache_hits": m["cache_hits"], "cost_usd": m["cost_usd"],
                               "avg_live_ms": round(m["live_ms_total"] / m["requests"], 1) if m["requests"] else None}
            for key, lim in self.limits.per_model.items():
                models.setdefault(key, {"model_id": None, "requests": 0, "limit": lim, "failed": 0, "cache_hits": 0, "cost_usd": 0.0, "avg_live_ms": None})
            return {"backend": self.backend, "total_requests": self.usage["total_requests"], "total_limit": self.limits.total,
                    "total_failed": self.usage.get("total_failed", 0), "cache_hits": self.usage["cache_hits"],
                    "cache_entries": len(self.cache), "total_cost_usd": self.usage["total_cost_usd"],
                    "assumed_cost_usd": self.usage.get("assumed_cost_usd", 0.0), "cost_limit_usd": self.limits.max_cost_usd,
                    "updated_at": self.usage["updated_at"], "models": models}

    def clear_all(self) -> None:
        """Delete cache, counters and log. Only allowed for the SIMULATED store (practice runs)."""
        if self.backend != "simulated":
            raise StoreError(f"refusing to clear the {self.backend} counters and cache: only the simulated practice data can be reset")
        with self._lock:
            for p in (self.cache_path, self.usage_path, self.log_path):
                if p.exists():
                    p.unlink()
            self.cache = {}
            self.usage = {"version": 1, "backend": self.backend, "total_requests": 0, "total_failed": 0, "cache_hits": 0,
                          "total_cost_usd": 0.0, "assumed_cost_usd": 0.0, "created_at": now_iso(), "updated_at": now_iso(), "models": {}}
            self._save_usage()


_STORES: Dict[str, UsageStore] = {}


def get_store(directory: Path, backend: str, limits: Optional[Limits] = None) -> UsageStore:
    """One store object per folder in this process, so every run sees the same counters."""
    key = str(Path(directory).resolve())
    store = _STORES.get(key)
    if store is None:
        store = UsageStore(directory, backend, limits)
        _STORES[key] = store
    else:
        store.limits = limits or store.limits
    return store


def forget_stores() -> None:
    """For tests: drop the in-process objects (the files stay), like a restart."""
    _STORES.clear()
