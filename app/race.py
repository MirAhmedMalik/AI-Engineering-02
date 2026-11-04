"""One trial = one model plays one puzzle. One race = all chosen models play the same puzzle."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

from .agents.base import Agent, AttemptLog, MoveRequest, TrialTimeout
from .config import Settings
from .cube.engine import ALL_MOVES, Cube
from .cube.puzzles import Puzzle
from .stats import BUDGET_EXHAUSTED, ERROR, OUT_OF_TIME, RAN_OUT_OF_MOVES, SOLVED, rank_trials

Emit = Callable[..., Any]


def _noop(*_a: Any, **_k: Any) -> None:
    return None


def _store_numbers(agent: Agent) -> Dict[str, Any]:
    gw = getattr(agent, "gateway", None)
    if gw is None:
        return {}
    return gw.store.snapshot()["models"].get(agent.spec.key, {})


async def run_trial(
    agent: Agent,
    puzzle: Puzzle,
    settings: Settings,
    emit: Emit = _noop,
    repeat: int = 0,
) -> Dict[str, Any]:
    """Play one puzzle with one model until solved, move limit, time limit, budget end or error."""
    spec = agent.spec
    backend = spec.backend
    move_limit = settings.move_limit_for(backend, spec.category)  # e.g. 20 for most models, 5 for Claude
    cube = Cube(puzzle.facelets)
    history: List[str] = []
    move_logs: List[Dict[str, Any]] = []
    attempt_logs: List[Dict[str, Any]] = []
    tokens_in = tokens_out = 0
    notes: set = set()
    status = RAN_OUT_OF_MOVES
    error_message: Optional[str] = None
    before = dict(_store_numbers(agent))

    await agent.prepare(puzzle)
    start = time.perf_counter()
    deadline = start + settings.trial_time_limit_for(backend)
    base = {"model": spec.key, "puzzle_id": puzzle.id, "repeat": repeat}
    emit("trial_start", **base, facelets=cube.facelets, move_limit=move_limit)

    for move_no in range(1, move_limit + 1):
        req = MoveRequest(
            facelets=cube.facelets,
            recent_moves=history[-settings.recent_moves_window:] if settings.recent_moves_window else [],
            moves_made=move_no - 1,
            move_limit=move_limit,
            allowed_moves=list(ALL_MOVES),
        )
        emit("thinking", **base, move_no=move_no, elapsed_s=time.perf_counter() - start)

        def on_attempt(log: AttemptLog, _move_no: int = move_no) -> None:
            entry = {"move_no": _move_no, **log.to_dict()}
            if not log.ok:
                attempt_logs.append(entry)
                emit("attempt_failed", **base, **entry, elapsed_s=time.perf_counter() - start)

        try:
            decision = await agent.decide(req, deadline, on_attempt)
        except TrialTimeout:
            status = OUT_OF_TIME
            break

        if decision.move is None:
            if decision.budget_exhausted:
                status = BUDGET_EXHAUSTED
            else:
                status = ERROR
            error_message = decision.error
            break

        cube = cube.apply(decision.move)
        history.append(decision.move)
        elapsed = time.perf_counter() - start
        tokens_in += decision.tokens_in or 0
        tokens_out += decision.tokens_out or 0
        if decision.source == "live":
            cost = decision.cost_usd or 0.0
        else:
            cost = 0.0

        log = {
            "n": move_no,
            "move": decision.move,
            "source": decision.source,  # "live" or "cache"
            "latency_ms": round(decision.latency_ms, 1),  # live API latency (for a cache hit: as first measured)
            "cache_ms": None if decision.cache_ms is None else round(decision.cache_ms, 3),  # time to answer from the cache
            "decision_ms": round(decision.decision_ms, 1),
            "confidence": decision.confidence,
            "failed_attempts": len(decision.failed_attempts),
            "elapsed_s": round(elapsed, 3),
            "stickers_in_place": cube.stickers_in_place(),
            "facelets": cube.facelets,
            "cost_usd": cost,
        }
        move_logs.append(log)
        emit(
            "move", **base, move_no=move_no, move=decision.move, source=decision.source,
            latency_ms=log["latency_ms"], cache_ms=log["cache_ms"], decision_ms=log["decision_ms"],
            confidence=decision.confidence, facelets=cube.facelets, elapsed_s=elapsed,
            stickers_in_place=log["stickers_in_place"], history=list(history), move_limit=move_limit,
        )
        if cube.is_solved():
            status = SOLVED
            break

    total_time = time.perf_counter() - start
    if status == OUT_OF_TIME:
        total_time = max(total_time, settings.trial_time_limit_for(backend))

    after = _store_numbers(agent)
    api_calls = after.get("requests", 0) - before.get("requests", 0)  # real requests, failed ones included
    cache_hits = after.get("cache_hits", 0) - before.get("cache_hits", 0)
    cost_usd = round(after.get("cost_usd", 0.0) - before.get("cost_usd", 0.0), 8)
    failed_requests = after.get("failed", 0) - before.get("failed", 0)

    kinds = [a["kind"] for a in attempt_logs]
    solved = status == SOLVED
    gap = (len(move_logs) - puzzle.reference_moves) if solved else None
    live_moves = sum(1 for m in move_logs if m["source"] == "live")

    trial = {
        "puzzle_id": puzzle.id,
        "repeat": repeat,
        "model": spec.key,
        "model_name": spec.name,
        "model_id": spec.model_id,
        "category": spec.category,
        "interface": spec.interface,
        "backend": backend,
        "mode": agent.mode,
        "status": status,
        "solved": solved,
        "move_limit": move_limit,
        "moves_used": len(move_logs),
        "moves": move_logs,
        "total_time_s": round(total_time, 3),
        "reference_moves": puzzle.reference_moves,
        "reference_kind": puzzle.reference_kind,
        "gap": gap,
        "gap_kind": puzzle.reference_kind if solved else None,
        "api_calls": api_calls,
        "cache_hits": cache_hits,
        "failed_requests": failed_requests,
        "live_moves": live_moves,
        "cached_moves": len(move_logs) - live_moves,
        "failed_attempts": len(attempt_logs),
        "retries": len(attempt_logs) - (1 if status == ERROR else 0),
        "invalid_outputs": sum(1 for k in kinds if k in ("invalid_move", "malformed")),
        "timeouts": sum(1 for k in kinds if k == "timeout"),
        "http_errors": sum(1 for k in kinds if k in ("http_error", "transport")),
        "attempt_log": attempt_logs,  # only failed attempts are listed; successful ones are in "moves"
        "error_message": error_message,
        "final_facelets": cube.facelets,
        "stickers_in_place": cube.stickers_in_place(),
        "cost_usd": cost_usd if (cost_usd or backend == "openrouter") else 0.0,
        "tokens_in": tokens_in or None,
        "tokens_out": tokens_out or None,
        "notes": sorted(getattr(agent, "downgrades", []) or []),
    }
    emit(
        "trial_end", **base, status=status, moves_used=trial["moves_used"], total_time_s=trial["total_time_s"],
        gap=gap, gap_kind=trial["gap_kind"], reference_moves=puzzle.reference_moves, move_limit=move_limit,
        stickers_in_place=trial["stickers_in_place"], error_message=error_message, facelets=cube.facelets,
        api_calls=api_calls, cache_hits=cache_hits, cost_usd=trial["cost_usd"],
    )
    return trial


async def run_race(
    agents: Dict[str, Agent],
    puzzle: Puzzle,
    settings: Settings,
    emit: Emit = _noop,
    parallel: bool = True,
    repeat: int = 0,
) -> List[Dict[str, Any]]:
    """All agents start from the same scrambled cube."""
    if parallel:
        trials = await asyncio.gather(*(run_trial(a, puzzle, settings, emit, repeat) for a in agents.values()))
        trials = list(trials)
    else:
        trials = []
        for a in agents.values():
            trials.append(await run_trial(a, puzzle, settings, emit, repeat))
    return trials


def ranking(trials: List[Dict[str, Any]]) -> List[str]:
    return rank_trials(trials)
