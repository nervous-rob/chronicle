# Running Chronicle on your own machine

## Requirements
- Python 3.11 or newer (uses `tomllib`).
- `numpy`. Nothing else for the scripted simulation: no network, no GPU.
- Optional, only for `--llm` and `bench.py`: a local [Ollama](https://ollama.com) server. The adapter itself uses only the standard library (`urllib`).
- Validated on CPython 3.13.5 / numpy 2.2.4 (Linux aarch64). Other platforms are untested.

## Quickstart
```
cd sim-core
pip install numpy
python run.py --days 365 --out runs/x --set population.initial=100
```
- `--days N` is how many days to step. Always pass it: the default config is 500 years.
- `--out DIR` output directory (created if missing).
- `--set KEY=JSON` overrides any config key, repeatable. Values are JSON, e.g. `--set population.initial=60`, `--set run.seed=7`, `--set 'weather.lightning_p={"spring":0,"summer":0,"autumn":0,"winter":0}'`. Unknown keys are rejected.
- `--config FILE.toml` uses a run TOML (see `run.example.toml`); omitted means `run.default.toml`.
- `--resume runs/x/checkpoint-day-0360.zip --days 100 --out runs/x` continues from a checkpoint (the config must match).
- `--quiet` suppresses progress lines.
- Reference timing: 100 people for 365 days took about 188 s on the development machine (a Raspberry-Pi-class aarch64 box). Expect it to scale with population.

## Running with a local model (Ollama)
**Status: untested against a real model.** The adapter (`chronicle/llm.py`, `run.py --llm`, `--replay`, `bench.py`) is validated only against a fake Ollama server inside `tests/test_llm.py`. `qwen3.5:9b` is the `[llm].model` default in `run.default.toml`; it has not been run by the author, and these steps are the first real exercise of it. Expect to adjust `[llm]` (model tag, `num_ctx`, `num_predict`, `format`, `think`, `timeout_s`) to what your hardware and Ollama version actually do.

1. Install Ollama and start the server (`ollama serve`, default `http://localhost:11434`; set `[llm].url` or `--set llm.url='"http://host:port"'` otherwise).
2. Pull the model:
```
ollama pull qwen3.5:9b
```
3. Benchmark the model on real observation packets first (read-only; writes `bench-results.json`; scores parsed / on-menu rate and latency for the schema, json and none reply formats):
```
python bench.py --n 24
```
4. Short LLM run (K people per day are decided by the model; everyone else follows routine logistics):
```
python run.py --llm --days 30 --out runs/llm --set population.initial=40
```
- Use a fresh `--out` directory: `digest.jsonl` and `decisions.jsonl` are appended to, not overwritten.
- Outputs add `decisions.jsonl` (every model decision, with the raw reply for invalid ones) and an `llm` block in `final.json` (calls, tokens, p50/p95 latency, invalid count).
- A bad reply, transport error or off-menu action is logged and falls back per `[llm].on_invalid` (`routine` = the person's usual work, `rest` = rest). It is never silently dropped.
- Replay a recorded run with no model contacted (the config and `--set` overrides must match the original run):
```
python run.py --replay runs/llm/decisions.jsonl --days 30 --out runs/llm-replay --set population.initial=40
```
- `--llm` and `--replay` are mutually exclusive. `--llm` against an unreachable server exits with a readable error; if the model is not pulled, `ping` names it and the pull command (both tested against the fake server only).
- `bench.py` has no automated test. It imports and prints `--help`, but no suite executes it end to end.

## Outputs (in `--out`)
- `setup.json`: generator report (households, farms, roles, clusters, warnings).
- `digest.jsonl`: one JSON line per simulated day: alive, dead, hungry, event counts by type, total food units, moisture/burning/planted summary. **Re-running into the same `--out` appends to it**; use a fresh directory for each run.
- `checkpoint-day-NNNN.zip`: full world state every 360 days (`checkpoint_every_ticks` × `days_per_tick`); about 3 MB at 100 people.
- `final.json`: run_id, seed, final day, summary (alive/dead, stocks, ecology, wildlife, conservation checks), digest hash, seconds.

Same config and seed give the same digest hash, including across processes (tested).

## Checking your install
```
python tests/validate.py core          # 14 suites (incl. test_llm.py, fake Ollama server), ~6 min
python tests/validate.py checkpoints
python selftest_config.py
```
Each writes JSON to `test-results/`. `SOURCE_MANIFEST.json` lists file hashes for the validated revision.

## What is NOT implemented yet
- **The LLM adapter has never been run against a real model.** Every LLM test uses a fake Ollama server (see `IMPLEMENTATION_STATUS_llm.md`). Nothing here has been run against qwen3.5:9b or any other real model.
- **No commitments** (promises, fulfillment/breach events).
- **People use scripted policies:** the engine's routine logistics (farm workers, pantry keepers) plus the policies tests bind. Nobody decides anything on their own.
- No material economy: burned houses and granaries are never rebuilt (farms are).
- Births and background deaths (demography) are still pending config.
- Results are not calibrated; treat runs as mechanism checks, not a society model.

## Pressure scenarios and scripted-vs-LLM comparison (pressure-v1, compare-v1)
`[scenario]` in `run.default.toml` is off by default (`enabled = false` leaves every digest unchanged). Turn it on with `--set scenario.enabled=true`; each effect has its own day and can be switched off with `day = -1`:
- `drought`: scales evaporation and rain for a window of days. `ignition`: lights the driest cells near buildings. `blight`: cuts crop health on planted cells once. `stranger`: an adult with no household arrives carrying grain.
- New verbs with a real engine process behind each: `give` (carried food to someone beside you), `hunt` (wildlife, capped at what you can carry), `firebreak` (clears fuel from your cell). The attention score has an `alarm` weight so people near smoke or fire get asked.
- Trade, evacuation, spotting and courts are NOT offered: nothing in the engine backs them yet.

Compare a scripted run with an LLM run of the same config and seed:
```
python compare.py --run --days 100 --scenario --out runs/cmp                 # needs Ollama running
python compare.py --run --days 100 --scenario --seeds 1,2,3 --out runs/cmp3  # one pair per seed
python compare.py runs/cmp/scripted runs/cmp/llm                             # compare two finished runs
```
The report lists survival, hunger, food, fire and ecology outcomes, the first day the worlds differ, and which kinds the model chose (including how often it used the new verbs). One seed is an anecdote.

**Status:** all of this is tested against scripted policies and a fake Ollama server (`tests/test_pressure.py`, `tests/test_compare.py`). No real model has used the new verbs yet; hunting yield and the scenario severity are uncalibrated.

## Memory and telling (memory-v1)
Memory already existed in the engine (witnessing, fidelity, hearsay with hop decay, a per-person limit by role). What was missing is a way for a model-driven person to use it.
- New verb `tell` (0.25 h): `{"kind":"tell","recipient":ID,"event":N}` passes one remembered event to a co-present person through `people.tell`: a private ledgered `tell` event, a hearsay record in the listener at fidelity x `tell_fidelity_mult`, hops+1, from = the teller.
- The menu entry is `{"kind":"tell","news":[{"recipient":ID,"events":[...]}]}`: per listener, only events they would be hearing for the first time. **This is a simplification**: a real person reads a blank face; here the engine reads it from the listener's memory. A tell the listener already holds is refused with no time spent.
- Each packet `memory` entry now also has `who`: the names of the people involved (actors), so the model can say who did what.
- The contact phase (background hearsay) is unchanged and still runs; `tell` is the deliberate version of it.
- What memory does NOT do yet: nothing in the world reacts to what a person knows except plan breaks ("surprise" events) and records they write. Knowing about a fire does not yet make anyone act differently unless the model chooses to.

## Watching a run (replay viewer)
Add `--frames` to any run (`--llm`, `--replay` or scripted) and it also writes, next to the digest:
- `world.json` (once): grid size, terrain, rivers, roads, buildings, role names.
- `frames.jsonl`: one line per day with every person's cell, building, role, food/health/rest, last action, plus burning and planted cells, burned buildings and the day's notable events (movement and goods transfers are left out).
- `context.jsonl` (with `--llm` / `--replay`): for each model decision, the menu the person was shown and their body bands. `decisions.jsonl` only stores a hash of the observation, so without this file the feed cannot show what the menu was.

`--frames` only reads state: the digest is identical with it on or off (`tests/test_frames.py`).

```
python run.py --llm --frames --days 30 --out runs/look --set population.initial=40
```
Then open `viewer.html` in a browser (no server, no internet) and drop `world.json`, `frames.jsonl`, `decisions.jsonl` and `context.jsonl` onto the window (or use "Load files"). Scrub with the slider, the timeline or the arrow keys (shift = 10 days), space plays. Click a person or cell on the map, or an entry in the Decisions feed. A white ring on the map marks people asked that day; the feed marks invalid replies and routine fallbacks.

**Status:** `viewer.html` has been exercised only by scripted checks (its data model against a real run, and a stubbed canvas to confirm it draws without errors). Nobody has looked at it in a real browser yet, so expect layout and usability problems. Frames from an old run cannot be made without re-running it (`--resume` from a checkpoint writes frames only from that day on).
