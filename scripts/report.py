#!/usr/bin/env python3
"""Fill the results section of README.md from the saved runs and write the SVG charts.

  python scripts/report.py                       # newest finished run of each backend and test set
  python scripts/report.py --run-ids run-A run-B # choose runs
  python scripts/report.py --allow-simulated     # only for trying the report with simulated practice data
"""
import _bootstrap  # noqa: F401

import argparse
import sys
from pathlib import Path

from app.config import ROOT, load_settings
from app.report import build_results_markdown, pick_runs, update_readme, write_charts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-ids", nargs="+")
    ap.add_argument("--allow-simulated", "--allow-mock", dest="allow_simulated", action="store_true")
    ap.add_argument("--readme", default=str(ROOT / "README.md"))
    ap.add_argument("--print", action="store_true", help="print the markdown instead of changing the README")
    args = ap.parse_args()

    settings = load_settings()
    try:
        docs = pick_runs(settings.results_dir, args.run_ids, args.allow_simulated)
    except (FileNotFoundError, ValueError) as exc:
        print("error:", exc, file=sys.stderr)
        return 2
    if not docs:
        print("No finished runs found in results/runs/. Run scripts/benchmark.py first "
              "(simulated runs are ignored unless you pass --allow-simulated).", file=sys.stderr)
        return 1

    charts_dir = settings.results_dir / "charts"
    readme = Path(args.readme)
    try:
        charts_rel = charts_dir.relative_to(readme.parent).as_posix()
    except ValueError:
        charts_rel = charts_dir.as_posix()
    charts = {key: write_charts(doc, charts_dir) for key, doc in docs.items()}
    block = build_results_markdown(docs, charts_rel, charts)
    (settings.results_dir / "RESULTS.md").write_text(block + "\n", encoding="utf-8")
    if args.print:
        print(block)
        return 0
    update_readme(readme, block)
    print(f"Updated {readme} and wrote {len(sum(charts.values(), []))} charts to {charts_dir}")
    print("Used runs:", ", ".join(f"{k}={d['run_id']}" for k, d in docs.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
