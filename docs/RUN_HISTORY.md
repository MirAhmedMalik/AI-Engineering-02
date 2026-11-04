# Run history (what happened, in order)

Honest log of every attempt, so the README and the report can describe it. Costs are from the saved usage counters.

| # | When / where | Backend | What happened | Requests | Cost |
|---|---|---|---|---|---|
| 1 | first real try, teacher's key | OpenRouter | `HTTP 401: API key expired` on the first request. The app stopped after 1 counted request. | 1 | $0 |
| 2 | v4 folder, my own key | OpenRouter | A Clef-only test, then the full run. Clef used its 20 requests (2 were failed 401 tries with the old key). Jev stopped at 17 because Windows blocked saving `usage.json` (`WinError 5`). Luna: `HTTP 502 OpenAI refused to answer question "next_move"`. Haiku: answer cut off at 300 tokens. No model solved the cube. | 56 of 65 | about $0.0069 |
| 3 | v6 folder, my own key | OpenRouter | Jev and Clef played 20 moves and did not solve. Luna refused again (HTTP 502). Haiku was cut off at 1000 tokens, twice. | 44 of 65 | about $0.0070 |
| 4 | v8 folder, my own key (final) | OpenRouter | Wording test with `scripts/probe_luna.py` (1 request, `PROMPT_STYLE=compact` worked for Luna), then the full run. All four models played. Jev and Clef 20 moves, Luna 11 moves then HTTP 502, Haiku 4 moves (its first request failed with HTTP 429, so it used its 5 requests). No model solved the cube. | 58 of 65 | about $0.0079 |
| 5 | v6 folder | Ollama | Qwen and Gemma played 20 moves, not solved. DeepSeek 1.5B did not answer within 600 s (error). `clef-flash` crashed in Ollama (`llama-server` exit code `0xc0000409`, a known Ollama issue). Earlier, with 3 models racing on one CPU, all three hit the 900 s limit. | 51 | $0 |

Total OpenRouter spend of runs 1 to 4: about $0.022 (limit $0.30 per run). Total real requests: 1 + 56 + 44 + 58 = 159.

## Fixes made because of these runs
* The counter file is saved with a retry (fixes the Windows file-lock error).
* Haiku's answer limit is 4000 tokens, and after a cut-off answer the retry uses a doubled limit.
* `scripts/probe_luna.py` tests whether another wording of the question stops Luna's refusal. The wording is chosen with `PROMPT_STYLE` and is the same for every model of a run.
* Ollama runs can use `--sequential` and longer time limits (`OLLAMA_REQUEST_TIMEOUT_S`, `OLLAMA_TRIAL_TIME_LIMIT_S`).

## Final experiment
v8 folder, my own key, 2026-11-04, `PROMPT_STYLE=compact`, run `run-20261104-153636-openrouter-teacher`.
58 of 65 requests, about $0.0079. Jev and Clef: 20 moves, not solved. Haiku: 4 moves, 5 of 5 requests used. Luna: 11 moves, then HTTP 502 (OpenAI refused). No model solved the cube. The Ollama results in the README come from run `run-20261104-134639-ollama-teacher` (v6 folder).
