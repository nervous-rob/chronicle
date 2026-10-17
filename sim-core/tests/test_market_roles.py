"""Designated market roles persist through a physical grocery transaction."""
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
    assert x,msg; checks.append(msg); print("ok",msg)
NOFIRE={s:0.0 for s in ("spring","summer","autumn","winter")}
cfg=load(overrides={"world.width_km":12,"world.height_km":8,"world.region_count":2,"weather.lightning_p":NOFIRE,"wildlife.raid_rate":0.0})
e=Engine(cfg); cell=int(np.flatnonzero(e.world.mainland() & (e.world.terrain==T["plains"]))[0])
market_cell=next(int(x) for x in e.world.neighbors(cell) if x >= 0 and e.world.mainland()[x] and e.world.terrain[x] == T["plains"])
market=e.seed_place("market",market_cell,None)
sh=e.seed_place("house",cell,"h:0"); ss=e.seed_place("granary",cell,"h:0"); sf=e.seed_place("farm",cell,"h:0")
bh=e.seed_place("house",cell,"h:1")
seller=e.add_person(-30*365,cell,sh,household_id=0,role="farmer",name="Seller",capabilities=["planning"])
shopper=e.add_person(-30*365,cell,bh,household_id=1,role="merchant",name="Shopper",capabilities=["planning"])
e.farming.register(sf,sh,ss); e.farming.assign(seller.id,sf)
seller.known_cells[market_cell]={"day":0,"source":"fixture-market"}; shopper.known_cells[market_cell]={"day":0,"source":"fixture-market"}
e.inventory.create(0,"grain",10000,f"b:{ss}","h:0",source="setup",process="fixture-surplus")
e.inventory.create(0,"coin",100,f"p:{shopper.id}","h:1",source="setup",process="fixture-coin")
e.agency.bind(seller.id,MarketPolicy(carry_offer_units=500,local_surplus_units=1000,mode="seller"))
e.agency.bind(shopper.id,MarketPolicy(grocery_units=420,mode="shopper"))
for _ in range(24): e.step()
trades=[x for x in e.ledger.events if x.type=="trade" and shopper.id in x.actors]
check(trades,"designated seller and shopper complete a paid exchange at a known off-home public market")
check(any(r["source"].startswith("plan:market-routine") for r in e.agency.log),"market roles use durable ordinary plans rather than daily pass-through")
check(any(r["action"].get("kind")=="rest" and r["person"]==seller.id and r["source"]=="market-routine" for r in e.agency.log),"seller waits at the physical market when no buyer is co-present")
home_food=sum(l.qty for l in e.inventory.lots.values() if l.holder==f"b:{bh}" and l.owner=="h:1" and l.item=="grain")
check(home_food>0,"shopper returns purchased food to the household store")
check(e.ledger.close_day(e.day,e.inventory.holdings())==[],"role plans and market transfer conserve inventory")
out={"suite":"market-roles","checks":len(checks),"failures":[]}
(ROOT/"test-results/market-roles.json").write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
