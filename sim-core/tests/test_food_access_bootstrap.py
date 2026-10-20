"""Worldgen food-access bootstrap: establishment agency cannot suppress a household pantry route."""
import sys, json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.agency import EstablishmentPolicy
from chronicle.world import T
checks=[]
def check(ok,msg):
    assert ok,msg; checks.append(msg); print('ok',msg,flush=True)
BASE={'world.width_km':8,'world.height_km':6,'world.region_count':1,'wildlife.raid_rate':0.,
      'weather.lightning_p':{s:0. for s in ('spring','summer','autumn','winter')}, 'contact.p_base':0.}
e=Engine(load(overrides=BASE))
c=int(np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))[0])
h=e.seed_place('house',c,'h:0'); g=e.seed_place('granary',c,'h:0'); cabin=e.seed_place('hunters_cabin',c,'h:0')
p=e.add_person(-30*365,c,h,household_id=0,name='Hunter',role='hunter')
e.farming.register_household(h,g); e.farming.assign_keeper(p.id,h)
e.inventory.create(0,'grain',1000,f'b:{g}','h:0',source='setup')
e.agency.bind(p.id,EstablishmentPolicy('producer', establishment=cabin, products=('meat',), provisions=('grain',), provision_reserve=100))
e.step()
e.step()
check(p.needs['food']==1. and p.starvation < 1.,'agency-bound household keeper returns to eat lawfully hauled household grain')
check(e.inventory.holdings().get(f'b:{h}',{}).get('grain',0)>0,'keeper physically moves household food from granary to home before agency work')
check(p.id in e.agency.agents and any(r['person']==p.id for r in e.agency.log),'establishment policy remains bound after pantry routine')
check(not e.ledger.residuals,'bootstrap pantry routine preserves conservation')
print(f'{len(checks)} checks passed')
(ROOT/'test-results'/'food-access-bootstrap.json').write_text(json.dumps({'suite':'food-access-bootstrap','checks':len(checks),'failures':[]},indent=2))
