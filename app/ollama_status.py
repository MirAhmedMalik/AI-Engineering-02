"""Is Ollama running, and are the configured models installed?  (GET /api/tags, free and local)"""
from __future__ import annotations

from typing import Any, Dict, List

import httpx

from .agents.registry import get_specs
from .config import Settings


def _installed(name: str, installed: List[str]) -> bool:
    """'qwen3:4b' matches 'qwen3:4b'; a tag-less 'clef' matches 'clef:latest'."""
    names = set(installed)
    if name in names:
        return True
    if ":" not in name and f"{name}:latest" in names:
        return True
    return False


async def check_ollama(settings: Settings, timeout_s: float = 1.5) -> Dict[str, Any]:
    out: Dict[str, Any] = {"base_url": settings.ollama_base_url, "reachable": False, "installed": [], "models": [], "error": None}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(f"{settings.ollama_base_url}/api/tags")
            r.raise_for_status()
            out["installed"] = sorted(m.get("name", "") for m in r.json().get("models", []))
            out["reachable"] = True
    except Exception as exc:
        out["error"] = f"{exc.__class__.__name__}"
    for spec in get_specs(settings, "ollama"):
        ok = out["reachable"] and _installed(spec.model_id, out["installed"])
        out["models"].append({"key": spec.key, "name": spec.name, "model_id": spec.model_id, "installed": bool(ok),
                              "pull": f"ollama pull {spec.model_id}"})
    return out
