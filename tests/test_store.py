"""The teacher's rules: persistent cache, request counters that survive a restart, hard limits, logging before sending."""
import asyncio
import json

import httpx
import pytest

from app.agents.base import AgentError, MoveRequest, RetryPolicy
from app.agents.decisions import DecisionsAgent
from app.agents.registry import get_specs
from app.config import HARD_TOTAL_CEILING, Settings
from app.cube.engine import ALL_MOVES, SOLVED
from app.gateway import Gateway
from app.store import BudgetExceeded, Limits, StoreError, UsageStore, cache_key, forget_stores
from app.suite import make_store

POLICY = RetryPolicy(max_attempts=2, request_timeout_s=5, backoff_s=0.0)


def run(coro):
    return asyncio.run(coro)


def lim(**per):
    return Limits(per_model=per, total=sum(per.values()) if per else None)


def req(moves_made=0):
    return MoveRequest(facelets=SOLVED, recent_moves=[], moves_made=moves_made, move_limit=20, allowed_moves=list(ALL_MOVES))


def decisions_reply(move="R"):
    return {"answers": {"next_move": {"type": "choice", "choice": move, "confidence": 0.9}}, "usage": {"cost": 0.001}}


def agent_with(tmp_path, handler, key="jev", limits=None):
    store = UsageStore(tmp_path, "openrouter", limits)
    gw = Gateway(store, "openrouter")
    sp = next(s for s in get_specs(Settings(), "openrouter") if s.key == key)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return DecisionsAgent(sp, POLICY, client, "k", "https://openrouter.ai", gw), store, client


# ------------------------------------------------------------------ cache
def test_cache_hit_sends_no_request_and_uses_no_budget(tmp_path):
    sent = []

    def handler(request):
        sent.append(1)
        return httpx.Response(200, json=decisions_reply("U"))

    async def go():
        agent, store, client = agent_with(tmp_path, handler)
        first = await agent.decide(req())
        second = await agent.decide(req())
        await client.aclose()
        return first, second, store

    first, second, store = run(go())
    assert first.move == second.move == "U"
    assert first.source == "live" and second.source == "cache"
    assert len(sent) == 1  # the second answer came from the cache
    snap = store.snapshot()
    assert snap["total_requests"] == 1 and snap["cache_hits"] == 1 and snap["cache_entries"] == 1
    assert second.cache_ms is not None and second.cache_ms < first.latency_ms + 1000
    assert second.latency_ms == pytest.approx(first.latency_ms, abs=0.01)  # live latency is kept apart from the cache time


def test_cache_key_depends_on_model_and_request():
    a = cache_key("openrouter", "m1", {"x": 1})
    assert a == cache_key("openrouter", "m1", {"x": 1})
    assert a != cache_key("openrouter", "m2", {"x": 1}) and a != cache_key("openrouter", "m1", {"x": 2})
    assert a != cache_key("simulated", "m1", {"x": 1})


def test_invalid_answers_are_not_cached(tmp_path):
    def handler(request):
        return httpx.Response(200, json=decisions_reply("NOT-A-MOVE"))

    async def go():
        agent, store, client = agent_with(tmp_path, handler)
        d = await agent.decide(req())
        await client.aclose()
        return d, store

    d, store = run(go())
    assert d.move is None
    snap = store.snapshot()
    assert snap["total_requests"] == 2 and snap["cache_entries"] == 0  # both tries were real requests, none cached


def test_cache_and_counters_survive_a_restart(tmp_path):
    def handler(request):
        return httpx.Response(200, json=decisions_reply("F"))

    async def first():
        agent, store, client = agent_with(tmp_path, handler)
        await agent.decide(req())
        await client.aclose()

    run(first())
    forget_stores()  # "restart": new process, only the files remain

    def must_not_be_called(request):
        raise AssertionError("a request was sent although the answer is cached")

    async def second():
        agent, store, client = agent_with(tmp_path, must_not_be_called)
        d = await agent.decide(req())
        await client.aclose()
        return d, store

    d, store = run(second())
    assert d.move == "F" and d.source == "cache"
    snap = store.snapshot()
    assert snap["total_requests"] == 1 and snap["cache_hits"] == 1  # counters were NOT reset by the restart


# ------------------------------------------------------------------ limits
def test_per_model_limit_is_enforced_before_sending(tmp_path):
    sent = []

    def handler(request):
        sent.append(1)
        return httpx.Response(200, json=decisions_reply())

    async def go():
        agent, store, client = agent_with(tmp_path, handler, "jev", Limits({"jev": 3}, 65))
        results = []
        for n in range(6):  # different states = no cache hits
            results.append(await agent.decide(MoveRequest(SOLVED, ["R"] * n, n, 20, list(ALL_MOVES))))
        await client.aclose()
        return results, store

    results, store = run(go())
    assert len(sent) == 3  # the 4th request was never sent
    assert [r.budget_exhausted for r in results] == [False, False, False, True, True, True]
    assert store.snapshot()["models"]["jev"]["requests"] == 3


def test_claude_limit_5_and_total_65_with_the_real_settings(tmp_path):
    s = Settings(data_dir=tmp_path)
    store = make_store(s, "openrouter")
    for _ in range(5):
        store.reserve("opus", "anthropic/claude-haiku-5.5")
    with pytest.raises(BudgetExceeded):
        store.reserve("opus", "anthropic/claude-haiku-5.5")  # the 6th Claude request
    for key in ("jev", "luna", "clef"):
        for _ in range(20):
            store.reserve(key, key)
        with pytest.raises(BudgetExceeded):
            store.reserve(key, key)  # the 21st
    assert store.snapshot()["total_requests"] == 65
    with pytest.raises(BudgetExceeded):
        store.reserve("jev", "jev")


def test_total_limit_stops_even_when_the_model_limit_is_free(tmp_path):
    store = UsageStore(tmp_path, "openrouter", Limits({"a": 10, "b": 10}, 12))
    for _ in range(10):
        store.reserve("a")
    store.reserve("b")
    store.reserve("b")
    with pytest.raises(BudgetExceeded, match="total"):
        store.reserve("b")


def test_total_limit_can_never_be_set_above_the_hard_ceiling():
    assert Settings(max_total_requests=500).total_request_limit == HARD_TOTAL_CEILING <= 95
    assert Settings().total_request_limit == 65


def test_limits_survive_a_restart(tmp_path):
    store = UsageStore(tmp_path, "openrouter", Limits({"opus": 5}, 65))
    for _ in range(5):
        store.reserve("opus")
    forget_stores()
    again = UsageStore(tmp_path, "openrouter", Limits({"opus": 5}, 65))
    assert again.snapshot()["models"]["opus"]["requests"] == 5
    with pytest.raises(BudgetExceeded):
        again.reserve("opus")


def test_failed_requests_and_timeouts_count(tmp_path):
    def handler(request):
        return httpx.Response(503, json={"error": {"message": "down"}})

    async def go():
        agent, store, client = agent_with(tmp_path, handler)
        d = await agent.decide(req())
        await client.aclose()
        return d, store

    d, store = run(go())
    assert d.move is None
    snap = store.snapshot()
    assert snap["total_requests"] == 2 and snap["total_failed"] == 2  # first try + retry, both counted


def test_cancelled_request_still_counts(tmp_path):
    async def hang(request):
        await asyncio.sleep(10)

    async def go():
        agent, store, client = agent_with(tmp_path, hang)
        agent.policy = RetryPolicy(max_attempts=1, request_timeout_s=0.1, backoff_s=0.0)
        d = await agent.decide(req())
        await client.aclose()
        return d, store

    d, store = run(go())
    assert d.move is None
    assert store.snapshot()["total_requests"] == 1 and store.snapshot()["total_failed"] == 1


# ------------------------------------------------------------------ logging and safety
def test_request_is_logged_before_it_is_sent(tmp_path):
    log_at_send = {}

    def handler(request):
        log_at_send["lines"] = (tmp_path / "request_log.jsonl").read_text().strip().splitlines()
        log_at_send["usage"] = json.loads((tmp_path / "usage.json").read_text())["total_requests"]
        return httpx.Response(200, json=decisions_reply())

    async def go():
        agent, store, client = agent_with(tmp_path, handler)
        await agent.decide(req())
        await client.aclose()

    run(go())
    assert json.loads(log_at_send["lines"][-1])["event"] == "request_start"  # written before the network call
    assert log_at_send["usage"] == 1  # and the counter was already saved
    events = [json.loads(x)["event"] for x in (tmp_path / "request_log.jsonl").read_text().splitlines()]
    assert events == ["request_start", "request_end"]


def test_damaged_usage_file_stops_instead_of_resetting(tmp_path):
    (tmp_path / "usage.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(StoreError):
        UsageStore(tmp_path, "openrouter")


def test_only_the_simulated_store_can_be_cleared(tmp_path):
    real = UsageStore(tmp_path / "real", "openrouter")
    real.reserve("jev")
    with pytest.raises(StoreError):
        real.clear_all()
    assert real.snapshot()["total_requests"] == 1
    sim = UsageStore(tmp_path / "sim", "simulated")
    sim.reserve("jev")
    sim.clear_all()
    assert sim.snapshot()["total_requests"] == 0


def test_secret_never_reaches_the_cache_counters_or_log(tmp_path):
    secret = "test-key-0123456789supersecretvalue"

    def handler(request):
        assert request.headers["authorization"] == f"Bearer {secret}"
        return httpx.Response(200, json=decisions_reply())

    async def go():
        store = UsageStore(tmp_path, "openrouter")
        sp = get_specs(Settings(), "openrouter")[0]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await DecisionsAgent(sp, POLICY, client, secret, "https://openrouter.ai", Gateway(store, "openrouter")).decide(req())

    run(go())
    for f in tmp_path.iterdir():
        assert secret not in f.read_text(encoding="utf-8")


def test_stores_are_separate_per_backend(tmp_path):
    s = Settings(data_dir=tmp_path)
    make_store(s, "simulated").reserve("jev")
    assert make_store(s, "openrouter").snapshot()["total_requests"] == 0
    assert make_store(s, "ollama").limits.total is None  # local models: no limit
    assert make_store(s, "openrouter").limits.total == 65


# ------------------------------------------------------------------ money cap
def capped(tmp_path, cap, est=0.05):
    return UsageStore(tmp_path, "openrouter", Limits({"a": 100}, 100, max_cost_usd=cap, cost_estimate={"a": est}))


def test_cost_cap_refuses_before_sending(tmp_path):
    store = capped(tmp_path, 0.20, est=0.05)
    n = 0
    with pytest.raises(BudgetExceeded, match="cost cap"):
        for _ in range(10):
            no = store.reserve("a")
            store.finish("a", no, True, 5.0, 0.05)  # every request really costs 0.05
            n += 1
    assert n == 4  # 4 x 0.05 = 0.20; the 5th would break the cap, so it is refused
    assert store.spent_usd() <= 0.20 + 1e-9


def test_cost_cap_counts_requests_that_are_still_running(tmp_path):
    store = capped(tmp_path, 0.10, est=0.05)
    store.reserve("a")
    store.reserve("a")  # two in flight, 0.05 each = the whole cap
    with pytest.raises(BudgetExceeded):
        store.reserve("a")


def test_unknown_price_is_counted_with_the_worst_case_guess(tmp_path):
    store = capped(tmp_path, 0.12, est=0.05)
    for _ in range(2):
        no = store.reserve("a")
        store.finish("a", no, True, 5.0, None)  # the API did not report a price
    assert store.spent_usd() == pytest.approx(0.10)
    with pytest.raises(BudgetExceeded):
        store.reserve("a")


def test_cost_cap_uses_the_dearest_price_seen_and_survives_restart(tmp_path):
    store = capped(tmp_path, 0.20, est=0.001)
    no = store.reserve("a")
    store.finish("a", no, True, 5.0, 0.09)  # a request cost much more than the guess
    forget_stores()
    again = capped(tmp_path, 0.20, est=0.001)
    assert again.spent_usd() == pytest.approx(0.09)
    again.reserve("a")  # 0.09 + 0.09 = 0.18 <= 0.20: allowed
    with pytest.raises(BudgetExceeded):
        again.reserve("a")  # 0.09 + 0.09 + 0.09 > 0.20


def test_cache_hits_cost_nothing_under_the_cap(tmp_path):
    s = Settings(data_dir=tmp_path, max_cost_usd=0.2)
    assert make_store(s, "openrouter").limits.max_cost_usd == 0.2
    assert make_store(s, "simulated").limits.max_cost_usd is None  # practice mode is never capped by money


def test_save_usage_retries_when_windows_blocks_the_file(tmp_path, monkeypatch):
    from app import store as store_mod
    st = UsageStore(tmp_path, "openrouter", lim(jev=20))
    real = store_mod.os.replace
    calls = {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real(a, b)

    monkeypatch.setattr(store_mod.os, "replace", flaky)
    st.reserve("jev", "typesafe/jev-1.13")  # must not raise BudgetExceeded
    assert calls["n"] == 3
