# Chronicle implementation status — October 8, 2026 (America/Denver)

## Verified increment
- inventory.py: physical lots, separate owner/custodian, atomic multi-leg transfer, integer quantities, carrying/storage limits, explicit resource flows, per-medium identity, marginal SEIR herd counts, spoilage and fire loss.
- people.py: individuals, derived stages, capability teaching, permanent learning, named-memory eligibility, same-place witness/privacy checks, physical record/read/copy/seal/open, daily food/rest/safety and provisional starvation rule.
- engine.py: daily integration harness connecting ecology, inventories, needs, observations and conservation. Place setup is explicit; no autonomous world generator yet.
- 53 new foundation checks pass; selftest-config, selftest-core, selftest-cells all rerun successfully after RNG correction. 60-day same-seed equality and 7-versus-30-day reporting-cadence invariance verified.
- Synthetic default-size 100-person/365-day integration completed; all 365 conservation closes balanced. See test-results/integration-365-days.json. This is a seeded-food smoke test, NOT a self-sustaining village.

## Critical correction
Original rng.py used day in the lowest advancing Philox counter word. Day 1 reused day 0's next four-value block. First-draw inequality tests missed this. Layout v2 reserves low 128 bits for block advancement, with day/entity in the upper words. rng.layout_version=2 is validated and included in run_id. audit-rng-counter v3 checks multi-block nonoverlap and coordinate bounds. Previous ecology numerical baselines are superseded.
New selftest-cells wet/dry harvest totals: 8036/4776 food quanta under that synthetic fixture (not yield calibration).

## Existing ecological gaps (not resolved by passing smoke tests)
- Disease test lacks an uninfected control; the global game decline also includes predation/habitat effects. Do NOT attribute 85% decline to murrain.
- Wildlife disease fractions do not yet advect with dispersing animals. Human/herd exposure and disease progression not implemented.
- Fire crossing longer than one day lacks persistent edge/front progress; externally supplied ignition hour is reset by step_fire; fuel loss is daily rather than burn-duration weighted.
- Fire/building coupling is provisional all-or-nothing destruction after a daily exposure threshold, no evacuation/partial structural damage.
- Predation/raiding/crop defaults are uncalibrated; an uninfected control and paired scenario tests are needed before tuning.
- A running expectation alone does not guarantee persistent generational shifting baselines. That earlier claim was too strong.

## Deliberate foundation boundaries
- Markets/prices, consent and action resolution, journeys and local access to remote stores, work production, shelter crowding/cold effects, social edges/leisure, births/age mortality/succession, attention/chroniclers and full observation/citation provenance remain unfinished.
- Transfer is a trusted low-level primitive; caller must resolve consent, physical feasibility and reach. Public Python state is engine state, not an agent API.
- Hern health is marginal counts per disease, not joint coinfection tracking; vehicles/herds are led/driven, not back-carried.
- Ground crops become physical item lots on harvest; do not reintroduce an abstract cell food stock.
- Media foundation supports literacy and place checks; automatic record supply and auto-append are not wired yet.
- People allocation currently work/rest only; neutral teaching trait factor until a configured trait-learning table exists.
- Starvation is a provisional deficit-equivalent-day rule; no medical calibration claimed. Health effects of cold/disease are pending.
- No resumable world serialization yet: digest is for comparison, NOT a checkpoint. Do not start a long background world run until resume round trips pass.
- Current people/inventory iteration is straightforward Python, not the final population-scale vectorization.

## Next bounded increment
1. Add controlled ecology regressions (infected vs uninfected; dispersal of disease; slow-edge fire progress).
2. Physical movement/local access and actual daily work/harvest/deposit before market economics.
3. Demography/contact disease and serialization; then a small self-sustaining multi-year village and calibration dashboard.

## Tooling
Project reached the documented 20-asset limit. No existing notes were deleted. Reuse existing versioned scripts and store source/tests/status in sim-core/ (Projects; Project examples documentation). Builder v1 assets preserve the original files; final source is authoritative in the workspace snapshot, including subsequent patches.
