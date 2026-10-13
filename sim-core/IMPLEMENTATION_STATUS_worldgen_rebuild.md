# Chronicle implementation status — worldgen-v1 + rebuild-v1

Validated by Observatory job 3126 (2026-10-11 00:07–00:13 UTC) on python 3.13.5 / numpy 2.2.4: core group, checkpoints group and `selftest_config.py` all exited 0. Both validation reports bind the same 36 .py/.toml source hashes, and the workspace matched them byte for byte when this file was written.

## worldgen-v1
- `chronicle/setup.py` builds a world as a pure function of (config, seed): population clusters, households, farms, pantry keepers/workers, roles, media and starting stocks. It writes a generator report (`setup.json`). A sorted-set determinism fix was needed: place ids once differed across processes because of the string hash seed; `test_worldgen.py` now checks cross-process determinism.
- `run.py` CLI: generate or `--resume` from a checkpoint, `--days`, `--out`, repeatable `--set KEY=JSON`. Writes `setup.json`, `digest.jsonl` (one line per day), `checkpoint-day-NNNN.zip`, `final.json`.
- Household/keeper logistics live in `farming.py`; the checkpoint stores a Household record.
- `crops.<id>.stress_loss_per_day` (grain 0.003) replaces the hardcoded 1.5%/day dry-day penalty: yield lost per fully moisture-stressed day (dry or waterlogged) × (1 − moisture factor).
- Calibration findings recorded earlier: `moisture_capacity_mm` 300, `raid_rate` 0.002, `fire.min_effective_fuel` 0.25.
- **Evidence run `subsist-100c`** (100 people, 365 days, lightning ON, seed 42, run_id `127f35fe1cf5`): **100/100 alive, 0 dead**, max hungry on any day 9, 11 ignitions (days 4, 5, 43, 112, 114, 150, 156, 158, 168, 179, 180), 2 `repaired` events (days 54, 59), 32 plantings and 32 harvests, conservation checks 365, residuals 0, digest `2bffdb47bd0d3dde…`, 187.51 s. Predecessor `subsist-100b` (before rebuild-v1) lost 2 farms on days 8–9 and 5 people the next winter (95/100).

## rebuild-v1
- A burned building can be rebuilt by its household only if its catalog row has no materials (`farming.rebuildable`). Today that means farms (and pastures).
- Cost is `labor_days × agriculture.activity_hours_per_day` (farm: 20 × 8 = 160 labor-hours), spent from the worker's daily budget minus the walk home, accumulated in `Farm.rebuild_hours`, labelled `build`. On completion the building is restored (condition 1.0, `destroyed_day` cleared) and a `repaired` event is ledgered. Nothing is created from nothing: conservation holds through burn + rebuild (tested).
- Checkpoint `VERSION = 7`: `Farm.rebuild_hours` is serialized; a checkpoint taken mid-rebuild replays bit-identically over 40 days (tested).
- **Not rebuildable: houses, granaries, manors, fisheries (anything with `materials`).** There is no material economy (timber/stone production, trade, stockpiles). The suite asserts a burned house is never rebuilt rather than papering over it.

## Suite counts (from `test-results/validation-core.json` and per-suite JSON)
| suite | checks | seconds |
|---|---|---|
| test_food_loop.py | 43 | 16.099 |
| test_ecology_controls.py | 13 | 3.161 |
| test_fire.py | 24 | 0.518 |
| test_wildlife_controls.py | 78 | 18.053 |
| test_multi_household.py | 39 | 103.305 |
| test_agency.py | 57 | 8.825 |
| test_sleep.py | 39 | 1.124 |
| test_cleanup.py | 36 | 1.254 |
| test_config_coverage.py | 5 | 0.337 |
| test_plans.py | 43 | 2.125 |
| test_contact.py | 44 | 2.668 |
| test_worldgen.py | 29 | 71.82 |
| test_rebuild.py | 11 | 85.811 |

Core group: 13 suites, 461 checks, 315.1 s of suite time. Checkpoints group: 82 checks (29.61 s). `selftest_config.py`: OK (12 rejection cases, 3 variants, regenerated `config.frozen.example.json`). `test_rebuild.py` writes no `rebuild.json`; its 11 checks come from `test-results/logs/test_rebuild.txt` (suite wall time 85.811 s).

## Open findings (carried, not fixed)
- Houses/granaries cannot be rebuilt after fire; a household that loses its home is stuck. Needs a material economy.
- Wet-region crop ceiling: region 4 sits above `moisture_max` all summer. In `subsist-100c` the end-of-year mean moisture is 0.997, so this persists.
- `subsist-100c` ends with raider wildlife total 35470 against game 312 and predator 876; grain stock more than doubled (1793098 → 3811291 food units). These are one seed on one year and are NOT calibration; worth a look before anyone reads them as demography.
- 100/100 alive is one seed (42), one year. It shows the rebuild loop closes the specific failure seen in `subsist-100b`; it is not evidence of long-run viability.

## Not in this increment
LLM adapter (TextPolicy is only the parsing seam), commitments/promises, material economy, markets, births/deaths demography (`birth_rate_per_year`/`death_base_per_year` still pending in config coverage). People follow scripted/routine logistics.
