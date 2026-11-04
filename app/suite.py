"""A benchmark run (suite): several puzzles, every chosen model plays each one."""
from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from .agents.registry import build_agents, get_specs
from .config import BACKENDS, Settings
from .agents.contract import prompt_style
from .cube.puzzles import TEST_SETS, make_puzzles
from .cube.solver import OptimalSolver
from .events import EventBus
from .gateway import Gateway
from .race import ranking, run_race
from .stats import aggregate
from .storage import save_run
from .store import Limits, UsageStore, get_store


def make_store(settings: Settings, backend: str) -> UsageStore:
    """The persistent cache + counters of one backend.

    OpenRouter and the simulated practice mode get the teacher's limits
    (Claude 5, decision models 20, total 65). Ollama is local and free: no limits.
    """
    limits: Optional[Limits] = None
    if backend in ("openrouter", "simulated"):
        specs = get_specs(settings, "openrouter")
        per = {s.key: settings.request_cap_for(s.category) for s in specs}
        cap = settings.max_cost_usd if (backend == "openrouter" and settings.max_cost_usd > 0) else None
        est = {s.key: (settings.cost_estimate_llm_usd if s.category == "llm" else settings.cost_estimate_decision_usd) for s in specs}
        limits = Limits(per_model=per, total=settings.total_request_limit, max_cost_usd=cap, cost_estimate=est)
    return get_store(settings.store_dir(backend), backend, limits)


@dataclass
class SuiteConfig:
    backend: str = "simulated"
    test_set: str = "teacher"
    num_puzzles: int = 1
    seed: int = 42
    model_keys: Optional[List[str]] = None  # None = every model of the backend
    parallel: bool = True
    repeats: int = 1
    pause_s: float = 0.0  # pause between races, so the result stays on screen (UI only)
    student_name: str = ""
    student_roll_no: str = ""
    confirm_live: bool = False  # must be True for backend "openrouter": it spends real requests

    def validate(self) -> None:
        if self.backend not in BACKENDS:
            raise ValueError(f"unknown backend {self.backend!r}; use one of {list(BACKENDS)}")
        if self.test_set not in TEST_SETS:
            raise ValueError(f"unknown test set {self.test_set!r}; use one of {sorted(TEST_SETS)}")
        if not 1 <= self.num_puzzles <= 200:
            raise ValueError("num_puzzles must be between 1 and 200")
        if not 1 <= self.repeats <= 20:
            raise ValueError("repeats must be between 1 and 20")
        if self.model_keys is not None and not self.model_keys:
            raise ValueError("choose at least one model")


def new_run_doc(cfg: SuiteConfig, settings: Settings) -> Dict[str, Any]:
    cfg.validate()
    if cfg.backend == "openrouter":
        if not settings.has_api_key:
            raise ValueError("OPENROUTER_API_KEY is not set. Add it to .env (see .env.example).")
        if not cfg.confirm_live:
            raise ValueError("This run spends real, limited OpenRouter requests. Confirm it first (confirm_live).")
    specs = get_specs(settings, cfg.backend, cfg.model_keys)
    if cfg.model_keys:
        unknown = set(cfg.model_keys) - {s.key for s in specs}
        if unknown:
            raise ValueError(f"unknown model keys for backend {cfg.backend}: {sorted(unknown)}")
    if not specs:
        raise ValueError("no models selected")
    now = dt.datetime.now()
    run_id = f"run-{now:%Y%m%d-%H%M%S}-{cfg.backend}-{cfg.test_set}"
    return {
        "run_id": run_id,
        "created_at": now.isoformat(timespec="seconds"),
        "finished_at": None,
        "status": "running",
        "mode": cfg.backend,
        "config": {
            "backend": cfg.backend,
            "test_set": cfg.test_set,
            "num_puzzles": cfg.num_puzzles,
            "seed": cfg.seed,
            "repeats": cfg.repeats,
            "parallel": cfg.parallel,
            "pause_s": cfg.pause_s,
            "prompt_style": prompt_style(),
            "models": [s.key for s in specs],
            "move_limit": settings.move_limit,
            "move_limits": {s.key: settings.move_limit_for(cfg.backend, s.category) for s in specs},
            "trial_time_limit_s": settings.trial_time_limit_for(cfg.backend),
            "request_timeout_s": settings.request_timeout_for(cfg.backend),
            "max_attempts": settings.max_attempts,
            "retry_backoff_s": settings.retry_backoff_s,
            "recent_moves_window": settings.recent_moves_window,
            "request_limits": ({"claude": settings.max_claude_requests, "per_decision_model": settings.max_requests_per_decision_model,
                                "total": settings.total_request_limit,
                                "max_cost_usd": settings.max_cost_usd or None} if cfg.backend != "ollama" else None),
            "opus_reasoning_effort": settings.opus_reasoning_effort or "provider default",
            "student": {"name": cfg.student_name or settings.student_name, "roll_no": cfg.student_roll_no or settings.student_roll_no},
        },
        "models": [{**s.to_dict(), "mode": cfg.backend} for s in specs],
        "puzzles": [],
        "trials": [],
    }


async def execute_run(
    doc: Dict[str, Any],
    settings: Settings,
    solver: OptimalSolver,
    bus: Optional[EventBus] = None,
    save: bool = True,
) -> Dict[str, Any]:
    """Run the whole suite. Mutates and returns ``doc``. Safe to cancel: partial results are saved."""
    emit = bus.emit if bus else (lambda *a, **k: None)
    cfg = doc["config"]
    backend = cfg["backend"]
    client: Optional[httpx.AsyncClient] = None
    agents: Dict[str, Any] = {}
    store: Optional[UsageStore] = None
    try:
        store = make_store(settings, backend)
        gateway = Gateway(store, backend, on_usage=lambda snap: emit("usage", backend=backend, usage=snap))
        puzzles = await asyncio.to_thread(make_puzzles, cfg["test_set"], cfg["num_puzzles"], cfg["seed"], solver)
        doc["puzzles"] = [p.to_dict() for p in puzzles]
        emit("run_start", run_id=doc["run_id"], mode=doc["mode"], backend=backend, config=cfg, models=doc["models"],
             puzzles=doc["puzzles"], total_races=len(puzzles) * cfg["repeats"])
        emit("usage", backend=backend, usage=store.snapshot())

        if backend != "simulated":
            client = httpx.AsyncClient(limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
        agents = build_agents(settings, backend, cfg["models"], client, gateway)

        race_no = 0
        total = len(puzzles) * cfg["repeats"]
        for puzzle in puzzles:
            for rep in range(cfg["repeats"]):
                race_no += 1
                emit("puzzle_start", puzzle=puzzle.to_dict(), repeat=rep, race_no=race_no, total_races=total)
                trials = await run_race(agents, puzzle, settings, emit, cfg["parallel"], rep)
                doc["trials"].extend(trials)
                doc["api_usage"] = store.snapshot()
                stats = aggregate(doc)
                emit("puzzle_end", puzzle_id=puzzle.id, repeat=rep, ranking=ranking(trials), stats=stats,
                     race_no=race_no, total_races=total)
                if save:
                    await asyncio.to_thread(save_run, doc, settings.results_dir)
                if cfg.get("pause_s") and race_no < total:
                    await asyncio.sleep(cfg["pause_s"])
        doc["status"] = "finished"
    except asyncio.CancelledError:
        doc["status"] = "cancelled"
    except Exception as exc:  # keep partial results, tell the browser what happened
        doc["status"] = "error"
        doc["error"] = f"{exc.__class__.__name__}: {exc}"
        emit("error", message=doc["error"])
    finally:
        for agent in agents.values():
            try:
                await agent.aclose()
            except Exception:
                pass
        if client is not None:
            await client.aclose()
        doc["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
        if store is not None:
            doc["api_usage"] = store.snapshot()
        stats = aggregate(doc)
        saved = None
        if save and doc["puzzles"]:
            saved = str(await asyncio.to_thread(save_run, doc, settings.results_dir))
        emit("run_end", status=doc["status"], run_id=doc["run_id"], stats=stats, saved_to=saved)
        if bus:
            bus.close()
    return doc
