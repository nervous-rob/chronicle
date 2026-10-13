"""Synthetic mechanism tests. No ecological defaults are calibrated here.

Run directly with Python + NumPy. Budgets measure realized float32 abundance
changes (float64 reductions); they are diagnostic, NOT the owned-stock ledger.
Paired totals include interactions and must not be added as independent causes.
"""
import sys, json, copy, hashlib, itertools, math, platform
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chronicle.config import load, ConfigError
from chronicle.cells import Cells
from chronicle.rng import Streams, LAYOUT_VERSION
from chronicle.world import World, T
from chronicle.engine import Engine

checks = []
def check(ok, label):
    assert bool(ok), label
    checks.append(label)
    print('ok', label, flush=True)

BASE = {'world.width_km': 12, 'world.height_km': 8, 'world.region_count': 2,
        'weather.lightning_p': {s: 0. for s in ('spring', 'summer', 'autumn', 'winter')},
        'weather.moisture_capacity_mm': 100.}   # the 56.7034 game fixture below was calibrated at the pre-worldgen default of 100 mm
def fixture(extra=None, uniform=False):
    cfg = load(overrides={**BASE, **(extra or {})})
    w = World(cfg) if uniform else World.generate(cfg)
    return Cells(cfg, w, Streams(cfg.seed))

def no_growth():
    return {f'catalog.wildlife.{w}.r': 0. for w in load().catalog['wildlife']}

def check_budget(c, label):
    report = c.wildlife_budget()
    check(max(abs(v['residual']) for v in report.values()) < 1e-8, label + ': component budget closes')
    return report

# Controls must fail closed; NaNs bypass ordinary comparisons unless checked.
for key, value in [
    ('wildlife.predation_rate', -0.1), ('wildlife.predation_rate', 1.1),
    ('wildlife.predation_rate', float('nan')), ('wildlife.predation_rate', True),
    ('wildlife.dispersal', float('inf')), ('wildlife.dispersal', float('nan')),
    ('wildlife.dispersal', -0.01), ('wildlife.dispersal', 1.01),
    ('wildlife.raid_rate', float('nan')), ('wildlife.habitat_coupling', 1),
    ('catalog.wildlife.game.r', -1.), ('catalog.wildlife.game.r', float('nan')),
    ('catalog.wildlife.game.k_per_km2', -1.), ('catalog.wildlife.game.k_per_km2', float('inf'))]:
    try:
        load(overrides={key: value})
    except ConfigError:
        rejected = True
    else:
        rejected = False
    check(rejected, f'reject invalid {key}={value!r}')

# Matched 2^4 design, including density/growth and dispersal on/off.
# Habitat-off freezes K at terrain target, not vegetation: weather, fuel and
# vegetation still evolve identically. No infection, hunting or ignitions.
runs = {}
def run_case(predation=True, habitat=True, dispersal=True, growth=True, days=180):
    extra = {'wildlife.predation_rate': .02 if predation else 0.,
             'wildlife.habitat_coupling': habitat,
             'wildlife.dispersal': .1 if dispersal else 0.}
    if not growth: extra.update(no_growth())
    c = fixture(extra); weather = hashlib.sha256(); initial = c.wildlife.copy()
    g = c.wl_ids.index('game'); trajectory = []
    valid = True; peak_residual = 0.; max_pred_fraction = 0.
    for day in range(days):
        c.step(day)
        for a in (c.moisture, c.vegetation, c.rain_mm, c.wet, c.wind_dir, c.wind_kph): weather.update(a.tobytes())
        valid &= bool(np.isfinite(c.wildlife).all() and (c.wildlife >= 0).all())
        for a in c.seir.values(): valid &= bool(np.isfinite(a).all() and (a >= 0).all() and np.allclose(a.sum(1), 1., atol=1e-5))
        budget = c.wildlife_budget()
        peak_residual = max(peak_residual, max(abs(v['residual']) for v in budget.values()))
        pi = c.wl_ids.index('predator')
        max_pred_fraction = max(max_pred_fraction, float((c.wildlife[:, pi] / np.maximum(c.carrying()[:, pi], 1e-6)).max()))
        if (day + 1) % 15 == 0:
            trajectory.append({'completed_days': day + 1, 'game': budget['game']['final'],
                               'components': budget['game']['components']})
    return c, {'run_id': c.cfg.run_id, 'initial_sha256': hashlib.sha256(initial.tobytes()).hexdigest(),
        'forcing_sha256': weather.hexdigest(), 'valid_all_days': valid, 'max_budget_residual': peak_residual,
        'max_predator_fraction_of_K': max_pred_fraction, 'budget': c.wildlife_budget(),
        'trajectory': trajectory, 'settings': {'predation': predation, 'habitat': habitat, 'dispersal': dispersal, 'growth': growth}}

for p, h, d, g in itertools.product((False, True), repeat=4):
    key = f'P{int(p)}H{int(h)}D{int(d)}G{int(g)}'
    c, result = run_case(p, h, d, g)
    runs[key] = result
    check(result['valid_all_days'] and result['max_budget_residual'] < 1e-8,
          key + ': finite/nonnegative abundance, normalized SEIR, all daily budgets close')
    check(all(abs(v['components'][x]) < 1e-10 for v in result['budget'].values() for x in ('disease', 'fire', 'hunt')),
          key + ': disabled hazards contribute no losses')
check(len({r['initial_sha256'] for r in runs.values()}) == 1, 'factorial starts with identical abundance arrays')
check(len({r['forcing_sha256'] for r in runs.values()}) == 1, 'all controls receive identical weather and vegetation forcing')
base = runs['P1H1D1G1']; off = runs['P0H1D1G1']
check(abs(base['budget']['game']['final'] - 56.703392028808594) < .0001,
      'uninfected decline reproduces prior ecology-control fixture')
check(off['budget']['game']['final'] > off['budget']['game']['initial'],
      'game increases without predation under the same evolving habitat')
check(base['budget']['game']['components']['predation'] < 0 and base['budget']['game']['components']['growth'] > 0,
      'baseline decline includes measured predation losses offset by positive growth')
check(all(abs(v['components']['growth']) + abs(v['components']['density_loss']) == 0
          for k, r in runs.items() if k.endswith('G0') for v in r['budget'].values()),
      'growth-off disables both positive growth and density loss')
contrasts = {}
for p, h, d, g in itertools.product((False, True), repeat=4):
    for axis, idx in [('predation', 0), ('habitat', 1), ('dispersal', 2), ('growth', 3)]:
        vals = [p,h,d,g]
        if vals[idx]: continue
        k0 = f'P{int(p)}H{int(h)}D{int(d)}G{int(g)}'
        vals[idx] = True
        k1 = f'P{int(vals[0])}H{int(vals[1])}D{int(vals[2])}G{int(vals[3])}'
        contrasts[f'{axis}:{k0}->{k1}'] = runs[k1]['budget']['game']['final'] - runs[k0]['budget']['game']['final']
repeat, repeated = run_case()
check(repeated == base, 'same seed reproduces all factorial baseline outputs and budgets')

# Exact local equation control: no growth or dispersal, pressure fixed at half K.
c = fixture({**no_growth(), 'wildlife.dispersal':0.}, uniform=True)
g = c.wl_ids.index('game'); pi = c.wl_ids.index('predator')
old = c.wildlife[:,g].copy(); pressure = .02*c.wildlife[:,pi]/c.carrying()[:,pi]
c.step_wildlife(0)
check(np.allclose(c.wildlife[:,g], old*(1-pressure), rtol=1e-6), 'isolated predation matches local equation')
check_budget(c, 'isolated predation')

# Previously possible: two over-capacity predators multiply two negative
# survival factors and resurrect prey. Saturate each predator's loss separately.
extra = {**no_growth(), 'wildlife.dispersal':0.,
         'catalog.wildlife.raider.diet':['game']}
c = fixture(extra, uniform=True); g = c.wl_ids.index('game')
for name in ('raider','predator'):
    j = c.wl_ids.index(name); c.wildlife[:,j] = 100*c.carrying()[:,j]
c.reset_wildlife_budget(); before = c.wildlife[:,g].copy(); c.step_wildlife(0)
check(np.all(c.wildlife[:,g] == 0) and np.all(c.wildlife[:,g] <= before),
      'overlapping excessive predator pressures cannot create prey')
check_budget(c, 'saturated predator losses')

# Habitat is a modifier of K: measure its local growth effect independently.
c = fixture({'wildlife.predation_rate':0.,'wildlife.dispersal':0.}, uniform=True)
fixed = fixture({'wildlife.predation_rate':0.,'wildlife.dispersal':0.,'wildlife.habitat_coupling':False}, uniform=True)
for x in (c, fixed): x.vegetation[:]=.1
initial = c.wildlife.copy(); c.step_wildlife(0); fixed.step_wildlife(0)
check(c.wildlife[:,0].sum() < initial[:,0].sum() < fixed.wildlife[:,0].sum(),
      'isolated habitat contraction causes density loss; frozen K control grows')
check(c.budget['density_loss'][0] < 0 and fixed.budget['density_loss'][0] == 0,
      'negative density change is separated from positive growth')
check_budget(c, 'habitat contraction'); check_budget(fixed, 'frozen habitat')

# Dispersal conservation: abundance AND infected counts, all species, each day.
c = fixture({**no_growth(),'wildlife.predation_rate':0.})
c.wildlife[:] = 0
source = int(np.flatnonzero(c.world.mainland())[0])
c.wildlife[source] = [100*(i+1) for i in range(len(c.wl_ids))]
c.infect('murrain','game',[source],.4); c.reset_wildlife_budget()
initial = c.wildlife.sum(0,dtype=np.float64); infected0=40.
max_mass_error=0.; max_infected_error=0.
for day in range(100):
    c.step_wildlife(day)
    max_mass_error=max(max_mass_error,float(np.max(np.abs(c.wildlife.sum(0,dtype=np.float64)-initial))))
    a=c.seir['murrain','game']; g=c.wl_ids.index('game')
    max_infected_error=max(max_infected_error,abs(float((a[:,2].astype(np.float64)*c.wildlife[:,g]).sum())-infected0))
check(max_mass_error < .001 and max_infected_error < .001, '100-day isolated dispersal conserves hosts and infected counts')
check((c.wildlife[~c.land]==0).all(), 'dispersal never deposits wildlife on water')
check_budget(c, 'isolated dispersal')

# Isolated land cell cannot leak via its artificially protected degree=1.
cfg = load(overrides={**BASE,**no_growth(),'wildlife.predation_rate':0.})
w = World(cfg); w.terrain[:]=T['water']; w.terrain[w.n//2]=T['plains']
c = Cells(cfg,w,Streams(cfg.seed)); initial=c.wildlife.copy()
for day in range(10): c.step_wildlife(day)
check(np.array_equal(c.wildlife,initial), 'isolated cell retains all hosts with no reachable neighbors')

# Disease/fire/hunting diagnostic budgets also measure actual, not requested, losses.
c = fixture({**no_growth(),'wildlife.predation_rate':0.,'wildlife.dispersal':0.})
c.infect('murrain','game',np.flatnonzero(c.land),.2)
for day in range(60):c.step_disease(day)
check(c.budget['disease'][c.wl_ids.index('game')]<0, 'disease has a measured negative budget contribution')
check_budget(c,'disease-only')
c = fixture(); source=int(np.flatnonzero(c.land)[0]); c.fuel[:]=np.where(c.land,.9,0);c.moisture[:]=0;c.wind_kph[:]=0
c.ignite(0,[source],'test',hour=20.); c.step_fire(0)
check((c.budget['fire']<0).all(),'duration-scaled fire losses enter the wildlife budget')
check_budget(c,'fire-only')
c = fixture(); source=int(np.flatnonzero(c.land)[0]);
for day in range(40):c.sample_hunt(day,1,'game',source)
check(c.budget['hunt'][c.wl_ids.index('game')]<0,'successful hunt reduces statistical abundance')
check_budget(c,'hunt-only')
c.wildlife[source,0]+=1
check(abs(c.wildlife_budget()['game']['residual'])>.5,'untracked abundance mutation remains visible; no automatic balancing')

# Scale control: identical uniform terrain and physical density, same weather
# region. No dispersal means this tests the local equations rather than a
# different map produced by each grid. Densities use actual represented area.
scales={}
for cell_km in (1.,.5):
    c=fixture({'units.cell_km':cell_km,'world.width_km':16,'world.height_km':16,
               'world.region_count':1,'wildlife.dispersal':0.},uniform=True)
    area=c.world.n*c.cfg.derived['cell_area_km2']; initial=c.wildlife.sum(0,dtype=np.float64)/area
    for day in range(180):c.step(day)
    scales[str(cell_km)]={'cell_km':cell_km,'cells':c.world.n,'represented_area_km2':area,
                         'initial_density_per_km2':initial.tolist(),
                         'final_density_per_km2':(c.wildlife.sum(0,dtype=np.float64)/area).tolist()}
check(np.allclose(scales['1.0']['final_density_per_km2'],scales['0.5']['final_density_per_km2'],rtol=1e-5,atol=1e-7),
      'uniform local ecology agrees across 1 km and 0.5 km at equal physical density')

# One-step spatial pulse demonstrates the current dispersal grid dependence
# without terrain/weather/boundary confounds: E[r^2] = fraction * cell_km^2.
pulses={}
for size in (1.,.5):
    c=fixture({**no_growth(),'units.cell_km':size,'world.width_km':16,'world.height_km':16,
               'world.region_count':1,'wildlife.predation_rate':0.},uniform=True)
    c.wildlife[:]=0;source=(c.world.h//2)*c.world.w+c.world.w//2;g=c.wl_ids.index('game')
    c.wildlife[source,g]=100.;c.reset_wildlife_budget();c.step_wildlife(0)
    dist2=(c.world.x_km-c.world.x_km[source])**2+(c.world.y_km-c.world.y_km[source])**2
    msd=float((c.wildlife[:,g]*dist2).sum()/c.wildlife[:,g].sum())
    pulses[str(size)]={'msd_km2_after_one_day':msd,'expected_km2':.1*size**2}
    check(abs(msd-.1*size**2)<1e-7, f'{size} km pulse confirms dispersal spatial second moment')
check(abs(pulses['0.5']['msd_km2_after_one_day']/pulses['1.0']['msd_km2_after_one_day']-.25)<1e-6,
      'known grid dependence: halving cell size quarters physical mean-square dispersal')

# Integrated reporting cadence does not change daily physics or diagnostics.
e7=Engine(load(overrides={**BASE,'run.days_per_tick':7}))
e30=Engine(load(overrides={**BASE,'run.days_per_tick':30}))
for day in range(60):e7.step();e30.step()
check(e7.digest()==e30.digest(),'7/30-day cadence leaves ecology and component budgets identical')
check(e7.ledger.checks==60 and e30.ledger.checks==60 and not e7.ledger.residuals and not e30.ledger.residuals,
      'integrated empty-population ecology retains exact daily inventory closes')

report={'suite':'wildlife-controls','verified_at_utc':datetime.now(timezone.utc).isoformat(),
    'checks':len(checks),'failures':[],'check_names':checks,
    'runtime':{'python':platform.python_version(),'numpy':np.__version__,'rng_layout':LAYOUT_VERSION},
    'days_per_case':180,'factorial':runs,'paired_final_game_deltas_on_minus_off':contrasts,
    'dispersal_conservation':{'days':100,'max_host_error':max_mass_error,'max_infected_error':max_infected_error},
    'uniform_cell_size_controls':scales,'dispersal_grid_probe':pulses,
    'scope':'Synthetic equation/mechanism evidence, not empirical calibration. Habitat changes K, not an independent additive sink. Paired differences interact. Current dispersal is per-cell per-day and not physical-scale invariant. Predator growth is not coupled to prey intake. Full serializer and society remain absent.'}
out=ROOT/'test-results/wildlife-controls.json';out.write_text(json.dumps(report,indent=2,allow_nan=False))
print(json.dumps({'checks':len(checks),'baseline':base['budget']['game'],
                  'without_predation':off['budget']['game'],
                  'fixed_habitat':runs['P1H0D1G1']['budget']['game'],
                  'grid_probe':pulses},indent=2),flush=True)
