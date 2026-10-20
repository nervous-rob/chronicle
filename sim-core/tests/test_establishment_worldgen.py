"""Generated worlds include the catalogued establishment network, not merely its unit fixtures."""
import sys, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle import setup
from chronicle.world import T
checks=[]
def check(x,m):
    assert x,m; checks.append(m); print("ok",m,flush=True)
NOFIRE={s:0.0 for s in ("spring","summer","autumn","winter")}
ov={"run.seed":42,"population.initial":500,"weather.lightning_p":NOFIRE}
def build():
    e=Engine(load(overrides=ov)); r=setup.generate(e); return e,r
e,r=build(); e2,r2=build()
required={"hunters_cabin":"hunter","butcher_shop":"butcher","restaurant":"chef","iron_mine":"iron_miner","coal_mine":"coal_miner","salt_mine":"salt_miner","quarry":"quarrier","smithy":"smith"}
check(e.digest()==e2.digest() and r==r2,"establishment-bearing world generation is deterministic")
for kind,role in required.items():
    places=[b for b in e.places.values() if b.kind==kind and b.destroyed_day is None]
    check(places and r["establishments"].get(kind)==len(places),f"generated world seeds {kind} establishments")
    staffed=[row for row in e.employment.staffing.values() if row.role==role and row.place in {b.id for b in places}]
    check(staffed,f"generated {kind} has a durable {role} operator")
    for row in staffed:
        p=e.people.rows[row.worker]; b=e.places[row.place]
        check(b.owner in e.people.owners(p),f"{role} operator has lawful access to its {kind}")
for kind in ("iron_mine","coal_mine","salt_mine","quarry"):
    allowed={T[x] for x in e.cfg.building(kind)["terrain_allow"]}
    check(all(int(e.world.terrain[b.cell]) in allowed for b in e.places.values() if b.kind==kind),f"{kind} uses legal extractive terrain")
(ROOT/"test-results").mkdir(exist_ok=True)
(ROOT/"test-results"/"establishment-worldgen.json").write_text(json.dumps({"suite":"establishment-worldgen","checks":len(checks),"failures":[]},indent=2))
print(f"{len(checks)} checks passed")
