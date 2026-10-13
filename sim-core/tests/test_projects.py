"""world-project-v1: clear -> extract timber -> haul/store -> build/repair; durable and checkpointed."""
import sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import ScriptedPolicy
checks=[]
def check(x,m): assert x,m;checks.append(m);print('ok',m,flush=True)
BASE={'world.width_km':12,'world.height_km':8,'world.region_count':2,'wildlife.raid_rate':0.,'weather.lightning_p':{s:0. for s in ('spring','summer','autumn','winter')},'weather.rain_mm_mean':{s:0. for s in ('spring','summer','autumn','winter')},'weather.evaporation_mm':{s:0. for s in ('spring','summer','autumn','winter')}}
def fixture():
 e=Engine(load(overrides=BASE)); land=np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains']));c=int(land[0]);home=e.seed_place('house',c,'h:0');store=e.seed_place('granary',c,'h:0');p=e.add_person(-30*365,c,home,household_id=0,name='Ada');e.inventory.create(0,'grain',500,f'b:{home}','h:0',source='setup');return e,p,home,store
def one(e,p,a):
 e.agency.bind(p.id,ScriptedPolicy(lambda pk:a,'p'));e.step();e.agency.unbind(p.id)
e,p,home,store=fixture()
# clearing is durable terrain change, and the start/work/completion are witnessed ledger history.
one(e,p,{'kind':'project','project':'clear'})
check(e.cells.cleared[p.cell]>.24,'clear project changes the cell persistently')
check([x.type for x in e.ledger.events if x.type.startswith('project_')]==['project_started','project_worked','project_completed'],'clear project has durable public history')
# extraction puts physical timber on this exact place; it must be hauled through the person into storage.
one(e,p,{'kind':'project','project':'extract','item':'timber'})
timber=[l for l in e.inventory.lots.values() if l.item=='timber'];check(len(timber)==1 and timber[0].holder==f'c:{p.cell}' and timber[0].ground_place==home,'extract creates timber as a ground lot at the work place')
# haul through the person: ground -> carry -> store, through the same agency verb used for food.
one(e,p,{'kind':'haul','from':f'c:{p.cell}','to':f'p:{p.id}'})
p.place=store
one(e,p,{'kind':'haul','from':f'p:{p.id}','to':f'b:{store}'})
# the catalog house needs timber, so start+finish a build from stored material by standing in the store.
p.place=store
one(e,p,{'kind':'project','project':'build','building':'house'})
for _ in range(45):
 one(e,p,{'kind':'project','id':2})
built=[b for b in e.places.values() if b.kind=='house' and b.id!=home];check(len(built)==1,'catalog material requirement is consumed to build a durable new place')
check(not [l for l in e.inventory.lots.values() if l.item=='timber'],'timber was consumed through the ledger build sink')
# ruined structure repair uses same grammar and remains resumable.
b=built[0];b.destroyed_day=e.day;p.place=store
e.cfg # keep fixture clear
# Give one timber back as a physical material and begin repair, then checkpoint.
e.inventory.create(e.day,'timber',1,f'b:{store}','h:0',source='production',process='test')
one(e,p,{'kind':'project','project':'repair','target_place':b.id,'building':'house'})
check(e.projects.projects,'repair is durable when unfinished')
with tempfile.TemporaryDirectory() as d:
 path=Path(d)/'p.zip';e.save_checkpoint(path);r=Engine.load_checkpoint(path,expected_config=e.cfg)
 check(r.digest()==e.digest(),'checkpoint preserves in-progress world projects exactly')
print(f'{len(checks)} checks passed')
