"""wildlife.prey_intake (capped intake): off = bit-identical legacy physics; on = each predator eats at most
intake_cap wildlife prey per day and grows only as far as it is fed. Mechanism + robustness test; the cap is
NOT calibrated against any real animal."""
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

ON = {'wildlife.prey_intake': True}
for ov in [{'wildlife.prey_intake': 1}, {'wildlife.intake_cap': 0.}, {'wildlife.intake_cap': float('nan')},
           {'wildlife.intake_cap': 1.5}, {'wildlife.intake_starve': -.1}, {'wildlife.intake_starve': float('inf')},
           {**ON, 'wildlife.prey_coupling': True}, {**ON, 'wildlife.prey_response': True}]:
    try: load(overrides=ov); ok = False
    except ConfigError: ok = True
    check(ok, f'reject invalid {ov}')
check(load()['wildlife']['prey_intake'] is False, 'default is OFF')
load(overrides={**ON, 'wildlife.prey_refuge': True}); check(True, 'prey_intake combines with prey_refuge')

def make(extra=None, seed=7):
    cfg = load(overrides={**BASE, 'run.seed': seed, **(extra or {})})
    return Cells(cfg, World.generate(cfg), Streams(cfg.seed))
def sim(days, extra=None, seed=7):
    c = make(extra, seed); g, p = c.wl_ids.index('game'), c.wl_ids.index('predator'); fills = []
    for d in range(days):
        c.step(d); K = c.carrying().sum(0); N = c.wildlife.sum(0); fills.append((N[g] / K[g], N[p] / K[p]))
    return c, np.array(fills)

# OFF: the new keys change nothing
a, _ = sim(365); b, _ = sim(365, {'wildlife.intake_cap': 0.9, 'wildlife.intake_starve': 0.9})
check(np.array_equal(a.wildlife, b.wildlife), 'OFF ignores intake_cap/intake_starve (legacy physics unchanged)')

# _intake: min(linear, cap) per predator; fed = capped intake / cap
c = make(ON); g, p = c.wl_ids.index('game'), c.wl_ids.index('predator'); K = c.carrying(); N = c.wildlife.copy()
scale, fed = c._intake(N, K, p, [g])
raw = c.cfg['wildlife']['predation_rate'] * N[:, g] / np.maximum(K[:, p], 1e-9); cap = c.cfg['wildlife']['intake_cap']
check(np.allclose(raw * scale, np.minimum(raw, cap), rtol=1e-6, atol=1e-12), 'intake per predator = min(linear response, intake_cap)')
check(((fed >= 0) & (fed <= 1)).all() and np.allclose(fed, np.clip(raw / cap, 0, 1)), 'fed = capped intake / intake_cap, in [0,1]')

# ON: daily game kills never exceed intake_cap x predators
c = make(ON); g, p = c.wl_ids.index('game'), c.wl_ids.index('predator'); worst = 0.
for d in range(60):
    before = c.budget['predation'][g]; c.step(d)
    killed = before - c.budget['predation'][g]; preds = float(c.wildlife[:, p].sum())
    worst = max(worst, killed / max(cap * preds, 1e-9))
check(worst <= 1.001, f'daily game kills <= intake_cap x predators over 60 days (worst ratio {worst:.3f})')

# ON with no prey at all: predators lose intake_starve per day (numerical response, unfed end)
c = make(ON); g, p = c.wl_ids.index('game'), c.wl_ids.index('predator')
c.wildlife[:, g] = 0; c.reset_wildlife_budget(); before = float(c.wildlife[:, p].sum()); c.step(0)
ratio = float(c.wildlife[:, p].sum()) / before; want = 1 - c.cfg['wildlife']['intake_starve']
check(abs(ratio - want) < 2e-4, f'unfed predators lose intake_starve per day ({ratio:.5f} vs {want:.5f})')

cc, fc = sim(730, ON); base730, fl = sim(730); r = cc.wl_ids.index('raider')
check(np.array_equal(cc.wildlife[:, r], base730.wildlife[:, r]), 'raiders (no wildlife prey) unaffected')
check(fc[-1, 0] > 10 * fl[-1, 0], f'ON leaves far more game than legacy at day 730 ({fc[-1,0]:.3f} vs {fl[-1,0]:.4f})')
check(max(abs(v['residual']) for v in cc.wildlife_budget().values()) < 1e-8, 'ON: budget closes')
check(np.isfinite(cc.wildlife).all() and (cc.wildlife >= 0).all(), 'ON: finite, non-negative')

# Robustness criterion, fixed BEFORE tuning: 10 years, no fire, game > 5% and predators > 20% of capacity
# after day 180, at the legacy predation_rate (3 seeds) and at 4x lower (1 seed), with default intake_cap.
for rate, seeds in ((0.02, (1, 2, 3)), (0.005, (1,))):
    for seed in seeds:
        _, f = sim(3650, {**ON, 'wildlife.predation_rate': rate}, seed)
        check(f[180:, 0].min() > 0.05 and f[180:, 1].min() > 0.20,
              f'rate {rate} seed {seed}: both healthy for 10 yr (game min {f[180:,0].min():.3f}, pred min {f[180:,1].min():.3f})')
print(f'{n} checks passed')
