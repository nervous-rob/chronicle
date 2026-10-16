"""Physical market survival integration: a fire-hit household buys and carries grain.

This is deliberately a scripted scenario, not autonomous merchant behavior.  It
proves the existing movement, custody, payment, market, fire, eating and ledger
primitives compose without creating food or granting remote access.
"""
import sys, json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import ScriptedPolicy

checks=[]
def check(x,msg):
    assert x,msg; checks.append(msg); print('ok',msg)

NOFIRE={s:0. for s in ('spring','summer','autumn','winter')}
cfg=load(overrides={'world.width_km':12,'world.height_km':8,'world.region_count':2,
                    'weather.lightning_p':NOFIRE,'wildlife.raid_rate':0.0,
                    'run.days_per_tick':30})
e=Engine(cfg)
cell=int(np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))[0])
# Two distinct household estates, one seller-side surplus, one buyer-side farm/store
# that is then destroyed.  The intact market is a third, neutral physical place.
market=e.seed_place('market',cell,None)
sh=e.seed_place('house',cell,'h:0'); ss=e.seed_place('granary',cell,'h:0'); sf=e.seed_place('farm',cell,'h:0')
bh=e.seed_place('house',cell,'h:1'); bs=e.seed_place('granary',cell,'h:1'); bf=e.seed_place('farm',cell,'h:1')
seller=e.add_person(-30*365,cell,sh,household_id=0,role='farmer',name='Seller')
buyer=e.add_person(-30*365,cell,bh,household_id=1,role='farmer',name='Buyer')
e.farming.register(sf,sh,ss)
e.farming.assign(seller.id,sf)
# Real surplus begins in the seller's granary, not in the market or buyer inventory.
e.inventory.create(0,'grain',10000,f'b:{ss}','h:0',source='setup',process='fixture-surplus')
e.inventory.create(0,'coin',50,f'p:{buyer.id}','h:1',source='setup',process='fixture-coin')
# Fire destroys the buyer store and its food. The home survives so bought food has
# a real destination; the buyer's farm is also marked unavailable by the same fire.
e.inventory.burn(0,f'b:{bs}'); e.places[bs].destroyed_day=0; e.places[bf].destroyed_day=0
e.ledger.event(0,'burned',cell=cell,place=bs,building=bs)
e.ledger.event(0,'burned',cell=cell,place=bf,building=bf)
check(not any(l.holder==f'b:{bs}' and l.item=='grain' for l in e.inventory.lots.values()),'fire-hit household has no surviving local granary food')

# Move the seller to store, physically pick up a 30kg carry load, then both people
# walk to the market. We use normal daily agency decisions, one ordinary action/day.
def policy(packet):
    pid=packet['self']['id']; day=packet['day']; kinds={m['kind']:m for m in packet['menu']}
    if pid==seller.id:
        if day==0: return {'kind':'go','place':ss}
        if day==1: return {'kind':'haul','from':f'b:{ss}','to':f'p:{seller.id}'}
        if day==2: return {'kind':'go','place':market}
        if day==3:
            m=kinds.get('trade')
            if m:
                grain=next(x for x in m['offers'] if x['item']=='grain')
                return {'kind':'trade','recipient':buyer.id,'lot':grain['lot'],'qty':500}
        return {'kind':'rest'}
    if day==0: return {'kind':'go','place':market}
    return {'kind':'rest'}

e.agency.bind(seller.id,ScriptedPolicy(policy)); e.agency.bind(buyer.id,ScriptedPolicy(policy))
for _ in range(4): e.step()
check(any(x.type=='trade' for x in e.ledger.events),'seller and buyer settle a paid sale at the intact market')
check(e.inventory.holdings()[f'p:{buyer.id}']['grain']==440,'buyer physically carries the purchased grain after one daily meal')
check(e.inventory.holdings()[f'p:{seller.id}']['coin']==10,'seller receives catalog-priced coin rather than a promise or credit')
check(e.places[bs].destroyed_day==0 and e.places[bf].destroyed_day==0,'purchase does not undo fire damage or recreate productive capacity')
# Return home and prove the buyer can eat only after physically bringing the purchased
# lot there. A fresh buyer cannot access the seller's remote stock.
e.agency.unbind(seller.id); e.agency.unbind(buyer.id)
e.farming.begin(e.day)
check(e.farming.move(buyer.id,bh),'buyer walks from market to own home before relying on bought food')
demand=e.cfg.derived['consumption_units_per_day']; before=sum(l.qty for l in e.inventory.lots.values() if l.holder==f'p:{buyer.id}' and l.item=='grain')
ate=e.inventory.eat(e.day,[f'p:{buyer.id}'],e.people.owners(buyer),demand)
after=sum(l.qty for l in e.inventory.lots.values() if l.holder==f'p:{buyer.id}' and l.item=='grain')
check(ate==demand and before-after==demand,'purchased grain sustains fire-hit buyer through ordinary physical eating')
check(e.ledger.close_day(e.day,e.inventory.holdings())==[],'fire, hauling, market sale and consumption conserve every item')

out={'suite':'trade-survival','checks':len(checks),'failures':[],
     'scope':'Scripted physical integration; autonomous market participation remains future work.'}
(ROOT/'test-results/trade-survival.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
