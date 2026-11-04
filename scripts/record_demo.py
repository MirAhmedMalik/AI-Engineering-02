#!/usr/bin/env python3
"""Record the screen-recording for the submission automatically (optional helper).

It opens the app in a browser window, runs the benchmark, waits for the
final ranking and then the statistics slide, and saves demo/demo.webm.
Your name and roll number are in the yellow bar the whole time (set STUDENT_NAME and
STUDENT_ROLL_NO in .env first).

Needs once:  pip install playwright && playwright install chromium
Start the server first (python -m app.main), then in a second terminal:
  python scripts/record_demo.py --backend ollama --test-set primary --puzzles 5 --pause 6
  python scripts/record_demo.py --backend simulated          # practice recording (not for submission)
  python scripts/record_demo.py --backend openrouter --yes   # the real experiment: spends real requests!
You can also record by hand with any screen recorder: see docs/RECORDING.md
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--backend", choices=["simulated", "ollama", "openrouter"], default="ollama")
    ap.add_argument("--test-set", default=None, help="default: teacher for openrouter/simulated, primary for ollama")
    ap.add_argument("--puzzles", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--pause", type=int, default=6, help="seconds the result of each race stays on screen")
    ap.add_argument("--hold", type=int, default=12, help="seconds to stay on the statistics slide")
    ap.add_argument("--out", default=str(ROOT / "demo" / "demo.webm"))
    ap.add_argument("--yes", action="store_true", help="openrouter: confirm that real, limited requests may be spent")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    args = ap.parse_args()

    try:
        cfg = get(args.url + "/api/config")
    except Exception as exc:
        print(f"Cannot reach {args.url}: {exc}\nStart the server first: python -m app.main", file=sys.stderr)
        return 2
    test_set = args.test_set or ("primary" if args.backend == "ollama" else "teacher")
    puzzles = args.puzzles or (5 if args.backend == "ollama" else 1)
    info = cfg["backends"].get(args.backend, {})
    if not info.get("available"):
        print(f"Backend {args.backend} is not available: {info.get('note') or info.get('error') or 'start Ollama / add the key to .env'}", file=sys.stderr)
        return 2
    if args.backend == "openrouter" and not args.yes:
        print("This records a REAL OpenRouter run and spends real, limited requests. Add --yes if you are sure.", file=sys.stderr)
        return 2
    st = cfg["student"]
    if not (st["name"] and st["roll_no"]):
        print("Set STUDENT_NAME and STUDENT_ROLL_NO in .env and restart the server: they must be on screen for the whole recording.", file=sys.stderr)
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is missing: pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / "_rec"
    shutil.rmtree(tmp, ignore_errors=True)
    launch = {"args": ["--no-sandbox"]}
    if os.environ.get("CHROME_PATH"):
        launch["executable_path"] = os.environ["CHROME_PATH"]
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(viewport={"width": args.width, "height": args.height},
                                  record_video_dir=str(tmp), record_video_size={"width": args.width, "height": args.height})
        page = ctx.new_page()
        page.goto(args.url)
        page.wait_for_selector(".lane")
        page.wait_for_timeout(2500)  # a still moment with the scrambled cubes and the name bar
        page.on("dialog", lambda d: d.accept())  # the confirm box of a real run (you passed --yes)
        page.select_option("#backend", args.backend)
        page.select_option("#testSet", test_set)
        page.fill("#numPuzzles", str(puzzles))
        page.fill("#seed", str(args.seed))
        page.fill("#pause", str(args.pause))
        page.wait_for_timeout(800)
        page.click("#btnRun")
        print("Recording: the benchmark is running. This takes as long as the models need...")
        page.wait_for_selector("#slide:not([hidden])", timeout=0)  # opens by itself at the end
        page.wait_for_timeout(args.hold * 1000)
        video = page.video
        ctx.close()
        browser.close()
        path = Path(video.path())
    shutil.move(str(path), str(out))
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"Saved {out} ({out.stat().st_size / 1e6:.1f} MB)")
    if shutil.which("ffmpeg"):  # an .mp4 plays everywhere, including on GitHub
        mp4 = out.with_suffix(".mp4")
        done = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                               "-crf", "26", "-movflags", "+faststart", str(mp4)])
        if done.returncode == 0:
            print(f"Also saved {mp4} ({mp4.stat().st_size / 1e6:.1f} MB). Use this one in the README.")
    else:
        print("ffmpeg not found: convert the .webm to .mp4 yourself (see docs/RECORDING.md).")
    print("Open it and check that the name and roll number are visible from the first to the last second.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
