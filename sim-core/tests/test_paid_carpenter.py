"""Paid carpentry: employer project -> skilled contract labor -> durable building."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import ScriptedPolicy
checks=[]
def check(x,m): assert x,m;checks.append(m);print("ok",m,flush=True)
BASE={"world.width_km":12,"world.height_km":8,"world.region_count":2,"catalog.buildings.house.labor_days":2.0,"wildlife.raid_rate":0.,"weather.lightning_p":{s:0. for s in ("spring","summer","autumn","winter")}}
e=Engine(load(overrides=BASE)); cell=int(np.flatnonzero(e.world.mainland()&(e.world.terrain==T["plains"]))[0])
shop=e.seed_place("workshop",cell,"h:0"); employer=e.add_person(-30*365,cell,shop,household_id=0,name="Builder")
carpenter=e.add_person(-25*365,cell,shop,household_id=1,name="Carpenter",aptitudes={"craft":2.0})
e.inventory.create(0,"timber",4,f"b:{shop}","h:0",source="setup");e.inventory.create(0,"coin",9,f"b:{shop}","h:0",source="setup")
def one(person,action):
 e.agency.bind(person.id,ScriptedPolicy(lambda packet:action,"test"));e.step();e.agency.unbind(person.id)
one(employer,{"kind":"project","project":"build","building":"house"})
project=next(iter(e.projects.projects.values()))
check(project.owner=="h:0" and project.inputs=={"timber":4},"employer reserves physical timber for building")
job=e.employment.post(shop,employer.id,wage=9,activity="project",project=project.id)
one(carpenter,{"kind":"labor","job":job.id})
check(not e.projects.projects and len([b for b in e.places.values() if b.kind=="house"])==1,"paid carpenter completes employer building")
coins=lambda holder:sum(l.qty for l in e.inventory.lots.values() if l.holder==holder and l.item=="coin")
check(coins(f"b:{shop}")==0 and coins(f"p:{carpenter.id}")==9,"wage moves only from workplace till to carpenter")
worked=[x for x in e.ledger.events if x.type=="project_worked"][-1]
check(worked.payload["paid"] and worked.payload["effective_hours"]==8.0,"craft aptitude changes output without minting time or material")
check(any(x.type=="paid" and x.payload["project"]==project.id for x in e.ledger.events),"contract wage is ledgered against its project")
print(f"{len(checks)} checks passed")
