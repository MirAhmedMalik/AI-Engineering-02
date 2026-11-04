#!/usr/bin/env python3
"""Show the saved request counters (they survive restarts).

  python scripts/usage.py                    # all backends
  python scripts/usage.py --reset-simulated  # clear ONLY the simulated practice counters and cache
The real counters (data/openrouter) can never be reset by this tool.
"""
import _bootstrap  # noqa: F401

import argparse
import sys

from app.config import BACKENDS, load_settings
from app.store import StoreError
from app.suite import make_store


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset-simulated", action="store_true")
    args = ap.parse_args()
    s = load_settings()
    if args.reset_simulated:
        make_store(s, "simulated").clear_all()
        print("Simulated counters and cache cleared.")
    for b in BACKENDS:
        try:
            snap = make_store(s, b).snapshot()
        except StoreError as exc:
            print(f"{b}: ERROR {exc}")
            return 2
        lim = f" of {snap['total_limit']}" if snap.get("total_limit") else ""
        print(f"{b:<11} requests {snap['total_requests']}{lim}   failed {snap['total_failed']}   cache hits {snap['cache_hits']}   cache entries {snap['cache_entries']}   cost ${snap['total_cost_usd'] + snap.get('assumed_cost_usd', 0):.4f}" + (f" of ${snap['cost_limit_usd']:.2f}" if snap.get("cost_limit_usd") else ""))
        for k, m in snap["models"].items():
            ml = f"/{m['limit']}" if m.get("limit") is not None else ""
            print(f"    {k:<12} {m['requests']}{ml}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
