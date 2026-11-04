"""Save and load benchmark runs (JSON + CSV files in results/runs/<run_id>/)."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .stats import aggregate

TRIAL_COLUMNS = [
    "run_id", "mode", "backend", "test_set", "puzzle_id", "repeat", "model", "model_id", "category", "status", "solved",
    "move_limit", "moves_used", "reference_moves", "reference_kind", "gap", "total_time_s", "avg_latency_ms",
    "api_calls", "cache_hits", "failed_requests", "live_moves", "cached_moves",
    "failed_attempts", "retries", "invalid_outputs", "timeouts", "http_errors", "cost_usd", "error_message",
]
MOVE_COLUMNS = [
    "run_id", "puzzle_id", "repeat", "model", "move_no", "move", "source", "latency_ms", "cache_ms", "decision_ms",
    "confidence", "failed_attempts", "elapsed_s", "stickers_in_place", "cost_usd",
]
SUMMARY_COLUMNS = [
    "model", "model_id", "backend", "category", "move_limit", "trials", "solved", "success_rate", "avg_latency_ms",
    "median_latency_ms", "p95_latency_ms", "avg_live_latency_ms", "avg_cache_response_ms", "live_moves", "cached_moves",
    "api_calls", "cache_hits", "failed_requests", "avg_decision_ms", "avg_total_time_s", "sum_total_time_s",
    "avg_solved_time_s", "avg_moves", "total_moves", "avg_moves_solved", "mean_gap", "gap_kind", "optimal_solves",
    "invalid_outputs", "timeouts", "http_errors", "retries", "avg_confidence", "cost_usd",
]


def runs_dir(results_dir: Path) -> Path:
    return Path(results_dir) / "runs"


def save_run(doc: Dict[str, Any], results_dir: Path) -> Path:
    folder = runs_dir(results_dir) / doc["run_id"]
    folder.mkdir(parents=True, exist_ok=True)
    stats = aggregate(doc)
    out = dict(doc)
    out["stats"] = stats
    tmp = folder / "run.json.tmp"
    tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
    tmp.replace(folder / "run.json")

    test_set = doc.get("config", {}).get("test_set")
    with open(folder / "trials.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TRIAL_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for t in doc["trials"]:
            lat = [m["latency_ms"] for m in t["moves"]]
            w.writerow({**t, "run_id": doc["run_id"], "test_set": test_set,
                        "avg_latency_ms": round(sum(lat) / len(lat), 1) if lat else ""})
    with open(folder / "moves.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MOVE_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for t in doc["trials"]:
            for m in t["moves"]:
                w.writerow({**m, "run_id": doc["run_id"], "puzzle_id": t["puzzle_id"], "repeat": t["repeat"],
                            "model": t["model"], "move_no": m["n"]})
    with open(folder / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for m in stats["models"]:
            w.writerow(m)
    return folder / "run.json"


def load_run(run_id_or_path: str, results_dir: Path) -> Optional[Dict[str, Any]]:
    p = Path(run_id_or_path)
    if not p.suffix:
        p = runs_dir(results_dir) / run_id_or_path / "run.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_runs(results_dir: Path) -> List[Dict[str, Any]]:
    out = []
    base = runs_dir(results_dir)
    if not base.exists():
        return out
    for folder in sorted(base.iterdir(), reverse=True):
        f = folder / "run.json"
        if not f.exists():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append(
            {
                "run_id": d.get("run_id"),
                "created_at": d.get("created_at"),
                "status": d.get("status"),
                "mode": d.get("mode"),
                "test_set": d.get("config", {}).get("test_set"),
                "n_puzzles": len(d.get("puzzles", [])),
                "n_trials": len(d.get("trials", [])),
            }
        )
    return out
