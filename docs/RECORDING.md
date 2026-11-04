# How to make the screen recording

The assignment is **invalid** without all four points below, so check each one when you have finished recording.

| # | The recording must show | Where it is in the app |
|---|---|---|
| 1 | **In-progress stage**: models playing, with time, moves and a "thinking / deciding" status for each model | the four lanes: timer, move counter, status line, current move, move history |
| 2 | **Completion stage**: final ranking with the labels *Solved*, *Ran out of moves*, *Out of time* | rank badge (1st, 2nd..) and a green or red result bar in each lane |
| 3 | **A final slide** with the statistical benchmarks | the *Statistics slide* (opens by itself at the end, or press **S**) |
| 4 | **Your name and roll number on screen for the entire demo** | the yellow bar at the top. It stays on top while you scroll and above the slide |

## Before you start

1. Put your details in `.env` (`STUDENT_NAME`, `STUDENT_ROLL_NO`) and restart the server. The yellow bar then shows them. You can also use the **Edit** button in the bar. If the bar is red, a detail is missing.
2. The coloured banner under the yellow bar tells the mode. The orange **PRACTICE** banner means simulated models: a recording of practice mode is **not** a valid result.
3. Close other windows and notifications. Use a browser window of about 1440 x 900 or full screen.
4. Check the request counter bar in the page (OpenRouter mode): it must be at 0 before your real run, and it stays visible in the recording.

## What to record (one video, about 5 to 8 minutes)

1. **The real OpenRouter experiment** (green banner "REAL mode"): choose *OpenRouter*, press **Run benchmark**, confirm the pop-up. One puzzle, four models. Show the timers, move counters, "deciding.." / "thinking.." status and the request counter going up. At the end show the ranking and the labels (*Solved / Ran out of moves / Out of time / Request budget used up*) and the statistics slide for at least 10 seconds. Press **Esc** to close the slide.
2. **An Ollama experiment** (blue banner "Local mode"): choose *Ollama*, test set *Primary benchmark*, 5 puzzles, **Pause = 5 to 8 seconds**. Same story: playing, ranking, statistics slide.

Optional: run the real cube a second time to show the **cache** (every move says "from cache", 0 new requests, the counter does not move).

Do not press *Run* more than needed in OpenRouter mode: the second press is free (cache) but a different cube is not.

## Option A: record by hand (any screen recorder, for example OBS Studio or the Windows Game Bar)

1. Start recording and keep the browser in front for the whole time. Do not open other windows.
2. Say nothing or explain briefly: the yellow bar must stay visible.
3. Stop recording. Scrub through the file: is the name and roll number visible in the first second, in the middle and in the last second?

## Option B: automatic recording (optional, practice and Ollama only)

```bash
pip install playwright && playwright install chromium
python -m app.main                  # terminal 1
python scripts/record_demo.py --backend ollama --test-set primary --puzzles 5 --pause 6    # terminal 2
```
It writes `demo/demo.webm` (and `demo/demo.mp4` if `ffmpeg` is installed). The script refuses to record when name or roll number are missing. For the real run use `--backend openrouter --yes`; it spends real requests, so recording it by hand (Option A) is the safer choice.

## Put it in the repository

1. Save the file as `demo/demo.mp4` (convert with `ffmpeg -i recording.mov -c:v libx264 -pix_fmt yuv420p -crf 26 -movflags +faststart demo/demo.mp4` if needed).
2. GitHub refuses files over 100 MB. Lower the quality (`-crf 30`), the size (`-vf scale=1280:-2`) or use Git LFS.
3. In the top of `README.md` replace `YOUR-USER` and `YOUR-REPO` in the direct link, and fill in your name and roll number.
4. Commit and push, then open the link on GitHub in a private window to check that it plays.
