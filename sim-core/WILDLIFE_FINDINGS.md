# Wildlife mechanism isolation — engineering evidence

Verified 2026-10-09T05:43:45.802763+00:00 (UTC). Runtime Python 3.13.5, NumPy 2.2.4, Philox layout 2.

## Paired 180-day outcome

Same seed, 12 × 8 km configured world, 2 weather regions, lightning disabled, no seeded infection, hunting or crops. Each factorial treatment has identical starting abundance and weather/vegetation forcing. Habitat-off fixes K at terrain target while vegetation continues evolving. Growth-off disables both logistic gain and density loss. Dispersal-off prevents movement; it does not silently add mortality.

| Condition | Initial game | Final game |
|---|---:|---:|
| Default predation and evolving habitat | 493.634505 | 56.703390 |
| No predation, evolving habitat | 493.634505 | 542.216714 |
| Predation, fixed habitat capacity | 493.634505 | 78.949224 |

The baseline realized budget (statistical animals, not owned goods) is:

`493.634505272 + 51.401921451 -0.102512121 -488.230613470 +0.000089012 = 56.703390144`

Residual: 0.0. Disease, fire and hunting: zero. Predation dominates this bounded uninfected decline. No-predation minus baseline final abundance is NOT the same as accumulated predation mortality, because survivor-dependent growth/density losses respond. Likewise habitat coupling affects both carrying capacity and predation pressure; it is not a second independent additive sink.

## Prey-regulation switches (2026-10-12; all OFF by default, uncalibrated controls)

The legacy rule kills `predation_rate × prey × predator/K` with predator K following vegetation, not food. At the default `predation_rate` 0.02, predators at capacity remove ~2%/day of game against game growth `r` 0.002/day, so game collapses (probe: ~1% of K) while predators hold ~60% of K. Four switches exist as controls; with all of them off, the 60-day/40-person/seed-42 digest is `618500b9…8bb28` (re-confirmed after the intake patch).

| Switch | Mechanism | Result (5 seeds, no fire/people) |
|---|---|---|
| `prey_coupling` | map-wide starvation drain on predators when prey is scarce | game recovers slowly; predators fall to ~0.1–0.2% of K (near extinction) |
| `prey_refuge` | kill fraction × clip(local prey fill / `refuge_fill`) | prevents the last extinction only; game ~0.7% of K |
| `prey_response` | predator target K × clip(local prey fill / `response_fill`, `response_floor`, 1) | healthy only if `predation_rate` ≤ ~0.005 |
| `prey_intake` | kills = min(rate × prey × pred, `intake_cap` × pred); fed fraction sets predator growth, unfed lose `intake_starve`/day | healthy at cap 0.004 for any rate 0.005–0.02; see sensitivity |

`prey_intake` replaces `prey_coupling` and `prey_response` (config rejects combining them); it may be combined with `prey_refuge`.

Healthy criterion (fixed before each sweep): game > 5% of K and predators > 20% of K, nothing extinct, budget closed.

**Intake cap sensitivity (rate 0.02, no fire):** cap 0.004 steady ~48% game for 15 yr; 0.005 slow decline 51% → 37%; 0.006 collapses to ~2% by yr 15; 0.008 collapses ~yr 7 and predators die out with it. At 0.004 predators are always fully fed, so the numerical response never engages; predator numbers still follow vegetation. Predators begin starving only when game is near 4% of K, too late to stop a decline. **With fire (cap 0.004, 2 seeds, 20 yr):** seed 2 stayed healthy (lows: game 31%, predators 32%); seed 1 fell to game 15% / predators 9% after fires and recovered to ~41% / ~23% by yr 20, spending years below the predator bar.

Open: predator carrying capacity is still vegetation-based (the structural limitation above). Hunting yield at the healthier game levels is untested, and so are full-size maps. These sweeps used a 12 × 8 km world. None of these values are measured from a real species.

## Correctness versus plausibility versus calibration

- Correctness tested: component budget closure each day; finite/nonnegative abundance and normalized SEIR; bounded overlapping predator removal; dispersal host/infected-count conservation; boundary behavior; repeat-seed and reporting-cadence equivalence; no ecological default changes on the 180-day comparison to published fire-front source.
- Structural limitations: predator abundance grows logistically with vegetation-based K rather than prey intake. Generalized trophic chains update sequentially in catalog order, not simultaneously. Habitat-off is a diagnostic counterfactual, not a proposed ecological default. Hunting is still a provisional statistical decrement/yield primitive, not a full physically scheduled production chain.
- Grid limitation: uniform local equations agree at equal density without dispersal; the per-cell daily dispersal fraction is not a physical diffusion coefficient. One-step pulse mean-square displacement is 0.1 km² on a 1 km grid and 0.025 km² on a 0.5 km grid. This diagnoses dependence, not a full convergence study. A physical movement contract is needed before comparing heterogeneous grids as if they were equivalent.
- Empirical calibration: **not performed**. No ecological defaults were tuned. Mechanism accounting does not establish historically realistic populations, household viability or society sustainability.

## Reproduction

From the directory containing `sim-core/`, run `python sim-core/tests/test_wildlife_controls.py`. It requires only NumPy and the standard library. Reports include all 16 treatments, 32 paired contrasts, sampled trajectories and exact diagnostic budgets in `test-results/wildlife-controls.json`. `test-results/wildlife-legacy-comparison.json` records the separate source comparison against the retained `chronicle-core-fire-front.zip`.

The original smoke-suite label “murrain kills game” is still a within-infected-run decline check and cannot establish causal mortality. Paired disease and wildlife reports govern interpretation.

## Persistence and next increment

Source/report history and versioned validation entry points follow **Projects** and **Project examples** documentation. No background society run or serialization claim was made. Add new ecological diagnostic state to the full serializer, alongside arrays, fire progress, people, places, inventory identities, ledgers, journeys, farming progress and callbacks. Test continuation outputs and conserved state, not just equal fingerprints.
