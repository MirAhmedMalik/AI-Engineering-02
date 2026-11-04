"""Settings. Everything comes from environment variables (see .env.example).

The OpenRouter key is read here, on the backend only. It is never sent to the
browser and never written into result files, cache files or logs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_MODEL_IDS: Dict[str, str] = {
    "jev": "typesafe/jev-1.13",
    "luna": "openai/gpt-6-luna-decisions",
    "clef": "cloudflare/clef",
    "opus": "anthropic/claude-haiku-5.5",
}

# Ollama tags are only defaults. Run `python scripts/check_models.py --backend ollama` to see what you have installed.
# Clef is a decision model (served on /v1/systemone). "clef-flash" is the 9B one (about 11 GB); "clef" is the 27B one.
DEFAULT_OLLAMA_MODELS: Dict[str, str] = {
    "ol_clef": "clef-flash",
    "ol_qwen": "qwen3:4b",
    "ol_deepseek": "deepseek-r1:1.5b",
    "ol_gemma": "gemma3:4b",
}

# The key of the teacher can never be used for more than 100 calls. We stop well before.
HARD_TOTAL_CEILING = 95

BACKENDS = ("openrouter", "ollama", "simulated")


def _is_placeholder(value: str) -> bool:
    v = value.strip().lower()
    return (not v) or "your_" in v or "xxxx" in v or v in {"changeme", "none", "null"}


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai"
    student_name: str = ""
    student_roll_no: str = ""

    # experiment rules (same for every model)
    move_limit: int = 20
    trial_time_limit_s: float = 300.0
    request_timeout_s: float = 90.0
    max_attempts: int = 2
    retry_backoff_s: float = 1.0
    recent_moves_window: int = 6

    # OpenRouter request limits from the teacher (enforced in app/store.py)
    max_claude_requests: int = 5
    max_requests_per_decision_model: int = 20
    max_total_requests: int = 65
    # money cap for the real OpenRouter run (0 = no cap). A request is refused BEFORE it is sent if it could break the cap.
    max_cost_usd: float = 0.0
    cost_estimate_llm_usd: float = 0.03  # worst-case guess for one Claude request until a real price is seen
    cost_estimate_decision_usd: float = 0.002  # same for one decision-model request

    opus_reasoning_effort: str = ""
    opus_max_tokens: int = 0

    # local models
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_request_timeout_s: float = 240.0
    ollama_trial_time_limit_s: float = 900.0
    ollama_think: str = "false"  # "false" | "true" | "" (do not send)
    ollama_models: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_OLLAMA_MODELS))

    mock_latency_scale: float = 1.0
    model_ids: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODEL_IDS))
    host: str = "127.0.0.1"
    port: int = 8000
    results_dir: Path = ROOT / "results"
    data_dir: Path = ROOT / "data"

    # ---------------------------------------------------------------- helpers
    @property
    def has_api_key(self) -> bool:
        return not _is_placeholder(self.openrouter_api_key)

    @property
    def total_request_limit(self) -> int:
        return min(max(self.max_total_requests, 0), HARD_TOTAL_CEILING)

    def store_dir(self, backend: str) -> Path:
        return Path(self.data_dir) / backend

    def trial_time_limit_for(self, backend: str) -> float:
        return self.ollama_trial_time_limit_s if backend == "ollama" else self.trial_time_limit_s

    def request_timeout_for(self, backend: str) -> float:
        return self.ollama_request_timeout_s if backend == "ollama" else self.request_timeout_s

    def request_cap_for(self, category: str) -> int:
        """Request limit of one model on OpenRouter (and in the simulated practice mode)."""
        return self.max_claude_requests if category == "llm" else self.max_requests_per_decision_model

    def move_limit_for(self, backend: str, category: str) -> int:
        """Moves a model may play. A model that has only 5 requests can play at most 5 moves."""
        if backend == "ollama":
            return self.move_limit
        return max(1, min(self.move_limit, self.request_cap_for(category)))

    def public_dict(self) -> dict:
        """Settings that are safe to show in the browser (no key)."""
        return {
            "has_api_key": self.has_api_key,
            "move_limit": self.move_limit,
            "trial_time_limit_s": self.trial_time_limit_s,
            "request_timeout_s": self.request_timeout_s,
            "max_attempts": self.max_attempts,
            "retry_backoff_s": self.retry_backoff_s,
            "recent_moves_window": self.recent_moves_window,
            "max_claude_requests": self.max_claude_requests,
            "max_requests_per_decision_model": self.max_requests_per_decision_model,
            "max_total_requests": self.total_request_limit,
            "max_cost_usd": self.max_cost_usd,
            "ollama_base_url": self.ollama_base_url,
            "ollama_trial_time_limit_s": self.ollama_trial_time_limit_s,
            "opus_reasoning_effort": self.opus_reasoning_effort or "provider default",
        }


def load_settings(env: Optional[Mapping[str, str]] = None, load_dotenv_file: bool = True) -> Settings:
    if env is None:
        if load_dotenv_file:
            try:
                from dotenv import load_dotenv

                load_dotenv(ROOT / ".env", override=False)
            except ImportError:  # python-dotenv is optional at runtime
                pass
        env = os.environ

    model_ids = dict(DEFAULT_MODEL_IDS)
    for key in model_ids:
        override = env.get(f"MODEL_{key.upper()}", "").strip()
        if override:
            model_ids[key] = override

    ollama_models = dict(DEFAULT_OLLAMA_MODELS)
    for key in ollama_models:
        override = env.get(f"OLLAMA_MODEL_{key[3:].upper()}", "").strip()
        if override:
            ollama_models[key] = override

    think = env.get("OLLAMA_THINK", "false").strip().lower()
    if think not in {"false", "true", ""}:
        think = "false"

    results_dir = env.get("RESULTS_DIR", "").strip()
    data_dir = env.get("DATA_DIR", "").strip()
    return Settings(
        openrouter_api_key=env.get("OPENROUTER_API_KEY", "").strip(),
        openrouter_base_url=(env.get("OPENROUTER_BASE_URL", "").strip() or "https://openrouter.ai").rstrip("/"),
        student_name=env.get("STUDENT_NAME", "").strip(),
        student_roll_no=env.get("STUDENT_ROLL_NO", "").strip(),
        move_limit=max(1, _int(env, "MOVE_LIMIT", 20)),
        trial_time_limit_s=_float(env, "TRIAL_TIME_LIMIT_S", 300.0),
        request_timeout_s=_float(env, "REQUEST_TIMEOUT_S", 90.0),
        max_attempts=max(1, _int(env, "MAX_ATTEMPTS", 2)),
        retry_backoff_s=_float(env, "RETRY_BACKOFF_S", 1.0),
        recent_moves_window=max(0, _int(env, "RECENT_MOVES_WINDOW", 6)),
        max_claude_requests=max(0, _int(env, "MAX_CLAUDE_REQUESTS", 5)),
        max_requests_per_decision_model=max(0, _int(env, "MAX_REQUESTS_PER_DECISION_MODEL", 20)),
        max_total_requests=max(0, _int(env, "MAX_TOTAL_REQUESTS", 65)),
        max_cost_usd=max(0.0, _float(env, "MAX_COST_USD", 0.0)),
        cost_estimate_llm_usd=max(0.0, _float(env, "COST_ESTIMATE_LLM_USD", 0.01)),
        cost_estimate_decision_usd=max(0.0, _float(env, "COST_ESTIMATE_DECISION_USD", 0.002)),
        opus_reasoning_effort=env.get("OPUS_REASONING_EFFORT", "").strip().lower(),
        opus_max_tokens=_int(env, "OPUS_MAX_TOKENS", 4000),
        ollama_base_url=(env.get("OLLAMA_BASE_URL", "").strip() or "http://127.0.0.1:11434").rstrip("/"),
        ollama_request_timeout_s=_float(env, "OLLAMA_REQUEST_TIMEOUT_S", 240.0),
        ollama_trial_time_limit_s=_float(env, "OLLAMA_TRIAL_TIME_LIMIT_S", 900.0),
        ollama_think=think,
        ollama_models=ollama_models,
        mock_latency_scale=_float(env, "MOCK_LATENCY_SCALE", 1.0),
        model_ids=model_ids,
        host=env.get("HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_int(env, "PORT", 8000),
        results_dir=Path(results_dir) if results_dir else ROOT / "results",
        data_dir=Path(data_dir) if data_dir else ROOT / "data",
    )
