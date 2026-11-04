#!/usr/bin/env python3
"""Find out whether another WORDING of the question stops Luna's refusal (HTTP 502 "OpenAI refused").

  python scripts/probe_luna.py            # asks you to confirm, then sends at most 2 REAL requests to Luna

For each wording ("compact", then "plain") ONE request is sent for the start position of the teacher
puzzle, no retry. The request goes through the same gateway as the benchmark: it is counted, logged and
a valid answer is cached (so the benchmark later reuses it for free). It stops at the first wording that works.
Only the wording changes, never the cube information. If a wording works, put it in .env:

  PROMPT_STYLE=compact      (or plain)

and it is then used for EVERY model of the run, so the race stays fair.
"""
import _bootstrap  # noqa: F401

import argparse
import asyncio
import os
import sys

import httpx

from app.agents.base import MoveRequest, RetryPolicy
from app.agents.contract import PROMPT_STYLES
from app.agents.decisions import DecisionsAgent
from app.agents.registry import get_specs
from app.config import load_settings
from app.cube.engine import ALL_MOVES
from app.cube.puzzles import make_puzzles
from app.cube.solver import OptimalSolver
from app.gateway import Gateway
from app.suite import make_store


async def main_async(styles, settings) -> int:
    store = make_store(settings, "openrouter")
    gateway = Gateway(store, "openrouter")
    spec = get_specs(settings, "openrouter", ["luna"])[0]
    solver = OptimalSolver()
    puzzle = (await asyncio.to_thread(make_puzzles, "teacher", 1, 42, solver))[0]
    req = MoveRequest(facelets=puzzle.facelets, recent_moves=[], moves_made=0, move_limit=settings.move_limit,
                      allowed_moves=list(ALL_MOVES))
    policy = RetryPolicy(max_attempts=1, request_timeout_s=settings.request_timeout_s, backoff_s=0.0)
    async with httpx.AsyncClient() as client:
        agent = DecisionsAgent(spec, policy, client, settings.openrouter_api_key, settings.openrouter_base_url, gateway)
        for style in styles:
            os.environ["PROMPT_STYLE"] = style
            d = await agent.decide(req)
            snap = store.snapshot()
            used = snap["models"].get("luna", {}).get("requests", 0)
            if d.move:
                print(f"  {style:<8} WORKS: Luna answered {d.move}  ({d.latency_ms:.0f} ms, source {d.source})   Luna requests used: {used}/20")
                print(f"\nPut this in .env and save:  PROMPT_STYLE={style}")
                return 0
            print(f"  {style:<8} failed: {d.error}   Luna requests used: {used}/20")
    print("\nNo wording worked. Report Luna's refusal in the README as a result.")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--styles", nargs="+", default=["compact", "plain"], choices=list(PROMPT_STYLES))
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    settings = load_settings()
    if not settings.has_api_key:
        print("error: OPENROUTER_API_KEY is not set in .env", file=sys.stderr)
        return 2
    print(f"This sends at most {len(args.styles)} REAL request(s) to Luna ({', '.join(args.styles)}), no retries.")
    if not args.yes and input("Type yes to continue: ").strip().lower() != "yes":
        print("cancelled")
        return 1
    return asyncio.run(main_async(args.styles, settings))


if __name__ == "__main__":
    sys.exit(main())
