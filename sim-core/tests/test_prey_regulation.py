"""wildlife.prey_refuge / wildlife.prey_response: off = bit-identical legacy physics; on = predators can't
hunt prey to nothing and track their food. Mechanism + sanity test; the rates are NOT calibrated."""
import sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chronicle.config import load, ConfigError
from probe_prey_coupling import BASE
from chronicle.cells import Cells
from chronicle.rng import Streams
from chronicle.world import World
n = 0
def check(ok, label):
    global n; assert bool(ok), label; n += 1; print('ok', label, flush=True)

for key, v in [('wildlife.prey_refuge', 1), ('wildlife.prey_response', 'yes'), ('wildlife.refuge_fill', 0.),
               ('wildlife.refuge_fill', float('nan')), ('wildlife.response_fill', 0.), ('wildlife.response_fill', 1.5),
               ('wildlife.response_floor', -.1), ('wildlife.response_floor', float('inf'))]:
    try: load(overrides={key: v}); ok = False
    except ConfigError: ok = True
    check(ok, f'reject invalid {key}={v!r}')
w = load()['wildlife']
check(w['prey_refuge'] is False and w['prey_response'] is False, 'both switches default OFF')

def sim(days=730, extra=None, seed=7):
    cfg = load(overrides={**BASE, 'run.seed': seed, **(extra or {})})
    c = Cells(cfg, World.generate(cfg), Streams(cfg.seed))
    g, p = c.wl_ids.index('game'), c.wl_ids.index('predator'); fills = []
    for d in range(days):
        c.step(d); K = c.carrying().sum(0); N = c.wildlife.sum(0); fills.append((N[g] / K[g], N[p] / K[p]))
    return c, np.array(fills)
base, fb = sim()
off, _ = sim(extra={'wildlife.refuge_fill': 0.5, 'wildlife.response_fill': 0.9, 'wildlife.response_floor': 0.9})
check(np.array_equal(base.wildlife, off.wildlife), 'OFF ignores refuge_fill/response_fill/response_floor (legacy physics unchanged)')

R = {'wildlife.prey_refuge': True}
RS = {'wildlife.prey_response': True, 'wildlife.response_fill': 0.3, 'wildlife.response_floor': 0.3}
cr, fr = sim(extra=R); cs, fs = sim(extra=RS); cb, fc = sim(extra={**R, **RS})
check(fr[-1, 0] > fb[-1, 0], f'refuge leaves more game than legacy ({fr[-1,0]:.4f} > {fb[-1,0]:.4f})')
check(np.array_equal(cr.wildlife[:, cr.wl_ids.index('predator')], base.wildlife[:, base.wl_ids.index('predator')]), 'refuge alone does not change predator numbers when predators are unaffected by prey')
check(fs[-1, 1] < fb[-1, 1], f'response lowers predators toward their food ({fs[-1,1]:.3f} < {fb[-1,1]:.3f})')
check(fc[365:, 1].min() >= 0.3 * 0.5 * fb[365:, 1].min() and fc[365:, 1].min() > 0.05, f'response floor keeps predators from collapsing (min {fc[365:,1].min():.3f})')
rr = cb.wl_ids.index('raider'); check(np.array_equal(cb.wildlife[:, rr], base.wildlife[:, rr]), 'raiders (no wildlife prey) unaffected by either switch')
for c, lab in ((cr, 'refuge'), (cs, 'response'), (cb, 'both')):
    check(max(abs(v['residual']) for v in c.wildlife_budget().values()) < 1e-8, f'{lab}: budget closes')
    check(np.isfinite(c.wildlife).all() and (c.wildlife >= 0).all(), f'{lab}: finite, non-negative')

# healthy-coexistence criterion, fixed BEFORE tuning: 3 seeds x 3 years, game >5% and predators >20% of capacity
for seed in (1, 2, 3):
    _, f = sim(1095, {**R, **RS, 'wildlife.predation_rate': 0.005}, seed)
    check(f[180:, 0].min() > 0.05 and f[180:, 1].min() > 0.20, f'seed {seed}: both species healthy for 3 yr (game min {f[180:,0].min():.3f}, pred min {f[180:,1].min():.3f})')
print(f'{n} checks passed')
