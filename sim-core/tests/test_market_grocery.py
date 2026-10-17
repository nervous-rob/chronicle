"""A household shopper gets food primarily by a paid local grocery run."""
import json, sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import MarketPolicy
checks=[]
def check(x,msg):
    assert x,msg; checks.append(msg); print('ok',msg)
NOFIRE={s:0.0 for s in ('spring','summer','autumn','winter')}
cfg=load(overrides={'world.width_km':12,'world.height_km':8,'world.region_count':2,
                    'weather.lightning_p':NOFIRE,'wildlife.raid_rate':0.0,'run.days_per_tick':30})
e=Engine(cfg); cell=int(np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))[0])
market=e.seed_place('market',cell,None)
sh=e.seed_place('house',cell,'h:0'); ss=e.seed_place('granary',cell,'h:0'); sf=e.seed_place('farm',cell,'h:0')
bh=e.seed_place('house',cell,'h:1')
seller=e.add_person(-30*365,cell,sh,household_id=0,role='farmer',name='Seller')
shopper=e.add_person(-30*365,cell,bh,household_id=1,role='merchant',name='Shopper')
e.farming.register(sf,sh,ss); e.farming.assign(seller.id,sf)
e.inventory.create(0,'grain',10000,f'b:{ss}','h:0',source='setup',process='fixture-surplus')
e.inventory.create(0,'coin',100,f'p:{shopper.id}','h:1',source='setup',process='fixture-coin')
e.agency.bind(seller.id,MarketPolicy(carry_offer_units=500,local_surplus_units=1000))
e.agency.bind(shopper.id,MarketPolicy(carry_offer_units=500,local_surplus_units=1000,grocery_units=420))
for _ in range(12): e.step()
trades=[x for x in e.ledger.events if x.type=='trade' and shopper.id in x.actors]
home_food=sum(l.qty for l in e.inventory.lots.values() if l.holder==f'b:{bh}' and l.owner=='h:1' and l.item=='grain')
check(trades,'shopper completes a paid local grocery purchase without a scripted action')
check(sum(x.payload['qty'] for x in trades)>=420,'successive grocery purchases replenish at least the configured weekly food buffer')
check(home_food>0,'shopper physically unloads bought grain into the household home store after eating')
check(any(x.type=='transfer' and x.payload.get('reason')=='trade' for x in e.ledger.events),'food reaches the household through the ordinary atomic market transfer')
check(all(r['source'] in ('market-routine','plan:market-routine') for r in e.agency.log),'seller and shopper decisions are bounded local routine choices or their persisted local-plan steps')
check(e.ledger.close_day(e.day,e.inventory.holdings())==[],'grocery hauling, sale, storage and consumption conserve all inventory')
out={'suite':'market-grocery','checks':len(checks),'failures':[], 'scope':'Paid local grocery run: a shopper buys carried food at a visible market and physically deposits it at home.'}
(ROOT/'test-results/market-grocery.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
