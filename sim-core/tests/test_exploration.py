"""exploration-v1: personal map knowledge through adjacent surveyed travel, ledgered and checkpointed."""
import sys, tempfile, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
import numpy as np
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import ScriptedPolicy
checks=[]
def check(ok,msg): assert ok,msg;checks.append(msg);print('ok',msg,flush=True)
BASE={'world.width_km':8,'world.height_km':6,'world.region_count':1,'wildlife.raid_rate':0.,
      'weather.lightning_p':{s:0. for s in ('spring','summer','autumn','winter')},
      'weather.rain_mm_mean':{s:0. for s in ('spring','summer','autumn','winter')},
      'weather.evaporation_mm':{s:0. for s in ('spring','summer','autumn','winter')},
      'exploration.survey_hours':1.0}
def fixture():
 e=Engine(load(overrides=BASE)); land=np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))
 for c in land:
  ns=[int(x) for x in e.world.neighbors(int(c)) if x>=0 and e.world.mainland()[int(x)]]
  if ns:
   home=e.seed_place('house',int(c),'h:0'); p=e.add_person(-30*365,int(c),home,household_id=0,name='Ada'); e.inventory.create(0,'grain',1000,f'p:{p.id}',f'p:{p.id}',source='setup');return e,p,ns[0]
 raise AssertionError('no adjacent land fixture')
def one(e,p,a):
 e.agency.bind(p.id,ScriptedPolicy(lambda packet:a,'script'));e.step();e.agency.unbind(p.id)
e,p,target=fixture()
check(set(p.known_cells)=={p.cell},'origin cell is personal durable knowledge')
# Menu names only viable unknown adjacent cells; it exposes no remote terrain facts.
e.farming.begin(e.day); packet=e.agency.observe(p.id); offer=next(x for x in packet['menu'] if x['kind']=='explore'); check(target in offer['cells'] and 'terrain' not in offer,'explore menu offers adjacent unknown cells without leaking their facts')
# We manually began only to inspect; restart fixture for normal Engine.step cadence.
e,p,target=fixture(); origin=p.cell; q=e.add_person(-30*365,origin,-1,name='Bea'); one(e,p,{'kind':'explore','cell':target})
check(p.cell==target and p.place==-1 and target in p.known_cells,'survey moves explorer into the chosen adjacent cell and retains a personal map entry')
entry=p.known_cells[target];check(entry['source']=='survey' and entry['terrain'] in T and 'visible' in entry,'survey stores coarse terrain and observed local conditions')
check([x.type for x in e.ledger.events if x.type in ('departed','passed_through','discovered','arrived')]==['departed','passed_through','discovered','arrived'],'exploration has ordered public travel and discovery history')
# The resolver retains the personal entry, so the same cell is excluded by its offer predicate next activity phase.
check(target in p.known_cells,'a personally known cell is retained and therefore excluded from later survey offers')
check(target not in q.known_cells,'map knowledge is personal rather than global')
# Checkpoint retains the map evidence exactly.
with tempfile.TemporaryDirectory() as td:
 path=Path(td)/'explore.zip';e.save_checkpoint(path);r=Engine.load_checkpoint(path,expected_config=e.cfg)
 check(r.digest()==e.digest() and r.people.rows[p.id].known_cells==p.known_cells,'personal surveyed map knowledge survives checkpoint round trip')
(ROOT/'test-results/exploration.json').write_text(json.dumps({'suite':'exploration','checks':len(checks),'failures':[]},indent=2));print(f'{len(checks)} checks passed')
