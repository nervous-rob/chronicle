"""A visible local job offer makes its remote physical workplace travelable."""
import json, sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.wages import WagePolicy
checks=[]
def check(x,m): assert x,m; checks.append(m); print('ok',m)
NOFIRE={s:0.0 for s in ('spring','summer','autumn','winter')}
cfg=load(overrides={'world.width_km':12,'world.height_km':8,'world.region_count':2,'weather.lightning_p':NOFIRE,'wildlife.raid_rate':0.0})
e=Engine(cfg)
home_cell=int(np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))[0])
work_cell=next(int(x) for x in e.world.neighbors(home_cell) if x>=0 and e.world.mainland()[x] and e.world.terrain[x]==T['plains'])
home=e.seed_place('house',home_cell,'h:1'); boss_home=e.seed_place('house',work_cell,'h:0'); workshop=e.seed_place('workshop',work_cell,'h:0')
boss=e.add_person(-35*365,work_cell,boss_home,household_id=0,role='crafter',name='Boss')
worker=e.add_person(-27*365,home_cell,home,household_id=1,role='merchant',name='Worker',capabilities=['planning'])
worker.known_cells[work_cell]={'day':0,'source':'fixture'}
e.inventory.create(0,'coin',10,f'b:{workshop}','h:0',source='setup',process='fixture-till')
e.employment.post(workshop,boss.id,wage=10,slots=1)
e.agency.bind(worker.id,WagePolicy(reserve_coin=10))
for _ in range(12): e.step()
paid=[x for x in e.ledger.events if x.type=='paid' and worker.id in x.actors]
check(paid,'worker reaches a known off-home workplace and earns its listed wage')
check(any(x.type=='arrived' and x.place==workshop and worker.id in x.actors for x in e.ledger.events),'job offer supplies a legal workplace destination')
check(sum(l.qty for l in e.inventory.lots.values() if l.holder==f'b:{home}' and l.item=='coin')==10,'worker returns earned coin to the household store')
check(e.ledger.close_day(e.day,e.inventory.holdings())==[],'remote work travel and wage transfer conserve inventory')
out={'suite':'remote-wage','checks':len(checks),'failures':[]}; (ROOT/'test-results/remote-wage.json').write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
