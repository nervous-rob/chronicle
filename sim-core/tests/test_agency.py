"""Handoff item E: per-person observation packets, legal action menus, engine-side
resolution and a replayable decision log, validated with scripted policies and a fake
text model. No LLM, GPU or network is involved; TextPolicy is exercised with a stub.
"""
import sys, json, math, time, tempfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chronicle.config import load, ConfigError
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.checkpoint import state_fingerprint
from chronicle.agency import ScriptedPolicy, ReplayPolicy, TextPolicy, AgencyError, packet_hash, parse_action, canonical

checks=[]
def check(x,msg):
    assert x,msg
    checks.append(msg); print('ok',msg,flush=True)
def raises(fn,exc):
    try: fn()
    except exc: return True
    return False

BASE={'world.width_km':12,'world.height_km':8,'world.region_count':2,'wildlife.raid_rate':0.0,
      'weather.lightning_p':{s:0. for s in ('spring','summer','autumn','winter')},
      'weather.rain_mm_mean':{s:0. for s in ('spring','summer','autumn','winter')},
      'weather.evaporation_mm':{s:0. for s in ('spring','summer','autumn','winter')},'time.work_days.farmer':1.0}

def world(extra=None):
    return Engine(load(overrides={**BASE,**(extra or {})}))

def fixture(extra=None, stranger=False):
    """One literate farmer + child at home with pantry/granary/field in one cell; optional
    co-present stranger from another household. Explicit seeding only."""
    e=world(extra)
    land=np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains']))
    for A in map(int,land):                       # first plains cell with a plains cell exactly two hexes away
        d=e.world.hex_distance(np.full(len(land),A),land)
        if (d==2).any(): FAR=int(land[d==2][0]); break
    else: raise RuntimeError('fixture: no plains pair at distance 2')
    ids={'cell':A,'far_cell':FAR}
    ids['home']=e.seed_place('house',A,'h:0'); ids['store']=e.seed_place('granary',A,'h:0'); ids['farm']=e.seed_place('farm',A,'h:0')
    ids['far_house']=e.seed_place('house',FAR,'h:1')
    e.farming.register(ids['farm'],ids['home'],ids['store'])
    p=e.add_person(-30*365,A,ids['home'],household_id=0,role='farmer',name='Ada',capabilities=['literate'])
    q=e.add_person(-10*365,A,ids['home'],household_id=0,name='Kid')
    e.farming.assign(p.id,ids['farm']); ids['ada'],ids['kid']=p.id,q.id
    e.inventory.create(0,'grain',5000,f'b:{ids["home"]}','h:0',source='setup')
    e.inventory.create(0,'grain',40000,f'b:{ids["store"]}','h:0',source='setup')
    ids['sheet']=e.inventory.create(0,'sheet',1,f'b:{ids["home"]}','h:0',source='setup')[0]
    ids['secret']=e.inventory.create(0,'sheet',1,f'b:{ids["home"]}','h:0',source='setup')[0]
    e.people.record(0,p.id,ids['secret'],[{'type':'note','text':'private'}]); e.people.seal(0,p.id,ids['secret'])
    if stranger:
        s=e.add_person(-30*365,A,ids['home'],household_id=1,name='Guest',capabilities=['literate'])
        e.inventory.create(0,'grain',300,f'p:{s.id}','h:1',source='setup'); ids['guest']=s.id
    return e,ids

def hidden_numbers(e):
    """Exact hidden quantities a packet must never contain as numbers."""
    out=set()
    for a in (e.cells.wildlife,e.cells.moisture,e.cells.vegetation,e.cells.fuel): out|={float(x) for x in a.ravel()[:50]}
    return out

t0=time.perf_counter()

# ---------------------------------------------------------------- configuration contract
check(raises(lambda:load(overrides={**BASE,'agency.action_hours.say':99.0}),ConfigError),'action hours above the daily activity budget are rejected')
check(raises(lambda:load(overrides={**BASE,'agency.packet_memory_limit':0}),ConfigError),'zero packet memory limit is rejected')
check(raises(lambda:load(overrides={**BASE,'agency.bogus':1}),ConfigError),'unknown agency keys are rejected')

# ---------------------------------------------------------------- packet contract
e,ids=fixture(stranger=True); ada,kid,guest=ids['ada'],ids['kid'],ids['guest']
e.agency.bind(ada,ScriptedPolicy(lambda pk:{'kind':'rest'}))
e.agency.bind(guest,ScriptedPolicy(lambda pk:{'kind':'rest'}))
check(raises(lambda:e.agency.observe(ada),AgencyError),'observation is only available inside the daily activity phase')
e.step()
e.farming.begin(1)
pa=e.agency.observe(ada); pg=e.agency.observe(guest)
json.loads(canonical(pa)); json.loads(canonical(pg))
pantry=sum(l.qty for l in e.inventory.lots.values() if l.holder==f'b:{ids["home"]}' and l.item=='grain')
check(pa['at_hand']=={f'b:{ids["home"]}':{'grain':pantry}} and 0<pantry<5000 and f'b:{ids["store"]}' not in pa['at_hand'],'owner at own house sees exactly the pantry at hand, nothing from the granary across the yard')
check(pg['at_hand']=={f'p:{guest}':{'grain':300-e.cfg.derived['consumption_units_per_day']}} and not pg['here']['at_own_place'],'a co-present guest sees only what they carry, never the host pantry')
check([m['id'] for m in pa['media']]==[ids['sheet'],ids['secret']] and next(m for m in pa['media'] if m['id']==ids['secret'])['entries'] is None,'media at hand are listed; sealed contents are not counted')
check([m['id'] for m in pg['media']]==[ids['sheet'],ids['secret']] and all(not m['mine'] for m in pg['media']),'a guest sees the physical media but owns none')
kinds=lambda pk:[m['kind'] for m in pk['menu']]
check(kinds(pa)==['rest','work','go','haul','hunt','firebreak','project','explore','say','tell','note','cite','read','teach','plan'],'literate assigned worker gets the full menu (plus the plan offer; give only appears when food is carried)')
check(kinds(pg)==['rest','go','haul','give','hunt','firebreak','project','explore','say','read','teach','plan'] and next(m for m in pg['menu'] if m['kind']=='give')['recipients']==[ada,kid],'guest carrying food may give it to the co-present household; no tell, since the household already remembers the one event the guest holds')
check('note' not in kinds(pg) and 'read' in kinds(pg) and 'work' not in kinds(pg) and 'haul' in kinds(pg),'guest: literate so can read, but no own medium to write, no farm, can haul only own carried food')
check(next(m for m in pa['menu'] if m['kind']=='note')['media']==[ids['sheet']] and next(m for m in pa['menu'] if m['kind']=='read')['media']==[ids['sheet']],'sealed medium is neither writable nor readable from the menu')
check(set(pa['here']['surroundings'])=={'smoke','fire','crop','dry','forage'} and all(isinstance(v,(bool,str)) or isinstance(v,dict) for v in pa['here']['surroundings'].values()),'surroundings are bands and flags only')
flat=json.dumps(pa)
check(not any(str(round(x,6)) in flat for x in hidden_numbers(e) if x not in (0.0,1.0)),'no hidden ecological quantity appears in the packet')
check(all(pk['body'][k] in ('ok','low','critical') for pk in (pa,pg) for k in ('last_meal','health','rest')),'body state is banded, not numeric')
check({c['id'] for c in pa['here']['company']}=={kid,guest} and {c['id'] for c in pg['here']['company']}=={ada,kid},'company is exact co-presence at the same place')
check([b['id'] for b in pa['here']['places']]==[ids['home'],ids['store'],ids['farm']] and [b['id'] for b in pg['here']['places']]==[ids['home'],ids['store'],ids['farm'],ids['far_house']],'places listed are own or in this cell; the guest also knows its own distant house')
# sleep-v1a: a non-household sleeper in Ada's house is found asleep (trespass rule); the sleeper and the resident household both remember it.
found=[ev.id for ev in e.ledger.events if ev.type=='found_asleep']
check(len(found)==1 and set(e.ledger.events[found[0]].actors)=={guest,ada,kid},'one found_asleep event names the sleeper and the resident household')
check([m['event'] for m in pa['memory']]==list(e.people.rows[ada].memory) and {m['type'] for m in pa['memory']}=={'record','seal','found_asleep'} and [m['type'] for m in pg['memory']]==['found_asleep'],'memory section lists exactly the events the person actually remembers (own record/seal + the found_asleep night), and the guest remembers only being found asleep')

# ---------------------------------------------------------------- resolution + rejection semantics
e,ids=fixture(); ada,kid=ids['ada'],ids['kid']; sheet=ids['sheet']
script={0:{'kind':'say','words':'the field looks dry'},
        1:{'kind':'note','medium':sheet,'text':'first note'},
        2:{'kind':'teach','capability':'literate','students':[kid],'medium':sheet},
        3:{'kind':'cite','medium':sheet,'event':'LATEST'},
        4:'garbage', 5:{'kind':'haul','from':f'b:{ids["store"]}','to':f'p:{ada}'},
        6:{'kind':'go','place':ids['far_house']}, 7:{'kind':'go','place':ids['store']},
        8:{'kind':'haul','from':f'b:{ids["store"]}','to':f'p:{ada}','limit':200},
        9:{'kind':'note','medium':sheet,'text':'away from home'}, 10:{'kind':'go','place':ids['home']},
        11:{'kind':'read','medium':ids['secret']}, 12:{'kind':'note','medium':sheet,'text':'x'*401},
        13:{'kind':'say','words':''}, 14:{'kind':'cite','medium':sheet,'event':999999},
        15:{'kind':'teach','capability':'literate','students':[kid],'medium':sheet},
        16:{'kind':'work'}, 17:{'kind':'go','place':ids['home']}, 18:{'kind':'work'}}
seen={}; kid_memory_day1=None
def pol(pk):
    global kid_memory_day1
    seen[pk['day']]=pk; a=script.get(pk['day'],{'kind':'rest'})
    if pk['day']==1: kid_memory_day1=dict(e.people.rows[kid].memory)
    if isinstance(a,dict) and a.get('event')=='LATEST': a={**a,'event':pk['memory'][-1]['event']}
    return a
e.agency.bind(ada,ScriptedPolicy(pol,'script'))
for _ in range(19): e.step()
log={r['day']:r for r in e.agency.log}
check(len(e.agency.log)==19 and all(r['person']==ada and r['source']=='script' for r in e.agency.log),'one logged decision per agent per day')
check(log[0]['ok'] and log[0]['hours']==[8.0,7.75] and e.ledger.events[log[0]['events'][0]].type=='say','say costs its configured hours and emits one event')
check(log[0]['events'][0] in kid_memory_day1 and kid_memory_day1[log[0]['events'][0]]['source']=='witnessed','a co-present named child witnessed the say at event time (checked next morning, before memory turnover)')
said=next(m for m in seen[1]['memory'] if m['event']==log[0]['events'][0])
check(said['type']=='say' and said['detail']=={'words':'the field looks dry'} and said['source']=='witnessed','next day the speaker remembers their own words through the memory section')
check(log[1]['ok'] and e.inventory.lots[sheet].entries[0]=={'type':'note','text':'first note'},'note writes to an own unsealed medium')
check(log[2]['ok'] and log[2]['hours']==[8.0,6.0] and e.people.rows[kid].learning['literate']>0,'teaching a co-present child advances learning and costs teach hours')
check(log[3]['ok'] and e.inventory.lots[sheet].entries[1]['type']=='cite' and e.inventory.lots[sheet].entries[1]['fidelity']==1.0,'citing a remembered event records it with the remembered fidelity')
check(not log[4]['ok'] and log[4]['reason']=='invalid_action' and log[4]['hours']==[8.0,8.0] and log[4]['events'][0]==log[4]['events'][1],'garbage is rejected: no hours, no events')
check(not log[5]['ok'] and log[5]['reason'].startswith('ValueError') and e.people.rows[ada].place==ids['home'],'hauling from a granary one is not standing in is rejected by the physical resolver')
check(not log[6]['ok'] and log[6]['reason']=='unknown_place','walking to a stranger house in another cell is not on the menu')
check(log[7]['ok'] and log[7]['reason']=='arrived' and seen[8]['here']['place']==ids['store'],'going to the own granary arrives the same day')
check(list(seen[8]['at_hand'])==[f'b:{ids["store"]}'] and 39000<seen[8]['at_hand'][f'b:{ids["store"]}']['grain']<=40000,'inside the granary the pantry at home is no longer at hand')
check(log[8]['ok'] and seen[9]['at_hand'].get(f'p:{ada}',{}).get('grain')==200-e.cfg.derived['consumption_units_per_day'],'limited haul into own carry; the evening meal came out of the carried food first')
check(not log[9]['ok'] and log[9]['reason']=='not_on_menu' and seen[9]['media']==[],'the sheet left at home is not at hand inside the granary: writing is not offered')
check(log[10]['ok'] and seen[11]['here']['place']==ids['home'],'walked home')
check(not log[11]['ok'] and log[11]['reason']=='not_on_menu' and log[11]['events'][0]==log[11]['events'][1],'reading a sealed record is refused at the menu, with no read event')
check(not log[12]['ok'] and log[12]['reason']=='invalid_text','an over-long note is refused before touching the medium')
check(not log[13]['ok'] and log[13]['reason']=='invalid_words','empty speech is refused')
check(not log[14]['ok'] and log[14]['reason']=='not_on_menu','citing an event one does not remember is refused: it was never offered')
check(log[15]['ok'] and e.people.rows[kid].learning['literate']>1.0,'a second lesson on a later day advances the child further')
check(log[16]['ok'] and log[16]['hours'][1]<8.0 and e.people.rows[ada].last_action in ('plant','travel','haul','harvest'),'work delegates to the routine within the same budget')
check(all(r['events'][1]-r['events'][0]==0 for r in e.agency.log if not r['ok']),'no rejected action produced an event')
check(not e.ledger.residuals and e.ledger.checks==e.day==19,'accounting stayed exact through accepted and rejected actions')

# ---------------------------------------------------------------- scripted work == routine household
def run(days,bind):
    e,ids=fixture(); 
    if bind: e.agency.bind(ids['ada'],ScriptedPolicy(lambda pk:{'kind':'work'},'work'))
    for _ in range(days): e.step()
    return e
a=run(240,False); b=run(240,True)
check([ev.to_json() for ev in a.ledger.events]==[ev.to_json() for ev in b.ledger.events] and a.inventory.holdings()==b.inventory.holdings(),'a policy that always chooses work reproduces the routine household event for event, including planting and harvest')
check(b.summary()['stocks']['grain']>a.summary()['stocks']['grain']-1 and any(ev.type=='harvest' for ev in b.ledger.events),'the agent-driven household harvested')

# ---------------------------------------------------------------- determinism + cadence invariance
def seeded(cadence,seed):
    e,ids=fixture({'run.days_per_tick':cadence,'run.seed':seed})
    e.agency.bind(ids['ada'],ScriptedPolicy(lambda pk:{'kind':'say','words':'rain?'} if pk['day']%3 else {'kind':'work'},'mix'))
    for _ in range(45): e.step()
    return e
x,y,z=seeded(7,5),seeded(30,5),seeded(7,6)
check(x.digest()==y.digest() and x.agency.log==y.agency.log,'reporting cadence does not change decisions or the world')
check(x.digest()!=z.digest(),'a different seed changes the world (sanity)')

# ---------------------------------------------------------------- text seam
calls=[]
def fake_model(prompt):
    calls.append(prompt)
    if len(calls)==1: return 'Sure! Here is my choice:\n{"kind": "say", "words": "hello"}\nThanks.'
    if len(calls)==2: return 'I would like to rest today.'
    return {'kind':'note','medium':sheet,'text':'from a dict'}
e,ids=fixture(); ada=ids['ada']; sheet=ids['sheet']
e.agency.bind(ada,TextPolicy(fake_model,'fake-llm'))
for _ in range(3): e.step()
L=e.agency.log
check(L[0]['ok'] and L[0]['action']=={'kind':'say','words':'hello'},'JSON embedded in prose is extracted')
check(L[1]['action']['kind']=='rest' and L[1]['action']['invalid_reply'].startswith('I would like') and L[1]['ok'],'an unparsable reply becomes rest and the raw text is kept in the log')
check(L[2]['ok'] and e.inventory.lots[sheet].entries==[{'type':'note','text':'from a dict'}],'dict replies pass straight through')
check('OBSERVATION' in calls[0] and json.loads(calls[0].split('OBSERVATION:\n',1)[1].rsplit('\n\nReply',1)[0])['self']['id']==ada,'the prompt carries exactly the canonical packet')
check(parse_action('{"kind": 1}') is None and parse_action('nope') is None and parse_action('[1,2]') is None,'parse_action never raises on bad shapes')

# ---------------------------------------------------------------- decision log replay through a checkpoint
def mixed(pk):
    d=pk['day']
    if d%11==0 and any(m['kind']=='say' for m in pk['menu']): return {'kind':'say','words':f'day {d}'}
    if d%17==0 and pk['memory'] and any(m['kind']=='cite' for m in pk['menu']): return {'kind':'cite','medium':pk['menu'][[m['kind'] for m in pk['menu']].index('cite')]['media'][0],'event':pk['memory'][-1]['event']}
    return {'kind':'work'}
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'agency.chk'
    full,ids=fixture(); full.agency.bind(ids['ada'],ScriptedPolicy(mixed,'mixed'))
    for _ in range(100): full.step()
    e,ids2=fixture(); e.agency.bind(ids2['ada'],ScriptedPolicy(mixed,'mixed'))
    for _ in range(40): e.step()
    e.save_checkpoint(path)
    r=Engine.load_checkpoint(path)
    check(r.agency.policies=={} and r.agency.agents=={ids2['ada']} and r.agency.log==e.agency.log,'reload keeps agents and the decision log but never a policy object')
    check(raises(r.step,AgencyError) and r.failed,'stepping a reloaded agent without rebinding fails loudly instead of silently resting')
    r=Engine.load_checkpoint(path); r.agency.bind(ids2['ada'],ReplayPolicy(full.agency.log))
    for _ in range(60): r.step()
    check(state_fingerprint(r)==state_fingerprint(full) and r.agency.log==full.agency.log,'replaying the saved decisions after reload reproduces the uninterrupted world and log exactly')
    r2=Engine.load_checkpoint(path); r2.agency.bind(ids2['ada'],ReplayPolicy(full.agency.log))
    r2.people.rows[ids2['ada']].traits=('restless',)
    check(raises(r2.step,AgencyError) and r2.failed,'replay refuses to continue when the observed packet differs from the recorded one')
    r3=Engine.load_checkpoint(path); r3.agency.bind(ids2['ada'],ReplayPolicy(full.agency.log[:50]))
    for _ in range(10): r3.step()
    check(raises(r3.step,AgencyError),'replay stops where the recorded log ends')
    check(raises(lambda:ReplayPolicy(full.agency.log+full.agency.log[:1]),AgencyError),'duplicate decisions are rejected when building a replay')
    # Rejected actions are replayed as rejections too: no hidden retry.
    e,ids3=fixture(); e.agency.bind(ids3['ada'],ScriptedPolicy(lambda pk:{'kind':'go','place':999},'bad'))
    for _ in range(5): e.step()
    e.save_checkpoint(path); r4=Engine.load_checkpoint(path); r4.agency.bind(ids3['ada'],ReplayPolicy(e.agency.log))
    e2,_=fixture(); e2.agency.bind(ids3['ada'],ScriptedPolicy(lambda pk:{'kind':'go','place':999},'bad'))
    for _ in range(5): e2.step()
    check(all(not x['ok'] for x in e.agency.log) and state_fingerprint(r4)==state_fingerprint(e2),'a log of rejected decisions round-trips and replays identically')

json.dump({'suite':'agency','checks':len(checks),'failures':0,'seconds':round(time.perf_counter()-t0,2),'messages':checks},
          open(ROOT/'test-results/agency.json','w'),indent=1)
print(f'\nagency: {len(checks)} checks passed in {time.perf_counter()-t0:.1f}s')
