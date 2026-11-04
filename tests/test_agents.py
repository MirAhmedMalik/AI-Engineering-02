"""Adapters and retry policy, tested with a fake HTTP server (no network, no key)."""
import asyncio
import json

import httpx
import pytest

from app.agents.base import Agent, AgentError, MoveRequest, RawResponse, RetryPolicy, TrialTimeout
from app.agents.chat import ChatAgent, parse_chat_response
from app.agents.contract import MOVE_DESCRIPTIONS, build_decision_question, build_state_payload
from app.agents.decisions import DecisionsAgent, parse_decisions_response
from app.agents.ollama import OllamaAgent, parse_ollama_response
from app.agents.registry import MODEL_SPECS, build_agents, get_specs
from app.config import Settings, load_settings
from app.cube.engine import ALL_MOVES, SOLVED
from app.gateway import Gateway
from app.store import Limits, UsageStore

POLICY = RetryPolicy(max_attempts=3, request_timeout_s=5, backoff_s=0.0)


def make_gw(tmp_path, backend="openrouter", limits=None):
    return Gateway(UsageStore(tmp_path / backend, backend, limits), backend)


def make_req():
    return MoveRequest(facelets=SOLVED, recent_moves=["R", "U'"], moves_made=2, move_limit=20, allowed_moves=list(ALL_MOVES))


def spec(key):
    return next(s for s in MODEL_SPECS if s.key == key)


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------- contract
def test_state_and_question_are_identical_for_all_decision_models(tmp_path):
    req = make_req()
    seen = []

    def handler(request: httpx.Request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"answers": {"next_move": {"type": "choice", "choice": "R", "probabilities": {}, "confidence": 0.5}}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for key in ("jev", "luna", "clef"):
                agent = DecisionsAgent(spec(key), POLICY, client, "k", "https://openrouter.ai", make_gw(tmp_path))
                await agent.decide(req)

    run(go())
    assert len(seen) == 3
    models = [b.pop("model") for b in seen]
    assert models == ["typesafe/jev-1.13", "openai/gpt-6-luna-decisions", "cloudflare/clef"]
    assert seen[0] == seen[1] == seen[2]  # only the model id differs
    q = seen[0]["questions"]["next_move"]
    assert q["type"] == "choice"
    assert list(q["criteria"]) == ALL_MOVES
    assert seen[0]["state"]["cube_facelets"] == SOLVED
    assert seen[0]["state"]["recent_moves"] == ["R", "U'"]


def test_move_descriptions_cover_all_moves():
    assert set(MOVE_DESCRIPTIONS) == set(ALL_MOVES)
    assert "180" in MOVE_DESCRIPTIONS["U2"]
    assert "counterclockwise" in MOVE_DESCRIPTIONS["U'"]


def test_state_payload():
    p = build_state_payload(SOLVED, ["R"], 1, 20)
    assert p["moves_remaining"] == 19
    assert p["faces"]["U"] == "U" * 9


# ------------------------------------------------------------- decisions parsing
def test_decisions_parse_ok():
    data = {"answers": {"next_move": {"type": "choice", "choice": "R'", "probabilities": {"R'": 0.9, "R": 0.1}, "confidence": 0.84}},
            "usage": {"cost": 0.0001}}
    raw = parse_decisions_response(data, 12.0)
    assert raw.move == "R'" and raw.confidence == 0.84 and raw.cost_usd == 0.0001
    assert raw.probabilities["R'"] == 0.9 and raw.http_ms == 12.0


@pytest.mark.parametrize("data", [
    [], {}, {"answers": []}, {"answers": {"next_move": "R"}},
    {"answers": {"next_move": {"type": "score", "choice": "R"}}},
    {"answers": {"next_move": {"type": "choice"}}},
])
def test_decisions_parse_malformed(data):
    with pytest.raises(AgentError) as e:
        parse_decisions_response(data, 1.0)
    assert e.value.kind == "malformed"


# ------------------------------------------------------------- chat parsing + request
def chat_ok(move='{"move": "U2"}'):
    return {"choices": [{"message": {"content": move}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.02}}


def test_chat_parse_ok():
    raw = parse_chat_response(chat_ok(), 100.0)
    assert raw.move == "U2" and raw.tokens_in == 10 and raw.tokens_out == 3 and raw.cost_usd == 0.02


@pytest.mark.parametrize("content", ["R", "```json\n{\"move\": \"R\"}\n```", '{"mv": "R"}', "", "[1]"])
def test_chat_parse_is_strict(content):
    with pytest.raises(AgentError) as e:
        parse_chat_response(chat_ok(content), 1.0)
    assert e.value.kind == "malformed"


def test_chat_request_uses_structured_output(tmp_path):
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json=chat_ok())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            agent = ChatAgent(spec("opus"), POLICY, client, "secret-key", "https://openrouter.ai", make_gw(tmp_path))
            return await agent.decide(make_req())

    d = run(go())
    assert d.move == "U2"
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["auth"] == "Bearer secret-key"
    b = seen["body"]
    assert b["model"] == "anthropic/claude-haiku-5.5"
    assert b["response_format"]["type"] == "json_schema"
    assert b["response_format"]["json_schema"]["schema"]["properties"]["move"]["enum"] == ALL_MOVES
    assert "temperature" in b  # reasoning is off by default
    assert "secret-key" not in json.dumps(b)


def test_chat_downgrades_unsupported_features(tmp_path):
    calls = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        calls.append(body)
        if "reasoning" in body:
            return httpx.Response(400, json={"error": {"message": "reasoning is not supported by this route"}})
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, json={"error": {"message": "response_format json_schema unsupported"}})
        return httpx.Response(200, json=chat_ok())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            agent = ChatAgent(spec("opus"), POLICY, client, "k", "https://openrouter.ai", make_gw(tmp_path), reasoning_effort="high")
            d = await agent.decide(make_req())
            return d, agent

    d, agent = run(go())
    assert d.move == "U2"
    assert d.failed_attempts == []  # feature negotiation does not use up a retry attempt
    assert "reasoning off" in agent.downgrades
    assert "json_schema -> json_object" in agent.downgrades
    assert calls[-1]["response_format"]["type"] == "json_object"
    # every one of those tries was a REAL request and was counted (no hidden requests)
    assert agent.gateway.store.snapshot()["models"]["opus"]["requests"] == len(calls)


# ------------------------------------------------------------- retry policy
class ScriptedAgent(Agent):
    """Returns scripted results: a RawResponse, or an exception to raise, or 'hang'."""

    def __init__(self, script):
        super().__init__(spec("jev"), RetryPolicy(max_attempts=3, request_timeout_s=0.2, backoff_s=0.0))
        self.script = list(script)
        self.calls = 0

    async def _call(self, req, timeout_s):
        self.calls += 1
        item = self.script.pop(0)
        if item == "hang":
            await asyncio.sleep(10)
        if isinstance(item, Exception):
            raise item
        return item


def test_invalid_move_is_retried_and_counted_not_repaired():
    agent = ScriptedAgent([RawResponse(move="r"), RawResponse(move="Z9"), RawResponse(move="F2", http_ms=7.0)])
    d = run(agent.decide(make_req()))
    assert d.move == "F2" and agent.calls == 3
    assert [a.kind for a in d.failed_attempts] == ["invalid_move", "invalid_move"]
    assert d.latency_ms == 7.0


def test_gives_up_after_max_attempts():
    agent = ScriptedAgent([RawResponse(move="x")] * 3)
    d = run(agent.decide(make_req()))
    assert d.move is None and agent.calls == 3 and "invalid_move" in d.error


def test_timeout_is_retried():
    agent = ScriptedAgent(["hang", RawResponse(move="U")])
    d = run(agent.decide(make_req()))
    assert d.move == "U"
    assert [a.kind for a in d.failed_attempts] == ["timeout"]


def test_non_retryable_error_stops_at_once():
    agent = ScriptedAgent([AgentError("http_error", "HTTP 401: bad key", status=401, retryable=False)])
    d = run(agent.decide(make_req()))
    assert d.move is None and agent.calls == 1 and "401" in d.error


def test_retryable_http_error_then_success():
    agent = ScriptedAgent([AgentError("http_error", "HTTP 503", status=503), RawResponse(move="D'")])
    d = run(agent.decide(make_req()))
    assert d.move == "D'" and agent.calls == 2


def test_trial_deadline_raises_trial_timeout():
    import time

    agent = ScriptedAgent(["hang"])
    with pytest.raises(TrialTimeout):
        run(agent.decide(make_req(), deadline=time.perf_counter() + 0.05))


# ------------------------------------------------------------- live adapter with real-looking failures
def test_decisions_http_errors(tmp_path):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited"}}, headers={"retry-after": "0"})
        if state["n"] == 2:
            return httpx.Response(200, json={"answers": {"next_move": {"type": "choice", "choice": "NOPE"}}})
        return httpx.Response(200, json={"answers": {"next_move": {"type": "choice", "choice": "L2", "confidence": 0.7}}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            agent = DecisionsAgent(spec("clef"), POLICY, client, "k", "https://openrouter.ai", make_gw(tmp_path))
            return await agent.decide(make_req())

    d = run(go())
    assert d.move == "L2" and d.confidence == 0.7
    assert [a.kind for a in d.failed_attempts] == ["http_error", "invalid_move"]
    assert d.failed_attempts[0].http_status == 429
    snap = make_gw(tmp_path).store.snapshot()["models"]["clef"]
    assert snap["requests"] == 3 and snap["failed"] == 1  # all 3 requests count against the budget (the 429 is also marked failed)


# ------------------------------------------------------------- ollama adapter
def test_ollama_parse_and_request(tmp_path):
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": '{"move": "F"}'}, "done": True})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sp = get_specs(Settings(), "ollama")[1]
            agent = OllamaAgent(sp, POLICY, client, "http://127.0.0.1:11434", make_gw(tmp_path, "ollama"), think="false")
            return await agent.decide(make_req())

    d = run(go())
    assert d.move == "F" and d.source == "live"
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert seen["body"]["stream"] is False and seen["body"]["options"]["temperature"] == 0
    assert seen["body"]["format"]["properties"]["move"]["enum"] == ALL_MOVES


def test_ollama_parse_is_strict():
    assert parse_ollama_response({"message": {"content": '{"move": "U"}'}}, 5.0).move == "U"
    for bad in ({}, {"message": {"content": "U"}}):
        try:
            parse_ollama_response(bad, 1.0)
            assert False
        except AgentError as e:
            assert e.kind == "malformed"


def test_ollama_clef_uses_the_systemone_endpoint_without_a_key(tmp_path):
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"model": "clef-flash", "answers": {"next_move": {"type": "choice", "choice": "B2", "probabilities": {}, "confidence": 0.6}}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            agents = build_agents(Settings(), "ollama", ["ol_clef"], client, make_gw(tmp_path, "ollama"))
            return await agents["ol_clef"].decide(make_req())

    d = run(go())
    assert d.move == "B2"
    assert seen["url"] == "http://127.0.0.1:11434/v1/systemone"
    assert "authorization" not in seen["headers"]
    assert seen["body"]["model"] == "clef-flash" and list(seen["body"]["questions"]) == ["next_move"]
    assert list(seen["body"]["questions"]["next_move"]["criteria"]) == ALL_MOVES  # same question as for OpenRouter decision models


# ------------------------------------------------------------- registry / settings
def test_rosters():
    s = Settings()
    assert [x.key for x in get_specs(s, "openrouter")] == ["jev", "luna", "clef", "opus"]
    assert [x.key for x in get_specs(s, "simulated")] == ["jev", "luna", "clef", "opus"]
    assert [x.key for x in get_specs(s, "ollama")] == ["ol_clef", "ol_qwen", "ol_deepseek", "ol_gemma"]
    assert [x.category for x in get_specs(s, "ollama")] == ["decision", "llm", "llm", "llm"]


def test_registry_builds_simulated_agents_without_key(tmp_path):
    s = Settings()
    agents = build_agents(s, "simulated", None, None, make_gw(tmp_path, "simulated"))
    assert list(agents) == ["jev", "luna", "clef", "opus"]
    assert all(a.mode == "simulated" for a in agents.values())


def test_registry_builds_live_agents(tmp_path):
    s = Settings(openrouter_api_key="test-key-abc123")

    async def go():
        async with httpx.AsyncClient() as c:
            return build_agents(s, "openrouter", None, c, make_gw(tmp_path)), build_agents(s, "ollama", None, c, make_gw(tmp_path, "ollama"))

    real, local = run(go())
    assert type(real["jev"]).__name__ == "DecisionsAgent" and type(real["opus"]).__name__ == "ChatAgent"
    assert all(a.mode == "live" for a in real.values())
    assert type(local["ol_clef"]).__name__ == "DecisionsAgent"  # Clef = decision model on /v1/systemone
    assert all(type(local[k]).__name__ == "OllamaAgent" for k in ("ol_qwen", "ol_deepseek", "ol_gemma"))


def test_model_id_override_and_public_settings_hide_key():
    s = load_settings({"OPENROUTER_API_KEY": "test-key-secret", "MODEL_JEV": "typesafe/jev-latest"}, load_dotenv_file=False)
    assert [x.model_id for x in get_specs(s)][0] == "typesafe/jev-latest"
    assert "secret" not in json.dumps(s.public_dict())


def test_placeholder_keys_count_as_missing():
    for k in ["", "your_openrouter_api_key_here", "test-key-xxxxxxxx", "changeme"]:
        assert not Settings(openrouter_api_key=k).has_api_key
    assert Settings(openrouter_api_key="test-key-0123456789abcdef").has_api_key


def test_chat_cut_off_answer_retries_with_a_bigger_limit(tmp_path):
    limits = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        limits.append(body.get("max_tokens"))
        if len(limits) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
        return httpx.Response(200, json=chat_ok())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            agent = ChatAgent(spec("opus"), POLICY, client, "k", "https://openrouter.ai", make_gw(tmp_path), max_tokens=1000)
            return await agent.decide(make_req())

    d = run(go())
    assert d.move == "U2"
    assert limits == [1000, 2000]


@pytest.mark.parametrize("style", ["standard", "compact", "plain"])
def test_prompt_styles_keep_the_same_cube_and_the_same_moves(style, monkeypatch):
    monkeypatch.setenv("PROMPT_STYLE", style)
    state = build_state_payload(SOLVED, ["R"], 1, 20)
    q = build_decision_question(ALL_MOVES)
    assert state["cube_facelets"] == SOLVED and state["recent_moves"] == ["R"]
    assert list(q["criteria"]) == ALL_MOVES and q["type"] == "choice"
    assert ("faces" in state) == (style == "standard")


def test_unknown_prompt_style_falls_back_to_standard(monkeypatch):
    monkeypatch.setenv("PROMPT_STYLE", "banana")
    assert "faces" in build_state_payload(SOLVED, [], 0, 20)
