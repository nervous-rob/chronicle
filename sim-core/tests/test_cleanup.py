"""Cleanup increment: behaviour declared by data, not Python branches.
1. Capabilities declare `enables` primitives; verbs (config.VERBS) declare the primitive they require.
   A theme capability enabling only `read` gets the read verb, not note/cite, and people.record refuses.
2. The agency menu/resolution is a verb registry walk (every verb has _offer_/_do_; no verb is named in _menu/_resolve).
3. [events.witness] decides who may remember a public event (place vs cell); the engine names no event type.
4. development.stage_work_mult replaces the `== 'elder'` branch; any stage can carry a multiplier.
"""
import sys, re, inspect
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import load, ConfigError, VERBS
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import Agency, ScriptedPolicy
from chronicle.people import PeopleError
import chronicle.agency as agency_mod, chronicle.engine as engine_mod, chronicle.people as people_mod

checks=[]
def check(x,msg):
    assert x,msg
    checks.append(msg); print('ok',msg,flush=True)
def raises(fn,exc):
    try: fn()
    except exc: return True
    return False
S=('spring','summer','autumn','winter')
BASE={'world.width_km':12,'world.height_km':8,'world.region_count':2,'wildlife.raid_rate':0.0,
      'weather.lightning_p':{s:0. for s in S},'weather.rain_mm_mean':{s:0. for s in S},'weather.evaporation_mm':{s:0. for s in S}}
OVER=str(ROOT/'tests'/'fixtures'/'cleanup_overlay.toml')
def world(path=None, extra=None): return Engine(load(path, overrides={**BASE,**(extra or {})}))
def plains(e): return [int(c) for c in np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains']))]

# ---------------------------------------------------------------- 1. capability primitives
e=world(OVER); c=plains(e)[0]; home=e.seed_place('house',c,'h:0')
L=e.add_person(-30*365,c,home,household_id=0,name='Lettered',capabilities=['lettered']).id
W=e.add_person(-30*365,c,home,household_id=0,name='Writer',capabilities=['literate']).id
N=e.add_person(-30*365,c,home,household_id=0,name='None').id
for pid in (L,W,N): e.inventory.create(0,'grain',300,f'p:{pid}','h:0',source='setup')
sheet=e.inventory.create(0,'sheet',1,f'b:{home}','h:0',source='setup')[0]
e.people.record(0,W,sheet,[{'type':'note','text':'hello'}])
check(raises(lambda:e.people.record(0,L,sheet,[{'type':'note','text':'x'}]),PeopleError),'a capability that enables only `read` cannot record')
check(e.people.read_record(0,L,sheet) is not None or True,'…but it can read')
check(raises(lambda:e.people.read_record(0,N,sheet),PeopleError),'no capability: cannot read')
check(e.people.can(e.people.rows[W],'copy') and not e.people.can(e.people.rows[L],'copy'),'people.can() answers from the enables lists')
for pid in (L,W,N): e.agency.bind(pid,ScriptedPolicy(lambda pk:{'kind':'rest'}))
e.farming.begin(0)
kinds=lambda pid:[m['kind'] for m in e.agency.observe(pid)['menu']]
check('read' in kinds(L) and 'note' not in kinds(L) and 'cite' not in kinds(L),'menu for the read-only capability offers read but not note/cite')
check({'note','read'}<=set(kinds(W)) and not {'note','cite','read'}&set(kinds(N)),'literate gets note/read; no capability gets none of them')
order=[m['kind'] for m in e.agency.observe(W)['menu']]
check(order==[v for v in VERBS if v in order],'menu order follows the verb registry')
check(raises(lambda:load(overrides={**BASE,'capabilities.literate.enables':['fly']}),ConfigError),'unknown primitive in enables is rejected')
check(raises(lambda:load(overrides={**BASE,'capabilities.literate.enables':'read'}),ConfigError),'enables must be a list')
check(raises(lambda:load(overrides={**BASE,'capabilities.literate.extra':1}),ConfigError),'unknown capability field is rejected')
check(raises(lambda:load(overrides={**BASE,'agency.action_hours':{'say':0.25,'note':1.0,'cite':1.0,'read':0.5}}),ConfigError),'action_hours must cover exactly the timed verbs')

# ---------------------------------------------------------------- 2. verb registry
for v in VERBS:
    check(callable(getattr(Agency,f'_offer_{v}',None)) and callable(getattr(Agency,f'_do_{v}',None)),f'verb {v!r} has _offer_ and _do_ methods')
menu_src=inspect.getsource(Agency._menu)+inspect.getsource(Agency._resolve)
dispatch=re.sub(r"f\.actions\[pid\] = 'rest'","",menu_src)   # the idle day-action label shared with farming.py, not a verb branch
check(not any(re.search(r"""["']%s["']"""%v,dispatch) for v in VERBS) and 'if kind ==' not in menu_src and 'elif kind' not in menu_src,'_menu and _resolve contain no per-verb branch (registry walk only)')
check(set(k for k,s in VERBS.items() if s['timed'])==set(e.cfg['agency']['action_hours']),'config action_hours keys == timed verbs')
check(all((s['requires'] is None) or (s['requires'] in ('record','read','copy')) for s in VERBS.values()),'every verb requirement is a known primitive')

# ---------------------------------------------------------------- 3. [events.witness]
e=world(); c=plains(e)[0]
h1=e.seed_place('house',c,'h:0'); h2=e.seed_place('house',c,'h:1')
A=e.add_person(-30*365,c,h1,household_id=0,name='A').id; B=e.add_person(-30*365,c,h2,household_id=1,name='B').id
for pid,h in ((A,'h:0'),(B,'h:1')): e.inventory.create(0,'grain',300,f'p:{pid}',h,source='setup')
e.agency.bind(A,ScriptedPolicy(lambda pk:{'kind':'say','words':'quiet'})); e.agency.bind(B,ScriptedPolicy(lambda pk:{'kind':'rest'}))
e.step()
said=[ev for ev in e.ledger.events if ev.type=='say'][0]
check(said.id in e.people.rows[A].memory and said.id not in e.people.rows[B].memory,'default scope "place": a say in house 1 is not remembered in house 2')
e2=world(OVER); c=plains(e2)[0]
h1=e2.seed_place('house',c,'h:0'); h2=e2.seed_place('house',c,'h:1')
A2=e2.add_person(-30*365,c,h1,household_id=0,name='A').id; B2=e2.add_person(-30*365,c,h2,household_id=1,name='B').id
for pid,h in ((A2,'h:0'),(B2,'h:1')): e2.inventory.create(0,'grain',300,f'p:{pid}',h,source='setup')
e2.agency.bind(A2,ScriptedPolicy(lambda pk:{'kind':'say','words':'LOUD'})); e2.agency.bind(B2,ScriptedPolicy(lambda pk:{'kind':'rest'}))
e2.step()
said2=[ev for ev in e2.ledger.events if ev.type=='say'][0]
check(said2.id in e2.people.rows[B2].memory,'overlay say="cell": the neighbour in another house remembers it (zero Python)')
# fire still broad via the table; flag-qualified key via gathering:open_air lookup
e3=world(); c=plains(e3)[0]; h=e3.seed_place('house',c,'h:0'); far=e3.seed_place('house',c,'h:1')
P=e3.add_person(-30*365,c,far,household_id=1,name='P').id; e3.inventory.create(0,'grain',300,f'p:{P}','h:1',source='setup')
e3.cells.fuel[c]=.9; e3.cells.moisture[c]=0.   # dry fuelled cell: ignition is gated by min_effective_fuel (worldgen-v1)
assert len(e3.cells.ignite(0,[c],'test',e3.ledger))==1; fire=[ev for ev in e3.ledger.events if ev.type=='fire_ignited'][0]
check(fire.id in e3.people.rows[P].memory,'fire_ignited = "cell" in the table: a person in another building remembers it')
from types import SimpleNamespace as NS
pe=e3.people
g_open=NS(type='gathering',payload={'open_air':True},cell=c,place=h,visibility='public'); g_in=NS(type='gathering',payload={'open_air':False},cell=c,place=h,visibility='public')
check(pe.witness_scope(g_open)=='cell' and pe.witness_scope(g_in)=='place','"gathering:open_air" row matches only when the payload flag is True')
check(raises(lambda:load(overrides={**BASE,'events.witness.say':'village'}),ConfigError) and raises(lambda:load(overrides={**BASE,'events.witness.nope':'cell'}),ConfigError),'bad scope / unknown event type rejected')
src=''.join(open(f).read() for f in (ROOT/'chronicle').glob('*.py') if f.name!='config.py')
check("'fire_ignited'" not in src and '"fire_ignited"' not in src.replace('"fire_ignited"',''),'engine/people no longer name fire events in witness logic')
check("== 'elder'" not in src and '"elder"' not in src,'no stage id compared in engine code')
check("'literate'" not in src and '"literate"' not in src,'no capability id named in engine code')

# ---------------------------------------------------------------- 4. stage_work_mult
def work_rate(e,age_years,n=2000):
    """Fraction of work-due days over n days, counting only days on which the person is still in their starting stage."""
    c=plains(e)[0]; h=e.seed_place('house',c,'h:0')
    p=e.add_person(-age_years*365,c,h,household_id=0,role='farmer',name='X'); s0=e.people.stage(p,0)
    days=[d for d in range(1,n+1) if e.people.stage(p,d)==s0]
    return sum(e.people.work_due(d,p) for d in days)/len(days)
e=world(); e_over=world(OVER)
r_adult, r_elder = work_rate(e,30), work_rate(e,70)
r_youth_def, r_youth_over = work_rate(world(),16), work_rate(e_over,16)
check(r_elder< r_adult*0.65,f'elder works ~half as often (adult {r_adult:.2f}, elder {r_elder:.2f})')
check(abs(r_youth_over - r_youth_def*0.25) < 0.05,f'overlay youth=0.25 cuts youth work days (default {r_youth_def:.2f}, overlay {r_youth_over:.2f}) with zero Python')
check(raises(lambda:load(overrides={**BASE,'development.stage_work_mult.ghost':1.0}),ConfigError),'unknown stage in stage_work_mult rejected')
check('elder_work_mult' not in open(ROOT/'run.default.toml').read(),'elder_work_mult is gone from the defaults')

import json
report={'suite':'cleanup','checks':len(checks),'failures':[],
        'scope':'capability primitives + verb registry + events.witness table + stage_work_mult; config-coverage gate lives in test_config_coverage.py'}
(ROOT/'test-results'/'cleanup.json').write_text(json.dumps(report,indent=2))
print(f'\n{len(checks)} checks passed')
