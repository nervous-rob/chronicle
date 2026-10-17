"""A worker's employer-funded wage becomes household grocery food via physical custody."""
import json, sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import MarketPolicy
from chronicle.wages import WagePolicy
checks=[]
def check(x,m): assert x,m; checks.append(m); print('ok',m)
NOFIRE={s:0.0 for s in ('spring','summer','autumn','winter')}
cfg=load(overrides={'world.width_km':12,'world.height_km':8,'world.region_count':2,'weather.lightning_p':NOFIRE,'wildlife.raid_rate':0.0})
e=Engine(cfg); home_cell=int(np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))[0])
market_cell=next(int(x) for x in e.world.neighbors(home_cell) if x >= 0 and e.world.mainland()[x] and e.world.terrain[x]==T['plains'])
market=e.seed_place('market',market_cell,None)
boss_home=e.seed_place('house',home_cell,'h:0'); workshop=e.seed_place('workshop',home_cell,'h:0')
seller_home=e.seed_place('house',home_cell,'h:2'); seller_store=e.seed_place('granary',home_cell,'h:2')
family_home=e.seed_place('house',home_cell,'h:1')
boss=e.add_person(-35*365,home_cell,boss_home,household_id=0,role='crafter',name='Boss')
seller=e.add_person(-30*365,home_cell,seller_home,household_id=2,role='farmer',name='Seller',capabilities=['planning'])
worker=e.add_person(-27*365,home_cell,family_home,household_id=1,role='merchant',name='Worker',capabilities=['planning'])
shopper=e.add_person(-26*365,home_cell,family_home,household_id=1,role='merchant',name='Shopper',capabilities=['planning'])
for p in (seller,worker,shopper): p.known_cells[market_cell]={'day':0,'source':'fixture'}
e.inventory.create(0,'coin',60,f'b:{workshop}','h:0',source='setup',process='fixture-till')
e.inventory.create(0,'grain',10000,f'b:{seller_store}','h:2',source='setup',process='fixture-surplus')
e.employment.post(workshop,boss.id,wage=20,slots=1)
e.agency.bind(seller.id,MarketPolicy(carry_offer_units=500,local_surplus_units=1000,mode='seller'))
e.agency.bind(worker.id,WagePolicy(reserve_coin=20))
e.agency.bind(shopper.id,MarketPolicy(grocery_units=420,mode='shopper'))
for _ in range(32): e.step()
paid=[x for x in e.ledger.events if x.type=='paid' and worker.id in x.actors]
trades=[x for x in e.ledger.events if x.type=='trade' and shopper.id in x.actors]
check(paid,'worker completes an employer-funded local shift')
check(trades,'shopper spends a wage-derived household coin float at the physical market')
home_food=sum(l.qty for l in e.inventory.lots.values() if l.holder==f'b:{family_home}' and l.owner=='h:1' and l.item=='grain')
check(home_food>0,'purchased grain is carried home and deposited for the household')
family_coin=sum(l.qty for l in e.inventory.lots.values() if l.owner=='h:1' and l.item=='coin')
check(family_coin < sum(x.payload['wage'] for x in paid),'household coin falls only after wages fund grocery purchasing')
check(e.ledger.close_day(e.day,e.inventory.holdings())==[],'work, hauling, and grocery transfers conserve every lot exactly')
out={'suite':'wage-grocery','checks':len(checks),'failures':[]}; (ROOT/'test-results/wage-grocery.json').write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
