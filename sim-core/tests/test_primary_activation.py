"""Generated primary producers autonomously extract and stock their workplace."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
import numpy as np
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import EstablishmentPolicy
checks=[]
def check(x,m): assert x,m; checks.append(m); print("ok",m,flush=True)
BASE={"world.width_km":12,"world.height_km":8,"world.region_count":2,"wildlife.raid_rate":0.,"weather.lightning_p":{s:0. for s in ("spring","summer","autumn","winter")}}
e=Engine(load(overrides=BASE)); cell=int(np.flatnonzero(e.world.mainland() & np.isin(e.world.terrain,[T["hills"],T["mountains"]]))[0])
for n,(kind,item) in enumerate((("iron_mine","iron_ore"),("coal_mine","coal"),("salt_mine","salt"),("quarry","stone"))):
    place=e.seed_place(kind,cell,f"h:{n}")
    p=e.add_person(-30*365,cell,place,household_id=n,name=kind)
    e.agency.bind(p.id,EstablishmentPolicy("producer",establishment=place,products=(item,)))
for _ in range(3): e.step()
for n,(_kind,item) in enumerate((("iron_mine","iron_ore"),("coal_mine","coal"),("salt_mine","salt"),("quarry","stone"))):
    total=sum(l.qty for l in e.inventory.lots.values() if l.holder==f"b:{n}" and l.item==item)
    check(total>0,item+" is autonomously extracted then hauled into its own workplace stock")
check(any(r["source"]=="establishment-routine" for r in e.agency.log),"activation uses ordinary agency decisions")
print(f"{len(checks)} checks passed")
