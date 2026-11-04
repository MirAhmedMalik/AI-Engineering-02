# Design notes

## Cube engine (`app/cube/engine.py`)
* **State**: a 54-character string, faces in the order `U R F D L B`, 9 letters each (the Kociemba facelet format). Every letter is the face the sticker belongs to. Solved = `UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB`.
* **Moves**: 18 face turns in the half-turn metric: `U U' U2 R R' R2 F F' F2 D D' D2 L L' L2 B B' B2`. Every one counts as 1 move.
* **No hand-typed tables**: each sticker gets a 3D position and direction, and a turn rotates the stickers of one layer by 90 degrees. The permutation of each move is computed from that. The tests check the group laws (every move has the right order, `R U R' U'` has order 6, centres never move, inverse sequences cancel) and some known sticker positions.
* **Solved check**: all six faces show a single letter.
* The same engine and the same move list are used for every model, the scramble generator, the solver and the UI.

## Scrambles (`app/cube/puzzles.py`)
* Seeded: `seed:test_set:index` gives the same puzzle every time, so all models and all repeats start from the same state.
* Two moves in a row never use the same axis (so no `U D U` that could be written shorter). A scramble that happens to be solved is replaced.
* Test sets: **teacher** (a cube that is EXACTLY 3 moves from solved; the puzzle is re-drawn until the exact BFS distance is 3, so a 3-turn scramble that happens to be shorter is rejected), warm-up 3-4 turns, primary 4-5 turns, extension 15-20 turns.

## Exact optimum (`app/cube/solver.py`)
* Breadth-first search is complete, so it gives the true shortest solution. A plain forward search to depth 5 or more is slow in Python, so the solver uses **meet in the middle**: one table of all positions up to 4 moves from solved (46 741 states, built once), then a forward search of up to 4 moves from the puzzle. Any puzzle with an optimum of up to 8 moves gets an exact answer.
* Primary scrambles have 4-5 turns, so their optimum is at most 5. They always get `reference_kind = "exact"`.
* Tests compare it with an independent plain BFS.
* Extension puzzles are too deep for this table. Their reference is an **upper bound** (the inverse scramble, or the Kociemba solution if `pip install kociemba` is present), so their gap is shown as "at most".

## Fairness rules (same for every model)
| Rule | Value |
|---|---|
| Start state | identical for all models in a race |
| Move options | the same 18 moves with the same descriptions |
| State sent | `cube_facelets`, the six faces, `recent_moves` (last 6), `moves_made`, `moves_remaining` |
| Move limit | **20** (`MOVE_LIMIT`). Reaching it = unsuccessful ("not solved") |
| Claude's limit | **5 real requests in the whole experiment** (`MAX_CLAUDE_REQUESTS`). So Claude can play at most 5 moves and is marked not solved if the cube is not solved by then |
| Decision models | at most **20** real requests each, **65** in total (`MAX_TOTAL_REQUESTS`); the code never allows a total above 95 |
| Attempts per move | 2 (`MAX_ATTEMPTS`): first try + 1 retry. Every try, also a failed one, is a real request and is counted |
| Request timeout / trial time | 90 s / 300 s on OpenRouter; 240 s / 900 s on Ollama (a laptop CPU is slow) |
| Invalid output | not in the list, wrong JSON, empty: logged, counted, retried. Never repaired (`r` is not turned into `R`) |
| No valid move after all attempts | the trial ends with status *Error* |
| Request budget used up | the trial ends with status *Request budget used up*; nothing is sent |

## Request rules: cache, counters, limits (`app/gateway.py`, `app/store.py`)
Every request of every adapter goes through one function, `Gateway.fetch`:

1. **Cache first.** The key is a SHA-256 of backend + model id + the exact request body. A hit returns the saved answer, sends nothing and costs no budget.
2. **Reserve one request.** The counter is increased and `usage.json` is written to disk BEFORE the request is sent. If a limit (model or total) is used up, `BudgetExceeded` is raised and nothing leaves the computer.
3. **Log first.** A `request_start` line is appended to `request_log.jsonl` before sending, a `request_end` line after.
4. **Send** and measure the LIVE latency.
5. **Cache only valid answers.** The adapter calls `accept()` only when the answer is a legal move, so a bad answer is never replayed and a retry is a real retry.

Other facts:
* The counters and the cache are files in `data/<backend>/`. They are read when the app starts and are never reset by a restart. A damaged `usage.json` stops the app (`StoreError`) instead of starting with zero.
* A request that times out is still counted (it was sent). A request that fails is counted and marked failed.
* **Live latency and cache time are kept apart.** A cached move stores the live latency of its first request (`latency_ms`) and the time the cache needed (`cache_ms`). The statistics show both. `avg_live_latency_ms` only uses moves answered live in that run.
* Per-model limits: Claude 5, each decision model 20. The limits only apply to OpenRouter and to the practice mode. Ollama is local and free: no limit.
* Only the practice (simulated) counters can be reset (`Reset practice counters` button, `scripts/usage.py --reset-simulated`). The real files are refused by `clear_all()`.
* The key is only used for the `Authorization` header. It is never written to the cache, the counters, the log, the results or any API answer (a test checks every file).

## Two interfaces, one contract (`app/agents/contract.py`)
* **Decision models**: `POST /api/alpha/decisions` with `state` and one question `next_move` of type `choice`. The options (`criteria`) are the 18 moves with their descriptions. The answer is `answers.next_move.choice` with `probabilities` and `confidence`.
* **Claude Haiku 5.5**: `POST /api/v1/chat/completions` with a system prompt, the same state and the same list of moves. Structured output (`json_schema` with an enum of the 18 moves) is requested. If the route does not support something (structured output, reasoning), the adapter turns that one feature off and sends the same request again; this is logged in the trial notes. The reply must be exactly `{"move": "R'"}`.
* **Ollama**: Clef is a decision model too. Ollama serves it on `POST http://127.0.0.1:11434/v1/systemone` with the same body and answer format (no key). Qwen, DeepSeek and Gemma are normal chat models: `POST /api/chat` with the same system and user text, a JSON-schema `format` with the 18 moves, temperature 0, seed 0.
* Both return a move string. Everything after that (validation, retry, cube update) is the same code.

## Metrics (`app/stats.py`)
* **Latency per move**: time of the request that produced the accepted move (retries not included). Mean, median and p95 are reported. `decision_ms` in the raw data also counts retries and back-off.
* **Total solve time**: from the first request until solved, the move limit, the time limit or an error.
* **Success rate**: solved puzzles / all trials.
* **Optimality gap**: moves played - shortest moves, for solved trials only. 0 = optimal.
* **Race ranking** (the badges in the UI): solved first (fewer moves, then less time), then the others by stickers in the right place.

## Three backends
| Backend | Models | Limits | Use it for |
|---|---|---|---|
| `simulated` (practice, default) | fake agents with the four OpenRouter names | the SAME limits (5 / 20 / 65), own folder `data/simulated` | testing the app and the rules without spending anything |
| `ollama` | Clef (decision), Qwen, DeepSeek, Gemma (local) | none | all the bigger experiments |
| `openrouter` | Jev, Luna, Clef, Claude Haiku | 5 / 20 / 65, enforced | ONE real experiment: 1 puzzle, exactly 3 moves from solved |

A real OpenRouter run needs the key in `.env` AND a confirmation (a pop-up in the app, `--yes` or typing `yes` in the terminal). Simulated runs are labelled everywhere ("Simulated" tag, orange banner, `-simulated-` in the run id, `"mode": "simulated"` in the data). `scripts/report.py` ignores them unless you pass `--allow-simulated`.
