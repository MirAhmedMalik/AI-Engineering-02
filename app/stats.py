"""Statistics for a benchmark run. Pure functions: a run document in, numbers out."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

SOLVED = "SOLVED"
RAN_OUT_OF_MOVES = "RAN_OUT_OF_MOVES"
OUT_OF_TIME = "OUT_OF_TIME"
ERROR = "ERROR"
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"

STATUS_LABELS = {
    SOLVED: "Solved",
    RAN_OUT_OF_MOVES: "Ran out of moves",
    OUT_OF_TIME: "Out of time",
    ERROR: "Error",
    BUDGET_EXHAUSTED: "Request budget used up",
}


def mean(xs: Iterable[float]) -> Optional[float]:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def percentile(xs: Iterable[float], p: float) -> Optional[float]:
    xs = sorted(xs)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def rank_key(trial: Dict[str, Any]):
    """Race ranking: solved first (fewer moves, then less time); the rest by stickers in place, then time."""
    if trial["status"] == SOLVED:
        return (0, trial["moves_used"], trial["total_time_s"])
    return (1, -trial.get("stickers_in_place", 0), trial["total_time_s"])


def rank_trials(trials: List[Dict[str, Any]]) -> List[str]:
    """Model keys, best first."""
    return [t["model"] for t in sorted(trials, key=rank_key)]


def _round(x: Optional[float], n: int = 2) -> Optional[float]:
    return None if x is None else round(x, n)


def model_stats(model: Dict[str, Any], trials: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trials)
    solved = [t for t in trials if t["status"] == SOLVED]
    latencies = [m["latency_ms"] for t in trials for m in t["moves"]]
    live_latencies = [m["latency_ms"] for t in trials for m in t["moves"] if m.get("source", "live") == "live"]
    cache_ms = [m["cache_ms"] for t in trials for m in t["moves"] if m.get("cache_ms") is not None]
    decision_ms = [m["decision_ms"] for t in trials for m in t["moves"]]
    confidences = [m["confidence"] for t in trials for m in t["moves"] if m.get("confidence") is not None]

    exact_gaps = [t["gap"] for t in solved if t.get("gap") is not None and t.get("gap_kind") == "exact"]
    upper_gaps = [t["gap"] for t in solved if t.get("gap") is not None and t.get("gap_kind") == "upper_bound"]
    if exact_gaps or not upper_gaps:
        gaps, gap_kind = exact_gaps, "exact"
    else:
        gaps, gap_kind = upper_gaps, "upper_bound"

    statuses: Dict[str, int] = {}
    for t in trials:
        statuses[t["status"]] = statuses.get(t["status"], 0) + 1

    costs = [t["cost_usd"] for t in trials if t.get("cost_usd") is not None]
    return {
        "key": model["key"],
        "name": model["name"],
        "category": model["category"],
        "interface": model["interface"],
        "model_id": model["model_id"],
        "mode": model.get("mode", "live"),
        "backend": model.get("backend"),
        "move_limit": trials[0].get("move_limit") if trials else None,
        "trials": n,
        "solved": len(solved),
        "success_rate": _round(100.0 * len(solved) / n, 1) if n else None,
        "statuses": statuses,
        "move_count": len(latencies),
        "avg_latency_ms": _round(mean(latencies), 1),
        "median_latency_ms": _round(percentile(latencies, 50), 1),
        "p95_latency_ms": _round(percentile(latencies, 95), 1),
        "min_latency_ms": _round(min(latencies), 1) if latencies else None,
        "max_latency_ms": _round(max(latencies), 1) if latencies else None,
        "avg_live_latency_ms": _round(mean(live_latencies), 1),
        "avg_cache_response_ms": _round(mean(cache_ms), 3),
        "live_moves": len(live_latencies),
        "cached_moves": len(cache_ms),
        "api_calls": sum(t.get("api_calls", 0) for t in trials),
        "cache_hits": sum(t.get("cache_hits", 0) for t in trials),
        "failed_requests": sum(t.get("failed_requests", 0) for t in trials),
        "avg_decision_ms": _round(mean(decision_ms), 1),
        "avg_total_time_s": _round(mean(t["total_time_s"] for t in trials), 2),
        "sum_total_time_s": _round(sum(t["total_time_s"] for t in trials), 2),
        "avg_solved_time_s": _round(mean(t["total_time_s"] for t in solved), 2),
        "avg_moves": _round(mean(t["moves_used"] for t in trials), 2),
        "total_moves": sum(t["moves_used"] for t in trials),
        "avg_moves_solved": _round(mean(t["moves_used"] for t in solved), 2),
        "mean_gap": _round(mean(gaps), 2),
        "gap_kind": gap_kind,
        "min_gap": min(gaps) if gaps else None,
        "max_gap": max(gaps) if gaps else None,
        "optimal_solves": sum(1 for g in gaps if g == 0),
        "optimal_rate": _round(100.0 * sum(1 for g in gaps if g == 0) / n, 1) if n and gaps is not None else None,
        "invalid_outputs": sum(t.get("invalid_outputs", 0) for t in trials),
        "timeouts": sum(t.get("timeouts", 0) for t in trials),
        "http_errors": sum(t.get("http_errors", 0) for t in trials),
        "retries": sum(t.get("retries", 0) for t in trials),
        "failed_attempts": sum(t.get("failed_attempts", 0) for t in trials),
        "avg_confidence": _round(mean(confidences), 3),
        "cost_usd": _round(sum(costs), 6) if costs else None,
        "tokens_in": sum(t.get("tokens_in") or 0 for t in trials),
        "tokens_out": sum(t.get("tokens_out") or 0 for t in trials),
    }


def aggregate(doc: Dict[str, Any]) -> Dict[str, Any]:
    models = doc.get("models", [])
    trials = doc.get("trials", [])
    per_model = [model_stats(m, [t for t in trials if t["model"] == m["key"]]) for m in models]

    puzzles = {p["id"]: p for p in doc.get("puzzles", [])}
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for t in trials:
        groups.setdefault((t["puzzle_id"], t.get("repeat", 0)), []).append(t)
    per_puzzle = []
    for (pid, rep), ts in groups.items():
        p = puzzles.get(pid, {})
        per_puzzle.append(
            {
                "puzzle_id": pid,
                "repeat": rep,
                "scramble": p.get("scramble", []),
                "scramble_len": p.get("scramble_len"),
                "reference_moves": p.get("reference_moves"),
                "reference_kind": p.get("reference_kind"),
                "results": {
                    t["model"]: {
                        "status": t["status"],
                        "moves": t["moves_used"],
                        "time_s": round(t["total_time_s"], 2),
                        "gap": t.get("gap"),
                        "avg_latency_ms": _round(mean(m["latency_ms"] for m in t["moves"]), 1),
                    }
                    for t in ts
                },
                "ranking": rank_trials(ts),
            }
        )

    return {
        "run_id": doc.get("run_id"),
        "test_set": doc.get("config", {}).get("test_set"),
        "mode": doc.get("mode"),
        "n_puzzles": len(puzzles),
        "n_trials": len(trials),
        "models": per_model,
        "per_puzzle": per_puzzle,
        "best": best_models(per_model),
    }


def best_models(per_model: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Which model(s) are best on each question of the assignment."""

    def argbest(field: str, lowest: bool) -> List[str]:
        vals = [(m[field], m["key"]) for m in per_model if m.get(field) is not None]
        if not vals:
            return []
        target = min(v for v, _ in vals) if lowest else max(v for v, _ in vals)
        return [k for v, k in vals if v == target]

    # "quickest" only makes sense for models that solved something (an instant error is not quick)
    solvers = [m for m in per_model if m.get("solved")]
    quickest = []
    if solvers:
        target = min(m["avg_total_time_s"] for m in solvers)
        quickest = [m["key"] for m in solvers if m["avg_total_time_s"] == target]

    return {
        "fastest": argbest("avg_latency_ms", True),
        "solve_rate": argbest("success_rate", False),
        "shortest": argbest("mean_gap", True),
        "fewest_moves_solved": argbest("avg_moves_solved", True),
        "quickest_total": quickest,
    }
