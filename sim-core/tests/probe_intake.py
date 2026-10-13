"""Diagnostic (not a test): prey_intake over predation_rate x intake_cap x seeds.
Usage: probe_intake.py DAYS SEEDS RATES CAPS [lightning]"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_prey_coupling import BASE
from chronicle.config import load
from chronicle.cells import Cells
from chronicle.rng import Streams
from chronicle.world import World
def run(seed, extra, days, lightning=False):
    base = dict(BASE)
    if lightning: base.pop('weather.lightning_p')
    cfg = load(overrides={**base, 'run.seed': seed, **extra})
    c = Cells(cfg, World.generate(cfg), Streams(cfg.seed))
    g, p, r = (c.wl_ids.index(x) for x in ('game', 'predator', 'raider'))
    lo = [9., 9.]; tail = []
    for d in range(days):
        c.step(d)
        K = c.carrying().sum(0); N = c.wildlife.sum(0)
        gf, pf = float(N[g]/K[g]), float(N[p]/K[p])
        if d >= 180: lo[0] = min(lo[0], gf); lo[1] = min(lo[1], pf)
        if d >= days - 365: tail.append((gf, pf))
    res = max(abs(v['residual']) for v in c.wildlife_budget().values())
    return lo, tail, res, (gf, pf)
if __name__ == '__main__':
    days = int(sys.argv[1]); seeds = [int(x) for x in sys.argv[2].split(',')]
    rates = [float(x) for x in sys.argv[3].split(',')]; caps = [x for x in sys.argv[4].split(',')]
    light = len(sys.argv) > 5 and sys.argv[5] == 'lightning'
    for rate in rates:
        for cap in caps:
            extra = {'wildlife.predation_rate': rate}
            if cap != 'legacy': extra.update({'wildlife.prey_intake': True, 'wildlife.intake_cap': float(cap)})
            for s in seeds:
                t = time.time(); lo, tail, res, end = run(s, extra, days, light)
                mg = sum(a for a, _ in tail)/len(tail); mp = sum(b for _, b in tail)/len(tail)
                ok = lo[0] > 0.05 and lo[1] > 0.20
                print(f'rate {rate:<6} cap {cap:<7} seed {s} | last-yr mean game {mg:.3f} pred {mp:.3f} | min d180+ game {lo[0]:.4f} pred {lo[1]:.4f} | end {end[0]:.3f}/{end[1]:.3f} | {"HEALTHY" if ok else "fail"} | resid {res:.0e} | {time.time()-t:.1f}s', flush=True)
