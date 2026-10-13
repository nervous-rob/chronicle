"""Diagnostic (not a test): yearly game/predator fill under prey_intake. Usage: probe_intake_traj.py DAYS CAPS SEEDS RATE [fire]"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_prey_coupling import BASE
from chronicle.config import load
from chronicle.cells import Cells
from chronicle.rng import Streams
from chronicle.world import World
days = int(sys.argv[1]); rate = float(sys.argv[4]); fire = len(sys.argv) > 5 and sys.argv[5] == 'fire'
for cap in sys.argv[2].split(','):
    for seed in [int(x) for x in sys.argv[3].split(',')]:
        b = dict(BASE)
        if fire: b.pop('weather.lightning_p')
        ex = {'wildlife.predation_rate': rate}
        if cap != 'legacy': ex.update({'wildlife.prey_intake': True, 'wildlife.intake_cap': float(cap)})
        cfg = load(overrides={**b, 'run.seed': seed, **ex}); c = Cells(cfg, World.generate(cfg), Streams(cfg.seed))
        g, p = c.wl_ids.index('game'), c.wl_ids.index('predator'); pts = []; lo = [9., 9.]
        for d in range(days):
            c.step(d)
            if d >= 180 and d % 5 == 0:
                K = c.carrying().sum(0); N = c.wildlife.sum(0); lo = [min(lo[0], N[g]/K[g]), min(lo[1], N[p]/K[p])]
            if (d + 1) % 365 == 0:
                K = c.carrying().sum(0); N = c.wildlife.sum(0); pts.append(f'{N[g]/K[g]:.2f}/{N[p]/K[p]:.2f}')
        print(f'{"fire" if fire else "nofire"} rate {rate} cap {cap} seed {seed} | min game {lo[0]:.3f} pred {lo[1]:.3f} | yearly game/pred:', ' '.join(pts), flush=True)
