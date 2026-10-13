"""worldgen-v1: setup generator + run.py contract.
A world is a pure function of (config, seed). Households get a home, a store, food, and (if any member
produces food) a farm with an assigned worker or a pantry keeper. Crop stress is catalog data.
Known, recorded, NOT asserted away: buildings burned by wildfire are never rebuilt (no rebuild
increment yet), so a household whose farm burns in spring starves next winter (subsist-100b: 5 deaths)."""
import sys, json, copy, subprocess, os, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
import numpy as np
from chronicle.config import load, ConfigError
from chronicle.engine import Engine
from chronicle import setup
checks=[]
def check(x,msg):
    assert x,msg
    checks.append(msg); print('ok',msg,flush=True)
NOFIRE={s:0.0 for s in ('spring','summer','autumn','winter')}
OV={'population.initial':40,'weather.lightning_p':NOFIRE}

def build(seed=42, days=0, ov=None):
    o=dict(OV); o.update(ov or {}); o['run.seed']=seed
    cfg=load(None,overrides=o); e=Engine(cfg); rep=setup.generate(e)
    for _ in range(days): e.step()
    return e,rep

# ---- determinism
e1,r1=build(days=30); e2,r2=build(days=30)
check(e1.digest()==e2.digest(),'same config+seed -> identical digest after 30 days')
check(json.dumps(r1,sort_keys=True)==json.dumps(r2,sort_keys=True),'same config+seed -> identical setup report')
e3,_=build(seed=7,days=30)
check(e3.digest()!=e1.digest(),'different seed -> different digest')

# ---- structure
e,rep=build()
alive=[p for p in e.people.rows.values() if p.alive]
check(len(alive)==40 and rep['people']==40,'40 people generated')
hh=e.farming.households
check(len(hh)>=8,f'households formed ({len(hh)})')
check(all(e.places[h.home].destroyed_day is None and e.places[h.store].destroyed_day is None for h in hh.values()),'every household has an intact home and store')
check(all(p.home_place is not None and p.home_place in e.places for p in alive),'every person has a home place')
members={}   # keyed by home place, which is how farming.households is keyed
for p in alive: members.setdefault(p.home_place,[]).append(p)
check(set(members)<=set(hh),'every person lives in a generated household home')
check(all(f'h:{p.household_id}'==hh[p.home_place].owner for p in alive),'person.household_id matches the household owner of their home')
def food(holder,owner): return sum(l.qty for l in e.inventory.lots.values() if l.holder==holder and l.owner==owner and e.cfg.item(l.item)['kind']=='food')
check(all(food(f'b:{h.home}',h.owner)+food(f'b:{h.store}',h.owner)>0 for h in hh.values()),'every household starts with food in its own store')
farms=e.farming.farms
check(len(farms)>=1 and rep['farms']==len(farms),f'farms registered ({len(farms)})')
farm_cells=[e.places[f].cell for f in farms]
check(len(set(farm_cells))==len(farm_cells),'one farm per cell')
check(all(e.cells.fields[c]>0 for c in farm_cells),'every farm cell has fields')
workers={f for f in e.farming.assignments.values()}
check(all(f in workers for f in farms),'every farm has an assigned worker')
for pid,f in e.farming.assignments.items():
    p=e.people.rows[pid]; role=e.cfg.role(p.role)
    assert role['workplace']==e.places[f].kind and e.cells.crop['item'] in role['produces'], pid
check(True,'every assigned worker has a food-producing role whose workplace is the farm')
check(all(any(q.id in e.farming.assignments for q in members.get(hid,[])) or any(k==h.home for k in e.farming.keepers.values()) or h.home==h.store or not members.get(hid) for hid,h in hh.items()),'each household has a farm worker, a pantry keeper, or a single home/store')
check(rep['subsistence'].get('filled',0)+rep['subsistence'].get('replaced',0)>=0 and 'subsistence' in rep,'subsistence fallback reported')
check(all(e.cells.cleared[c]>0 for c in farm_cells),'worked cells are partially cleared of wild fuel')

# ---- config contract
for bad in ({'catalog.crops.grain.stress_loss_per_day':1.0},{'catalog.crops.grain.stress_loss_per_day':-0.1}):
    try: load(None,overrides=dict(OV,**bad)); ok=False
    except ConfigError: ok=True
    check(ok,f'crops.stress_loss_per_day rejected: {list(bad.values())[0]}')
try:
    load(None,overrides=dict(OV,**{'population.cluster_weights':[1.0]})); ok=False
except ConfigError: ok=True
check(ok,'population.cluster_weights length must equal clusters')

# ---- crop stress is data. Raider damage (cells.step_wildlife) still costs health at zero stress, so the
# contract is monotone in the catalog value, not 'health == 1'.
e0,_=build(days=120,ov={'catalog.crops.grain.stress_loss_per_day':0.0})
pl=np.flatnonzero(e0.cells.planted); h0=float(e0.cells.crop_health[pl].mean())
es,_=build(days=120,ov={'catalog.crops.grain.stress_loss_per_day':0.05})
pls=np.flatnonzero(es.cells.planted); hs=float(es.cells.crop_health[pls].mean())
check(len(pl)>0 and len(pls)>0 and h0>0.8 and hs<h0-0.2,f'crop health is monotone in stress_loss_per_day (0 -> {h0:.2f}, 0.05 -> {hs:.2f})')

# ---- survival through first harvest (lightning off isolates the generator from the fire model)
e,_=build(days=240)
died=[ev for ev in e.ledger.events if ev.type=='died']
check(not died,f'no deaths in the first 240 days (through harvest); got {[(ev.day,ev.payload) for ev in died]}')
check(sum(ev.type=='harvest' for ev in e.ledger.events)>=len(e.farming.farms),'every farm harvested in autumn')
fu=lambda: sum(l.qty for l in e.inventory.lots.values() if e.cfg.item(l.item)['kind']=='food')
check(fu()>0,'food on hand after harvest')

# ---- run.py end to end
with tempfile.TemporaryDirectory() as td:
    out=os.path.join(td,'w')
    r=subprocess.run([sys.executable,'run.py','--days','10','--out',out,'--set','population.initial=40','--set','weather.lightning_p='+json.dumps(NOFIRE),'--quiet'],cwd=ROOT,capture_output=True,text=True)
    check(r.returncode==0,f'run.py exits 0 ({r.stderr[-300:]})')
    check(all(os.path.exists(os.path.join(out,f)) for f in ('setup.json','digest.jsonl','final.json')),'run.py writes setup.json, digest.jsonl, final.json')
    lines=[json.loads(l) for l in open(os.path.join(out,'digest.jsonl'))]
    check(len(lines)==10 and lines[-1]['day']==9 and lines[-1]['alive']==40,'digest has one line per day')
    fin=json.load(open(os.path.join(out,'final.json')))
    r2=subprocess.run([sys.executable,'run.py','--days','10','--out',out+'2','--set','population.initial=40','--set','weather.lightning_p='+json.dumps(NOFIRE),'--quiet'],cwd=ROOT,capture_output=True,text=True)
    fin2=json.load(open(os.path.join(out+'2','final.json')))
    check(fin['digest_sha']==fin2['digest_sha'],'run.py is deterministic across processes')

(ROOT/'test-results').mkdir(exist_ok=True)
(ROOT/'test-results'/'worldgen.json').write_text(json.dumps({'suite':'worldgen','checks':len(checks),'failures':[],
  'known_open':'burned buildings are never rebuilt; subsist-100b (lightning on) lost 2 farms on days 8-9 and 5 people the next winter'},indent=2))
print(f'\n{len(checks)} checks passed')
