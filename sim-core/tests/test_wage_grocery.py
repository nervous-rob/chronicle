"""A worker's employer-funded wage becomes household grocery food via physical custody."""
import json, sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import MarketPolicy
from chronicle.wages import WagePolicy, WageGroceryPolicy
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
e.agency.bind(worker.id,WageGroceryPolicy(reserve_coin=20,grocery_units=420))
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
# The same provisioner can perform both roles; this is required for ordinary
# two-adult farm households where the other adult remains assigned to the field.
e2=Engine(cfg); cell2=home_cell; market2=e2.seed_place('market',market_cell,None); boss_home2=e2.seed_place('house',cell2,'h:0'); workshop2=e2.seed_place('workshop',cell2,'h:0'); seller_home2=e2.seed_place('house',cell2,'h:2'); seller_store2=e2.seed_place('granary',cell2,'h:2'); family_home2=e2.seed_place('house',cell2,'h:1')
boss2=e2.add_person(-35*365,cell2,boss_home2,household_id=0,role='crafter',name='Boss2'); seller2=e2.add_person(-30*365,cell2,seller_home2,household_id=2,role='farmer',name='Seller2',capabilities=['planning']); provisioner=e2.add_person(-27*365,cell2,family_home2,household_id=1,role='farmer',name='Provisioner',capabilities=['planning'])
for p2 in (seller2,provisioner): p2.known_cells[market_cell]={'day':0,'source':'fixture'}
e2.inventory.create(0,'coin',60,f'b:{workshop2}','h:0',source='setup',process='fixture-till'); e2.inventory.create(0,'grain',10000,f'b:{seller_store2}','h:2',source='setup',process='fixture-surplus'); e2.employment.post(workshop2,boss2.id,wage=20,slots=1)
e2.agency.bind(seller2.id,MarketPolicy(carry_offer_units=500,local_surplus_units=1000,mode='seller')); e2.agency.bind(provisioner.id,WageGroceryPolicy(reserve_coin=20,grocery_units=420))
for _ in range(40): e2.step()
paid2=[x for x in e2.ledger.events if x.type=='paid' and provisioner.id in x.actors]; trades2=[x for x in e2.ledger.events if x.type=='trade' and provisioner.id in x.actors]
check(paid2 and trades2,'a single household provisioner can earn wages and later buy and return grocery food')
check(e2.ledger.close_day(e2.day,e2.inventory.holdings())==[],'single-provisioner wage and grocery loop conserves every lot exactly')

# A combined provisioner must retain its spring planting duty rather than chasing
# a visible funded job; this protects the population-scale food-production window.
e3=Engine(cfg); c3=home_cell; h3=e3.seed_place('house',c3,'h:0'); f3=e3.seed_place('farm',c3,'h:0'); s3=e3.seed_place('granary',c3,'h:0'); wh3=e3.seed_place('workshop',c3,'h:9')
farmer=e3.add_person(-30*365,c3,h3,household_id=0,role='farmer',name='SeasonalProvisioner',capabilities=['planning']); boss3=e3.add_person(-35*365,c3,wh3,household_id=9,role='crafter',name='SeasonalBoss')
e3.farming.register(f3,h3,s3); e3.inventory.create(0,'coin',100,f'b:{wh3}','h:9',source='setup',process='fixture-till'); e3.employment.post(wh3,boss3.id,wage=4,slots=10)
e3.agency.bind(farmer.id,WageGroceryPolicy(reserve_coin=20,grocery_units=420)); e3.step()
check(not [x for x in e3.ledger.events if x.type=='paid'], 'combined provisioner keeps the spring agricultural day instead of taking a visible wage')
check(e3.cells.planted[c3], 'ordinary farm work remains available to the combined provisioner during planting season')

out={'suite':'wage-grocery','checks':len(checks),'failures':[]}; (ROOT/'test-results/wage-grocery.json').write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
