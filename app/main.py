"""FastAPI backend.

    GET  /api/config              settings that are safe to show, rosters of the three backends, limits, usage
    GET  /api/backends            which backends can be used right now (key found? Ollama running?)
    GET  /api/usage               request counters, cache size and cache hits of every backend
    POST /api/simulated/reset     clear the SIMULATED practice counters and cache (never the real ones)
    POST /api/scramble            make a puzzle (preview, with the exact shortest solution)
    POST /api/runs                start a benchmark run in the background
    GET  /api/runs                list saved runs
    GET  /api/runs/{id}           run document (live or saved) + statistics
    GET  /api/runs/{id}/events    live events (Server-Sent Events)
    POST /api/runs/{id}/cancel    stop a running benchmark

The OpenRouter key stays in this process. The browser never receives it.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .agents.registry import get_specs
from .config import BACKENDS, ROOT, Settings, load_settings
from .cube.puzzles import TEST_SETS, make_puzzle
from .cube.solver import OptimalSolver
from .events import EventBus
from .ollama_status import check_ollama
from .stats import aggregate
from .storage import list_runs, load_run
from .store import StoreError
from .suite import SuiteConfig, execute_run, make_store, new_run_doc

FRONTEND_DIR = ROOT / "frontend"


class ScrambleRequest(BaseModel):
    test_set: str = "teacher"
    seed: int = 42
    index: int = Field(0, ge=0, le=500)


class RunRequest(BaseModel):
    backend: str = "simulated"
    test_set: str = "teacher"
    num_puzzles: int = Field(1, ge=1, le=200)
    seed: int = 42
    models: Optional[List[str]] = None
    parallel: bool = True
    repeats: int = Field(1, ge=1, le=20)
    pause_s: float = Field(0, ge=0, le=120)
    student_name: str = ""
    student_roll_no: str = ""
    confirm_live: bool = False


@dataclass
class RunState:
    doc: Dict[str, Any]
    bus: EventBus
    task: Optional[asyncio.Task] = None


class RunManager:
    def __init__(self, settings: Settings, solver: OptimalSolver) -> None:
        self.settings = settings
        self.solver = solver
        self.runs: Dict[str, RunState] = {}

    async def start(self, cfg: SuiteConfig) -> RunState:
        if any(r.task and not r.task.done() for r in self.runs.values()):
            raise HTTPException(409, "A benchmark is already running. Stop it first or wait until it is finished.")
        if cfg.backend == "ollama":
            status = await check_ollama(self.settings)
            if not status["reachable"]:
                raise HTTPException(400, f"Ollama is not running at {self.settings.ollama_base_url}. Start the Ollama app and try again.")
            chosen = set(cfg.model_keys or [m["key"] for m in status["models"]])
            missing = [m for m in status["models"] if m["key"] in chosen and not m["installed"]]
            if missing:
                raise HTTPException(400, "These Ollama models are not installed: " + "; ".join(f"{m['model_id']} (run: {m['pull']})" for m in missing))
        try:
            doc = new_run_doc(cfg, self.settings)
            make_store(self.settings, cfg.backend)  # fails early if a usage file is damaged
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except StoreError as exc:
            raise HTTPException(500, str(exc))
        state = RunState(doc=doc, bus=EventBus())
        state.task = asyncio.create_task(execute_run(doc, self.settings, self.solver, state.bus))
        self.runs[doc["run_id"]] = state
        return state


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    solver = OptimalSolver()
    manager = RunManager(settings, solver)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await asyncio.to_thread(lambda: solver.table)  # build the BFS table once
        yield
        for state in manager.runs.values():
            if state.task and not state.task.done():
                state.task.cancel()

    app = FastAPI(title="Rubik's Cube Race", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.manager = manager

    def usage_all() -> Dict[str, Any]:
        out = {}
        for b in BACKENDS:
            try:
                out[b] = make_store(settings, b).snapshot()
            except StoreError as exc:
                out[b] = {"error": str(exc)}
        return out

    async def backends_info() -> Dict[str, Any]:
        oll = await check_ollama(settings)
        return {
            "simulated": {"available": True, "note": "Practice mode: no real model, no real request."},
            "ollama": {"available": oll["reachable"], **oll},
            "openrouter": {"available": settings.has_api_key,
                           "note": "Real models. Limited requests." if settings.has_api_key else "No OPENROUTER_API_KEY in .env"},
        }

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/config")
    async def config() -> dict:
        return {
            "version": __version__,
            "student": {"name": settings.student_name, "roll_no": settings.student_roll_no},
            "settings": settings.public_dict(),
            "rosters": {b: [s.to_dict() for s in get_specs(settings, b)] for b in BACKENDS},
            "backends": await backends_info(),
            "usage": usage_all(),
            "test_sets": [
                {"key": t.key, "label": t.label, "min_turns": t.min_turns, "max_turns": t.max_turns,
                 "default_count": t.default_count, "purpose": t.purpose}
                for t in TEST_SETS.values()
            ],
        }

    @app.get("/api/backends")
    async def backends() -> dict:
        return await backends_info()

    @app.get("/api/usage")
    async def usage() -> dict:
        return usage_all()

    @app.post("/api/simulated/reset")
    async def reset_simulated() -> dict:
        if any(r.task and not r.task.done() for r in manager.runs.values()):
            raise HTTPException(409, "A benchmark is running.")
        make_store(settings, "simulated").clear_all()  # only the simulated folder, never openrouter or ollama
        return {"ok": True, "usage": make_store(settings, "simulated").snapshot()}

    @app.post("/api/scramble")
    async def scramble(body: ScrambleRequest) -> dict:
        if body.test_set not in TEST_SETS:
            raise HTTPException(400, f"unknown test set {body.test_set!r}")
        puzzle = await asyncio.to_thread(make_puzzle, body.test_set, body.seed, body.index, solver)
        return puzzle.to_dict()

    @app.post("/api/runs")
    async def start_run(body: RunRequest) -> dict:
        cfg = SuiteConfig(
            backend=body.backend,
            test_set=body.test_set,
            num_puzzles=body.num_puzzles,
            seed=body.seed,
            model_keys=body.models or None,
            parallel=body.parallel,
            repeats=body.repeats,
            pause_s=body.pause_s,
            student_name=body.student_name,
            student_roll_no=body.student_roll_no,
            confirm_live=body.confirm_live,
        )
        state = await manager.start(cfg)
        return {"run_id": state.doc["run_id"], "mode": state.doc["mode"]}

    @app.get("/api/runs")
    async def runs() -> dict:
        saved = await asyncio.to_thread(list_runs, settings.results_dir)
        live = [
            {"run_id": rid, "created_at": s.doc["created_at"], "status": s.doc["status"], "mode": s.doc["mode"],
             "test_set": s.doc["config"]["test_set"], "n_puzzles": len(s.doc["puzzles"]), "n_trials": len(s.doc["trials"])}
            for rid, s in manager.runs.items()
        ]
        live_ids = {r["run_id"] for r in live}
        return {"runs": live + [r for r in saved if r["run_id"] not in live_ids]}

    def _find(run_id: str) -> Optional[Dict[str, Any]]:
        if run_id in manager.runs:
            return manager.runs[run_id].doc
        return load_run(run_id, settings.results_dir)

    @app.get("/api/runs/{run_id}")
    async def run_doc(run_id: str, full: bool = Query(False)) -> dict:
        doc = _find(run_id)
        if doc is None:
            raise HTTPException(404, "run not found")
        out = {k: v for k, v in doc.items() if k != "trials"} if not full else dict(doc)
        out["stats"] = aggregate(doc)
        out["n_trials"] = len(doc["trials"])
        return out

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request, last: int = Query(0, ge=0)) -> StreamingResponse:
        state = manager.runs.get(run_id)
        if state is None:
            raise HTTPException(404, "run is not active in this server session")
        start = last
        header = request.headers.get("last-event-id")
        if header and header.isdigit():
            start = int(header) + 1

        async def gen():
            async for event in state.bus.stream(start):
                if await request.is_disconnected():
                    return
                if event is None:
                    yield ": keep-alive\n\n"
                else:
                    yield f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n"

        return StreamingResponse(
            gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel(run_id: str) -> dict:
        state = manager.runs.get(run_id)
        if state is None or state.task is None:
            raise HTTPException(404, "run not found")
        if not state.task.done():
            state.task.cancel()
        return {"ok": True}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
    return app


app = create_app()


def main() -> None:  # pragma: no cover
    import uvicorn

    s = load_settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port, reload=False)


if __name__ == "__main__":  # pragma: no cover
    main()
