# data/ : request counters, cache and request log

| Folder | What it is |
|---|---|
| `openrouter/` | the REAL experiment: `usage.json` (counters), `cache.jsonl` (cached answers), `request_log.jsonl` (a line written BEFORE every real request) |
| `ollama/` | the same for the local models (no limit) |
| `simulated/` | practice mode only (ignored by git) |

**Do not delete or edit these files.** The app reads them at start-up, so the counters (Claude 5, decision models 20, total 65) and the cache are NOT reset when you restart.
They contain no API key.
