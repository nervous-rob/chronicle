import sys, json, tempfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.agency import ScriptedPolicy
from chronicle.world import T
checks=[]
def check(x,msg): assert x,msg; checks.append(msg); print('ok',msg)
def fixture():
 e=Engine(load(overrides={'world.width_km':12,'world.height_km':8,'world.region_count':2,'weather.lightning_p':{s:0. for s in ('spring','summer','autumn','winter')}})); cell=int(np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))[0]); market=e.seed_place('market',cell,'h:0'); seller=e.add_person(-30*365,cell,market,household_id=0,name='Seller'); buyer=e.add_person(-30*365,cell,market,household_id=1,name='Buyer'); e.inventory.create(0,'grain',1000,f'p:{seller.id}','h:0',source='setup'); e.inventory.create(0,'coin',20,f'p:{buyer.id}','h:1',source='setup'); return e,seller,buyer,market
e,seller,buyer,market=fixture()
def sell(packet):
 if packet['self']['id']==seller.id:
  m=next(x for x in packet['menu'] if x['kind']=='trade'); return {'kind':'trade','recipient':buyer.id,'lot':m['offers'][0]['lot'],'qty':500}
 return {'kind':'rest'}
e.agency.bind(seller.id,ScriptedPolicy(sell)); e.agency.bind(buyer.id,ScriptedPolicy(sell)); e.step(); h=e.inventory.holdings()
check(h[f'p:{buyer.id}']['grain']==440 and h[f'p:{seller.id}']['coin']==10,'market sale swaps carried grain for catalog-priced coin')
check(h[f'p:{buyer.id}']['coin']==10 and h[f'p:{seller.id}']['grain']==439,'trade conserves both sides of the exchange')
tr=[x for x in e.ledger.events if x.type=='transfer' and x.payload.get('reason')=='trade']; te=[x for x in e.ledger.events if x.type=='trade']
check(len(tr)==len(te)==1 and te[0].payload['price']==10,'trade has physical transfer and semantic ledger event')
check(e.ledger.close_day(e.day-1,e.inventory.holdings())==[],'trade closes conservation exactly')
e,seller,buyer,market=fixture(); coin=next(l for l in e.inventory.lots.values() if l.item=='coin'); e.inventory.lots.pop(coin.id); e.farming.begin(0); packet=e.agency.observe(seller.id)
check(not any(x['kind']=='trade' for x in packet['menu']),'no buyer coin means no sale offer')
e,seller,buyer,market=fixture(); house=e.seed_place('house',seller.cell,'h:0'); seller.place=buyer.place=house; e.farming.begin(0); packet=e.agency.observe(seller.id)
check(not any(x['kind']=='trade' for x in packet['menu']),'trade is gated by an actual market place')
e,seller,buyer,market=fixture(); e.agency.bind(seller.id,ScriptedPolicy(lambda p:{'kind':'rest'})); e.step()
with tempfile.TemporaryDirectory() as td:
 path=Path(td)/'market.zip'; e.save_checkpoint(path); loaded=Engine.load_checkpoint(path,expected_config=e.cfg); check(loaded.digest()==e.digest(),'checkpoint round-trip preserves market inventory and trade-capable state')
(ROOT/'test-results/trade.json').write_text(json.dumps({'suite':'trade','checks':len(checks),'failures':[]},indent=2)); print(json.dumps({'suite':'trade','checks':len(checks),'failures':[]},indent=2))
