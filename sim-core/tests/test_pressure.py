"""pressure-v1: scenario schedule (drought, ignition, blight, stranger), the give/hunt/firebreak verbs, the attention alarm term.
Everything runs the real engine. Scenarios are off by default and must leave every digest unchanged when off."""
import sys, json, time, tempfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chronicle.config import load, ConfigError
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import ScriptedPolicy, ReplayPolicy
from chronicle.attention import Attention
from chronicle import setup

checks=[]
def check(x,msg):
    assert x,msg
    checks.append(msg); print('ok',msg,flush=True)
def raises(fn,exc):
    try: fn()
    except exc: return True
    return False

SEAS=('spring','summer','autumn','winter')
BASE={'world.width_km':12,'world.height_km':8,'world.region_count':2,'wildlife.raid_rate':0.0,
      'weather.lightning_p':{s:0. for s in SEAS},'time.work_days.farmer':1.0}

def fixture(extra=None, guest=False):
    e=Engine(load(overrides={**BASE,**(extra or {})}))
    land=np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains']))
    A=int(land[0])
    ids={'cell':A}
    ids['home']=e.seed_place('house',A,'h:0'); ids['store']=e.seed_place('granary',A,'h:0'); ids['farm']=e.seed_place('farm',A,'h:0')
    e.farming.register(ids['farm'],ids['home'],ids['store'])
    p=e.add_person(-30*365,A,ids['home'],household_id=0,role='farmer',name='Ada')
    e.farming.assign(p.id,ids['farm']); ids['ada']=p.id
    e.inventory.create(0,'grain',5000,f'b:{ids["home"]}','h:0',source='setup')
    e.inventory.create(0,'grain',40000,f'b:{ids["store"]}','h:0',source='setup')
    if guest:
        s=e.add_person(-30*365,A,ids['home'],household_id=1,name='Guest')
        e.inventory.create(0,'grain',900,f'p:{s.id}','h:1',source='setup'); ids['guest']=s.id
    return e,ids

def food_units(e): return sum(l.qty for l in e.inventory.lots.values() if e.cfg.item(l.item)['kind']=='food')
def types(e,t): return [ev for ev in e.ledger.events if ev.type==t]

# ---------------------------------------------------------------- configuration contract
d=load()
check(d['scenario']['enabled'] is False,'scenarios are off by default')
check(raises(lambda:load(overrides={'scenario.enabled':'yes'}),ConfigError),'scenario.enabled must be a boolean')
check(raises(lambda:load(overrides={'scenario.blight':{'day':5}}),ConfigError),'a scenario table with missing keys is rejected')
check(raises(lambda:load(overrides={'scenario.blight':{'day':5,'loss':1.5}}),ConfigError),'blight loss above 1 is rejected')
check(raises(lambda:load(overrides={'scenario.stranger':{'day':5,'count':1,'ration_kg':-1.0}}),ConfigError),'a negative ration is rejected')
check(raises(lambda:load(overrides={'agency.action_hours.hunt':99.0}),ConfigError),'hunt hours above the daily budget are rejected')
check(raises(lambda:load(overrides={'agency.hunt_item':''}),ConfigError),'an empty hunt item is rejected')
check(set(d['agency']['action_hours'])>={'give','hunt','firebreak'},'the new timed verbs have action hours')

# ---------------------------------------------------------------- off means off
OFF={'scenario.drought':{'start_day':-1,'days':0,'evaporation_mult':1.0,'rain_mult':1.0},'scenario.ignition':{'day':-1,'distance_cells':3,'count':2},
     'scenario.blight':{'day':-1,'loss':0.5},'scenario.stranger':{'day':-1,'count':1,'ration_kg':5.0}}
a,_=fixture(); b,_=fixture({'scenario.enabled':True,**OFF})
for _ in range(25): a.step(); b.step()
check(a.digest()==b.digest(),'enabled with every effect off (day -1) leaves the digest identical to disabled')

# ---------------------------------------------------------------- drought
c1,_=fixture(); c2,_=fixture({'scenario.enabled':True,**OFF,'scenario.drought':{'start_day':2,'days':10,'evaporation_mult':3.0,'rain_mult':0.2}})
for _ in range(14): c1.step(); c2.step()
check(float(c2.cells.moisture.mean())<float(c1.cells.moisture.mean()),'a drought window leaves the land drier than the control')
check([ev.payload['kind'] for ev in types(c2,'scenario')]==['drought_start','drought_end'] and [ev.day for ev in types(c2,'scenario')]==[2,12],'drought start and end are ledgered on the right days')
check(len(types(c1,'scenario'))==0,'a control run has no scenario events')

# ---------------------------------------------------------------- blight
f1,_=fixture({'scenario.enabled':True,**OFF,'scenario.blight':{'day':5,'loss':0.5}})
f0,_=fixture({'scenario.enabled':True,**OFF})
for _ in range(4): f1.step(); f0.step()
f1.cells.plant(f1.day,np.flatnonzero(f1.cells.fields>0)[:3]); f0.cells.plant(f0.day,np.flatnonzero(f0.cells.fields>0)[:3])
planted=np.flatnonzero(f1.cells.planted)
for _ in range(2): f1.step(); f0.step()
check(len(planted)>0 and bool((f1.cells.crop_health[planted]<f0.cells.crop_health[planted]).all()),'blight lowers crop health on every planted cell relative to the control')
check(types(f1,'scenario')[0].payload['kind']=='blight' and types(f1,'scenario')[0].payload['cells']==len(planted),'the blight event records how many cells it hit')

# ---------------------------------------------------------------- ignition
g,ids=fixture({'scenario.enabled':True,**OFF,'scenario.ignition':{'day':3,'distance_cells':2,'count':2}})
g.cells.moisture[:]=0.0; g.cells.fuel[:]=0.9; g.cells.vegetation[:]=0.9
g.cells.cleared[:]=0.0
for _ in range(4): g.step()
fires=[ev for ev in types(g,'fire_ignited') if ev.payload.get('source')=='scenario']
check(len(fires)==2 and all(ev.day==3 for ev in fires),'the scenario ignites exactly the requested number of cells on its day')
near=g.world.hex_distance(np.array([ev.cell for ev in fires]),np.full(len(fires),ids['cell']))
check(bool((near<=2).all()),'ignited cells lie within distance_cells of a building')
sc=[ev.payload for ev in types(g,'scenario')]
check(sc and sc[0]['kind']=='ignition' and sc[0]['ignited']==2,'the ignition summary event reports what lit')

# ---------------------------------------------------------------- stranger
s,ids=fixture({'scenario.enabled':True,**OFF,'scenario.stranger':{'day':2,'count':2,'ration_kg':5.0}})
n0=len(s.people.rows)
for _ in range(4): s.step()
check(len(s.people.rows)==n0+2,'the stranger scenario adds the requested number of people')
new=[p for p in s.people.rows.values() if p.id>=n0]
check(all(p.household_id is None and s.people.stage(p,s.day)=='adult' for p in new),'strangers are adults with no household')
arr=[ev for ev in types(s,'arrived') if ev.payload.get('source')=='scenario']
check(len(arr)==2 and {ev.actors[0] for ev in arr}=={p.id for p in new},'each stranger arrival is ledgered as an arrived event with source scenario (journeys also emit arrived)')
check(all(p.alive for p in new),'a stranger with a ration survives the first days')
check(s.ledger.residuals==[] or len(s.ledger.residuals)==0,'conservation holds with a stranger arriving carrying food')

# ---------------------------------------------------------------- give
e,ids=fixture(guest=True); ada,guest=ids['ada'],ids['guest']
e.agency.bind(ada,ScriptedPolicy(lambda pk:{'kind':'rest'}))
seen={}
def gp(pk):
    m=next((m for m in pk['menu'] if m['kind']=='give'),None); seen['menu']=m
    return {'kind':'give','recipient':ada,'kg':2} if pk['day']==1 else {'kind':'give','recipient':ada,'kg':999} if pk['day']==2 else {'kind':'give','recipient':guest,'kg':1}
e.agency.bind(guest,ScriptedPolicy(gp))
before=food_units(e)
e.step(); e.step()   # day 0 (no decision logged), day 1
e.step(); e.step()
L=[r for r in e.agency.log if r['person']==guest]
check(seen['menu'] is not None and ada in seen['menu']['recipients'],'a person carrying food beside someone is offered give with that person as recipient')
d1=next(r for r in L if r['day']==1)
check(d1['action']['kind']=='give' and d1['ok'],'give of carried food succeeds')
tr=[ev for ev in types(e,'transfer') if ev.payload.get('reason')=='give']
check(len(tr)>=1 and set(tr[0].actors)=={ada,guest},'the give is a ledgered transfer between the two people')
d2=next(r for r in L if r['day']==2)
check(not d2['ok'] and d2['reason']=='invalid_args','giving more than is carried is rejected')
d3=next(r for r in L if r['day']==3)
check(not d3['ok'] and d3['reason']=='not_on_menu','giving to someone not offered (oneself) is rejected')
check(food_units(e)<before and e.ledger.residuals==[],'food is conserved through a give (only meals leave the world)')

# ---------------------------------------------------------------- hunt
h,ids=fixture(); ada=ids['ada']
ci=h.cells.wl_ids.index('game')
h.cells.wildlife[ids['cell'],ci]=float(h.cells.carrying(ids['cell'])[ci])*5   # abundant, so the success roll is as high as it gets
h.agency.bind(ada,ScriptedPolicy(lambda pk:{'kind':'hunt','target':'game'} if any(m['kind']=='hunt' for m in pk['menu']) else {'kind':'rest'}))
for _ in range(25): h.step()
hunts=types(h,'hunt'); kills=[ev for ev in hunts if ev.payload['killed_kg']>0]
check(len(hunts)>=1 and len(kills)>=1,'a hunter on abundant ground makes at least one kill in 25 days')
check(all(ev.payload['carried_kg']<=h.cfg['people']['carry_cap'] for ev in hunts) and all(ev.payload['carried_kg']<=ev.payload['killed_kg'] for ev in hunts),'carried meat never exceeds the kill or the carry limit')
check(any(l.item=='meat' for l in h.inventory.lots.values()) or any(ev.payload['carried_kg']>0 for ev in hunts),'a kill puts meat into the hunter inventory')
check(h.ledger.residuals==[],'conservation holds through hunting')
hh=[r for r in h.agency.log if r['action']['kind']=='hunt']
check(all(r['ok'] for r in hh),'every offered hunt resolves ok')
bad=[]
h2,ids2=fixture(); h2.agency.bind(ids2['ada'],ScriptedPolicy(lambda pk:{'kind':'hunt','target':'dragon'}))
h2.step(); h2.step()
r=[r for r in h2.agency.log if r['person']==ids2['ada']][0]
check(not r['ok'] and r['reason']=='not_on_menu','hunting something that is not on the menu is rejected')

# ---------------------------------------------------------------- firebreak
k,ids=fixture(); ada=ids['ada']
k.cells.fuel[ids['cell']]=0.8; k.cells.vegetation[ids['cell']]=0.8; k.cells.cleared[ids['cell']]=0.0
k.agency.bind(ada,ScriptedPolicy(lambda pk:{'kind':'firebreak'}))
f0=float(k.cells.fuel[ids['cell']])
k.step(); k.step()
rec=[r for r in k.agency.log if r['person']==ada][0]
check(rec['ok'] and float(k.cells.cleared[ids['cell']])>=k.cfg['agency']['firebreak_clear']-1e-6,'a firebreak raises the cleared share of the cell by the configured fraction')
check(float(k.cells.fuel[ids['cell']])<f0,'a firebreak lowers standing fuel')
check(len(types(k,'firebreak'))>=1 and types(k,'firebreak')[0].actors==(ada,) or list(types(k,'firebreak')[0].actors)==[ada],'a firebreak is ledgered with its maker')
k.cells.cleared[ids['cell']]=1.0
pk=k.agency.observe  # menu check uses a fresh day below
k.farming.begin(k.day)
check(not any(m['kind']=='firebreak' for m in k.agency.observe(ada)['menu']),'a fully cleared cell no longer offers a firebreak')

# ---------------------------------------------------------------- attention alarm
w,ids=fixture(); w.people.add(-30*365,ids['cell'],-1,day=0,name='Far')
far=[p for p in w.people.rows.values() if p.name=='Far'][0]
cells=[int(c) for c in np.flatnonzero(w.cells.land) if int(c)!=ids['cell'] and int(c) not in set(int(x) for x in w.world.neighbors(ids['cell']))]
far.cell=cells[0]
att=Attention(w)
s0=att.scores()[0]
w.cells.burning[ids['cell']]=True
s1=att.scores()[0]
check(s1[ids['ada']]-s0[ids['ada']]>=w.cfg['attention']['weights']['alarm']-1e-9,'smoke or fire at a person adds exactly the alarm weight to their attention score')
check(abs(s1[far.id]-s0[far.id])<1e-9,'a person far from any fire gets no alarm term')
w.cells.burning[ids['cell']]=False

# ---------------------------------------------------------------- checkpoint + replay with scenarios on
CFG={'population.initial':12,'scenario.enabled':True,'scenario.drought':{'start_day':3,'days':6,'evaporation_mult':3.0,'rain_mult':0.2},
     'scenario.ignition':{'day':6,'distance_cells':3,'count':2},'scenario.blight':{'day':8,'loss':0.5},'scenario.stranger':{'day':4,'count':1,'ration_kg':5.0}}
def gen():
    e=Engine(load(overrides=CFG)); setup.generate(e); return e
u=gen()
for _ in range(14): u.step()
r=gen()
for _ in range(7): r.step()
tmp=tempfile.mkdtemp(); path=str(Path(tmp)/'ck.zip'); r.save_checkpoint(path)
r2=Engine.load_checkpoint(path,expected_config=load(overrides=CFG))
for _ in range(7): r2.step()
check(u.digest()==r2.digest(),'a checkpoint taken mid-scenario resumes to the identical digest (the stranger arrival and ignition fire once)')
check(len(types(u,'scenario'))==len(types(r2,'scenario')),'scenario events are not duplicated across a resume')

# replay: a scripted run using the new verbs replays bit-identically with no policy
def run_policy(policy_for):
    e=gen(); alive=sorted(pid for pid,p in e.people.rows.items() if p.alive and e.people.at_least(p,0,'youth'))[:6]
    for pid in alive: e.agency.bind(pid,policy_for(pid))
    for _ in range(12): e.step()
    return e
def pol(pid):
    def fn(pk):
        kinds=[m['kind'] for m in pk['menu']]
        return {'kind':'firebreak'} if pk['day']%3==0 and 'firebreak' in kinds else {'kind':'hunt','target':'game'} if pk['day']%3==1 and 'hunt' in kinds else {'kind':'rest'}
    return ScriptedPolicy(fn)
o=run_policy(pol)
used={r['action']['kind'] for r in o.agency.log}
check({'firebreak','hunt'}<=used,'the scripted run exercised the new verbs')
log=json.loads(json.dumps(o.agency.log))
rp=gen(); rep=ReplayPolicy(log)
for pid in sorted({r['person'] for r in log}): rp.agency.bind(pid,rep)
for _ in range(12): rp.step()
check(rp.digest()==o.digest(),'a run using the new verbs replays to the identical digest')

print(f'\npressure: {len(checks)} checks passed')
