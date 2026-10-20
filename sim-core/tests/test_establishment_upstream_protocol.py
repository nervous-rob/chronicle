"""Autonomous supplier recipe protocol: fund -> commission -> labor -> collect -> stock -> sell."""
import sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
import numpy as np
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import EstablishmentPolicy

checks=[]
def check(x,m): assert x,m; checks.append(m); print('ok',m,flush=True)
BASE={"world.width_km":12,"world.height_km":8,"world.region_count":2,
      "wildlife.raid_rate":0.,"scarcity.theft_enabled":False,
      "catalog.items.meal.spoil_per_day":0.,"catalog.items.butchered_meat.spoil_per_day":0.,
      "weather.lightning_p":{s:0. for s in ('spring','summer','autumn','winter')}}
e=Engine(load(overrides=BASE))
cell=int(np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))[0])
butch=e.seed_place('butcher_shop',cell,'h:0'); rest=e.seed_place('restaurant',cell,'h:1')
butcher=e.add_person(-30*365,cell,butch,household_id=0,name='Butcher')
chef=e.add_person(-30*365,cell,rest,household_id=1,name='Chef')
diner=e.add_person(-24*365,cell,rest,household_id=2,name='Diner')
# The only supplier input is real butcher-shop meat; both buyers begin with actual
# workplace/customer coin. No test-side order, transfer, or labor call follows.
e.inventory.create(0,'meat',10,f'b:{butch}','h:0',source='setup')
e.inventory.create(0,'coin',2,f'b:{rest}','h:1',source='setup')
e.inventory.create(0,'coin',1,f'p:{diner.id}','h:2',source='setup')
for p, owner in ((butcher,'h:0'),(chef,'h:1'),(diner,'h:2')):
    e.inventory.create(0,'grain',1000,f'p:{p.id}',owner,source='setup')
e.agency.bind(butcher.id,EstablishmentPolicy('operator',wage=1,establishment=butch,stock_targets={'meat':10}))
e.agency.bind(chef.id,EstablishmentPolicy('operator',wage=1,establishment=rest,stock_targets={'butchered_meat':9},upstream_orders=((butch,'butcher_portion',1),),visit_period=7))
e.agency.bind(diner.id,EstablishmentPolicy('customer',recipes=('restaurant_meal',),price=1,establishment=rest,visit_period=1))
for _ in range(18): e.step()
orders=list(e.employment.work_orders.values())
upstream=[q for q in orders if q.recipe=='butcher_portion']
downstream=[q for q in orders if q.recipe=='restaurant_meal']
check(len(upstream)==1 and upstream[0].status=='complete','restaurant operator completes one physical upstream butcher commission without a test-side action')
check(any(ev.type=='paid' and ev.payload.get('activity')=='order' and ev.place==butch for ev in e.ledger.events),'butcher operator posts and performs the visible paid processing shift')
check(any(ev.type=='transfer' and ev.payload.get('reason')=='work-order-pickup' and ev.place==butch for ev in e.ledger.events),'chef physically collects customer-owned butcher output at the supplier')
check(sum(l.qty for l in e.inventory.lots.values() if l.holder==f'b:{rest}' and l.owner=='h:1' and l.item=='butchered_meat') in (0,6,9),'returned upstream output is handled through ordinary restaurant custody or consumed by the downstream order')
check(len(downstream)>=1 and any(q.status=='complete' for q in downstream),'restaurant closes a downstream paid meal order after autonomous upstream replenishment')
check(any(ev.type=='transfer' and ev.payload.get('reason')=='work-order-pickup' and ev.place==rest for ev in e.ledger.events),'diner physically picks up prepared meals from the restaurant')
e.agency.unbind(butcher.id); e.agency.unbind(chef.id); e.agency.unbind(diner.id); e.step()
from chronicle.checkpoint import save, load as load_checkpoint
with tempfile.TemporaryDirectory() as td:
 path=Path(td)/'upstream.zip'; save(e,path); r=load_checkpoint(path)
 check(r.digest()==e.digest(),'upstream protocol result survives checkpoint/reload')
print(f'{len(checks)} checks passed')
