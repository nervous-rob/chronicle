"""Diagnostic: prey_refuge / prey_coupling variants over seeds. Usage: probe_refuge.py DAYS SEEDS"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from probe_prey_coupling import BASE
from chronicle.config import load
from chronicle.cells import Cells
from chronicle.rng import Streams
from chronicle.world import World
R={'wildlife.prey_refuge':True}
def V(fill,floor,refuge=True,**kw): return {**(R if refuge else {}),'wildlife.prey_response':True,'wildlife.response_fill':fill,'wildlife.response_floor':floor,**kw}
VARIANTS = {
 'legacy': {},
 'refuge': R,
 'resp only .3/.1': V(.3,.1,False),
 'ref+resp .3/.1': V(.3,.1),
 'ref+resp .1/.1': V(.1,.1),
 'ref+resp .1/.3': V(.1,.3),
 'ref+resp .3/.3': V(.3,.3),
}
def run(seed, extra, days):
    cfg = load(overrides={**BASE, 'run.seed': seed, **extra})
    c = Cells(cfg, World.generate(cfg), Streams(cfg.seed))
    g, p = c.wl_ids.index('game'), c.wl_ids.index('predator')
    lo = [9., 9.]; tail = []
    for d in range(days):
        c.step(d)
        K = c.carrying().sum(0); N = c.wildlife.sum(0)
        gf, pf = float(N[g]/K[g]), float(N[p]/K[p])
        if d > 180: lo[0] = min(lo[0], gf); lo[1] = min(lo[1], pf)
        if d >= days - 365: tail.append((gf, pf))
    res = max(abs(v['residual']) for v in c.wildlife_budget().values())
    return lo, tail, res
if __name__ == '__main__':
    days = int(sys.argv[1]); seeds = [int(x) for x in sys.argv[2].split(',')]
    for name, extra in VARIANTS.items():
        for s in seeds:
            t = time.time(); lo, tail, res = run(s, extra, days)
            mg = sum(a for a, _ in tail)/len(tail); mp = sum(b for _, b in tail)/len(tail)
            print(f'{name:16s} seed {s} last-yr mean game {mg:.3f} pred {mp:.3f} | min after d180 game {lo[0]:.4f} pred {lo[1]:.4f} | resid {res:.1e} | {time.time()-t:.0f}s', flush=True)
