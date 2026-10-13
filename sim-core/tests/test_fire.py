"""Fire front correctness: persistent multi-day crossings, preserved ignition hours, extinguished
sources, firebreaks, re-pricing under changing weather, event uniqueness and duration-scaled losses.
These were formerly recorded as KNOWN GAPS in test_ecology_controls.py; they are assertions now."""
import sys, json
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from chronicle.config import load
from chronicle.cells import Cells
from chronicle.rng import Streams
from chronicle.world import World
from chronicle.ledger import Ledger
from chronicle.engine import Engine
from chronicle.world import T

checks = []
def check(ok, msg):
    assert ok, msg
    checks.append(msg); print('ok', msg)
base = {'world.width_km': 12, 'world.height_km': 8, 'world.region_count': 2,
        'weather.lightning_p': {s: 0. for s in ('spring', 'summer', 'autumn', 'winter')}}
def fixture(extra=None, fuel=.9):
    cfg = load(overrides={**base, **(extra or {})}); w = World.generate(cfg)
    c = Cells(cfg, w, Streams(cfg.seed)); L = Ledger(cfg)
    c.moisture[:] = 0; c.fuel[:] = np.where(c.land, fuel, 0); c.wind_kph[:] = 0
    src = int(np.flatnonzero(w.mainland())[0])
    return cfg, w, c, L, src
def land_nbrs(w, c, i):
    nb = w.nbr[i]; return [int(j) for j in nb if j >= 0 and c.land[j]]
def events(L, kinds=('fire_ignited', 'fire_spread')):
    return [e for e in L.events if e.type in kinds]

# 1. explicit ignition hour survives the daily pass (former probe: external_ignition_hour_preserved=false)
cfg, w, c, L, src = fixture()
c.ignite(0, [src], 'test', L, hour=20.); c.step_fire(0, L)
check(abs(float(c.burn_hour[src]) - 20.) < 1e-6, 'external ignition hour is preserved by the daily fire pass')
ev = [e for e in events(L) if e.cell == src]
check(len(ev) == 1 and abs(ev[0].payload['hour'] - 20.) < 1e-6, 'ignition event carries the real hour')
late_fuel = float(c.fuel[src])
cfg, w, c2, L2, _ = fixture(); c2.ignite(0, [src], 'test', L2, hour=0.); c2.step_fire(0, L2)
check(late_fuel > float(c2.fuel[src]), 'a cell lit at hour 20 loses less fuel today than one lit at hour 0')
check(abs(float(c2.fuel[src]) - .9 * .3) < 1e-6 and abs(late_fuel - .9 * .3 ** (4 / 24)) < 1e-5, 'fuel loss is an explicit function of hours burned')
try: c2.ignite(0, [src], 'test', L2, hour=30.); bad = False
except ValueError: bad = True
check(bad, 'ignition hour outside the day is rejected')
c2.step_fire(1, L2)
check(float(c2.burn_hour[src]) == 0. and c2.burn_since_day[src] == 0, 'a continuing fire burns from hour 0 the next day and keeps its episode start')

# 2. slow crossing (44.4 h at fuel .9) completes across a day boundary (former probe: slow_edge_burned_cells=1)
cfg, w, c, L, src = fixture({'fire.base_hours': 40.})
cost = 40. / .9
c.ignite(0, [src], 'test', L); c.step_fire(0, L)
nbrs = land_nbrs(w, c, src)
check(int((c.burn_scar_day >= 0).sum()) == 1, 'day 0: nothing beyond the source burns on a 44 h edge')
check(np.allclose(c.edge_progress[src][[k for k in range(6) if w.nbr[src, k] in nbrs]], 24. / cost, atol=1e-5), 'unfinished crossings carry completed fraction 24/44.4 into the next day')
c.step_fire(1, L)
spread = [e for e in events(L, ('fire_spread',)) if e.day == 1]
check(len(spread) == len(nbrs) and all(abs(e.payload['hour'] - (cost - 24.)) < 1e-3 for e in spread), 'all land neighbours ignite on day 1 at hour 44.4-24 = 20.4')
check(int((c.burn_scar_day >= 0).sum()) == 1 + len(nbrs), 'a viable multi-day crossing reaches its neighbour')
for d in range(2, 6): c.step_fire(d, L)
per_cell = {}
for e in events(L): per_cell[e.cell] = per_cell.get(e.cell, 0) + 1
check(max(per_cell.values()) == 1, 'each cell gets exactly one ignition/spread event per burn episode')
check(np.all(c.edge_progress >= 0) and np.all(c.edge_progress <= 1), 'edge progress stays within [0,1]')
burned_total_slow = int((c.burn_scar_day >= 0).sum())

# 3. an extinguished source does not keep heating its neighbours
cfg, w, c, L, src = fixture({'fire.base_hours': 40., 'fire.min_effective_fuel': 0.}, fuel=.9)   # gate off: a .1-fuel source must still catch
c.fuel[src] = .1                               # .1 * .3 = .03 < burnout after one day
c.ignite(0, [src], 'test', L)
for d in range(10): c.step_fire(d, L)
check(int((c.burn_scar_day >= 0).sum()) == 1 and not c.burning.any(), 'source burns out after one day; no neighbour ignites later')
check(np.all(c.edge_progress[src] == 0.), 'extinguished source loses its crossing progress')

# 4. firebreak / water: unburnable neighbour never ignites and accumulates no progress
cfg, w, c, L, src = fixture({'fire.base_hours': 40.})
nbrs = land_nbrs(w, c, src); brk = nbrs[0]
c.fuel[brk] = 0.
c.ignite(0, [src], 'test', L)
for d in range(4): c.step_fire(d, L)
check(c.burn_scar_day[brk] < 0, 'a fuel-free neighbour (firebreak) never ignites')
kb = int(np.flatnonzero(w.nbr[src] == brk)[0])
check(c.edge_progress[src, kb] == 0., 'no progress is accumulated across an impassable edge')
check(all(c.burn_scar_day[j] < 0 for j in np.flatnonzero(~c.land)), 'water cells never burn')

# 5. re-pricing under changing weather preserves the fraction, not the hours
cfg, w, c, L, src = fixture({'fire.base_hours': 40.})
c.ignite(0, [src], 'test', L); c.step_fire(0, L)          # 24/44.4 = .54 complete at cost 44.4
c.moisture[c.land] = .5                                    # cost doubles to 88.9 h
c.step_fire(1, L)
check(not [e for e in events(L, ('fire_spread',)) if e.day == 1], 'after moisture rises the remaining .46 x 88.9 h does not finish on day 1')
c.step_fire(2, L)
spread = [e for e in events(L, ('fire_spread',)) if e.day == 2]
expect = (1 - 24 / (40 / .9) - 24 / (40 / .45)) * (40 / .45)
check(spread and all(abs(e.payload['hour'] - expect) < 1e-2 for e in spread), f'crossing completes on day 2 at hour {expect:.2f} under the re-priced edge')

# 6. overlapping fronts: a target reached by any route clears all unfinished crossings into it
cfg, w, c, L, src = fixture({'fire.base_hours': 40.})
nbrs = land_nbrs(w, c, src); other = nbrs[0]
c.ignite(0, [src], 'test', L); c.step_fire(0, L)
c.ignite(1, [other], 'test', L)
check(c.edge_progress[src, int(np.flatnonzero(w.nbr[src] == other)[0])] == 0., 'ignition by another source clears progress toward that cell')
c.step_fire(1, L)
per_cell = {}
for e in events(L): per_cell[e.cell] = per_cell.get(e.cell, 0) + 1
check(max(per_cell.values()) == 1, 'overlapping fronts never double-ignite a cell')

# 7. engine coupling: ground stock burns exactly once, conservation stays exact, seed/cadence invariance
def eng(cadence):
    e = Engine(load(overrides={**base, 'run.days_per_tick': cadence, 'fire.base_hours': 40.}))
    land = np.flatnonzero(e.world.mainland() & (e.world.terrain == T['plains'])); cell = int(land[0])
    farm = e.seed_place('farm', cell, 'h:0'); e.add_person(-30 * 365, cell, farm, household_id=0, name='W')
    e.cells.fields[cell] = 1; e.cells.plant(0, np.array([cell])); e.cells.crop_progress[cell] = 1.
    e.inventory.receive_harvest(0, e.cells, [cell], owner='h:0', place=farm)
    e.cells.moisture[:] = 0; e.cells.fuel[:] = np.where(e.cells.land, .9, 0)
    e.cells.ignite(0, [cell], 'test', e.ledger, hour=18.)
    for _ in range(6): e.step()
    return e
a, b = eng(7), eng(30)
check(a.ledger.bucket_totals['sink:burn']['grain'] == 320000 and not a.ledger.residuals and a.ledger.checks == 6, 'engine: exposed harvest burns once, six exact daily closes')
check(a.digest() == b.digest(), 'fire state is deterministic and reporting-cadence invariant')
check(len([e for e in a.ledger.events if e.type == 'fire_spread']) > 0, 'engine-driven multi-day front spreads to neighbours')

report = {'suite': 'fire', 'checks': len(checks), 'failures': [],
          'slow_edge_burned_cells': burned_total_slow,
          'external_ignition_hour_preserved': True,
          'semantics': {'edge_progress': 'completed fraction of crossing source->neighbour, re-priced at today cost',
                        'extinguished_source': 'progress cleared', 'target_ignited': 'progress toward it cleared',
                        'continuing_fire': 'burns from hour 0; ignition hour only for cells lit today',
                        'losses': 'fuel/vegetation/wildlife scaled by hours burned today / day_hours'},
          'scope': 'Front persistence and timing semantics only. Building partial damage, evacuation and spotting remain unimplemented; no empirical fire calibration.'}
(ROOT / 'test-results/fire.json').write_text(json.dumps(report, indent=2)); print(json.dumps(report, indent=2))
