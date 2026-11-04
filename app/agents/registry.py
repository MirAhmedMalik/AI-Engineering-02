"""The models of the experiment (three rosters) and how to build an agent for each."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import httpx

from ..config import Settings
from .base import Agent, RetryPolicy
from .chat import ChatAgent
from .decisions import DecisionsAgent
from .mock import MockAgent
from .ollama import OllamaAgent


@dataclass(frozen=True)
class ModelSpec:
    key: str
    name: str
    category: str  # "decision" | "llm"
    interface: str  # "decisions" | "chat" | "ollama"
    model_id: str
    color: str
    backend: str = "openrouter"

    def to_dict(self) -> dict:
        return asdict(self)


# The four models of the assignment (OpenRouter). The simulated practice mode uses the same four.
OPENROUTER_SPECS: List[ModelSpec] = [
    ModelSpec("jev", "Jev 1.13", "decision", "decisions", "typesafe/jev-1.13", "#7B3FE4"),
    ModelSpec("luna", "GPT-6 Luna Decisions", "decision", "decisions", "openai/gpt-6-luna-decisions", "#0E8F6A"),
    ModelSpec("clef", "Cloudflare Clef", "decision", "decisions", "cloudflare/clef", "#E8710A"),
    ModelSpec("opus", "Claude Haiku 5.5", "llm", "chat", "anthropic/claude-haiku-5.5", "#B4472B"),
]
MODEL_SPECS = OPENROUTER_SPECS  # kept for older imports

# Local models on Ollama: one decision model (Clef) and three open LLMs.
OLLAMA_SPECS: List[ModelSpec] = [
    # Clef is a DECISION model: Ollama serves it on /v1/systemone (same format as the OpenRouter Decisions API).
    ModelSpec("ol_clef", "Clef (Ollama)", "decision", "decisions", "clef-flash", "#E8710A", "ollama"),
    ModelSpec("ol_qwen", "Qwen (Ollama)", "llm", "ollama", "qwen3:4b", "#2563EB", "ollama"),
    ModelSpec("ol_deepseek", "DeepSeek (Ollama)", "llm", "ollama", "deepseek-r1:1.5b", "#0891B2", "ollama"),
    ModelSpec("ol_gemma", "Gemma (Ollama)", "llm", "ollama", "gemma3:4b", "#C026D3", "ollama"),
]


def get_specs(settings: Settings, backend: str = "openrouter", keys: Optional[List[str]] = None) -> List[ModelSpec]:
    specs: List[ModelSpec] = []
    if backend == "ollama":
        for s in OLLAMA_SPECS:
            if keys is None or s.key in keys:
                specs.append(ModelSpec(s.key, s.name, s.category, s.interface, settings.ollama_models.get(s.key, s.model_id), s.color, "ollama"))
        return specs
    for s in OPENROUTER_SPECS:
        if keys is None or s.key in keys:
            specs.append(ModelSpec(s.key, s.name, s.category, s.interface, settings.model_ids.get(s.key, s.model_id), s.color,
                                   "simulated" if backend == "simulated" else "openrouter"))
    return specs


def build_agents(
    settings: Settings,
    backend: str,
    keys: Optional[List[str]],
    client: Optional[httpx.AsyncClient],
    gateway: Any,
) -> Dict[str, Agent]:
    """One agent per model, all with the same RetryPolicy."""
    policy = RetryPolicy(
        max_attempts=settings.max_attempts,
        request_timeout_s=settings.request_timeout_for(backend),
        backoff_s=settings.retry_backoff_s,
    )
    agents: Dict[str, Agent] = {}
    for spec in get_specs(settings, backend, keys):
        if backend == "simulated":
            agents[spec.key] = MockAgent(spec, policy, gateway, latency_scale=settings.mock_latency_scale)
            continue
        if client is None:
            raise ValueError("an httpx client is needed for live agents")
        if backend == "ollama" and spec.interface == "decisions":
            agents[spec.key] = DecisionsAgent(spec, policy, client, "", settings.ollama_base_url, gateway, path="/v1/systemone", auth=False)
        elif backend == "ollama":
            agents[spec.key] = OllamaAgent(spec, policy, client, settings.ollama_base_url, gateway, think=settings.ollama_think)
        elif spec.interface == "decisions":
            agents[spec.key] = DecisionsAgent(spec, policy, client, settings.openrouter_api_key, settings.openrouter_base_url, gateway)
        else:
            agents[spec.key] = ChatAgent(
                spec, policy, client, settings.openrouter_api_key, settings.openrouter_base_url, gateway,
                reasoning_effort=settings.opus_reasoning_effort, max_tokens=settings.opus_max_tokens,
            )
    return agents
