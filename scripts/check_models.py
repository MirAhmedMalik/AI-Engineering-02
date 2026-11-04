#!/usr/bin/env python3
"""Preflight checks. They never spend OpenRouter requests unless you ask with --ping.

  python scripts/check_models.py --backend ollama      # which Ollama models are installed (and the pull commands)
  python scripts/check_models.py --backend openrouter  # are the 4 model IDs in the public catalogue? (free, no key sent)
  python scripts/check_models.py --backend openrouter --ping   # + ONE real request per model (uses 4 of your 65!)
"""
import _bootstrap  # noqa: F401

import argparse
import asyncio
import sys

import httpx

from app.agents.registry import get_specs
from app.config import load_settings
from app.ollama_status import check_ollama


def fetch_ids(client: httpx.Client, base: str, params: dict) -> set:
    # The catalogue is public: NO key is sent here.
    r = client.get(f"{base}/api/v1/models", params=params, timeout=30)
    r.raise_for_status()
    return {m["id"] for m in r.json().get("data", [])}


def check_ollama_cmd(s) -> int:
    st = asyncio.run(check_ollama(s))
    if not st["reachable"]:
        print(f"Ollama is NOT running at {s.ollama_base_url}. Install it from https://ollama.com and start the app.")
        return 1
    print(f"Ollama is running. Installed models: {', '.join(st['installed']) or 'none'}")
    bad = 0
    for m in st["models"]:
        print(f"  {'installed' if m['installed'] else 'MISSING  '} {m['model_id']:<24} ({m['name']})" + ("" if m["installed"] else f"   ->  {m['pull']}"))
        bad += 0 if m["installed"] else 1
    if bad:
        print("\nIf a model has a different tag on your machine, set it in .env (OLLAMA_MODEL_CLEF, OLLAMA_MODEL_QWEN, OLLAMA_MODEL_DEEPSEEK, OLLAMA_MODEL_GEMMA).")
    return 1 if bad else 0


def check_openrouter_cmd(s, ping: bool) -> int:
    print(f"OpenRouter base: {s.openrouter_base_url}   key set: {'yes' if s.has_api_key else 'NO'}")
    problems = 0
    try:
        with httpx.Client() as c:
            decision_ids = fetch_ids(c, s.openrouter_base_url, {"output_modalities": "decisions"})
            chat_ids = fetch_ids(c, s.openrouter_base_url, {})
        for spec in get_specs(s, "openrouter"):
            pool = decision_ids if spec.interface == "decisions" else chat_ids
            ok = spec.model_id in pool
            problems += 0 if ok else 1
            print(f"  {'found  ' if ok else 'MISSING'} {spec.model_id:<32} ({spec.name}, {spec.interface})")
        if problems:
            print("\nDecision models listed now:")
            for i in sorted(decision_ids):
                print("   ", i)
            print("Set the right ID in .env (MODEL_JEV, MODEL_LUNA, MODEL_CLEF, MODEL_OPUS) and check again.")
    except httpx.HTTPError as exc:
        print("Could not read the model catalogue:", exc)
        problems += 1
    if not ping:
        print("\nNo request was sent to any model (use the app or benchmark.py for the real run).")
        return 1 if problems else 0
    print("\n--ping is not available in this version: every real request must go through the benchmark, "
          "so that it is counted, cached and limited. Run: python scripts/benchmark.py --backend openrouter")
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=["ollama", "openrouter"], default="ollama")
    ap.add_argument("--ping", action="store_true")
    args = ap.parse_args()
    s = load_settings()
    return check_ollama_cmd(s) if args.backend == "ollama" else check_openrouter_cmd(s, args.ping)


if __name__ == "__main__":
    sys.exit(main())
