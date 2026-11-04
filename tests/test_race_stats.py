"""Race runner, statistics, storage and the HTTP API (all with simulated agents)."""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.agents.base import Agent, MoveRequest, RawResponse, RetryPolicy
from app.agents.registry import MODEL_SPECS, build_agents
from app.config import Settings
from app.cube.engine import Cube
from app.cube.puzzles import make_puzzle
from app.events import EventBus
from app.main import create_app
from app.race import run_race, run_trial
from app.stats import OUT_OF_TIME, RAN_OUT_OF_MOVES, SOLVED, ERROR, aggregate, percentile, rank_trials
from app.storage import list_runs, load_run, save_run
from app.suite import SuiteConfig, execute_run, new_run_doc


def run(coro):
    return asyncio.run(coro)


class PerfectAgent(Agent):
    """Plays the known optimal solution (for testing the runner)."""
    def __init__(self, key="jev"):
        spec = next(s for s in MODEL_SPECS if s.key == key)
        super().__init__(spec, RetryPolicy(3, 5, 0))
    async def prepare(self, puzzle):
        self.plan = list(puzzle.reference_solution)
    async def _call(self, req, timeout_s):
        return RawResponse(move=self.plan.pop(0), http_ms=1.0, confidence=0.9)


class RepeatAgent(PerfectAgent):
    """Always plays U (never solves)."""
    async def _call(self, req, timeout_s):
        return RawResponse(move="U", http_ms=2.0)


class BrokenAgent(PerfectAgent):
    async def _call(self, req, timeout_s):
        return RawResponse(move="nonsense")


class SlowAgent(PerfectAgent):
    async def _call(self, req, timeout_s):
        await asyncio.sleep(0.05)
        return RawResponse(move="U", http_ms=50.0)


def test_solved_trial_has_zero_gap(solver, sim_settings):
    p = make_puzzle("primary", 1, 0, solver)
    t = run(run_trial(PerfectAgent(), p, sim_settings))
    assert t["status"] == SOLVED and t["solved"]
    assert t["moves_used"] == p.reference_moves and t["gap"] == 0 and t["gap_kind"] == "exact"
    assert Cube(t["final_facelets"]).is_solved()
    assert all(m["latency_ms"] == 1.0 for m in t["moves"])


def test_move_limit_is_a_failure(solver, sim_settings):
    p = make_puzzle("primary", 1, 0, solver)
    t = run(run_trial(RepeatAgent(), p, sim_settings))
    assert t["status"] == RAN_OUT_OF_MOVES and not t["solved"]
    assert t["moves_used"] == sim_settings.move_limit == 20
    assert t["gap"] is None


def test_invalid_outputs_end_in_error_and_are_counted(solver, sim_settings):
    p = make_puzzle("primary", 1, 0, solver)
    t = run(run_trial(BrokenAgent(), p, sim_settings))
    assert t["status"] == ERROR
    assert t["moves_used"] == 0
    assert t["invalid_outputs"] == 3 and t["failed_attempts"] == 3 and t["retries"] == 2
    assert "invalid_move" in t["error_message"]


def test_trial_time_limit(solver, sim_settings):
    from dataclasses import replace
    s = replace(sim_settings, trial_time_limit_s=0.12)
    p = make_puzzle("primary", 1, 0, solver)
    t = run(run_trial(SlowAgent(), p, s))
    assert t["status"] == OUT_OF_TIME and not t["solved"]
    assert t["total_time_s"] >= 0.12


def test_recent_moves_window(solver, sim_settings):
    seen = []

    class Spy(PerfectAgent):
        async def _call(self, req, timeout_s):
            seen.append(list(req.recent_moves))
            return RawResponse(move="U", http_ms=1.0)

    p = make_puzzle("primary", 1, 0, solver)
    run(run_trial(Spy(), p, sim_settings))
    assert seen[0] == [] and len(seen[3]) == 3 and max(len(x) for x in seen) == sim_settings.recent_moves_window


def test_all_models_start_from_the_same_state(solver, sim_settings):
    p = make_puzzle("primary", 3, 2, solver)
    starts = []

    class Spy(PerfectAgent):
        async def _call(self, req, timeout_s):
            if req.moves_made == 0:
                starts.append(req.facelets)
            return await super()._call(req, timeout_s)

    agents = {k: Spy(k) for k in ("jev", "luna", "clef", "opus")}
    trials = run(run_race(agents, p, sim_settings))
    assert len(starts) == 4 and len(set(starts)) == 1 and starts[0] == p.facelets
    assert all(t["status"] == SOLVED for t in trials)


def test_ranking_rule(solver, sim_settings):
    p = make_puzzle("primary", 1, 0, solver)
    good = run(run_trial(PerfectAgent("opus"), p, sim_settings))
    bad = run(run_trial(RepeatAgent("jev"), p, sim_settings))
    err = run(run_trial(BrokenAgent("clef"), p, sim_settings))
    assert rank_trials([bad, err, good])[0] == "opus"


def test_percentile():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([5], 95) == 5
    assert percentile([], 50) is None


def test_suite_end_to_end_with_simulated_models(solver, big_settings):
    sim_settings = big_settings
    cfg = SuiteConfig(backend="simulated", test_set="primary", num_puzzles=4, seed=7, repeats=2)
    doc = new_run_doc(cfg, sim_settings)
    bus = EventBus()
    run(execute_run(doc, sim_settings, solver, bus))
    assert doc["status"] == "finished" and doc["mode"] == "simulated" and doc["run_id"].endswith("-simulated-primary")
    assert len(doc["puzzles"]) == 4 and len(doc["trials"]) == 4 * 4 * 2
    assert all(t["mode"] == "simulated" and t["backend"] == "simulated" for t in doc["trials"])

    types = [e["type"] for e in bus.events]
    assert types[0] == "run_start" and types[-1] == "run_end"
    assert types.count("puzzle_start") == 8 and types.count("puzzle_end") == 8
    assert types.count("trial_end") == 32 and "move" in types and "thinking" in types and "usage" in types

    # repeat 1 = real (simulated) requests, repeat 2 = the same puzzle again = answered from the cache
    first = [t for t in doc["trials"] if t["repeat"] == 0]
    second = [t for t in doc["trials"] if t["repeat"] == 1]
    assert all(t["api_calls"] > 0 and t["cache_hits"] == 0 for t in first if t["moves_used"])
    assert all(t["api_calls"] == 0 and t["cached_moves"] == t["moves_used"] for t in second)
    assert [t["status"] for t in first] == [t["status"] for t in second]

    stats = aggregate(doc)
    assert len(stats["models"]) == 4 and len(stats["per_puzzle"]) == 8
    for m in stats["models"]:
        assert m["trials"] == 8 and 0 <= m["success_rate"] <= 100
        assert m["avg_latency_ms"] is not None
        assert m["api_calls"] > 0 and m["cache_hits"] > 0
        if m["solved"]:
            assert m["avg_moves_solved"] is not None and m["mean_gap"] is not None
    assert stats["best"]["fastest"]

    folder = sim_settings.results_dir / "runs" / doc["run_id"]
    for name in ("run.json", "trials.csv", "moves.csv", "summary.csv"):
        assert (folder / name).exists()
    loaded = load_run(doc["run_id"], sim_settings.results_dir)
    assert loaded["stats"]["n_trials"] == 32
    assert loaded["api_usage"]["total_requests"] == sum(t["api_calls"] for t in doc["trials"])
    assert list_runs(sim_settings.results_dir)[0]["run_id"] == doc["run_id"]
    assert "api_key" not in json.dumps(loaded).lower()


def test_suite_can_be_cancelled_and_keeps_partial_results(solver, tmp_path):
    s = Settings(mock_latency_scale=1.0, retry_backoff_s=0.0, results_dir=tmp_path / "r", data_dir=tmp_path / "d")

    async def go():
        doc = new_run_doc(SuiteConfig(test_set="primary", num_puzzles=5, seed=1, model_keys=["jev"]), s)
        task = asyncio.create_task(execute_run(doc, s, solver, EventBus()))
        await asyncio.sleep(0.8)
        task.cancel()
        await task
        return doc

    doc = run(go())
    assert doc["status"] == "cancelled"
    assert (s.results_dir / "runs" / doc["run_id"] / "run.json").exists()


def test_bad_suite_config():
    s = Settings()
    with pytest.raises(ValueError):
        new_run_doc(SuiteConfig(test_set="nope"), s)
    with pytest.raises(ValueError):
        new_run_doc(SuiteConfig(model_keys=["gpt-x"]), s)
    with pytest.raises(ValueError):
        new_run_doc(SuiteConfig(backend="nowhere"), s)


def test_openrouter_run_needs_key_and_confirmation():
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        new_run_doc(SuiteConfig(backend="openrouter", confirm_live=True), Settings())
    with pytest.raises(ValueError, match="Confirm"):
        new_run_doc(SuiteConfig(backend="openrouter"), Settings(openrouter_api_key="test-key-0123456789"))
    doc = new_run_doc(SuiteConfig(backend="openrouter", confirm_live=True), Settings(openrouter_api_key="test-key-0123456789"))
    assert doc["config"]["move_limits"] == {"jev": 20, "luna": 20, "clef": 20, "opus": 5}
    assert doc["config"]["request_limits"]["total"] == 65


def test_claude_is_stopped_after_5_moves_and_budget_end_is_a_status(solver, sim_settings):
    """Claude has 5 requests: it can play at most 5 moves; with the budget gone a model ends as BUDGET_EXHAUSTED."""
    from app.stats import BUDGET_EXHAUSTED
    from app.suite import make_store

    p = make_puzzle("primary", 1, 0, solver)
    agents = build_agents(sim_settings, "simulated", ["opus"], None, __import__("app.gateway", fromlist=["Gateway"]).Gateway(make_store(sim_settings, "simulated"), "simulated"))
    t = run(run_trial(agents["opus"], p, sim_settings))
    assert t["move_limit"] == 5 and t["moves_used"] <= 5 and t["api_calls"] <= 5
    # a different puzzle: the 5 requests are already spent, nothing may be sent
    p2 = make_puzzle("primary", 2, 1, solver)
    t2 = run(run_trial(agents["opus"], p2, sim_settings))
    assert t2["status"] == BUDGET_EXHAUSTED and t2["api_calls"] == 0
    assert make_store(sim_settings, "simulated").snapshot()["models"]["opus"]["requests"] == 5


# ------------------------------------------------------------- HTTP API
@pytest.fixture
def client(big_settings):
    with TestClient(create_app(big_settings)) as c:
        yield c


def test_api_config_has_no_secret(sim_settings):
    with TestClient(create_app(sim_settings)) as c:
        _check_config(c)


def _check_config(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    data = r.json()
    assert data["student"] == {"name": "Test Student", "roll_no": "TEST-001"}
    assert [m["key"] for m in data["rosters"]["openrouter"]] == ["jev", "luna", "clef", "opus"]
    assert [m["key"] for m in data["rosters"]["ollama"]] == ["ol_clef", "ol_qwen", "ol_deepseek", "ol_gemma"]
    s = data["settings"]
    assert s["move_limit"] == 20 and s["max_claude_requests"] == 5 and s["max_requests_per_decision_model"] == 20
    assert [t["key"] for t in data["test_sets"]] == ["teacher", "warmup", "primary", "extension"]
    assert set(data["usage"]) == {"openrouter", "ollama", "simulated"}
    assert data["backends"]["simulated"]["available"] is True


def test_api_never_returns_the_key(tmp_path, big_settings):
    from dataclasses import replace
    secret = "test-key-0123456789supersecretvalue"
    s = replace(big_settings, openrouter_api_key=secret)
    with TestClient(create_app(s)) as c:
        assert c.get("/api/config").json()["settings"]["has_api_key"] is True
        assert secret not in c.get("/api/config").text
        rid = c.post("/api/runs", json={"backend": "simulated", "test_set": "warmup", "num_puzzles": 1}).json()["run_id"]
        with c.stream("GET", f"/api/runs/{rid}/events") as resp:
            body = "".join(resp.iter_lines())
        assert secret not in body
        assert secret not in c.get(f"/api/runs/{rid}?full=true").text
        assert secret not in c.get("/api/usage").text
    for folder in (s.results_dir, s.data_dir):  # not in any file on disk either (results, cache, counters, request log)
        for f in folder.rglob("*"):
            if f.is_file():
                assert secret not in f.read_text(encoding="utf-8")


def test_api_serves_frontend(client):
    r = client.get("/")
    assert r.status_code == 200 and "Rubik" in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_api_scramble(client):
    r = client.post("/api/scramble", json={"test_set": "primary", "seed": 5, "index": 0})
    assert r.status_code == 200
    p = r.json()
    assert len(p["facelets"]) == 54 and p["reference_kind"] == "exact" and 4 <= p["scramble_len"] <= 5
    t = client.post("/api/scramble", json={"test_set": "teacher", "seed": 5, "index": 0}).json()
    assert t["reference_moves"] == 3 and t["reference_kind"] == "exact"  # exactly 3 moves from solved
    assert client.post("/api/scramble", json={"test_set": "bad"}).status_code == 400


def test_api_run_flow_with_sse(client):
    r = client.post("/api/runs", json={"backend": "simulated", "test_set": "warmup", "num_puzzles": 2, "seed": 3})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    events = []
    with client.stream("GET", f"/api/runs/{run_id}/events") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert events[0]["type"] == "run_start" and events[-1]["type"] == "run_end"
    assert [e["seq"] for e in events] == list(range(len(events)))
    d = client.get(f"/api/runs/{run_id}").json()
    assert d["status"] == "finished" and d["stats"]["n_trials"] == 8
    assert any(x["run_id"] == run_id for x in client.get("/api/runs").json()["runs"])
    assert client.get("/api/runs/does-not-exist").status_code == 404
    full = client.get(f"/api/runs/{run_id}?full=true").json()
    assert len(full["trials"]) == 8


def test_api_rejects_second_run_while_running(sim_settings):
    from dataclasses import replace
    s = replace(sim_settings, mock_latency_scale=1.0)
    with TestClient(create_app(s)) as c:
        first = c.post("/api/runs", json={"test_set": "primary", "num_puzzles": 5}).json()["run_id"]
        assert c.post("/api/runs", json={"test_set": "primary", "num_puzzles": 1}).status_code == 409
        assert c.post(f"/api/runs/{first}/cancel").status_code == 200


def test_api_real_run_needs_key_and_confirmation(sim_settings):
    from dataclasses import replace
    with TestClient(create_app(sim_settings)) as c:
        r = c.post("/api/runs", json={"backend": "openrouter", "confirm_live": True})
        assert r.status_code == 400 and "OPENROUTER_API_KEY" in r.json()["detail"]
    with TestClient(create_app(replace(sim_settings, openrouter_api_key="test-key-0123456789"))) as c:
        r = c.post("/api/runs", json={"backend": "openrouter"})
        assert r.status_code == 400 and "Confirm" in r.json()["detail"]
        assert c.get("/api/usage").json()["openrouter"]["total_requests"] == 0  # nothing was sent


def test_api_ollama_not_running_gives_a_clear_error(sim_settings):
    from dataclasses import replace
    s = replace(sim_settings, ollama_base_url="http://127.0.0.1:9")
    with TestClient(create_app(s)) as c:
        r = c.post("/api/runs", json={"backend": "ollama"})
        assert r.status_code == 400 and "Ollama is not running" in r.json()["detail"]
        assert c.get("/api/backends").json()["ollama"]["available"] is False


def test_api_reset_touches_only_simulated(sim_settings):
    from app.suite import make_store
    make_store(sim_settings, "simulated").reserve("jev", "typesafe/jev-1.13")
    make_store(sim_settings, "openrouter").reserve("jev", "typesafe/jev-1.13")
    with TestClient(create_app(sim_settings)) as c:
        assert c.post("/api/simulated/reset").status_code == 200
        u = c.get("/api/usage").json()
        assert u["simulated"]["total_requests"] == 0 and u["openrouter"]["total_requests"] == 1


def test_teacher_puzzle_is_exactly_three_moves_from_solved(solver):
    for seed in range(1, 12):
        p = make_puzzle("teacher", seed, 0, solver)
        assert p.reference_kind == "exact" and p.reference_moves == 3
        assert solver.distance(p.facelets) == 3 and not Cube(p.facelets).is_solved()
