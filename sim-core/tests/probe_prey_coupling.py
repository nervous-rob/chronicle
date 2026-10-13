"""Paired prey_coupling off/on probe (diagnostic, not a test). Usage: probe_prey_coupling.py DAYS SEEDS"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chronicle.config import load
from chronicle.cells import Cells
from chronicle.rng import Streams
from chronicle.world import World
BASE={'world.width_km':12,'world.height_km':8,'world.region_count':2,
 'weather.lightning_p':{s:0. for s in ('spring','summer','autumn','winter')}}
def run(seed, on, days, extra=None):
    cfg=load(overrides={**BASE,'run.seed':seed,'wildlife.prey_coupling':on,**(extra or {})})
    c=Cells(cfg,World.generate(cfg),Streams(cfg.seed))
    g,r,p=[c.wl_ids.index(x) for x in ('game','raider','predator')]
    out={}
    for d in range(days):
        c.step(d)
        if (d+1) in (180,365,730,1095):
            K=c.carrying().sum(0); N=c.wildlife.sum(0)
            out[d+1]=[round(float(N[i]/K[i]),3) for i in (g,p,r)]
    b=c.wildlife_budget()
    return out, max(abs(v['residual']) for v in b.values())
if __name__=='__main__':
    days=int(sys.argv[1]); seeds=[int(x) for x in sys.argv[2].split(',')]
    for seed in seeds:
        for on in (False,True):
            o,res=run(seed,on,days)
            print('seed',seed,'ON ' if on else 'OFF','game/pred/raider fill',o,'resid',f'{res:.1e}',flush=True)
