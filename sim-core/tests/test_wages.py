"""A paid local shift transfers only an employer-funded workplace till."""
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
h=e.seed_place('house',cell,'h:0'); shop=e.seed_place('workshop',cell,'h:0'); wh=e.seed_place('house',cell,'h:1')
boss=e.add_person(-35*365,cell,h,household_id=0,role='crafter',name='Boss')
worker=e.add_person(-25*365,cell,wh,household_id=1,role='merchant',name='Worker',capabilities=['planning'])
worker.known_cells[cell]={'day':0,'source':'fixture'}
e.inventory.create(0,'coin',12,f'b:{shop}','h:0',source='setup',process='fixture-till')
job=e.employment.post(shop,boss.id,wage=3,slots=1)
e.agency.bind(worker.id,WagePolicy())
e.step()
e.step()
paid=[x for x in e.ledger.events if x.type=='paid']
check(len(paid)==1 and paid[0].payload['wage']==3,'worker completes a visible local shift and receives its posted wage')
worker_coin=sum(l.qty for l in e.inventory.lots.values() if l.holder==f'p:{worker.id}' and l.item=='coin')
till_coin=sum(l.qty for l in e.inventory.lots.values() if l.holder==f'b:{shop}' and l.item=='coin')
check(worker_coin==3 and till_coin==9,'wage comes from the employer-funded physical workplace till')
check(e.ledger.close_day(e.day,e.inventory.holdings())==[],'paid work conserves coin exactly')
out={'suite':'wages','checks':len(checks),'failures':[]}; (ROOT/'test-results/wages.json').write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
