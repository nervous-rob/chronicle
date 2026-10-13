"""plans-v1: a policy may answer with a multi-day PLAN of ordinary menu actions. The engine runs one
step per day through the ordinary resolver without consulting the policy, and consults it again only
on a BREAK (finished, expired, step_refused, expectation, assumption, surprise, critical). Caps come
from [plans] and the planning capability (level 0/1); the engine truncates, never judges content.
Verified here: config contract, packet/menu shape, policy call counts, each break kind, truncation,
rejection paths, checkpoint round trip + bit-identical replay, and that legality is unchanged by planning.
"""
import sys, time, json, tempfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import load, ConfigError, VERBS
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import ScriptedPolicy, ReplayPolicy, AgencyError, lookup, BREAKS, PLAN_FIELDS
from chronicle.checkpoint import state_fingerprint, CheckpointError
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
def fixture(extra=None, stranger=False, planner=False):
    e=Engine(load(overrides={**BASE,**(extra or {})}))
    land=np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains']))
    for A in map(int,land):
        d=e.world.hex_distance(np.full(len(land),A),land)
        if (d==2).any(): FAR=int(land[d==2][0]); break
    ids={'cell':A,'far_cell':FAR}
    ids['home']=e.seed_place('house',A,'h:0'); ids['store']=e.seed_place('granary',A,'h:0'); ids['farm']=e.seed_place('farm',A,'h:0')
    e.farming.register(ids['farm'],ids['home'],ids['store'])
    caps=['literate']+(['planning'] if planner else [])
    p=e.add_person(-30*365,A,ids['home'],household_id=0,role='farmer',name='Ada',capabilities=caps)
    q=e.add_person(-10*365,A,ids['home'],household_id=0,name='Kid')
    e.farming.assign(p.id,ids['farm']); ids['ada'],ids['kid']=p.id,q.id
    e.inventory.create(0,'grain',5000,f'b:{ids["home"]}','h:0',source='setup')
    e.inventory.create(0,'grain',40000,f'b:{ids["store"]}','h:0',source='setup')
    ids['sheet']=e.inventory.create(0,'sheet',1,f'b:{ids["home"]}','h:0',source='setup')[0]
    if stranger:
        s=e.add_person(-30*365,A,ids['home'],household_id=1,name='Guest',capabilities=['literate']); ids['guest']=s.id
    return e,ids
class Counting(ScriptedPolicy):
    def __init__(self,fn,name='script'): super().__init__(fn,name); self.calls=[]
    def decide(self,packet): self.calls.append(packet); return self.fn(packet)
t0=time.perf_counter()

# ---------------------------------------------------------------- config contract
check('plan' in VERBS and not VERBS['plan']['timed'],'plan is a registered untimed meta-verb')
check(raises(lambda:load(overrides={**BASE,'plans.surprise_events':['nope']}),ConfigError),'unknown surprise event type is rejected')
check(raises(lambda:load(overrides={**BASE,'plans.base_steps':0}),ConfigError),'zero base steps is rejected')
check(raises(lambda:load(overrides={**BASE,'plans.goal_kinds':[]}),ConfigError),'empty goal vocabulary is rejected')
check(raises(lambda:load(overrides={**BASE,'plans.bogus':1}),ConfigError),'unknown plans key is rejected')
check(load(overrides={**BASE,'plans.goal_kinds':['food','pilgrimage']})['plans']['goal_kinds']==['food','pilgrimage'],'a theme can change the goal vocabulary without Python changes')
check(lookup({'a':{'b':[1,{'c':2}]}},'a.b.1.c')==2 and raises(lambda:lookup({'a':1},'a.b'),KeyError),'dotted packet lookup walks dicts and lists and fails loudly')

# ---------------------------------------------------------------- packet/menu shape and caps
e,ids=fixture(); ada=ids['ada']
e.agency.bind(ada,ScriptedPolicy(lambda pk:{'kind':'rest'})); e.farming.begin(0)
pk=e.agency.observe(ada); offer=next(m for m in pk['menu'] if m['kind']=='plan')
check(pk['plan'] is None,'a person without a plan sees plan: null')
check(offer['max_steps']==2 and offer['horizon_days']==3 and offer['goal_kinds']==e.cfg['plans']['goal_kinds'],'the plan offer carries base caps for someone who cannot plan')
e2,ids2=fixture(planner=True); e2.agency.bind(ids2['ada'],ScriptedPolicy(lambda pk:{'kind':'rest'})); e2.farming.begin(0)
offer2=next(m for m in e2.agency.observe(ids2['ada'])['menu'] if m['kind']=='plan')
check(offer2['max_steps']==6 and offer2['horizon_days']==14,'the planning capability raises step cap and horizon through [plans] per-level terms')
check(not any(f.read_text().count("'planning'") for f in (ROOT/'chronicle').glob('*.py')),'no engine source names the planning capability (it enables the plan primitive)')

# ---------------------------------------------------------------- adopt + execute without the policy
def three_step(pk):
    if pk['day']==0:
        return {'kind':'plan','goal':{'kind':'food','why':'fetch grain'},
                'steps':[{'action':{'kind':'go','place':ids['store']},'expects':{'path':'here.place','value':ids['store']}},
                         {'action':{'kind':'haul','from':f'b:{ids["store"]}','to':f'p:{ada}','limit':20}},
                         {'action':{'kind':'go','place':ids['home']}}],
                'assumptions':[{'path':'here.cell','value':ids['cell']}]}
    return {'kind':'work'}
e,ids=fixture(planner=True); ada=ids['ada']; pol=Counting(three_step); e.agency.bind(ada,pol)
for _ in range(5): e.step()
log=e.agency.log
check([c['day'] for c in pol.calls]==[0,3,4],'the policy is consulted on adoption day, skipped on days 1-2 while the plan runs, and consulted again once it finishes')
check(log[0]['action']['kind']=='plan' and log[0]['ok'] and log[0]['source']=='script' and log[0]['plan']=={'made_day':0,'step':0,'of':3,'break':None,'truncated':0},'adoption is logged with the plan reply and plan info')
check(e.people.rows[ada].place==ids['store'] or log[0]['reason'] in ('arrived','en_route'),'the first step runs on adoption day')
check(log[1]['source']=='plan:script' and log[1]['action']['kind']=='haul' and log[1]['ok'] and log[1]['plan']['step']==1,'day 1 executes step 2 under source plan:<author> with no policy call')
check(log[2]['action']=={'kind':'go','place':ids['home']} and log[2]['ok'],'day 2 executes the last step')
check(pol.calls[1]['plan']['break']=='finished' and log[3]['plan']['break']=='finished' and log[3]['action']['kind']=='work','the wake-up packet says why (finished) and the fresh decision records the break')
check(ada not in e.agency.plans and log[4]['plan'] is None,'a finished plan is dropped; later decisions carry no plan info')
check(abs((log[0]['hours'][0]-log[0]['hours'][1])-(log[2]['hours'][0]-log[2]['hours'][1]))<1e-9,'adopting a plan costs no time beyond its first step (adoption-day go costs the same as the plain go on day 2)')
check(all(set(r)=={'day','person','packet','action','ok','reason','hours','events','source','plan'} for r in log),'decision records carry the plan field')

# ---------------------------------------------------------------- truncation + rejections
e,ids=fixture(); ada=ids['ada']
long=lambda pk:{'kind':'plan','goal':{'kind':'other','why':'long'},'steps':[{'action':{'kind':'rest'}}]*5,'assumptions':[]} if pk['day']==0 else {'kind':'rest'}
e.agency.bind(ada,ScriptedPolicy(long)); e.step()
check(e.agency.log[0]['ok'] and e.agency.log[0]['plan']['of']==2 and e.agency.log[0]['plan']['truncated']==3 and len(e.agency.plans[ada]['steps'])==2,'a five-step plan from a non-planner is truncated to the base cap of 2 and the truncation is logged')
bad=[({'kind':'plan','goal':{'kind':'war','why':''},'steps':[{'action':{'kind':'rest'}}]},'invalid_goal'),
     ({'kind':'plan','goal':{'kind':'food','why':'x'},'steps':[]},'invalid_steps'),
     ({'kind':'plan','goal':{'kind':'food','why':'x'},'steps':[{'action':{'kind':'plan'}}]},'invalid_steps'),
     ({'kind':'plan','goal':{'kind':'food','why':'x'},'steps':[{'action':{'kind':'rest'},'expects':{'path':1}}]},'invalid_expects'),
     ({'kind':'plan','goal':{'kind':'food','why':'x'},'steps':[{'action':{'kind':'rest'}}],'assumptions':[{'path':'here.cell','value':-5}]},'assumption_false'),
     ({'kind':'plan','goal':{'kind':'food','why':'x'},'steps':[{'action':{'kind':'rest'}}],'assumptions':[{'path':'menu.0.kind','value':'rest'}]},'invalid_assumptions')]
for i,(reply,reason) in enumerate(bad):
    e,ids=fixture(); e.agency.bind(ids['ada'],ScriptedPolicy(lambda pk,r=reply:r)); e.step(); r0=e.agency.log[0]
    check(not r0['ok'] and r0['reason']==reason and ids['ada'] not in e.agency.plans and r0['hours'][0]==r0['hours'][1] and r0['events'][0]==r0['events'][1],f'bad plan #{i} is refused as {reason}: no plan, no time, no events')
e,ids=fixture(); e.agency.bind(ids['ada'],ScriptedPolicy(lambda pk:{'kind':'plan','goal':{'kind':'food','why':'x'},'steps':[{'action':{'kind':'go','place':999}}]}))
e.step(); r0=e.agency.log[0]
check(r0['ok'] is False and r0['reason']=='unknown_place' and ids['ada'] in e.agency.plans and e.agency.plans[ids['ada']]['last_ok'] is False,'a plan whose first step is illegal is adopted, the step is refused by the ordinary resolver, and the refusal is remembered on the plan')
e.step()
check(e.agency.log[1]['plan']['break']=='step_refused' and ids['ada'] not in e.agency.plans or e.agency.log[1]['action']['kind']=='plan','next morning the refusal breaks the plan and the policy is consulted')

# ---------------------------------------------------------------- break kinds
def plan_of(steps,assumptions=None,goal='other'):
    return {'kind':'plan','goal':{'kind':goal,'why':'t'},'steps':steps,'assumptions':assumptions or []}
# expectation
e,ids=fixture(); ada=ids['ada']; pol=Counting(lambda pk:plan_of([{'action':{'kind':'rest'},'expects':{'path':'here.place','value':ids['store']}},{'action':{'kind':'rest'}}]) if pk['day']==0 else {'kind':'rest'})
e.agency.bind(ada,pol); e.step(); e.step()
check([c['day'] for c in pol.calls]==[0,1] and e.agency.log[1]['plan']['break']=='expectation','a step whose expects does not hold next morning breaks the plan (expectation)')
# assumption
e,ids=fixture(); ada=ids['ada']
pol=Counting(lambda pk:plan_of([{'action':{'kind':'go','place':ids['store']}},{'action':{'kind':'rest'}}],[{'path':'here.place','value':ids['home']}]) if pk['day']==0 else {'kind':'rest'})
e.agency.bind(ada,pol); e.step(); e.step()
check(e.agency.log[0]['ok'] and e.agency.log[1]['plan']['break']=='assumption' and [c['day'] for c in pol.calls]==[0,1],'an assumption true at adoption that stops holding breaks the plan (assumption)')
# expired
e,ids=fixture({'plans.base_steps':3,'plans.base_horizon_days':1}); ada=ids['ada']
pol=Counting(lambda pk:plan_of([{'action':{'kind':'rest'}}]*3) if pk['day']==0 else {'kind':'rest'}); e.agency.bind(ada,pol)
for _ in range(3): e.step()
check([c['day'] for c in pol.calls]==[0,2] and e.agency.log[2]['plan']['break']=='expired' and e.agency.log[1]['source']=='plan:script','a plan past its horizon breaks (expired) even with steps left')
# surprise: a trespasser found asleep at home after the plan was made
e,ids=fixture(stranger=True); ada=ids['ada']
pol=Counting(lambda pk:plan_of([{'action':{'kind':'work'}}]*2) if pk['day']==0 else {'kind':'work'}); e.agency.bind(ada,pol)
e.agency.bind(ids['guest'],ScriptedPolicy(lambda pk:{'kind':'rest'})); e.step(); e.step()
mem=[m for m in pol.calls[1]['memory'] if m['type']=='found_asleep']
check([c['day'] for c in pol.calls]==[0,1] and pol.calls[1]['plan']['break']=='surprise' and mem and mem[-1]['event']>=e.agency.log[0]['events'][0],'a surprise event remembered since adoption (found_asleep) breaks the plan')
e,ids=fixture({'plans.surprise_events':['died']},stranger=True); ada=ids['ada']
pol=Counting(lambda pk:plan_of([{'action':{'kind':'work'}}]*2) if pk['day']==0 else {'kind':'work'}); e.agency.bind(ada,pol)
e.agency.bind(ids['guest'],ScriptedPolicy(lambda pk:{'kind':'rest'})); e.step(); e.step()
check([c['day'] for c in pol.calls]==[0] and [r['source'] for r in e.agency.log if r['person']==ada]==['script','plan:script'],'which events count as surprises is config: without found_asleep in the list the plan runs on')
# critical
e,ids=fixture(); ada=ids['ada']
pol=Counting(lambda pk:plan_of([{'action':{'kind':'rest'}}]*2) if pk['day']==0 else {'kind':'rest'}); e.agency.bind(ada,pol)
e.step(); e.people.rows[ada].needs['rest']=0.05; e.step()
check([c['day'] for c in pol.calls]==[0,1] and pol.calls[1]['plan']['break']=='critical' and pol.calls[1]['body']['rest']=='critical','a critical body band breaks the plan')
check(set(BREAKS)=={'finished','expired','step_refused','expectation','assumption','surprise','critical'},'the break vocabulary is exactly the seven decided kinds')
# re-plan on break
e,ids=fixture(); ada=ids['ada']
pol=Counting(lambda pk:plan_of([{'action':{'kind':'rest'}}])); e.agency.bind(ada,pol)
for _ in range(4): e.step()
check([c['day'] for c in pol.calls]==[0,1,2,3] and all(r['action']['kind']=='plan' and r['ok'] for r in e.agency.log) and all(r['plan']['break']==(None if r['day']==0 else 'finished') for r in e.agency.log),'a policy may answer a break with a new plan; one-step plans finish every morning')

# ---------------------------------------------------------------- checkpoint round trip + replay
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'plans.chk'
    def author(pk):
        d=pk['day']
        if d%7==0: return plan_of([{'action':{'kind':'go','place':ids['store']},'expects':{'path':'here.place','value':ids['store']}},
                                   {'action':{'kind':'haul','from':f'b:{ids["store"]}','to':f'p:{ids["ada"]}','limit':10}},
                                   {'action':{'kind':'go','place':ids['home']}}],[{'path':'here.cell','value':ids['cell']}],'food')
        return {'kind':'work'}
    full,ids=fixture(planner=True); full.agency.bind(ids['ada'],ScriptedPolicy(author,'author'))
    for _ in range(30): full.step()
    e,ids_b=fixture(planner=True); e.agency.bind(ids_b['ada'],ScriptedPolicy(author,'author'))
    for _ in range(15): e.step()      # day 14 adopted a plan; it is mid-flight at the checkpoint
    check(ids_b['ada'] in e.agency.plans and e.agency.plans[ids_b['ada']]['cursor']==1,'checkpoint taken with a plan in flight')
    e.save_checkpoint(path); r=Engine.load_checkpoint(path)
    check(r.agency.plans==e.agency.plans and set(r.agency.plans[ids_b['ada']])==PLAN_FIELDS and r.agency.policies=={},'a reloaded engine keeps the in-flight plan (plain JSON) and no policy')
    r.agency.bind(ids_b['ada'],ReplayPolicy(full.agency.log))
    for _ in range(15): r.step()
    check(state_fingerprint(r)==state_fingerprint(full) and r.agency.log==full.agency.log,'resuming mid-plan and replaying the log reproduces the uninterrupted world and log exactly')
    check(sum(1 for x in full.agency.log if x['source']=='plan:author')==9 and sum(1 for x in full.agency.log if x['action']['kind']=='plan')==5 and full.agency.plans[ids['ada']]['cursor']==2,'over 30 days: five plans adopted (days 0,7,...,28), nine plan-executed days, the fifth plan still in flight, policy consulted on the rest')
    r.agency.plans[ids_b['ada']]={'bogus':1}
    check(raises(lambda:r.save_checkpoint(Path(tmp)/'bad.chk'),CheckpointError),'a malformed plan record is refused by checkpoint validation')

# ---------------------------------------------------------------- planning never changes legality
e,ids=fixture(planner=True); e.agency.bind(ids['ada'],ScriptedPolicy(lambda pk:plan_of([{'action':{'kind':'haul','from':f'b:{ids["store"]}','to':f'p:{ids["ada"]}'}}]*2) if pk['day']==0 else {'kind':'rest'}))
e.step(); r0=e.agency.log[0]
check(r0['ok'] is False and r0['reason'].startswith('ValueError:source not at exact place') and r0['events'][0]==r0['events'][1] and r0['hours'][0]==r0['hours'][1],'a plan step passes through the same physical resolver as a direct action: hauling from a granary one does not stand in is refused with no time or events')

report={'suite':'plans','checks':len(checks),'failures':[],
        'scope':'plans-v1: plan adoption, policy-free step execution, seven break kinds, caps by planning level, truncation, rejections, checkpoint round trip. No estimate noise, no knacks, no household plans, no model.'}
(ROOT/'test-results/plans.json').write_text(json.dumps(report,indent=2))
print(f'\nplans: {len(checks)} checks passed in {time.perf_counter()-t0:.1f}s')
