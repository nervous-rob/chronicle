# Chronicle implementation status — October 8, 2026, late evening (America/Denver)

## Current increment: fire front correctness (handoff item A)
- `cells.py` fire now carries unfinished crossings across days: `edge_progress[cell, k]` is the completed fraction of the crossing toward neighbour k, re-priced each day at that day's edge cost (fuel, moisture, wind). A viable crossing longer than one day completes once enough burning hours accumulate.
- Explicit ignition hours are preserved (`ignite(hour=)` is validated to lie within the day). Continuing fires burn from hour 0; `burn_since_day` distinguishes new ignitions from continuing burns.
- Extinguished sources lose their progress; a target that ignites by any route clears all unfinished progress toward it, so overlapping fronts never double-ignite. Each cell gets exactly one ignition/spread event per burn episode.
- Fuel/vegetation/wildlife losses now scale with hours burned today (`loss ** (hours/day_hours)`), so a cell lit at hour 20 is not charged a full burn day. Building exposure (`engine._burn_stores`) still uses the lethal-day threshold; partial structural damage, evacuation and spotting remain unimplemented.
- New `tests/test_fire.py` (24 assertions, `test-results/fire.json`) turns both former KNOWN GAP probes into assertions and adds: firebreak/water barriers, extinguished sources, re-pricing under rising moisture, overlapping fronts, event uniqueness, duration-scaled losses, engine coupling with exact daily closes, and seed/cadence invariance. The two probes in `test_ecology_controls.py` are now asserted (13 checks).
- All prior suites rerun green after the change: food-loop 43, people-inventory 53, selftest-cells, selftest-config. Food-loop budget unchanged (655,420 − 180,067 = 475,353). Numerical fire baselines from earlier revisions are superseded (losses are now duration-scaled); `digest()` includes the new fire arrays.
- Not done: fire calibration, building partial damage, evacuation, spotting (`spot_p` still 0), hearth ignition coupling.

## Previous increment: physical food loop and controlled ecology

## Current increment: physical food loop and controlled ecology
- Added `chronicle/farming.py`: explicitly assigned owner/household workers, planted crop cohorts, accumulated per-hectare planting/harvest labor, exact-place pickup, finite loads, physical store/pantry delivery and daily consumption. Travel, handling and field work spend one common per-person daily activity budget.
- Journeys retain unfinished edge travel across days; carried lots remain with the carrier. Distinct places in one hex have a configurable nonzero travel time. Arrivals/departures are ledger events.
- Harvest lots keep owner, exact field place and harvest-event provenance through splitting and delivery. Exposed ground stocks can spoil and burn; all losses are ledgered. Same-origin fungible food compacts at day boundaries to avoid unbounded lot fragmentation.
- People witness at event time, not from their final evening location. Traveling to a building does not retroactively expose its earlier events.
- Wildlife SEIR compartments now disperse with host abundance. Birth increments are susceptible; density/predation losses proportionally remove host compartments. Dispersal-only tests conserve total and infected hosts.
- Added `tests/test_food_loop.py` and `tests/test_ecology_controls.py`; results are in `test-results/food-loop.json` and `test-results/ecology-controls.json`. See `verification.json` for final suite status.

## What the successful integration proves
One assigned adult household, a finite setup buffer of 15,420 ten-gram food units, two full seasons of planting/harvesting, 730 daily conservation checks and no post-setup food injection. Repeat seeds and 7/30-day reporting cadences agree. The favorable fixture explicitly holds moisture optimal and disables raids and lightning. This proves the implemented food path works; it is NOT a calibrated historical society or an ecosystem sustainability claim.

The matched baseline-weather/raiding fixture (lightning still disabled) dies within 365 days. No ecological defaults were adjusted to hide that outcome. Reports retain its harvest and death events. Planting/harvest labor and handling values are new provisional engineering assumptions, not researched historical measurements.

## Bugs found and fixed in this increment
- Full stores caused carried grain to block every future task, including the next planting season. Seasonal labor can now proceed with undeliverable cargo; overflow remains physically present.
- Daily transfers created hundreds of tiny food lots. Identical-provenance lots compact; media never compact. Future lot-reservation contracts must pin their lots or reference provenance/quantities instead of assuming surviving lot ids.
- Disease fractions stayed behind when wildlife dispersed. Fractions now travel as compartment counts.
- End-of-day witnessing would have leaked events after introducing travel. Direct witnessing is now synchronous with event emission.
- Ground harvest escaped the building-only fire bridge. Exposed lots now have explicit burn losses.

## Controls and unresolved defects
- Disease-only infected/uninfected controls isolate mortality. Full-ecology pairing shows major game decline even without infection; predation/habitat dynamics are not calibrated. Old `selftest-cells` v1's label "murrain kills game" is only a within-run decline check, NOT causal attribution. The new paired report is authoritative for that distinction.
- (Fixed in the fire increment above) slow inter-cell crossings and external ignition hours. Building loss is still the coarse lethal-day rule; fire is not calibrated.
- Disease cross-host updates, human/herd exposure and full contact progression remain unfinished; per-disease herd counts are marginal, not joint co-infection.
- Expectation smoothing alone does not guarantee intergenerational baselines; prior claim was overstated.

## Bounded resolver assumptions (not silently generalized)
- One registered farm and crop cohort per cell. Multiple owners/cohorts in a cell are rejected; no ownership mixing.
- Farms, workers, home and store must be explicitly assigned. No autonomous settlement generator, hiring/consent market, guarding, seed-grain inputs, soil nutrient accounting, tools, fodder or non-crop production yet.
- Basic logistics use trusted assigned household stock counts. General agent decisions must later use observation/belief state; do not expose global arrays to agents.
- Field labor is an accumulated cohort task with one materialization on completion, not partial harvesting. Long-distance commuting and general food/ration planning remain limited.
- Role/age/capability checks apply to farming. Other direct People primitives are still trusted low-level APIs; a future universal action scheduler must unify teaching/recording/guarding with this budget.
- Paths hold an entered edge's cost until crossed and recalculate later edges. Dynamic rerouting, fire avoidance/evacuation, fatigue and road wear are pending.
- Food coalescence preserves represented provenance, not physical batch ages (age-dependent spoilage is not modeled). Same seed/cadence equality is verified; bitwise equivalence to older source revisions is not promised.
- Initial 53 people/inventory checks, config/core/ecology smoke suites and Philox audit have been rerun after integration.
- No full resumable world serialization. `digest()` is a regression fingerprint, not a checkpoint. Do not start a long background society run until resume round trips pass.
- Demography, succession, markets, general agent menus, social edges, attention and chroniclers remain unfinished.

## Recommended next increment
Fire front repaired; next isolate predation/habitat losses before tuning. Then add serialized world round trips and small multi-household runs with explicit transport/ration constraints before markets or large-scale history.

## Reproducibility and tools
Workspace source and the versioned food-loop ZIP are authoritative. Historical builder assets predate subsequent fixes; do not rerun them to patch current sources. `build-engine` head validates the current workspace rather than overwriting it. Original foundations snapshot and status remain available.
Project remains within the documented existing-asset limit by reusing versioned entry points (Projects; Project examples documentation). Test failures were code assertions, not a disabled or broken Observatory feature (Troubleshooting tactics).
