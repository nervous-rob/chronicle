"""critical-scarcity-v1: physical migration, concealed taking, and starvation continuity."""
import sys, json, tempfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T

checks=[]
def check(ok,msg):
    assert ok,msg; checks.append(msg); print('ok',msg,flush=True)
BASE={'world.width_km':8,'world.height_km':6,'world.region_count':1,'wildlife.raid_rate':0.,
      'weather.lightning_p':{'spring':0.,'summer':0.,'autumn':0.,'winter':0.},
      'demography.enabled':False,'contact.p_base':0.}
def cell(e): return int(np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains']))[0])
def person(e,c,place,household=0,name='Hungry'):
    return e.add_person(-30*365,c,place,household_id=household_id if False else household, name=name)

# A critical person walks to legitimate food rather than taking foreign food.
e=Engine(load(overrides=BASE)); c=cell(e); home=e.seed_place('house',c,'h:0'); other=int(next(x for x in np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains'])) if x!=c)); store=e.seed_place('granary',other,'h:0')
p=e.add_person(-30*365,c,home,household_id=0,name='Hungry')
e.inventory.create(0,'grain',100, f'b:{store}', 'h:0', source='setup')
p.migrate=True
e.step()
check((p.id in e.farming.journeys or (p.cell,p.place)==(other,store)) and any(x.type=='migrated' and x.actors==(p.id,) for x in e.ledger.events),
      'critical hunger commits a person to physical migration toward reachable owned food and ledgers it')
for _ in range(30):
    if (p.cell,p.place)==(other,store): break
    p.migrate=True; e.step()
check((p.cell,p.place)==(other,store) and p.needs['food']==1. and not any(x.type=='steal' for x in e.ledger.events),
      'legitimate migration eventually reaches food and feeds without theft')

# A critical flag must not hijack a farm worker's existing physical food routine.
e=Engine(load(overrides=BASE)); c=cell(e); other=int(next(x for x in np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains'])) if x!=c))
h=e.seed_place('house',c,'h:0'); g=e.seed_place('granary',other,'h:0'); f=e.seed_place('farm',c,'h:0')
e.farming.register(f,h,g); p=e.add_person(-30*365,c,h,household_id=0,role='farmer',name='Worker')
e.farming.assign(p.id,f); e.inventory.create(0,'grain',100,f'b:{g}','h:0',source='setup'); p.migrate=True
e.step()
check(p.id not in e.farming.journeys and not any(x.type=='migrated' and x.actors==(p.id,) for x in e.ledger.events),
      'critical marker does not hijack an assigned worker from its established food routine')

# With no reachable owned food, taking is a concealed conserved transfer at the exact place.
e=Engine(load(overrides=BASE)); c=cell(e); foreign=e.seed_place('granary',c,'h:1'); observer=e.seed_place('house',c,'h:1')
p=e.add_person(-30*365,c,foreign,household_id=0,name='Taker')
q=e.add_person(-30*365,c,foreign,household_id=1,name='Owner')
e.inventory.create(0,'grain',100,f'b:{foreign}','h:1',source='setup')
p.migrate=True
e.step()
steals=[x for x in e.ledger.events if x.type=='steal']
check(len(steals)==1 and steals[0].visibility=='concealed' and steals[0].actors==(p.id,),
      'unrelieved critical hunger performs one concealed theft event')
check(p.needs['food']==1. and e.inventory.holdings().get(f'p:{p.id}',{}).get('grain',0)==0,
      'stolen ration is physically carried then consumed that day')
check(steals[0].id in p.memory and steals[0].id not in q.memory,
      'concealed theft is known to its actor but not automatically witnessed by co-present owner')
check(not e.ledger.residuals,'scarcity theft preserves inventory conservation')

# Existing starvation still kills after the configured horizon when neither relief path exists.
e=Engine(load(overrides={**BASE,'scarcity.theft_enabled':False})); c=cell(e); h=e.seed_place('house',c,'h:0')
p=e.add_person(-30*365,c,h,household_id=0,name='Starving'); p.migrate=True; p.starvation=float(e.cfg['needs']['starve_days'])
e.step()
check(not p.alive and any(x.type=='died' and x.payload.get('cause')=='starvation' for x in e.ledger.events),
      'critical hunger with no migration or theft relief still reaches starvation death')

# A pre-resolution critical marker survives a checkpoint and yields the same physical result.
e=Engine(load(overrides=BASE)); c=cell(e); h=e.seed_place('house',c,'h:0'); other=int(next(x for x in np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains'])) if x!=c)); g=e.seed_place('granary',other,'h:0')
p=e.add_person(-30*365,c,h,household_id=0,name='Reloaded'); e.inventory.create(0,'grain',100,f'b:{g}','h:0',source='setup'); p.migrate=True
# Establish a legal completed boundary, then restore the deliberately staged critical marker.
e.step()
p.cell, p.place = c, h
p.migrate=True
with tempfile.TemporaryDirectory() as td:
    path=Path(td)/'scarcity.zip'; e.save_checkpoint(path); r=Engine.load_checkpoint(path,expected_config=e.cfg)
    e.step(); r.step()
    check(e.digest()==r.digest() and r.people.rows[p.id].place==g,'critical scarcity resolution is deterministic across checkpoint reload')

report={'suite':'scarcity','checks':len(checks),'failures':[], 'scope':'critical marker resolves owned-food migration, then concealed exact-place food taking; ordinary nutrition/starvation remains the mortality mechanism'}
(ROOT/'test-results'/'scarcity.json').write_text(json.dumps(report,indent=2));print(f'{len(checks)} checks passed')
