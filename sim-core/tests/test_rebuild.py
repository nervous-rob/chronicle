"""rebuild-v1: a household can rebuild a burned labor-only building (a farm) with its own labor.
Cost is catalog labor_days x activity_hours_per_day, spent from the worker's daily budget; nothing is conjured.
Buildings that need materials (house, granary) are NOT rebuildable yet: recorded, asserted, not papered over."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
import numpy as np
from chronicle.config import load
from chronicle.engine import Engine
from chronicle import setup
checks=[]
def check(x,msg):
    assert x,msg
    checks.append(msg); print('ok',msg,flush=True)
NOFIRE={s:0.0 for s in ('spring','summer','autumn','winter')}
def build(days=0):
    e=Engine(load(None,overrides={'population.initial':40,'weather.lightning_p':NOFIRE})); setup.generate(e)
    for _ in range(days): e.step()
    return e
def burn_farm(e,fid):
    b=e.places[fid]; b.destroyed_day=e.day; e.inventory.burn(e.day,f'b:{fid}'); e.inventory.holders[f'b:{fid}'].store=False
    e.ledger.event(e.day,'burned',cell=b.cell,place=fid,building=fid)
e=build(); fid=sorted(e.farming.farms)[0]; f=e.farming.farms[fid]
worker=[p for p,x in e.farming.assignments.items() if x==fid][0]
spec=e.cfg.building('farm'); need=spec['labor_days']*e.cfg['agriculture']['activity_hours_per_day']
check(e.farming.rebuildable(fid),'a farm (no materials) is rebuildable')
check(not any(e.farming.rebuildable(h.home) for h in e.farming.households.values()),'a house (timber) is not rebuildable yet')
burn_farm(e,fid)
for _ in range(120): e.step()
rep=[ev for ev in e.ledger.events if ev.type=='repaired' and ev.place==fid]
check(len(rep)==1,'burned farm is repaired exactly once within 120 days')
check(e.places[fid].destroyed_day is None and e.places[fid].condition==1.0,'repaired farm is intact')
lab=[x for x in e.farming.labor if x['activity']=='rebuild']
hours=sum(r['hours'] for r in e.ledger.events if False)
check(rep[0].day>=int(need/e.cfg['agriculture']['activity_hours_per_day']) ,f'repair takes at least labor_days of work (day {rep[0].day}, need {spec["labor_days"]} work-days)')
# labor spent is exactly the catalog cost: re-run, summing the daily labor trace
e=build(); burn_farm(e,fid); tot=0.
for _ in range(120):
    e.step(); tot+=sum(x['hours'] for x in e.farming.labor if x['activity']=='rebuild' and x['farm']==fid)
check(abs(tot-need)<1e-6,f'rebuild labor sunk == labor_days x hours/day ({tot:.2f} vs {need:.2f})')
check(e.ledger.checks>0 and not e.ledger.residuals,'conservation holds through burn+rebuild')
# a burned farm is farmed again: run into the next spring/summer and see a planting on that cell
e=build(); burn_farm(e,fid)
for _ in range(400): e.step()
pl=[ev for ev in e.ledger.events if ev.type=='planted' and ev.cell==e.places[fid].cell]
check(len(pl)>=1,'rebuilt farm is planted again')
# a worker with a burned HOME does not try to rebuild (house needs timber): no repaired event for it
e=build(); h=e.places[f.home]; hid=f.home
h.destroyed_day=0; e.inventory.burn(0,f'b:{hid}')
for _ in range(30): e.step()
check(not [ev for ev in e.ledger.events if ev.type=='repaired' and ev.place==hid],'a burned house is never rebuilt without a material economy')
# checkpoint round trip mid-rebuild
import tempfile
e=build(); burn_farm(e,fid)
for _ in range(10): e.step()
check(e.farming.farms[fid].rebuild_hours>0,'rebuild is in progress at day 10')
with tempfile.TemporaryDirectory() as d:
    pth=Path(d)/'c.zip'; e.save_checkpoint(str(pth)); e2=Engine.load_checkpoint(str(pth),expected_config=e.cfg)
    for _ in range(40): e.step(); e2.step()
    check(e.digest()==e2.digest(),'checkpoint mid-rebuild replays bit-identically over 40 days')
print(f'\n{len(checks)} checks passed')
