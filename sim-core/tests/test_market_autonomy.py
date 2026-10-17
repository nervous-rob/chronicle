"""Autonomous local market routine: no scripted sale and no remote stock access."""
import sys, json
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
NOFIRE={s:0. for s in ('spring','summer','autumn','winter')}
cfg=load(overrides={'world.width_km':12,'world.height_km':8,'world.region_count':2, 'weather.lightning_p':NOFIRE,'wildlife.raid_rate':0.0,'run.days_per_tick':30})
e=Engine(cfg)
cell=int(np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))[0])
market=e.seed_place('market',cell,None)
sh=e.seed_place('house',cell,'h:0'); ss=e.seed_place('granary',cell,'h:0'); sf=e.seed_place('farm',cell,'h:0')
bh=e.seed_place('house',cell,'h:1'); bs=e.seed_place('granary',cell,'h:1'); bf=e.seed_place('farm',cell,'h:1')
seller=e.add_person(-30*365,cell,sh,household_id=0,role='farmer',name='Seller')
buyer=e.add_person(-30*365,cell,bh,household_id=1,role='farmer',name='Buyer')
e.farming.register(sf,sh,ss); e.farming.assign(seller.id,sf)
e.inventory.create(0,'grain',10000,f'b:{ss}','h:0',source='setup',process='fixture-surplus')
e.inventory.create(0,'coin',50,f'p:{buyer.id}','h:1',source='setup',process='fixture-coin')
e.inventory.burn(0,f'b:{bs}'); e.places[bs].destroyed_day=0; e.places[bf].destroyed_day=0
e.agency.bind(seller.id,MarketPolicy()); e.agency.bind(buyer.id,MarketPolicy())
for _ in range(8): e.step()
trades=[x for x in e.ledger.events if x.type=='trade']
check(trades,'local autonomous routine completes a paid trade without ScriptedPolicy')
check(all(r['source']=='market-routine' for r in e.agency.log),'all choices come from the bounded market routine')
check(any(x.payload['item']=='grain' for x in trades),'the autonomous sale is food carried from seller inventory')
check(any(x.type=='transfer' and x.payload.get('reason')=='trade' and buyer.id in x.actors for x in e.ledger.events),'buyer takes physical custody of the purchased grain before later consumption')
check(any(x.type=='transfer' and x.payload.get('reason')=='trade' and seller.id in x.actors for x in e.ledger.events),'seller receives physical coin in the atomic sale')
check(e.ledger.close_day(e.day,e.inventory.holdings())==[],'autonomous hauling, trade and consumption conserve all inventory')
out={'suite':'market-autonomy','checks':len(checks),'failures':[], 'scope':'Local visible-market routine; no remote-price, remote-stock, or partner-hunger access.'}
(ROOT/'test-results/market-autonomy.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
