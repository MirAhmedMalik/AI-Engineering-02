#### OpenRouter (real models, limited requests): Teacher setup (cube exactly 3 moves from solved)

- Run: `run-20261104-153636-openrouter-teacher` (2026-11-04 15:36:36), seed `42`
- 1 puzzle x 1 repeat(s) x 4 models = **4 trials**
- Scramble length 3 turn(s); move limit 20 (Claude only 5, because it has only 5 requests); trial time limit 300 s; request timeout 90 s; up to 2 attempts per move (same for every model)
- LLM reasoning effort: provider default; models raced at the same time

**Speed**

| Model | Avg latency / move | Median | p95 | Avg total time / puzzle | Total time, all puzzles |
| --- | ---: | ---: | ---: | ---: | ---: |
| Jev 1.13 | 767 ms | 651 ms | 913 ms | 15.4 s | 15.4 s |
| GPT-6 Luna Decisions | **649 ms** | 495 ms | 1.24 s | **7.9 s** | 7.9 s |
| Cloudflare Clef | 1.28 s | 1.21 s | 2.23 s | 25.8 s | 25.8 s |
| Claude Haiku 5.5 | 6.75 s | 7.00 s | 7.12 s | 39.1 s | 39.1 s |

**Quality**

| Model | Success rate | Solved | Avg moves (all puzzles) | Avg moves (solved only) | Optimality gap (mean) | Optimal solves |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Jev 1.13 | **0.0%** | 0 / 1 | 20.00 | n/a | n/a (no solved puzzle) | 0 / 1 |
| GPT-6 Luna Decisions | **0.0%** | 0 / 1 | 11.00 | n/a | n/a (no solved puzzle) | 0 / 1 |
| Cloudflare Clef | **0.0%** | 0 / 1 | 20.00 | n/a | n/a (no solved puzzle) | 0 / 1 |
| Claude Haiku 5.5 | **0.0%** | 0 / 1 | 4.00 | n/a | n/a (no solved puzzle) | 0 / 1 |

**Reliability and cost**

| Model | Moves played | Invalid outputs | Timeouts | HTTP errors | Retries | Avg confidence | Cost (USD) | Final status counts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Jev 1.13 | 20 | 0 | 0 | 0 | 0 | 0.281 | 0.0009 | Ran out of moves: 1 |
| GPT-6 Luna Decisions | 11 | 0 | 0 | 2 | 1 | 0.261 | 0.0007 | Error: 1 |
| Cloudflare Clef | 20 | 0 | 0 | 0 | 0 | 0.021 | 0.0041 | Ran out of moves: 1 |
| Claude Haiku 5.5 | 4 | 0 | 0 | 1 | 1 | n/a | 0.0021 | Request budget used up: 1 |

**API calls, cache and cost**

| Model | Actual API calls | Cache hits | Failed requests | Live latency / move | Cache response time | Actual cost (USD) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Jev 1.13 | 20 | 0 | 0 | 767 ms | n/a (no cache hit) | 0.000926 |
| GPT-6 Luna Decisions | 12 | 1 | 2 | 582 ms | 1.487 ms | 0.000731 |
| Cloudflare Clef | 20 | 0 | 0 | 1.28 s | n/a (no cache hit) | 0.004108 |
| Claude Haiku 5.5 | 5 | 0 | 1 | 6.75 s | n/a (no cache hit) | 0.002104 |

Total actual requests counted for this backend (all runs, saved on disk): **58** of 65 allowed; cache entries: 55; total actual cost so far: $0.0079.

Bold = best value in the column. Success rate counts only puzzles solved inside the move limit and the time limit. Latency is the live API latency of the single request that produced the accepted move (for a replay from the cache: as first measured). The time to answer from the cache is shown separately. Optimality gap = model moves - exact shortest moves, for solved puzzles only (0 = optimal).

**Charts**

![Success rate (higher is better)](results/charts/openrouter_teacher_success_rate.svg)

![Average latency per move (lower is faster)](results/charts/openrouter_teacher_latency.svg)

![Average total time per puzzle](results/charts/openrouter_teacher_solve_time.svg)

![Average moves per puzzle](results/charts/openrouter_teacher_moves.svg)

![Optimality gap, solved puzzles (0 is optimal)](results/charts/openrouter_teacher_gap.svg)

<details><summary>Every puzzle, one row each (1 races)</summary>

| Puzzle | Scramble | Shortest | Jev 1.13 | GPT-6 Luna Decisions | Cloudflare Clef | Claude Haiku 5.5 |
| --- | --- | --- | --- | --- | --- | --- |
| teacher-01 | `R D' F` | 3 | ran out of moves, 20 moves, 15.4 s | error, 11 moves, 7.9 s | ran out of moves, 20 moves, 25.8 s | request budget used up, 4 moves, 39.1 s |

</details>

<details><summary>Failed attempts: invalid outputs, timeouts, HTTP errors (3)</summary>

| Model | Puzzle | Move no. | Attempt | Kind | Detail |
| --- | --- | --- | --- | --- | --- |
| GPT-6 Luna Decisions | teacher-01 | 12 | 1 | http_error | HTTP 502: OpenAI refused to answer question "next_move" |
| GPT-6 Luna Decisions | teacher-01 | 12 | 2 | http_error | HTTP 502: OpenAI refused to answer question "next_move" |
| Claude Haiku 5.5 | teacher-01 | 1 | 1 | http_error | HTTP 429: OpenRouter could not verify available credits for this request in time. Retry shortly. |

</details>

Raw data: `results/runs/run-20261104-153636-openrouter-teacher/` (`run.json`, `trials.csv`, `moves.csv`, `summary.csv`).

#### Ollama (local models): Teacher setup (cube exactly 3 moves from solved)

- Run: `run-20261104-134639-ollama-teacher` (2026-11-04 13:46:39), seed `42`
- 1 puzzle x 1 repeat(s) x 4 models = **4 trials**
- Scramble length 3 turn(s); move limit 20; trial time limit 3600 s; request timeout 600 s; up to 2 attempts per move (same for every model)
- models raced one after another

**Speed**

| Model | Avg latency / move | Median | p95 | Avg total time / puzzle | Total time, all puzzles |
| --- | ---: | ---: | ---: | ---: | ---: |
| Clef (Ollama) | n/a | n/a | n/a | 3 min 5 s | 3 min 5 s |
| Qwen (Ollama) | **48.60 s** | 51.29 s | 94.28 s | **2 min 15 s** | 2 min 15 s |
| DeepSeek (Ollama) | 173.31 s | 173.31 s | 212.71 s | 20 min 1 s | 20 min 1 s |
| Gemma (Ollama) | 55.32 s | 38.05 s | 131.12 s | 4 min 49 s | 4 min 49 s |

**Quality**

| Model | Success rate | Solved | Avg moves (all puzzles) | Avg moves (solved only) | Optimality gap (mean) | Optimal solves |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Clef (Ollama) | **0.0%** | 0 / 1 | 0.00 | n/a | n/a (no solved puzzle) | 0 / 1 |
| Qwen (Ollama) | **0.0%** | 0 / 1 | 20.00 | n/a | n/a (no solved puzzle) | 0 / 1 |
| DeepSeek (Ollama) | **0.0%** | 0 / 1 | 2.00 | n/a | n/a (no solved puzzle) | 0 / 1 |
| Gemma (Ollama) | **0.0%** | 0 / 1 | 20.00 | n/a | n/a (no solved puzzle) | 0 / 1 |

**Reliability and cost**

| Model | Moves played | Invalid outputs | Timeouts | HTTP errors | Retries | Avg confidence | Cost (USD) | Final status counts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Clef (Ollama) | 0 | 0 | 0 | 2 | 1 | n/a | 0.0000 | Error: 1 |
| Qwen (Ollama) | 20 | 0 | 0 | 0 | 0 | n/a | 0.0000 | Ran out of moves: 1 |
| DeepSeek (Ollama) | 2 | 0 | 2 | 0 | 1 | n/a | 0.0000 | Error: 1 |
| Gemma (Ollama) | 20 | 0 | 0 | 0 | 0 | n/a | 0.0000 | Ran out of moves: 1 |

**API calls, cache and cost**

| Model | Actual API calls | Cache hits | Failed requests | Live latency / move | Cache response time | Actual cost (USD) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Clef (Ollama) | 2 | 0 | 2 | n/a (no live move) | n/a (no cache hit) | 0 (free, local) |
| Qwen (Ollama) | 7 | 13 | 0 | 19.24 s | 4.236 ms | 0 (free, local) |
| DeepSeek (Ollama) | 2 | 2 | 2 | n/a (no live move) | 2.552 ms | 0 (free, local) |
| Gemma (Ollama) | 11 | 9 | 0 | 26.22 s | 2.327 ms | 0 (free, local) |

Total actual requests counted for this backend (all runs, saved on disk): **51**; cache entries: 42; total actual cost so far: $0.0000.

Bold = best value in the column. Success rate counts only puzzles solved inside the move limit and the time limit. Latency is the live API latency of the single request that produced the accepted move (for a replay from the cache: as first measured). The time to answer from the cache is shown separately. Optimality gap = model moves - exact shortest moves, for solved puzzles only (0 = optimal).

**Charts**

![Success rate (higher is better)](results/charts/ollama_teacher_success_rate.svg)

![Average latency per move (lower is faster)](results/charts/ollama_teacher_latency.svg)

![Average total time per puzzle](results/charts/ollama_teacher_solve_time.svg)

![Average moves per puzzle](results/charts/ollama_teacher_moves.svg)

![Optimality gap, solved puzzles (0 is optimal)](results/charts/ollama_teacher_gap.svg)

<details><summary>Every puzzle, one row each (1 races)</summary>

| Puzzle | Scramble | Shortest | Clef (Ollama) | Qwen (Ollama) | DeepSeek (Ollama) | Gemma (Ollama) |
| --- | --- | --- | --- | --- | --- | --- |
| teacher-01 | `R D' F` | 3 | error, 0 moves, 3 min 5 s | ran out of moves, 20 moves, 2 min 15 s | error, 2 moves, 20 min 1 s | ran out of moves, 20 moves, 4 min 49 s |

</details>

<details><summary>Failed attempts: invalid outputs, timeouts, HTTP errors (4)</summary>

| Model | Puzzle | Move no. | Attempt | Kind | Detail |
| --- | --- | --- | --- | --- | --- |
| Clef (Ollama) | teacher-01 | 1 | 1 | http_error | HTTP 500: llama-server process has terminated: exit status 0xc0000409: The system detected an overrun of a stack-based buffer in this applic |
| Clef (Ollama) | teacher-01 | 1 | 2 | http_error | HTTP 500: llama-server process has terminated: exit status 0xc0000409: The system detected an overrun of a stack-based buffer in this applic |
| DeepSeek (Ollama) | teacher-01 | 3 | 1 | timeout | no answer within 600 s |
| DeepSeek (Ollama) | teacher-01 | 3 | 2 | timeout | no answer within 600 s |

</details>

Raw data: `results/runs/run-20261104-134639-ollama-teacher/` (`run.json`, `trials.csv`, `moves.csv`, `summary.csv`).

### Answers to the five questions

_Generated from the numbers above by `scripts/report.py`. Check them against the tables._

#### Based on: OpenRouter (real models, limited requests), Teacher setup (cube exactly 3 moves from solved)

##### 1. Which model is fastest at making decisions?

**GPT-6 Luna Decisions**, with an average of 649 ms per move in this experiment. Ranking by average latency per move: GPT-6 Luna Decisions 649 ms, Jev 1.13 767 ms, Cloudflare Clef 1.28 s, Claude Haiku 5.5 6.75 s. The slowest model (Claude Haiku 5.5) needs about 10.4x as long per move.

##### 2. Which model has the highest solve rate?

**Jev 1.13, GPT-6 Luna Decisions, Cloudflare Clef and Claude Haiku 5.5** (tie) (0%, 0 of 1 puzzles). All models: Jev 1.13 0% (0/1), GPT-6 Luna Decisions 0% (0/1), Cloudflare Clef 0% (0/1), Claude Haiku 5.5 0% (0/1).

##### 3. Which model found the shortest solutions, and how close to the optimum?

No model solved a puzzle, so no optimality gap can be computed.

##### 4. Did the fastest model also give the best solution quality?

**Cannot say.** No model solved the puzzle, so solution quality cannot be compared. GPT-6 Luna Decisions is the fastest (649 ms per move).

##### 5. Fastest decision model, frontier LLM or hybrid agent?

- In this experiment the best decision model (GPT-6 Luna Decisions) solved 0% of the puzzles, Claude Haiku 5.5 solved 0%.
- Per move, GPT-6 Luna Decisions needs 649 ms and Claude Haiku 5.5 needs 6.75 s (about 10x slower).
- Optimality gap: GPT-6 Luna Decisions n/a (no solved puzzle), Claude Haiku 5.5 n/a (no solved puzzle).
- Cost of the run: GPT-6 Luna Decisions $0.0007, Claude Haiku 5.5 $0.0021.

**No recommendation from these numbers:** no model solved the puzzle, so speed is the only difference. Run more puzzles (for example the Ollama primary benchmark) before choosing an architecture.

#### Based on: Ollama (local models), Teacher setup (cube exactly 3 moves from solved)

##### 1. Which model is fastest at making decisions?

**Qwen (Ollama)**, with an average of 48.60 s per move in this experiment. Ranking by average latency per move: Qwen (Ollama) 48.60 s, Gemma (Ollama) 55.32 s, DeepSeek (Ollama) 173.31 s. The slowest model (DeepSeek (Ollama)) needs about 3.6x as long per move.

##### 2. Which model has the highest solve rate?

**Clef (Ollama), Qwen (Ollama), DeepSeek (Ollama) and Gemma (Ollama)** (tie) (0%, 0 of 1 puzzles). All models: Clef (Ollama) 0% (0/1), Qwen (Ollama) 0% (0/1), DeepSeek (Ollama) 0% (0/1), Gemma (Ollama) 0% (0/1).

##### 3. Which model found the shortest solutions, and how close to the optimum?

No model solved a puzzle, so no optimality gap can be computed.

##### 4. Did the fastest model also give the best solution quality?

**Cannot say.** No model solved the puzzle, so solution quality cannot be compared. Qwen (Ollama) is the fastest (48.60 s per move).

##### 5. Fastest decision model, frontier LLM or hybrid agent?

- In this experiment the best decision model (Clef (Ollama)) solved 0% of the puzzles, Qwen (Ollama) solved 0%.
- Per move, Clef (Ollama) needs n/a and Qwen (Ollama) needs 48.60 s.
- Optimality gap: Clef (Ollama) n/a (no solved puzzle), Qwen (Ollama) n/a (no solved puzzle).
- Cost of the run: Clef (Ollama) $0.0000, Qwen (Ollama) $0.0000.

**No recommendation from these numbers:** no model solved the puzzle, so speed is the only difference. Run more puzzles (for example the Ollama primary benchmark) before choosing an architecture.

