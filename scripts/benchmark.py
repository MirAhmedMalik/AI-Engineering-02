#!/usr/bin/env python3
"""Run the benchmark from the command line (no browser needed).

  python scripts/benchmark.py --backend simulated                 # practice, no real request (do this first)
  python scripts/benchmark.py --backend ollama --suite all        # local models: warm-up, primary, extension
  python scripts/benchmark.py --backend openrouter                # THE ONE real experiment (asks you to confirm)

OpenRouter always uses the teacher setup: ONE cube exactly 3 moves from solved, move limit 20,
Claude at most 5 requests, 65 requests in total. The limits are enforced in the backend and the
counters are saved in data/openrouter/ (do not delete that folder).
Results are saved to results/runs/<run id>/ . Afterwards run: python scripts/report.py
"""
import _bootstrap  # noqa: F401  (must be first)

import argparse
import asyncio
import dataclasses
import sys

from app.config import BACKENDS, load_settings
from app.cube.puzzles import TEST_SETS
from app.cube.solver import OptimalSolver
from app.events import EventBus
from app.ollama_status import check_ollama
from app.store import StoreError
from app.suite import SuiteConfig, execute_run, make_store, new_run_doc

OLLAMA_SUITE = [("teacher", 1), ("warmup", 3), ("primary", 10), ("extension", 5)]


async def watch(bus: EventBus, quiet: bool) -> None:
    names = {}
    async for ev in bus.stream():
        if ev is None or quiet:
            continue
        t = ev["type"]
        if t == "run_start":
            names = {m["key"]: m["name"] for m in ev["models"]}
            print(f"  run {ev['run_id']} ({ev['backend']}), {ev['total_races']} race(s)")
        elif t == "puzzle_start":
            p = ev["puzzle"]
            print(f"  race {ev['race_no']}/{ev['total_races']} {p['id']}: scramble {' '.join(p['scramble'])} "
                  f"(shortest {'<= ' if p['reference_kind'] != 'exact' else ''}{p['reference_moves']})")
        elif t == "attempt_failed":
            print(f"    ! {names.get(ev['model'], ev['model'])} move {ev['move_no']}: attempt {ev['attempt']} failed: {ev['kind']} {ev['detail'][:90]}")
        elif t == "trial_end":
            extra = f", gap {ev['gap']:+d}" if ev["gap"] is not None else ""
            print(f"    {names.get(ev['model'], ev['model']):<22} {ev['status']:<17} {ev['moves_used']:>2} moves, {ev['total_time_s']:>6.1f} s, "
                  f"{ev.get('api_calls', 0)} calls, {ev.get('cache_hits', 0)} cache hits{extra}")
        elif t == "error":
            print("  ERROR:", ev["message"])
        elif t == "run_end":
            print(f"  -> {ev['status']}, saved to {ev['saved_to']}")


async def run_one(settings, backend, test_set, puzzles, args, solver) -> int:
    cfg = SuiteConfig(
        backend=backend, test_set=test_set, num_puzzles=puzzles, seed=args.seed, repeats=args.repeats,
        model_keys=args.models or None, parallel=not args.sequential, pause_s=0,
        student_name=settings.student_name, student_roll_no=settings.student_roll_no, confirm_live=True,
    )
    doc = new_run_doc(cfg, settings)
    bus = EventBus()
    watcher = asyncio.create_task(watch(bus, args.quiet))
    await execute_run(doc, settings, solver, bus)
    await watcher
    return 0 if doc["status"] == "finished" else 1


def confirm_openrouter(settings, assume_yes: bool) -> bool:
    store = make_store(settings, "openrouter")
    snap = store.snapshot()
    print("\nThis will make REAL OpenRouter requests with your personal key.")
    print(f"  Limits: Claude {settings.max_claude_requests}, each decision model {settings.max_requests_per_decision_model}, total {settings.total_request_limit}.")
    print(f"  Already used (saved on disk): {snap['total_requests']} requests, {snap['cache_entries']} cached answers.")
    if assume_yes:
        return True
    return input("Type yes to continue: ").strip().lower() == "yes"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=list(BACKENDS), default="simulated")
    ap.add_argument("--suite", choices=["all"], help="ollama / simulated only: teacher, warm-up, primary and extension one after another")
    ap.add_argument("--test-set", choices=sorted(TEST_SETS), default="teacher")
    ap.add_argument("--puzzles", type=int, help="number of puzzles (default: the test set default)")
    ap.add_argument("--repeats", type=int, default=1, help="repeat every puzzle this many times")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--models", nargs="+", help="model keys, e.g. jev luna clef opus  (ollama: ol_clef ol_qwen ol_deepseek ol_gemma)")
    ap.add_argument("--sequential", action="store_true", help="models play one after another instead of at the same time")
    ap.add_argument("--fast", action="store_true", help="simulated only: no waiting")
    ap.add_argument("--yes", action="store_true", help="openrouter: do not ask for confirmation")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    settings = load_settings()
    if args.backend == "simulated" and args.fast:
        settings = dataclasses.replace(settings, mock_latency_scale=0.0, retry_backoff_s=0.0)
    if args.backend == "openrouter":
        if args.suite or args.test_set != "teacher" or (args.puzzles and args.puzzles != 1) or args.repeats != 1:
            print("OpenRouter has a tiny request budget: only the teacher set, 1 puzzle, 1 repeat is allowed here. "
                  "Use --backend ollama for bigger experiments.", file=sys.stderr)
            return 2
        if not settings.has_api_key:
            print("OPENROUTER_API_KEY is not set. Put your key in .env (see .env.example).", file=sys.stderr)
            return 2
    try:
        if args.backend == "openrouter" and not confirm_openrouter(settings, args.yes):
            print("Cancelled. Nothing was sent.")
            return 1
    except StoreError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 2
    if args.backend == "ollama":
        status = asyncio.run(check_ollama(settings))
        if not status["reachable"]:
            print(f"Ollama is not running at {settings.ollama_base_url}. Start the Ollama app first.", file=sys.stderr)
            return 2
        chosen = set(args.models or [m["key"] for m in status["models"]])
        missing = [m for m in status["models"] if m["key"] in chosen and not m["installed"]]
        if missing:
            print("These Ollama models are not installed yet:", file=sys.stderr)
            for m in missing:
                print(f"   {m['pull']}", file=sys.stderr)
            return 2

    print(f"backend: {args.backend}   move limit {settings.move_limit}   trial time limit {settings.trial_time_limit_for(args.backend):.0f} s")
    solver = OptimalSolver()
    _ = solver.table
    plan = OLLAMA_SUITE if args.suite == "all" else [(args.test_set, args.puzzles or TEST_SETS[args.test_set].default_count)]
    if args.suite == "all" and args.puzzles:
        plan = [(ts, args.puzzles if ts == "primary" else n) for ts, n in OLLAMA_SUITE]
    code = 0
    for ts, n in plan:
        print(f"== {TEST_SETS[ts].label}: {n} puzzle(s) x {args.repeats} repeat(s)")
        code |= asyncio.run(run_one(settings, args.backend, ts, n, args, solver))
    print("Next step: python scripts/report.py")
    return code


if __name__ == "__main__":
    sys.exit(main())
