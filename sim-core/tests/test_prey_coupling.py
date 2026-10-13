"""wildlife.prey_coupling: off = bit-identical legacy physics; on = predators starve when prey is scarce.
Mechanism test only; rates are NOT calibrated."""
import sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chronicle.config import load, ConfigError
from probe_prey_coupling import run, BASE
from chronicle.cells import Cells
from chronicle.rng import Streams
from chronicle.world import World
n = 0
def check(ok, label):
    global n; assert bool(ok), label; n += 1; print('ok', label, flush=True)

for key, v in [('wildlife.prey_coupling', 1), ('wildlife.prey_satiation', 0.), ('wildlife.prey_satiation', float('nan')),
               ('wildlife.starvation_rate', -.1), ('wildlife.starvation_rate', 1.5), ('wildlife.starvation_rate', float('inf'))]:
    try: load(overrides={key: v}); ok = False
    except ConfigError: ok = True
    check(ok, f'reject invalid {key}={v!r}')
check(load()['wildlife']['prey_coupling'] is False, 'default is OFF')

def final(on, **extra):
    cfg = load(overrides={**BASE, 'wildlife.prey_coupling': on, **extra})
    c = Cells(cfg, World.generate(cfg), Streams(cfg.seed)); pi = c.wl_ids.index('predator')
    for d in range(120): c.step(d)
    return c, c.wildlife.copy(), float(c.wildlife[:, pi].sum())
c0, w0, p0 = final(False)
c1, w1, p1 = final(True)
c2, w2, _ = final(False, **{'wildlife.starvation_rate': 0.5})
check(np.array_equal(w0, w2), 'OFF ignores starvation_rate (legacy physics unchanged)')
check(p1 < p0, f'ON lowers predators ({p1:.0f} < {p0:.0f})')
g = c0.wl_ids.index('game'); check(w1[:, g].sum() > w0[:, g].sum(), 'ON leaves more game than OFF')
r = c0.wl_ids.index('raider'); check(np.array_equal(w0[:, r], w1[:, r]), 'raiders (no wildlife prey) unaffected')
for c, lab in ((c0, 'OFF'), (c1, 'ON')):
    check(max(abs(v['residual']) for v in c.wildlife_budget().values()) < 1e-8, f'{lab}: budget closes')
    check(np.isfinite(c.wildlife).all() and (c.wildlife >= 0).all(), f'{lab}: finite, non-negative')
# no prey available at all -> starvation applies at max rate, still non-negative
cz = load(overrides={**BASE, 'wildlife.prey_coupling': True, 'wildlife.starvation_rate': 1.0})
cc = Cells(cz, World.generate(cz), Streams(cz.seed)); cc.wildlife[:, cc.wl_ids.index('game')] = 0
pi = cc.wl_ids.index('predator'); before = float(cc.wildlife[:, pi].sum())
cc.reset_wildlife_budget(); cc.step(0)
# growth is applied before the starvation loss, so a sliver (today's logistic gain) remains
check((cc.wildlife >= 0).all() and cc.wildlife[:, pi].sum() < 0.01 * before, 'no prey + rate 1 removes >99% of predators, no negatives')
print(f'{n} checks passed')
