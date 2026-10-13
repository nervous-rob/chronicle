# Chronicle implementation status — llm-v1 (local-model adapter, increment F part 1)

**READ THIS FIRST: every LLM test used a fake Ollama server. Nothing was run against a real model. `qwen3.5:9b` has never been run against this code, and no result in this file says anything about how it, or any real model, behaves.**

Written 2026-10-11 01:09 UTC. Validated by Observatory job 3170 (2026-10-11 01:00–01:07 UTC, python 3.13.5 / numpy 2.2.4): `CORE EXIT 0` (14 suites), `CHECKPOINTS EXIT 0` (1 suite) and `SELFTEST EXIT 0` all appeared. Both validation reports bind the same 40 .py/.toml source hashes, and the workspace matched them byte for byte when this package was built.

## What was added
- `chronicle/llm.py`: `OllamaClient` (Ollama `/api/chat`, standard library only; model, `stream:false`, `think`, JSON-schema / json / no `format`, `num_ctx`, `num_predict`, `keep_alive` from `[llm]`; retry with backoff; `<think>` stripping; retry without `think` on an HTTP 400 from a model that has no thinking switch) and `LLMPolicy` (a `TextPolicy` over the client; unparsable reply, transport error or off-menu kind is logged and falls back per `[llm].on_invalid`).
- `chronicle/attention.py`: deterministic selection of K people per day (`[attention]`), children never consulted, less-recently-consulted people favoured, `run_day`.
- `run.py --llm`, `--replay DECISIONS_JSONL` (no model contacted), `decisions.jsonl`, an `llm` block in `final.json`; `bench.py` (model/format comparison on collected observation packets).
- `[llm]` and `[attention]` config sections with validation (`format`, `think`, `on_invalid`, `url`, `retries`, `timeout_s`, ...).

## Evidence (all against a fake server unless stated)
`tests/test_llm.py`: 50 checks, 0 failures. The fake server (`tests/test_llm.py`, class `H`) answers `/api/tags` and `/api/chat` with canned replies chosen by a mode switch: always-`work`/`rest`, prose garbage, a leaked `<think>` block, an off-menu action, an empty reply, injected HTTP 500s, and an HTTP 400 for `think`. It reports fixed token counts (900 prompt / 12 eval). What the 50 checks cover:
- config validation of every new `[llm]` / `[attention]` key (7 rejection checks) and defaults;
- request shape: model, `stream:false`, `think:false`, schema `required: kind`, schema enum = verb registry, `num_ctx`/`num_predict`/`keep_alive`, `json` and `none` formats;
- reply handling: think-block stripping, one 500 retried, exhausted retries become an `!llm_error` text and never an exception, empty reply is an error, HTTP 400 on `think` retried without it;
- fallback policy: garbage + `routine` -> work, garbage + `rest` -> rest, off-menu kind invalid, valid `rest` accepted;
- attention: deterministic and sorted, K from `k_per_tick` / `k_per_pop`, scaling and cap, children excluded, consulted people carry less debt;
- whole runs through `run.py` with the fake server: 160 decisions over 20 days logged with the model name as source, `final.json` carries calls/tokens/p50/invalid, an always-work fake keeps the 40-person village alive exactly like the routine baseline (40/40), a garbage-only fake is logged as invalid with people still fed, a checkpoint is written every 5 days and an LLM run resumes from it (day 15, 40 alive);
- replay: zero model calls, bit-identical digest match against the LLM run, matching daily alive counts;
- CLI: `--llm` against a dead port exits with a readable error; `--llm` with `--replay` is rejected.

Per-suite check counts (from `test-results/logs/*.txt`, `ok` lines):

| suite | checks |
|---|---|
| test_agency.py | 57 |
| test_checkpoints.py | 82 |
| test_cleanup.py | 36 |
| test_config_coverage.py | 5 |
| test_contact.py | 44 |
| test_ecology_controls.py | 13 |
| test_fire.py | 24 |
| test_food_loop.py | 43 |
| test_llm.py | 50 |
| test_multi_household.py | 39 |
| test_plans.py | 43 |
| test_rebuild.py | 11 |
| test_sleep.py | 39 |
| test_wildlife_controls.py | 78 |
| test_worldgen.py | 29 |

Core group: 14 suites, 511 checks. Checkpoints group: 82 checks. `selftest_config.py`: OK (12 rejection cases, 3 variants, regenerated `config.frozen.example.json`).

## What this does NOT show
- **No real model was ever called.** Not qwen3.5:9b, not any other. Whether that model tag resolves in your Ollama install, whether Ollama's structured-output `format="schema"` is honoured by it, whether `think:false` is accepted, and what it actually replies are all unknown.
- Reply quality: valid-JSON rate, on-menu rate and the quality of its choices. The fake server's "always work" village says only that the plumbing is neutral; it says nothing about how a model's decisions change the society.
- Latency, tokens/s, VRAM fit (the `run.default.toml` comment about a 6.6 GB Q4_K_M model fitting a 12 GB card is an unmeasured assumption), and whether `num_ctx = 4096` / `num_predict = 300` suit a real packet (the ~1k-token packet size is an estimate). `avg_scene_seconds = 3.0` and `gpu_hours_budget = 40` are guesses, not measurements.
- `bench.py` is not executed by any test. It is hash-bound by validation, and it imports and prints `--help` (checked by hand when this package was built), but no suite runs it end to end, even against the fake server.
- Replay was tested bit-identical only for runs recorded from the fake server. Whether a real model's free-text replies replay identically follows from the design (decisions are logged and re-fed) but is untested.
- Not in this increment: chronicler cadence and prompts (`[chroniclers]`, `[prompts]` are still PENDING in config coverage), commitments, material economy, markets, demography, calibration.

## Supersedes
Earlier status files (`IMPLEMENTATION_STATUS.md`, `..._worldgen_rebuild.md`) say no LLM code exists in the package. That is stale as of this revision: the adapter exists but is only fake-server tested, as stated above.

## First real exercise (see `README_RUN.md`)
```
ollama pull qwen3.5:9b
python bench.py --n 24
python run.py --llm --days 30 --out runs/llm --set population.initial=40
```
Treat the output of those three commands as the first real evidence about the adapter; until then it is unvalidated against a real model.
