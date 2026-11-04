import asyncio
import xml.etree.ElementTree as ET

import pytest

from app.events import EventBus
from app.report import (BEGIN, END, answers_md, build_results_markdown, pick_runs, svg_bars, update_readme, write_charts)
from app.stats import aggregate
from app.suite import SuiteConfig, execute_run, new_run_doc


def make_doc(settings, solver, test_set="primary", n=4, seed=3, backend="simulated"):
    doc = new_run_doc(SuiteConfig(backend=backend, test_set=test_set, num_puzzles=n, seed=seed), settings)
    asyncio.run(execute_run(doc, settings, solver, EventBus()))
    return doc


def test_svg_is_valid_xml_and_escapes():
    svg = svg_bars('A <b> & "c"', [{"label": "x<y", "value": 1.0, "text": "1", "color": "#123456"},
                                    {"label": "none", "value": None, "text": "n/a", "color": "#000"}], note="n & m")
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert "x&lt;y" in svg and "n &amp; m" in svg


def test_pick_runs_ignores_simulated_unless_allowed(big_settings, solver):
    make_doc(big_settings, solver)
    assert pick_runs(big_settings.results_dir) == {}
    docs = pick_runs(big_settings.results_dir, allow_simulated=True)
    assert list(docs) == ["simulated:primary"]
    with pytest.raises(ValueError):
        pick_runs(big_settings.results_dir, [docs["simulated:primary"]["run_id"]])
    assert pick_runs(big_settings.results_dir, [docs["simulated:primary"]["run_id"]], allow_simulated=True)
    with pytest.raises(FileNotFoundError):
        pick_runs(big_settings.results_dir, ["nope"], allow_simulated=True)


def test_full_report_has_all_sections(big_settings, solver, tmp_path):
    docs = {f"simulated:{ts}": make_doc(big_settings, solver, ts, 3) for ts in ("teacher", "warmup", "primary", "extension")}
    docs["ollama:primary"] = docs["simulated:primary"]
    docs["ollama:primary"] = dict(docs["ollama:primary"], config={**docs["ollama:primary"]["config"], "backend": "ollama"}, mode="ollama")
    charts = {k: write_charts(d, tmp_path / "charts") for k, d in docs.items()}
    assert len(list((tmp_path / "charts").glob("*.svg"))) == 5 * 5  # 5 charts for each of the 5 runs, files do not overwrite each other
    for f in (tmp_path / "charts").glob("*.svg"):
        ET.parse(f)
    md = build_results_markdown(docs, "results/charts", charts)
    for needle in ["Teacher setup", "Warm-up", "Primary benchmark", "Extension", "Avg latency / move", "Success rate",
                   "Optimality gap", "Invalid outputs", "Timeouts", "API calls, cache and cost", "Cache hits", "Cache response time",
                   "##### 1.", "##### 2.", "##### 3.", "##### 4.", "##### 5.", "Ollama (local models)",
                   "results/charts/simulated_primary_latency.svg", "SIMULATED"]:
        assert needle in md, needle
    assert "infx" not in md and "nanx" not in md and "None" not in md


def test_answers_handle_ties_and_zero_latency(big_settings, solver):
    doc = make_doc(big_settings, solver)
    st = aggregate(doc)
    for m in st["models"]:  # force a total tie
        m.update(avg_latency_ms=0.0, success_rate=100.0, mean_gap=0.0, solved=m["trials"])
    doc["stats"] = st
    txt = answers_md(doc)
    assert "infx" not in txt and "(tie)" in txt


def _fake(models):
    return {"stats": {"models": models}}


def _m(key, cat, sr, lat, gap=0.0):
    return {"key": key, "name": key, "category": cat, "success_rate": sr, "solved": 1, "trials": 2, "avg_latency_ms": lat,
            "mean_gap": gap, "gap_kind": "exact", "optimal_solves": 1, "cost_usd": None, "avg_moves_solved": 5.0}


def test_recommendation_rules():
    dec = _m("d", "decision", 60, 50)
    llm_hi = _m("o", "llm", 100, 3000)
    assert "hybrid agent" in answers_md(_fake([dec, llm_hi]))
    llm_lo = _m("o", "llm", 40, 3000)
    assert "Recommendation: the fast decision model.**" in answers_md(_fake([dec, llm_lo]))
    close = _m("o", "llm", 60, 3000)
    assert "fallback" in answers_md(_fake([dec, close]))
    # close on primary but much better on the hard set -> hybrid
    ext = _fake([_m("d", "decision", 10, 50), _m("o", "llm", 90, 3000)])
    assert "hybrid agent" in answers_md(_fake([dec, close]), ext)
    # LLM better and about as fast -> LLM
    assert "frontier LLM.**" in answers_md(_fake([dec, _m("o", "llm", 100, 60)]))


def test_update_readme(tmp_path):
    f = tmp_path / "README.md"
    f.write_text(f"# T\n\n{BEGIN}\nold\n{END}\n\nkeep me\n", encoding="utf-8")
    update_readme(f, "NEW BLOCK")
    t = f.read_text()
    assert "NEW BLOCK" in t and "old" not in t and "keep me" in t and t.count(BEGIN) == 1
    update_readme(f, "SECOND")
    assert "SECOND" in f.read_text() and "NEW BLOCK" not in f.read_text()
    g = tmp_path / "B.md"
    g.write_text("# B\n", encoding="utf-8")
    update_readme(g, "X")
    assert BEGIN in g.read_text() and "X" in g.read_text()
