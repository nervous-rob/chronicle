"""A funded farm can hire a nearby worker for real seasonal crop labor."""
import json, sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.wages import WagePolicy
checks=[]
def check(x,m): assert x,m; checks.append(m); print('ok',m)
NOFIRE={s:0.0 for s in ('spring','summer','autumn','winter')}
cfg=load(overrides={'world.width_km':10,'world.height_km':8,'world.region_count':2,'weather.lightning_p':NOFIRE,'wildlife.raid_rate':0.0})
e=Engine(cfg); cell=int(np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))[0])
owner_home=e.seed_place('house',cell,'h:0'); farm=e.seed_place('farm',cell,'h:0'); store=e.seed_place('granary',cell,'h:0'); worker_home=e.seed_place('house',cell,'h:1')
owner=e.add_person(-35*365,cell,owner_home,household_id=0,role='farmer',name='Owner')
workers=[e.add_person(-(25+i)*365,cell,worker_home,household_id=1+i,role='merchant',name=f'Worker{i}') for i in range(8)]
for worker in workers: worker.known_cells[cell]={'day':0,'source':'fixture'}
e.farming.register(farm,owner_home,store); e.inventory.create(0,'coin',32,f'b:{farm}','h:0',source='setup',process='farm-till')
e.employment.post(farm,owner.id,wage=4,slots=8,activity='plant')
for worker in workers: e.agency.bind(worker.id,WagePolicy(reserve_coin=4))
e.step()
e.step()
paid=[x for x in e.ledger.events if x.type=='paid']
check(len(paid)==8 and paid[0].payload['activity']=='plant','farm shift pays only after a real planting contribution')
check(e.cells.planted[cell], 'paid field labor advances the owned farm into a planted crop state')
worker_coin=sum(l.qty for l in e.inventory.lots.values() if l.holder.startswith('p:') and l.item=='coin')
till_coin=sum(l.qty for l in e.inventory.lots.values() if l.holder==f'b:{farm}' and l.item=='coin')
check(worker_coin==32 and till_coin==0,'farm wages move coin from the owner-funded farm till to workers')
check(any(row.get('paid') for row in e.farming.labor),'farm labor trace distinguishes a paid shift from household labor')
check(e.ledger.close_day(e.day,e.inventory.holdings())==[],'paid farm labor conserves both crop inventory and coin')
out={'suite':'paid-farm-labor','checks':len(checks),'failures':[]}; (ROOT/'test-results/paid-farm-labor.json').write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
