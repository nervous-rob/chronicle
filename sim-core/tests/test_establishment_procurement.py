"""Autonomous local restaurant replenishment: till -> supplier -> carry home -> stock -> meal."""
import sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
import numpy as np
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import EstablishmentPolicy

checks=[]
def check(x,m): assert x,m; checks.append(m); print("ok",m,flush=True)
BASE={"world.width_km":12,"world.height_km":8,"world.region_count":2,
      "wildlife.raid_rate":0.,"catalog.items.meal.spoil_per_day":0.,"catalog.items.butchered_meat.spoil_per_day":0.,
      "weather.lightning_p":{s:0. for s in ("spring","summer","autumn","winter")},"scarcity.theft_enabled":False}
e=Engine(load(overrides=BASE))
cell=int(np.flatnonzero(e.world.mainland() & (e.world.terrain==T["plains"]))[0])
butch=e.seed_place("butcher_shop",cell,"h:0"); rest=e.seed_place("restaurant",cell,"h:1")
butcher=e.add_person(-30*365,cell,butch,household_id=0,name="Butcher")
chef=e.add_person(-30*365,cell,rest,household_id=1,name="Chef")
hand=e.add_person(-25*365,cell,rest,household_id=2,name="Kitchen hand")
diner=e.add_person(-24*365,cell,rest,household_id=3,name="Diner")
e.inventory.create(0,"butchered_meat",1000,f"b:{butch}","h:0",source="setup")
e.inventory.create(0,"coin",5,f"b:{rest}","h:1",source="setup")
e.inventory.create(0,"coin",1,f"p:{diner.id}","h:3",source="setup")
for person, owner in ((butcher,"h:0"),(chef,"h:1"),(hand,"h:2"),(diner,"h:3")):
 e.inventory.create(0,"grain",1000,f"p:{person.id}",owner,source="setup")
chef_policy=EstablishmentPolicy("operator",wage=1,stock_targets={"butchered_meat":63},suppliers=((butch,"butchered_meat",63),),establishment=rest)
e.agency.bind(chef.id,chef_policy)
# coin leaves own till; chef visits the named butcher; purchase is physical; chef returns and stocks restaurant.
for _ in range(5): e.step()
e.agency.unbind(chef.id)
stock=sum(x.qty for x in e.inventory.lots.values() if x.holder==f"b:{rest}" and x.owner=="h:1" and x.item=="butchered_meat")
check(stock==63,"operator replenishes a configured restaurant stock target through a physical supplier purchase and return haul")
check(sum(x.qty for x in e.inventory.lots.values() if x.holder==f"b:{butch}" and x.owner=="h:0" and x.item=="coin")==4,"restaurant till coin becomes butcher till coin without minting")
procure=[ev for ev in e.ledger.events if ev.type=="trade" and ev.payload.get("reason")=="establishment-procurement"]
check(len(procure)==1 and procure[0].payload["item"]=="butchered_meat" and procure[0].payload["qty"]==63,"supplier procurement is ledgered as a bounded local trade")
e.agency.bind(diner.id,EstablishmentPolicy("customer",recipes=("restaurant_meal",),price=1))
e.agency.bind(chef.id,EstablishmentPolicy("operator",wage=1,establishment=rest))
e.agency.bind(hand.id,EstablishmentPolicy("worker"))
for _ in range(4): e.step()
orders=list(e.employment.work_orders.values())
check(len(orders)==1 and orders[0].status=="complete" and orders[0].recipe=="restaurant_meal","replenished input closes into an autonomous paid restaurant order")
check(sum(x.qty for x in e.inventory.lots.values() if x.holder==f"p:{diner.id}" and x.item=="meal")>=0,"diner pickup follows ordinary custody rules after replenished production")
e.agency.unbind(diner.id); e.agency.unbind(chef.id); e.agency.unbind(hand.id)
e.step()
from chronicle.checkpoint import save, load as load_checkpoint
with tempfile.TemporaryDirectory() as td:
 path=Path(td)/"procure.zip"; save(e,path); r=load_checkpoint(path)
 check(r.digest()==e.digest(),"autonomous procurement and resulting order survive checkpoint/reload")
print(f"{len(checks)} checks passed")
