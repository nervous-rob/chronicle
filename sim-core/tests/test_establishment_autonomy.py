"""Local policy loop: customer -> order -> shift -> pickup, without test-side queue calls."""
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
      "wildlife.raid_rate":0.,"catalog.items.meal.spoil_per_day":0.,
      "weather.lightning_p":{s:0. for s in ("spring","summer","autumn","winter")}}
e=Engine(load(overrides=BASE))
cell=int(np.flatnonzero(e.world.mainland() & (e.world.terrain==T["plains"]))[0])
restaurant=e.seed_place("restaurant",cell,"h:0")
chef=e.add_person(-30*365,cell,restaurant,household_id=0,name="Chef")
hand=e.add_person(-25*365,cell,restaurant,household_id=1,name="Kitchen hand")
diner=e.add_person(-24*365,cell,restaurant,household_id=2,name="Diner")
e.inventory.create(0,"butchered_meat",3,f"b:{restaurant}","h:0",source="setup")
e.inventory.create(0,"coin",1,f"p:{diner.id}","h:2",source="setup")
# A generated customer starts with household coin, not a magical purse.
# Create this home during setup; seed_place is deliberately setup-only.
home=e.seed_place("workshop",cell,"h:9")
remote=e.add_person(-24*365,cell,home,household_id=9,name="Remote diner")
e.inventory.create(0,"coin",1,f"b:{home}","h:9",source="setup")
e.agency.bind(remote.id,EstablishmentPolicy("customer",recipes=("restaurant_meal",),price=1,establishment=restaurant))
e.agency.bind(diner.id,EstablishmentPolicy("customer",recipes=("restaurant_meal",),price=1))
e.agency.bind(chef.id,EstablishmentPolicy("operator",wage=1))
e.agency.bind(hand.id,EstablishmentPolicy("worker"))
# The remote customer independently loads its household-held float while the
# local restaurant queue follows its normal day-0 through day-3 cadence.
for _ in range(4): e.step()
check(sum(l.qty for l in e.inventory.lots.values() if l.holder==f"p:{remote.id}" and l.item=="coin")==1,"remote customer physically loads the stated meal price from home")
e.agency.unbind(remote.id)
orders=list(e.employment.work_orders.values())
check(len(orders)==1 and orders[0].status=="complete" and orders[0].recipe=="restaurant_meal","local autonomous roles close the restaurant order without test-side employment calls")
pickup=[ev for ev in e.ledger.events if ev.type=="transfer" and ev.payload.get("reason")=="work-order-pickup"]
check(len(pickup)==1 and pickup[0].payload["legs"][0]["qty"]==3 and pickup[0].payload["legs"][0]["to"]==f"p:{diner.id}","customer receives three physical prepared meals through the autonomous queue before ordinary daily consumption")
check(sum(l.qty for l in e.inventory.lots.values() if l.holder==f"p:{hand.id}" and l.item=="coin")==1,"order payment funds an actual worker wage")
check(not e.employment.jobs,"completed autonomous order removes its stale shift")
# Policies are runtime-only, while durable order/custody state remains checkpointable.
e.agency.unbind(diner.id); e.agency.unbind(chef.id); e.agency.unbind(hand.id)
e.step()
from chronicle.checkpoint import save, load as load_checkpoint
with tempfile.TemporaryDirectory() as td:
 path=Path(td)/"autonomy.zip"; save(e,path); r=load_checkpoint(path)
 check(r.digest()==e.digest() and r.employment.work_orders[0].status=="complete","autonomous establishment result survives checkpoint/reload")

print(f"{len(checks)} checks passed")
