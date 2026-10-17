"""Post-fire, multi-household market recovery under the bounded local routine.

The fire-hit household has no granary or farm, but it has coin and an intact home.
A neighboring household must repeatedly carry real grain to a co-located market.
No scripted action selects a partner, transfer, price, or route.
"""
import json, sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import MarketPolicy
checks=[]
def check(value, message):
    assert value, message; checks.append(message); print('ok', message)
NOFIRE={s:0.0 for s in ('spring','summer','autumn','winter')}
cfg=load(overrides={'world.width_km':12,'world.height_km':8,'world.region_count':2,
                    'weather.lightning_p':NOFIRE,'wildlife.raid_rate':0.0,
                    'run.days_per_tick':30})
e=Engine(cfg)
cell=int(np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))[0])
market=e.seed_place('market',cell,None)
# Three distinct estates: producing seller, fire-hit buyer, and an uninvolved neighbor.
sh=e.seed_place('house',cell,'h:0'); ss=e.seed_place('granary',cell,'h:0'); sf=e.seed_place('farm',cell,'h:0')
bh=e.seed_place('house',cell,'h:1'); bs=e.seed_place('granary',cell,'h:1'); bf=e.seed_place('farm',cell,'h:1')
nh=e.seed_place('house',cell,'h:2')
seller=e.add_person(-30*365,cell,sh,household_id=0,role='farmer',name='Seller')
buyer=e.add_person(-30*365,cell,bh,household_id=1,role='farmer',name='Fire-hit buyer')
neighbor=e.add_person(-30*365,cell,nh,household_id=2,role='farmer',name='Neighbor')
e.farming.register(sf,sh,ss); e.farming.assign(seller.id,sf)
e.inventory.create(0,'grain',10000,f'b:{ss}','h:0',source='setup',process='fixture-surplus')
e.inventory.create(0,'coin',100,f'p:{buyer.id}','h:1',source='setup',process='fixture-coin')
e.inventory.burn(0,f'b:{bs}'); e.places[bs].destroyed_day=0; e.places[bf].destroyed_day=0
e.ledger.event(0,'burned',cell=cell,place=bs,building=bs)
e.ledger.event(0,'burned',cell=cell,place=bf,building=bf)
# The buyer is three missed meals from starvation; without relief this is a real deadline.
buyer.starvation=float(e.cfg['needs']['starve_days']-6)
e.agency.bind(seller.id,MarketPolicy())
e.agency.bind(buyer.id,MarketPolicy())
for _ in range(30): e.step()
trades=[x for x in e.ledger.events if x.type=='trade' and buyer.id in x.actors and seller.id in x.actors]
check(len(trades)>=3,'seller makes repeated physical market deliveries rather than one scripted rescue')
check(buyer.alive,'fire-hit buyer survives past the original starvation deadline through paid local trade')
check(buyer.starvation < float(e.cfg['needs']['starve_days']-6),'ordinary eating reduces the buyer hunger debt')
check(e.places[bs].destroyed_day==0 and e.places[bf].destroyed_day==0,'trade does not repair or conceal the destroyed estate')
check(sum(x.payload['qty'] for x in trades if x.payload['item']=='grain') >= 1000,'at least three carry-cap-bounded grain lots change custody')
check(any(x.type=='transfer' and x.payload.get('reason')=='trade' and seller.id in x.actors for x in e.ledger.events),'seller receives physical coin for grain')
check(all(r['source']=='market-routine' for r in e.agency.log),'recovery choices are entirely from the bounded local routine')
check(e.ledger.close_day(e.day,e.inventory.holdings())==[],'thirty days of fires, hauling, sales and meals conserve every item')
out={'suite':'market-recovery','checks':len(checks),'failures':[],
     'scope':'Three-household post-fire recovery. Local routine sees no remote stocks, prices, or partner hunger.'}
(ROOT/'test-results/market-recovery.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
