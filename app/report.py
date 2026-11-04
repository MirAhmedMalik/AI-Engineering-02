"""Turn saved benchmark runs into the README results section and SVG charts.

``python scripts/report.py`` reads the newest finished run of each (backend, test set)
from results/runs/, writes charts to results/charts/*.svg and fills the block
between ``<!-- RESULTS:START -->`` and ``<!-- RESULTS:END -->`` in README.md.

Simulated runs are ignored unless you pass --allow-simulated, so practice
numbers cannot end up in the submission by accident.
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict, List, Optional

from .stats import STATUS_LABELS, aggregate
from .storage import list_runs, load_run

BEGIN = "<!-- RESULTS:START -->"
END = "<!-- RESULTS:END -->"
SET_ORDER = ["teacher", "warmup", "primary", "extension"]
SET_TITLES = {"teacher": "Teacher setup (cube exactly 3 moves from solved)", "warmup": "Warm-up", "primary": "Primary benchmark",
              "extension": "Extension (hard scrambles)"}
BACKEND_ORDER = ["openrouter", "ollama", "simulated"]
BACKEND_TITLES = {"openrouter": "OpenRouter (real models, limited requests)", "ollama": "Ollama (local models)",
                  "simulated": "SIMULATED practice (not real results)"}


# ------------------------------------------------------------------ formatting
def f_ms(ms: Optional[float]) -> str:
    if ms is None:
        return "n/a"
    return f"{ms / 1000:.2f} s" if ms >= 1000 else f"{ms:.0f} ms"


def f_s(s: Optional[float]) -> str:
    if s is None:
        return "n/a"
    if s >= 60:
        return f"{int(s // 60)} min {s % 60:.0f} s"
    return f"{s:.1f} s"


def f_num(x: Optional[float], d: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{d}f}"


def f_gap(m: Dict[str, Any]) -> str:
    g = m.get("mean_gap")
    if g is None:
        return "n/a (no solved puzzle)"
    prefix = "≤ " if m.get("gap_kind") == "upper_bound" else ""
    return f"{prefix}{g:+.2f}" if g else f"{prefix}0.00"


# ------------------------------------------------------------------ choosing runs
def run_key(doc: Dict[str, Any]) -> str:
    return f"{doc.get('config', {}).get('backend') or doc.get('mode')}:{doc['config']['test_set']}"


def pick_runs(results_dir: Path, run_ids: Optional[List[str]] = None, allow_simulated: bool = False) -> Dict[str, Dict[str, Any]]:
    """Return {"backend:test_set": run document}. Newest finished run of each key unless run_ids are given."""
    chosen: Dict[str, Dict[str, Any]] = {}
    if run_ids:
        for rid in run_ids:
            doc = load_run(rid, results_dir)
            if doc is None:
                raise FileNotFoundError(f"run not found: {rid}")
            if doc.get("mode") in ("simulated", "mock") and not allow_simulated:
                raise ValueError(f"{rid} is a simulated run; pass --allow-simulated to use it anyway")
            chosen[run_key(doc)] = doc
        return chosen
    for info in list_runs(results_dir):  # newest first
        if info.get("status") != "finished":
            continue
        if info.get("mode") in ("simulated", "mock") and not allow_simulated:
            continue
        doc = load_run(info["run_id"], results_dir)
        if doc and run_key(doc) not in chosen:
            chosen[run_key(doc)] = doc
    return chosen


# ------------------------------------------------------------------ SVG charts
def svg_bars(title: str, rows: List[Dict[str, Any]], max_value: Optional[float] = None, note: str = "") -> str:
    """Horizontal bar chart. rows = [{label, value, text, color}]. value None = no data."""
    width, left, right, bar_h, gap, top = 720, 200, 110, 24, 12, 54
    height = top + len(rows) * (bar_h + gap) + (24 if note else 10)
    vals = [r["value"] for r in rows if r["value"] is not None]
    vmax = max_value if max_value else (max(vals) if vals and max(vals) > 0 else 1.0)
    track = width - left - right
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="{html.escape(title)}">',
        '<style>text{font-family:Segoe UI,Helvetica,Arial,sans-serif;fill:#14202a}.t{font-size:18px;font-weight:700}.l{font-size:14px}.v{font-size:14px;font-weight:700}.n{font-size:12px;fill:#55636d}</style>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text class="t" x="16" y="30">{html.escape(title)}</text>',
    ]
    y = top
    for r in rows:
        w = 0 if r["value"] is None else max(2.0, track * min(abs(r["value"]) / vmax, 1.0))
        out.append(f'<text class="l" x="16" y="{y + 17}">{html.escape(r["label"])}</text>')
        out.append(f'<rect x="{left}" y="{y}" width="{track}" height="{bar_h}" rx="4" fill="#e8ecea"/>')
        if r["value"] is not None:
            out.append(f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="4" fill="{r["color"]}"/>')
        out.append(f'<text class="v" x="{left + track + 10}" y="{y + 17}">{html.escape(r["text"])}</text>')
        y += bar_h + gap
    if note:
        out.append(f'<text class="n" x="16" y="{height - 8}">{html.escape(note)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def write_charts(doc: Dict[str, Any], charts_dir: Path) -> List[Dict[str, str]]:
    """Write the SVG charts of one run. Returns [{title, file}] relative to charts_dir."""
    stats = doc.get("stats") or aggregate(doc)
    colors = {m["key"]: m.get("color", "#555") for m in doc["models"]}
    prefix = run_key(doc).replace(":", "_")
    charts_dir.mkdir(parents=True, exist_ok=True)

    def rows(field: str, fmt) -> List[Dict[str, Any]]:
        return [{"label": m["name"], "value": m[field], "text": "n/a" if m[field] is None else fmt(m[field]),
                 "color": colors.get(m["key"], "#555")} for m in stats["models"]]

    specs = [
        ("success_rate", "Success rate (higher is better)", rows("success_rate", lambda v: f"{v:.0f}%"), 100.0, ""),
        ("latency", "Average latency per move (lower is faster)", rows("avg_latency_ms", f_ms), None, ""),
        ("solve_time", "Average total time per puzzle", rows("avg_total_time_s", f_s), None, "Includes puzzles that were not solved (time until the limit or error)."),
        ("moves", "Average moves per puzzle", rows("avg_moves", lambda v: f"{v:.1f}"), None, f"Moves played, solved or not; the limit is {doc['config']['move_limit']} (Claude: at most {doc['config'].get('move_limits', {}).get('opus', doc['config']['move_limit'])})."),
        ("gap", "Optimality gap, solved puzzles (0 is optimal)", rows("mean_gap", lambda v: f"{v:+.2f}" if v else "0.00"), None,
         "Gap = model moves minus the shortest solution. Models without a solved puzzle have no bar."),
    ]
    out = []
    for key, title, rws, vmax, note in specs:
        name = f"{prefix}_{key}.svg"
        (charts_dir / name).write_text(svg_bars(title, rws, vmax, note), encoding="utf-8")
        out.append({"title": title, "file": name})
    return out


# ------------------------------------------------------------------ markdown
def _table(header: List[str], rows: List[List[str]], align: Optional[List[str]] = None) -> str:
    align = align or ["---"] * len(header)
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(align) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _best(stats: Dict[str, Any], field: str, lowest: bool) -> List[str]:
    vals = [(m[field], m["key"]) for m in stats["models"] if m.get(field) is not None]
    if not vals:
        return []
    t = min(v for v, _ in vals) if lowest else max(v for v, _ in vals)
    return [k for v, k in vals if v == t]


def _bold_if(best: List[str], key: str, text: str) -> str:
    return f"**{text}**" if key in best else text


def set_section(doc: Dict[str, Any], charts: List[Dict[str, str]], charts_rel: str) -> str:
    stats = doc.get("stats") or aggregate(doc)
    cfg = doc["config"]
    ts = cfg["test_set"]
    backend = cfg.get("backend") or doc.get("mode")
    models = stats["models"]
    names = {m["key"]: m["name"] for m in models}
    lines: List[str] = []
    mode_note = "  \n> **SIMULATED DATA** (no real model was called). Do not submit." if backend in ("simulated", "mock") else ""
    lines.append(f"#### {BACKEND_TITLES.get(backend, backend)}: {SET_TITLES.get(ts, ts)}{mode_note}\n")
    reps = cfg.get("repeats", 1)
    lines.append(
        f"- Run: `{doc['run_id']}` ({doc.get('created_at', '')[:19].replace('T', ' ')}), seed `{cfg['seed']}`\n"
        f"- {stats['n_puzzles']} {'puzzle' if stats['n_puzzles'] == 1 else 'puzzles'} x {reps} repeat(s) x {len(models)} models = **{stats['n_trials']} trials**\n"
        f"- Scramble length {_range(doc)} turn(s); move limit {cfg['move_limit']}"
        + (f" (Claude only {cfg['move_limits']['opus']}, because it has only {cfg['move_limits']['opus']} requests)" if cfg.get("move_limits", {}).get("opus", cfg["move_limit"]) != cfg["move_limit"] else "")
        + f"; trial time limit {cfg['trial_time_limit_s']:.0f} s; "
        f"request timeout {cfg['request_timeout_s']:.0f} s; up to {cfg['max_attempts']} attempts per move (same for every model)\n"
        + (f"- LLM reasoning effort: {cfg.get('opus_reasoning_effort', 'provider default')}; " if backend != "ollama" else "- ")
        + f"models raced {'at the same time' if cfg.get('parallel', True) else 'one after another'}\n"
    )

    best_fast = _best(stats, "avg_latency_ms", True)
    best_sr = _best(stats, "success_rate", False)
    best_gap = _best(stats, "mean_gap", True)
    best_total = _best(stats, "avg_total_time_s", True)
    best_moves = _best(stats, "avg_moves_solved", True)

    lines.append("**Speed**\n")
    lines.append(_table(
        ["Model", "Avg latency / move", "Median", "p95", "Avg total time / puzzle", "Total time, all puzzles"],
        [[m["name"], _bold_if(best_fast, m["key"], f_ms(m["avg_latency_ms"])), f_ms(m["median_latency_ms"]), f_ms(m["p95_latency_ms"]),
          _bold_if(best_total, m["key"], f_s(m["avg_total_time_s"])), f_s(m["sum_total_time_s"])] for m in models],
        ["---", "---:", "---:", "---:", "---:", "---:"]))
    lines.append("\n**Quality**\n")
    lines.append(_table(
        ["Model", "Success rate", "Solved", "Avg moves (all puzzles)", "Avg moves (solved only)", "Optimality gap (mean)", "Optimal solves"],
        [[m["name"], _bold_if(best_sr, m["key"], f"{m['success_rate']:.1f}%"), f"{m['solved']} / {m['trials']}", f_num(m["avg_moves"], 2),
          _bold_if(best_moves, m["key"], f_num(m["avg_moves_solved"], 2)), _bold_if(best_gap, m["key"], f_gap(m)),
          f"{m['optimal_solves']} / {m['trials']}"] for m in models],
        ["---", "---:", "---:", "---:", "---:", "---:", "---:"]))
    lines.append("\n**Reliability and cost**\n")
    lines.append(_table(
        ["Model", "Moves played", "Invalid outputs", "Timeouts", "HTTP errors", "Retries", "Avg confidence", "Cost (USD)", "Final status counts"],
        [[m["name"], str(m["move_count"]), str(m["invalid_outputs"]), str(m["timeouts"]), str(m["http_errors"]), str(m["retries"]),
          f_num(m["avg_confidence"], 3) if m["avg_confidence"] is not None else "n/a",
          f"{m['cost_usd']:.4f}" if m["cost_usd"] is not None else "n/a",
          ", ".join(f"{STATUS_LABELS.get(k, k)}: {v}" for k, v in sorted(m["statuses"].items()))] for m in models],
        ["---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---"]))
    lines.append("\n**API calls, cache and cost**\n")
    lines.append(_table(
        ["Model", "Actual API calls", "Cache hits", "Failed requests", "Live latency / move", "Cache response time", "Actual cost (USD)"],
        [[m["name"], str(m["api_calls"]), str(m["cache_hits"]), str(m["failed_requests"]),
          f_ms(m["avg_live_latency_ms"]) if m["live_moves"] else "n/a (no live move)",
          (f"{m['avg_cache_response_ms']:.3f} ms" if m["cached_moves"] and m["avg_cache_response_ms"] is not None else "n/a (no cache hit)"),
          f"{m['cost_usd']:.6f}" if m["cost_usd"] else ("0 (free, local)" if backend == "ollama" else "n/a")] for m in models],
        ["---", "---:", "---:", "---:", "---:", "---:", "---:"]))
    usage = doc.get("api_usage")
    if usage and "total_requests" in usage:
        lines.append(f"\nTotal actual requests counted for this backend (all runs, saved on disk): **{usage['total_requests']}**"
                     + (f" of {usage['total_limit']} allowed" if usage.get("total_limit") else "")
                     + f"; cache entries: {usage.get('cache_entries', 0)}; total actual cost so far: ${usage.get('total_cost_usd', 0):.4f}.")
    if any(m["cache_hits"] and not m["api_calls"] for m in models):
        lines.append("\nA model with 0 API calls was answered completely from the cache that an earlier run filled; "
                     "its real requests, latency and cost are counted in that earlier run and in the total above.")
    lines.append("\nBold = best value in the column. Success rate counts only puzzles solved inside the move limit and the time limit. "
                 "Latency is the live API latency of the single request that produced the accepted move (for a replay from the cache: as first measured). The time to answer from the cache is shown separately. Optimality gap = model moves - exact shortest moves, "
                 "for solved puzzles only (0 = optimal)."
                 + (" For this set the shortest solution is an upper bound, so the gap is shown as at most the given value." if any(m.get("gap_kind") == "upper_bound" for m in models) else "")
                 + "\n")

    lines.append("**Charts**\n")
    for c in charts:
        lines.append(f"![{c['title']}]({charts_rel}/{c['file']})\n")

    lines.append(f"<details><summary>Every puzzle, one row each ({len(stats['per_puzzle'])} races)</summary>\n")
    head = ["Puzzle", "Scramble", "Shortest"] + [m["name"] for m in models]
    rows = []
    for p in stats["per_puzzle"]:
        cells = []
        for m in models:
            r = p["results"].get(m["key"])
            if not r:
                cells.append("-")
            elif r["status"] == "SOLVED":
                g = "" if r["gap"] is None else f" (gap {r['gap']:+d})" if r["gap"] else " (optimal)"
                cells.append(f"solved, {r['moves']} moves{g}, {f_s(r['time_s'])}")
            else:
                cells.append(f"{STATUS_LABELS.get(r['status'], r['status']).lower()}, {r['moves']} moves, {f_s(r['time_s'])}")
        shortest = f"{'≤ ' if p['reference_kind'] == 'upper_bound' else ''}{p['reference_moves']}"
        rows.append([f"{p['puzzle_id']}" + (f" r{p['repeat'] + 1}" if p["repeat"] else ""), "`" + " ".join(p["scramble"]) + "`", shortest] + cells)
    lines.append(_table(head, rows))
    lines.append("\n</details>\n")

    bad = [(t["model"], t["puzzle_id"], a) for t in doc["trials"] for a in t.get("attempt_log", [])]
    if bad:
        lines.append(f"<details><summary>Failed attempts: invalid outputs, timeouts, HTTP errors ({len(bad)})</summary>\n")
        rows = [[names.get(k, k), pid, str(a["move_no"]), str(a["attempt"]), a["kind"], (a["detail"] or "").replace("|", "/").replace("\n", " ")[:140]]
                for k, pid, a in bad[:100]]
        lines.append(_table(["Model", "Puzzle", "Move no.", "Attempt", "Kind", "Detail"], rows))
        if len(bad) > 100:
            lines.append(f"\n...and {len(bad) - 100} more in `results/runs/{doc['run_id']}/run.json`.")
        lines.append("\n</details>\n")
    else:
        lines.append("No failed attempts (no invalid outputs, timeouts or HTTP errors) in this run.\n")
    lines.append(f"Raw data: `results/runs/{doc['run_id']}/` (`run.json`, `trials.csv`, `moves.csv`, `summary.csv`).\n")
    return "\n".join(lines)


def _range(doc: Dict[str, Any]) -> str:
    lens = [p["scramble_len"] for p in doc["puzzles"]]
    if not lens:
        return "n/a"
    return str(min(lens)) if min(lens) == max(lens) else f"{min(lens)}-{max(lens)}"


# ------------------------------------------------------------------ the five questions
def _join(names: List[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def answers_md(primary: Dict[str, Any], extension: Optional[Dict[str, Any]] = None) -> str:
    st = primary.get("stats") or aggregate(primary)
    models = st["models"]
    names = {m["key"]: m["name"] for m in models}
    by = {m["key"]: m for m in models}
    n = models[0]["trials"] if models else 0

    fast = _best(st, "avg_latency_ms", True)
    sr = _best(st, "success_rate", False)
    gap = _best(st, "mean_gap", True)

    out: List[str] = []
    ordered = sorted([m for m in models if m["avg_latency_ms"] is not None], key=lambda m: m["avg_latency_ms"])
    out.append("##### 1. Which model is fastest at making decisions?\n")
    if ordered:
        slowest = ordered[-1]
        lead = (f"**{_join([names[k] for k in fast])}** (tie)" if len(fast) > 1 else f"**{names[fast[0]]}**")
        text = (f"{lead}, with an average of {f_ms(by[fast[0]]['avg_latency_ms'])} per move in this experiment. "
                "Ranking by average latency per move: " + ", ".join(f"{m['name']} {f_ms(m['avg_latency_ms'])}" for m in ordered) + ".")
        if ordered[0]["avg_latency_ms"] and slowest["avg_latency_ms"] > ordered[0]["avg_latency_ms"]:
            text += f" The slowest model ({slowest['name']}) needs about {slowest['avg_latency_ms'] / ordered[0]['avg_latency_ms']:.1f}x as long per move."
        out.append(text + "\n")
    else:
        out.append("No latency data.\n")

    out.append("##### 2. Which model has the highest solve rate?\n")
    out.append(
        f"**{_join([names[k] for k in sr])}**{' (tie)' if len(sr) > 1 else ''} ({by[sr[0]]['success_rate']:.0f}%, {by[sr[0]]['solved']} of {by[sr[0]]['trials']} puzzles). "
        "All models: " + ", ".join(f"{m['name']} {m['success_rate']:.0f}% ({m['solved']}/{m['trials']})" for m in models) + ".\n")

    out.append("##### 3. Which model found the shortest solutions, and how close to the optimum?\n")
    if gap:
        g = by[gap[0]]
        out.append(
            f"**{_join([names[k] for k in gap])}**{' (tie)' if len(gap) > 1 else ''}: mean optimality gap {f_gap(g)} moves over its solved puzzles, "
            f"with {g['optimal_solves']} of {g['trials']} puzzles solved with exactly the shortest number of moves. "
            "Gap per model (solved puzzles only): " + ", ".join(f"{m['name']} {f_gap(m)} ({m['optimal_solves']} optimal of {m['solved']} solved)" for m in models)
            + ". The optimum comes from an exact breadth-first search, so a gap of 0 means a provably shortest solution.\n")
    else:
        out.append("No model solved a puzzle, so no optimality gap can be computed.\n")

    out.append("##### 4. Did the fastest model also give the best solution quality?\n")
    f0 = fast[0] if fast else None
    if f0 and all((m["success_rate"] or 0) == 0 for m in models):
        out.append(f"**Cannot say.** No model solved the puzzle, so solution quality cannot be compared. {names[f0]} is the fastest ({f_ms(by[f0]['avg_latency_ms'])} per move).\n")
    elif f0:
        is_sr = f0 in sr
        is_gap = f0 in gap
        if is_sr and is_gap:
            verdict = f"**Yes.** {names[f0]} is fastest and also has the highest solve rate and the smallest optimality gap."
        elif not is_sr and not is_gap:
            verdict = (f"**No.** {names[f0]} is the fastest ({f_ms(by[f0]['avg_latency_ms'])} per move) but it is not the best on quality: "
                       f"its solve rate is {by[f0]['success_rate']:.0f}% (best {by[sr[0]]['success_rate']:.0f}%) and its mean gap is {f_gap(by[f0])}"
                       + (f" (best {f_gap(by[gap[0]])})." if gap else "."))
        else:
            what = "highest solve rate" if is_sr else "smallest optimality gap"
            other = "optimality gap" if is_sr else "solve rate"
            verdict = (f"**Partly.** {names[f0]} is the fastest and has the {what}, but it is not the best on {other} "
                       f"(solve rate {by[f0]['success_rate']:.0f}%, mean gap {f_gap(by[f0])}).")
        out.append(verdict + "\n")
    else:
        out.append("Not enough data.\n")

    out.append("##### 5. Fastest decision model, frontier LLM or hybrid agent?\n")
    out.append(_recommendation(models, extension))
    return "\n".join(out)


def _recommendation(models: List[Dict[str, Any]], extension: Optional[Dict[str, Any]]) -> str:
    dec = [m for m in models if m["category"] == "decision"]
    llm = [m for m in models if m["category"] == "llm"]
    if not dec or not llm:
        return "Both a decision model and the LLM are needed to compare them.\n"
    best_dec = sorted(dec, key=lambda m: (-m["success_rate"], m["mean_gap"] if m["mean_gap"] is not None else 99, m["avg_latency_ms"] or 1e9))[0]
    timed_dec = sorted([m for m in dec if m["avg_latency_ms"] is not None], key=lambda m: m["avg_latency_ms"])
    fastest_dec = timed_dec[0] if timed_dec else best_dec
    o = sorted(llm, key=lambda m: (-m["success_rate"], m["mean_gap"] if m["mean_gap"] is not None else 99, m["avg_latency_ms"] or 1e9))[0]
    speedup = (o["avg_latency_ms"] / fastest_dec["avg_latency_ms"]) if o["avg_latency_ms"] and fastest_dec["avg_latency_ms"] else None
    diff = o["success_rate"] - best_dec["success_rate"]

    ev = [
        f"- In this experiment the best decision model ({best_dec['name']}) solved {best_dec['success_rate']:.0f}% of the puzzles, "
        f"{o['name']} solved {o['success_rate']:.0f}%.",
        f"- Per move, {fastest_dec['name']} needs {f_ms(fastest_dec['avg_latency_ms'])} and {o['name']} needs {f_ms(o['avg_latency_ms'])}"
        + (f" (about {speedup:.0f}x slower)." if speedup else "."),
        f"- Optimality gap: {best_dec['name']} {f_gap(best_dec)}, {o['name']} {f_gap(o)}.",
    ]
    if o["cost_usd"] is not None and best_dec["cost_usd"] is not None:
        ev.append(f"- Cost of the run: {best_dec['name']} ${best_dec['cost_usd']:.4f}, {o['name']} ${o['cost_usd']:.4f}.")
    ext_diff: Optional[float] = None
    if extension is not None:
        es = (extension.get("stats") or aggregate(extension))["models"]
        ev.append(f"- On the extension set (15-20 turn scrambles, {extension.get('config', {}).get('move_limit', 20)} move limit) the solve rates were: "
                  + ", ".join(f"{m['name']} {m['success_rate']:.0f}%" for m in es) + ".")
        e_llm = [m["success_rate"] for m in es if m["category"] == "llm"]
        e_dec = [m["success_rate"] for m in es if m["category"] == "decision"]
        if e_llm and e_dec:
            ext_diff = max(e_llm) - max(e_dec)

    if best_dec["success_rate"] == 0 and o["success_rate"] == 0:
        advice = ("**No recommendation from these numbers:** no model solved the puzzle, so speed is the only difference. "
                  "Run more puzzles (for example the Ollama primary benchmark) before choosing an architecture.")
        return "\n".join(ev) + "\n\n" + advice + "\n"
    llm_better = diff >= 10 or (ext_diff is not None and ext_diff >= 20)
    dec_better = diff <= -10 and (ext_diff is None or ext_diff <= 0)
    much_slower = bool(speedup and speedup >= 3)
    if llm_better and much_slower:
        advice = ("**Recommendation: a hybrid agent.** The frontier LLM was more reliable here (on the primary set, the harder extension set, or both), "
                  "but it is much slower per decision. A hybrid lets the fast decision model make the routine moves and hands the hard cases "
                  "(low confidence, repeated moves, no progress) to the LLM. That keeps the average latency low and still uses the LLM's reliability where it matters.")
    elif llm_better:
        advice = ("**Recommendation: the frontier LLM.** It was more reliable and its extra latency per move is small compared with that gain, "
                  "so a decision model brings no clear advantage in this task.")
    elif dec_better:
        advice = ("**Recommendation: the fast decision model.** It solved more puzzles than the LLM and is faster per move, "
                  "so the LLM adds cost and latency without adding quality in this task.")
    else:
        advice = ("**Recommendation: the fast decision model, with the LLM as a fallback (hybrid).** Solve rates are close, "
                  "so the much lower latency of the decision model decides, and the LLM is kept only for cases where the decision model fails.")
    caveat = ("Limits of this evidence: very few puzzles (the OpenRouter experiment is one cube, because the request budget is small), "
              "one scramble family and one prompt wording. More puzzles and repeated runs would make the comparison more certain.")
    return "\n".join(ev) + "\n\n" + advice + "\n\n_" + caveat + "_\n"


# ------------------------------------------------------------------ whole section
def _ordered_keys(docs: Dict[str, Dict[str, Any]]) -> List[str]:
    def k(key: str):
        b, t = key.split(":", 1)
        return (BACKEND_ORDER.index(b) if b in BACKEND_ORDER else 9, SET_ORDER.index(t) if t in SET_ORDER else 9)

    return sorted(docs, key=k)


def build_results_markdown(docs: Dict[str, Dict[str, Any]], charts_rel: str, charts: Dict[str, List[Dict[str, str]]]) -> str:
    parts: List[str] = []
    if any(d.get("mode") in ("simulated", "mock") for d in docs.values()):
        parts.append("> **SIMULATED DATA.** At least one run below was made with simulated models, not the real ones.\n")
    keys = _ordered_keys(docs)
    for key in keys:
        parts.append(set_section(docs[key], charts.get(key, []), charts_rel))

    # Answers: the real OpenRouter experiment first, then the Ollama primary benchmark.
    answer_keys = [k for k in ("openrouter:teacher", "simulated:teacher") if k in docs][:1]
    if "ollama:primary" in docs:
        answer_keys.append("ollama:primary")
    elif "ollama:teacher" in docs:
        answer_keys.append("ollama:teacher")
    if not answer_keys and keys:
        answer_keys = [keys[0]]
    if answer_keys:
        parts.append("### Answers to the five questions\n")
        parts.append("_Generated from the numbers above by `scripts/report.py`. Check them against the tables._\n")
        for key in answer_keys:
            backend = key.split(":")[0]
            parts.append(f"#### Based on: {BACKEND_TITLES.get(backend, backend)}, {SET_TITLES.get(key.split(':')[1], key)}\n")
            parts.append(answers_md(docs[key], docs.get("ollama:extension") if backend == "ollama" else None))
    return "\n".join(parts)


def update_readme(readme: Path, block: str) -> None:
    text = readme.read_text(encoding="utf-8")
    new = f"{BEGIN}\n{block.strip()}\n{END}"
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        text = head + new + tail
    else:
        text = text.rstrip() + "\n\n## Results\n\n" + new + "\n"
    readme.write_text(text, encoding="utf-8")
