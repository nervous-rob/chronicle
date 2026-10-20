"""Data-driven mines/quarry -> recipes -> durable stone construction."""
import sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
import numpy as np
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import ScriptedPolicy
from chronicle.inventory import Leg
checks=[]
def check(x,m): assert x,m; checks.append(m); print("ok",m,flush=True)
BASE={"world.width_km":12,"world.height_km":8,"world.region_count":2,"wildlife.raid_rate":0.,"weather.lightning_p":{s:0. for s in ("spring","summer","autumn","winter")},"weather.rain_mm_mean":{s:0. for s in ("spring","summer","autumn","winter")},"weather.evaporation_mm":{s:0. for s in ("spring","summer","autumn","winter")},"catalog.items.meal.spoil_per_day":0.,"catalog.buildings.stone_house.labor_days":2.0}
def turn(e,p,a):
 e.agency.bind(p.id,ScriptedPolicy(lambda packet:a,"extract-test")); e.step(); e.agency.unbind(p.id)
def qty(e,h,item,owner=None): return sum(x.qty for x in e.inventory.lots.values() if x.holder==h and x.item==item and (owner is None or x.owner==owner))
def move_lot(e,lot,to,owner,actors,cell,place,reason): e.inventory.transfer(e.day,[Leg(lot.id,lot.qty,to,owner)],actors=actors,cell=cell,place=place,reason=reason)
def extract(e,p,item): turn(e,p,{"kind":"project","project":"extract","item":item})
# Seed every workplace during setup. Then extractors use only their own legal site.
e=Engine(load(overrides=BASE)); hills=np.flatnonzero(e.world.mainland() & np.isin(e.world.terrain,[T["hills"],T["mountains"]])); plains=np.flatnonzero(e.world.mainland() & (e.world.terrain==T["plains"])); assert len(hills) and len(plains)
hill,plain=int(hills[0]),int(plains[0])
iron=e.seed_place("iron_mine",hill,"h:0"); coal=e.seed_place("coal_mine",hill,"h:1"); salt=e.seed_place("salt_mine",hill,"h:2"); quarry=e.seed_place("quarry",hill,"h:3")
smithy=e.seed_place("smithy",plain,"h:4"); butcher=e.seed_place("butcher_shop",plain,"h:5"); restaurant=e.seed_place("restaurant",plain,"h:6"); store=e.seed_place("warehouse",plain,"h:3")
extractors={}
for hid,(place,item,amount) in enumerate(((iron,"iron_ore",10),(coal,"coal",12),(salt,"salt",8),(quarry,"stone",8))):
 p=e.add_person(-30*365,hill,place,household_id=hid,name=item+" worker"); extractors[place]=p; extract(e,p,item)
 check(qty(e,f"c:{hill}",item)>=amount,"physical "+item+" extraction follows workplace catalog data")
 lot=next(x for x in e.inventory.lots.values() if x.holder==f"c:{hill}" and x.ground_place==place and x.item==item)
 move_lot(e,lot,f"b:{place}",f"h:{hid}",(p.id,),hill,place,"supply-haul")
 check(qty(e,f"b:{place}",item,f"h:{hid}")==amount,"extracted "+item+" enters establishment stock only by custody transfer")
smith=e.add_person(-30*365,plain,smithy,household_id=4,name="Smith"); smith_hand=e.add_person(-28*365,plain,smithy,household_id=7,name="Smith hand")
butcher_owner=e.add_person(-30*365,plain,butcher,household_id=5,name="Butcher"); butcher_hand=e.add_person(-28*365,plain,butcher,household_id=8,name="Butcher hand")
chef=e.add_person(-30*365,plain,restaurant,household_id=6,name="Chef"); chef_hand=e.add_person(-28*365,plain,restaurant,household_id=9,name="Chef hand")
# Supplier goods are transferred into the smithy; the paid order transforms them.
for source,item,n in ((iron,"iron_ore",2),(coal,"coal",1)):
 lot=next(x for x in e.inventory.lots.values() if x.holder==f"b:{source}" and x.item==item)
 e.inventory.transfer(e.day,[Leg(lot.id,n,f"b:{smithy}","h:4")],actors=(extractors[source].id,smith.id),cell=plain,place=smithy,reason="supply-sale")
chef.place=smithy; e.inventory.create(e.day,"coin",9,f"p:{chef.id}","h:6",source="setup")
o=e.employment.commission_recipe(chef.id,smith.id,smithy,"smith_tools",9,e.day+10); j=e.employment.post_order_shift(o.id,9); turn(e,smith_hand,{"kind":"labor","job":j.id}); e.employment.collect_order(chef.id,o.id)
check(o.status=="complete" and qty(e,f"p:{chef.id}","tools","h:6")==1,"iron ore plus coal become customer-owned tools through a paid smithy shift")
# Salt preservation closes mine -> butcher -> restaurant -> diner.
e.inventory.create(e.day,"meat",10,f"b:{butcher}","h:5",source="setup")
sl=next(x for x in e.inventory.lots.values() if x.holder==f"b:{salt}" and x.item=="salt"); e.inventory.transfer(e.day,[Leg(sl.id,1,f"b:{butcher}","h:5")],actors=(extractors[salt].id,butcher_owner.id),cell=plain,place=butcher,reason="supply-sale")
chef.place=butcher; e.inventory.create(e.day,"coin",9,f"p:{chef.id}","h:6",source="setup")
o=e.employment.commission_recipe(chef.id,butcher_owner.id,butcher,"butcher_preserve",9,e.day+10); j=e.employment.post_order_shift(o.id,9); turn(e,butcher_hand,{"kind":"labor","job":j.id}); e.employment.collect_order(chef.id,o.id)
check(o.status=="complete" and qty(e,f"p:{chef.id}","preserved_meat","h:6")==9,"salt is physically consumed in butcher preservation")
for lot in list(e.inventory.lots.values()):
 if lot.holder==f"p:{chef.id}" and lot.item=="preserved_meat": move_lot(e,lot,f"b:{restaurant}","h:6",(chef.id,),plain,restaurant,"restaurant-stock")
diner=e.add_person(-24*365,plain,restaurant,household_id=10,name="Diner"); chef.place=restaurant; e.inventory.create(e.day,"coin",9,f"p:{diner.id}","h:10",source="setup")
o=e.employment.commission_recipe(diner.id,chef.id,restaurant,"restaurant_preserved_meal",9,e.day+10); j=e.employment.post_order_shift(o.id,9)
while o.status=="open": turn(e,chef_hand,{"kind":"labor","job":j.id})
e.employment.collect_order(diner.id,o.id)
check(o.status=="complete" and qty(e,f"p:{diner.id}","meal","h:10")==3,"preserved meat reaches diner as restaurant meals")
# Quarry stone is physically supplied to a construction site. The completed stone house
# survives the actual fire resolver while the timber house in the same cell burns.
mason=e.add_person(-30*365,plain,store,household_id=3,name="Mason")
qlot=next(x for x in e.inventory.lots.values() if x.holder==f"b:{quarry}" and x.item=="stone"); e.inventory.transfer(e.day,[Leg(qlot.id,6,f"b:{store}","h:3")],actors=(extractors[quarry].id,mason.id),cell=plain,place=store,reason="stone-haul")
e.inventory.create(e.day,"timber",1,f"b:{store}","h:3",source="setup"); turn(e,mason,{"kind":"project","project":"build","building":"stone_house"})
for _ in range(12):
 if not e.projects.projects: break
 turn(e,mason,{"kind":"project","id":next(iter(e.projects.projects))})
stone_house=next(x for x in e.places.values() if x.kind=="stone_house"); wood=e.create_place("house",plain,"h:11")
e.cells.burning[plain]=True; e.cells.burn_hour[plain]=0.; e._burn_stores()
check(e.places[wood].destroyed_day==e.day and e.places[stone_house.id].destroyed_day is None,"stone construction has real fire resistance in the building fire resolver")
e.step()
from chronicle.checkpoint import save, load as load_checkpoint
with tempfile.TemporaryDirectory() as td:
 path=Path(td)/"extract.zip"; save(e,path); r=load_checkpoint(path); check(r.digest()==e.digest(),"extractive establishments survive checkpoint/reload")
print(f"{len(checks)} checks passed")
