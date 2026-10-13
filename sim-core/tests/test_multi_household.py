"""Handoff item D: small explicitly seeded multi-household scenarios.

Several households with finite initial buffers, unequal physical access, competing
travel/labor, pantry depletion, store overflow and deaths, with a daily per-owner
no-teleportation guard and a physical cause trace for every deficit and death.
Rain/evaporation, raids and lightning are disabled by explicit configuration overrides,
not instance patches, so the scenarios remain checkpointable. This is not calibration, markets, demography or a society.
"""
import sys, json, math, time, tempfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.checkpoint import state_fingerprint

checks=[]
def check(x,msg):
    assert x,msg
    checks.append(msg); print('ok',msg,flush=True)
def rejects(fn):
    try: fn()
    except ValueError: return True
    return False

# Explicit config-level interventions (checkpointable, unlike instance monkeypatches):
# no rain/evaporation holds the generated moisture; raids and lightning off.
BASE={'world.width_km':12,'world.height_km':8,'world.region_count':2,'wildlife.raid_rate':0.0,
      'weather.lightning_p':{s:0. for s in ('spring','summer','autumn','winter')},
      'weather.rain_mm_mean':{s:0. for s in ('spring','summer','autumn','winter')},
      'weather.evaporation_mm':{s:0. for s in ('spring','summer','autumn','winter')}}

def favorable(e):
    return e

def plains_by_distance(e, origin):
    land=np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))
    d=e.world.hex_distance(np.full(len(land),origin),land)
    return {int(k):[int(c) for c in land[d==k]] for k in sorted(set(d.tolist()))}

def world(cadence=30, seed=None):
    ov={**BASE,'run.days_per_tick':cadence}
    if seed is not None: ov['run.seed']=seed
    e=Engine(load(overrides=ov))
    return e

def household(e, hid, home_cell, farm_cell=None, store_cell=None, store_kind='granary', adults=1, children=0, pantry=420, buffer=0, name='H'):
    """Explicit seeding only. Returns dict of ids. No autonomous settlement."""
    owner=f'h:{hid}'; out={'owner':owner,'hid':hid,'people':[],'workers':[]}
    out['home']=e.seed_place('house',home_cell,owner)
    if store_kind=='house': out['store']=out['home']
    else: out['store']=e.seed_place(store_kind,store_cell if store_cell is not None else home_cell,owner)
    if farm_cell is not None:
        out['farm']=e.seed_place('farm',farm_cell,owner)
        e.farming.register(out['farm'],out['home'],out['store'])
    for i in range(adults):
        p=e.add_person(-30*365,home_cell,out['home'],household_id=hid,role='farmer' if farm_cell is not None else None,name=f'{name}{hid}-a{i}')
        out['people'].append(p.id)
        if farm_cell is not None: e.farming.assign(p.id,out['farm']); out['workers'].append(p.id)
    for i in range(children):
        p=e.add_person(-8*365,home_cell,out['home'],household_id=hid,name=f'{name}{hid}-c{i}')
        out['people'].append(p.id)
    if pantry: e.inventory.create(0,'grain',pantry,f'b:{out["home"]}',owner,source='setup',process='initial-pantry')
    if buffer: e.inventory.create(0,'grain',buffer,f'b:{out["store"]}',owner,source='setup',process='initial-buffer')
    return out

def owner_stock(e, owner):
    return sum(l.qty for l in e.inventory.lots.values() if l.owner==owner and e.cfg.item(l.item)['kind']=='food')

def harvests_today(e, day, farms_by_owner):
    out={}
    for ev in e.ledger.events_on(day):
        if ev.type=='harvest':
            out[farms_by_owner[ev.place]]=out.get(farms_by_owner[ev.place],0)+ev.payload['units']
    return out

class OwnerGuard:
    """Daily no-teleportation invariant: a household's food can only rise by its own harvests."""
    def __init__(self, e, households):
        self.e=e; self.h=households
        self.farms={h['farm']:h['owner'] for h in households if 'farm' in h}
        self.prev={h['owner']:owner_stock(e,h['owner']) for h in households}
        self.setup=dict(self.prev); self.grown={h['owner']:0 for h in households}; self.days=0
    def after_step(self):
        e=self.e; d=e.day-1; gained=harvests_today(e,d,self.farms)
        for h in self.h:
            o=h['owner']; now=owner_stock(e,o); g=gained.get(o,0); self.grown[o]+=g
            assert now<=self.prev[o]+g, f'day {d}: {o} stock {now} exceeds yesterday {self.prev[o]} + own harvest {g}'
            self.prev[o]=now
        for lot in e.inventory.lots.values():
            if e.cfg.item(lot.item)['kind']=='food': assert lot.owner in self.grown, f'food lot with unexpected owner {lot.owner}'
        self.days+=1

t0=time.perf_counter()

# ---------------------------------------------------------------- setup contracts
e=world(); dist=plains_by_distance(e,10); A=10; B=dist[3][0]; C=dist[2][0]
h0=household(e,0,A,A)
check(rejects(lambda:household(e,9,A,A)),'second farm cohort in an occupied cell is rejected')
h1=household(e,1,B,B)
check(rejects(lambda:e.farming.assign(h0['workers'][0],h1['farm'])),'worker cannot be assigned to another household farm')
stranger=e.add_person(-30*365,A,h0['store'],household_id=1,name='Stranger')
e.inventory.create(0,'grain',5000,f'b:{h0["store"]}','h:0',source='setup')
e.farming.begin(0); e.people.step_person(0,stranger.id,e.household_stores(stranger),False)
check(stranger.needs['food']==0 and owner_stock(e,'h:0')==5000+420,'co-presence in another household granary grants no meal')
check(rejects(lambda:e.farming.move_food(stranger.id,f'b:{h0["store"]}',f'p:{stranger.id}','h:0')),'cannot haul another household stock out of its granary')
flows=e.ledger.n_flows
check(e.farming.move_food(stranger.id,f'b:{h0["store"]}',f'p:{stranger.id}','h:1')==0 and e.ledger.n_flows==flows and owner_stock(e,'h:0')==5420,'asking for own-household stock in another granary moves nothing (no lots match the owner)')

# ---------------------------------------------------------------- pantry depletion -> traced death
e=world(); h2=household(e,2,A,None,adults=1,children=1,pantry=1200)
pid_a,pid_c=h2['people']
for _ in range(60): e.step()
dead=[ev for ev in e.ledger.events if ev.type=='died']
t=e.deprivation
check(len(dead)==2 and all(ev.payload['cause']=='starvation' for ev in dead),'non-producing household starves out entirely')
first=min(r['day'] for r in t['recent']); spoiled=e.ledger.bucket_totals.get('sink:spoil',{}).get('grain',0)
partial=next(r for r in t['recent'] if r['day']==first)
check(e.ledger.bucket_totals['sink:consume']['grain']+spoiled==1200 and partial['ate']==1200-spoiled-120*first-60 and 0<partial['ate']<60,'pantry exhaustion day and the partial last meal follow exactly from 1200 units, 120/day and ledgered spoilage')
for ev in dead:
    tr=e.cause_of_death(ev.actors[0])
    person=e.people.rows[ev.actors[0]]; sd=e.cfg['needs']['starve_days']
    check(tr['day']==ev.day and tr['last_cause']=='no_owned_food' and sd<=tr['deficit_days']<=sd+1 and person.starvation>=sd-1e-9,'death trace matches ledger day; deficit-equivalent days reached the provisional starve_days rule (a partial first day may add one calendar day)')
check(set(t['by_cause'])=={'no_owned_food'} and t['deficit_days']==sum(v['deficit_days'] for v in t['by_person'].values()),'all deficits classified as no owned food anywhere')
check(e.cause_of_death(999) is None and not e.ledger.residuals,'unknown person has no death trace; accounting balanced through deaths')

# ---------------------------------------------------------------- owned food physically out of reach
e=world(); dist=plains_by_distance(e,10); NEAR=dist[2][0]; FAR4=dist[4][0]
h3=household(e,3,A,A,store_cell=NEAR,pantry=0,buffer=20000); w=h3['workers'][0]
for _ in range(14): e.step()
check(w not in e.deprivation['by_person'] and e.people.rows[w].needs['food']==1.0,'a granary two cells away is fetched within the day-0 budget: no deficit')
e=world(); h3=household(e,3,A,A,store_cell=FAR4,pantry=0,buffer=20000); w=h3['workers'][0]
for _ in range(14): e.step()
recs=[x for x in e.deprivation['recent'] if x['person']==w]
check(recs and all(x['cause']=='owned_food_out_of_reach' and x['out_of_reach']['store']>0 for x in recs),'deficits during a multi-day fetch are traced to owned units sitting in the remote granary, never to absence of food')
check(any(x['in_transit'] for x in recs) and e.people.rows[w].needs['food']==1.0 and recs[-1]['day']<e.day-1,'the fetch journey is visible in the trace and the deficit ends once the pantry is stocked')
check(owner_stock(e,'h:3')+e.ledger.bucket_totals['sink:consume']['grain']+e.ledger.bucket_totals.get('sink:spoil',{}).get('grain',0)==20000,'remote buffer only reaches the pantry by physical haul; totals reconcile')

# ---------------------------------------------------------------- store overflow stays physical
e=world(); h4=household(e,4,A,A,store_kind='house',pantry=0)
e.cells.plant(0,np.array([A])); e.cells.crop_progress[A]=1.
e.inventory.receive_harvest(0,e.cells,[A],owner='h:4',place=h4['farm'],actors=h4['workers'])
cap=6000  # house store 60 kg at 10 g units
for _ in range(30): e.step()
hold=e.inventory.holdings()
ground=hold.get(f'c:{A}',{}).get('grain',0); house=hold.get(f'b:{h4["home"]}',{}).get('grain',0)
spoil=e.ledger.bucket_totals.get('sink:spoil',{}).get('grain',0); eaten=e.ledger.bucket_totals['sink:consume']['grain']
check(house<=cap and ground>0,'house capacity caps delivery; the rest of the harvest stays on the field ground')
check(ground+house+spoil+eaten+sum(q for h,s in hold.items() if h.startswith('p:') for q in s.values())==320000,'overflow is never lost or teleported: field + house + carried + spoil + eaten = harvest')
check(spoil>0 and not e.ledger.residuals,'exposed overflow spoils and every loss is ledgered')

# ---------------------------------------------------------------- competing travel and labor
def plains_from(e, origin, k, exclude=()):
    land=np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))
    d=e.world.hex_distance(np.full(len(land),origin),land)
    return next(int(c) for c in land[d==k] if int(c) not in exclude)

e=world(); dist=plains_by_distance(e,10); FAR=dist[5][0]
h5=household(e,5,A,FAR,pantry=420,buffer=20000)
for _ in range(120): e.step()
check(not e.cells.planted[FAR] and not [x for x in e.ledger.events if x.type=='planted'],'a field five cells from home cannot be worked under the return-home routine (travel consumes the whole budget)')
check(sum(x['hours'] for x in e.farming.labor)==0 and any(x.type=='departed' for x in e.ledger.events),'the worker travels but never accrues field labor; limitation is observable, not hidden')

e=world(); E=dist[1][1]; MID=dist[3][0]
edges=e.world.edge_hours('spring'); arr,_=e.world.dijkstra([E],edges); one_way=float(arr[MID])
h6=household(e,6,E,MID,pantry=420,buffer=20000); f=e.farming.farms[h6['farm']]
need=f.fields*e.cells.crop['hectares_per_field']*e.cfg['agriculture']['plant_labor_days_per_ha']*e.cfg['agriculture']['activity_hours_per_day']
peak=0.
for _ in range(100):
    e.step(); peak=max(peak,f.plant_hours)
check(2<one_way<4 and 0<peak<need and f.plant_hours==0 and not [x for x in e.ledger.events if x.type=='planted'],
      f'a {one_way:.2f} h commute leaves too little daily field time: {peak:.1f} of {need:.0f} planting hours accrue, then the unfinished cohort is forfeited at season end')

# ---------------------------------------------------------------- two-year multi-household scenario
def run(cadence=30, seed=None, days=730, checkpoint_at=None, tmpdir=None):
    e=world(cadence,seed); dist=plains_by_distance(e,10)
    A=10; C=dist[2][0]; D=dist[1][0]; E=dist[1][1]; F=plains_from(e,E,2,exclude=(A,C,D))
    hs=[household(e,0,A,A,adults=2,pantry=840,buffer=30000),                  # local field, two workers
        household(e,1,E,F,adults=1,pantry=420,buffer=15000),                   # field two cells from home
        household(e,2,D,None,adults=1,children=1,pantry=1200),                 # non-producing
        household(e,3,C,C,store_cell=A,adults=1,pantry=420,buffer=15000)]      # granary two cells from home
    guard=OwnerGuard(e,hs); timeline=[]; reloaded=False; labor=[]
    for d in range(days):
        e.step(); guard.after_step(); labor.extend(e.farming.labor)
        if checkpoint_at is not None and e.day==checkpoint_at:
            path=Path(tmpdir)/'multi.zip'; e.save_checkpoint(path)
            e=Engine.load_checkpoint(path); guard.e=e; reloaded=True
        if d%30==29 or d==days-1:
            timeline.append({'day':e.day,'alive':e.summary()['alive'],'stock':{h['owner']:owner_stock(e,h['owner']) for h in hs},
                             'deficit_days':e.deprivation['deficit_days']})
    return e,hs,guard,timeline,reloaded,labor

e,hs,guard,timeline,_,labor=run()
alive={h['owner']:[p for p in h['people'] if e.people.rows[p].alive] for h in hs}
check(guard.days==730 and e.ledger.checks==730 and not e.ledger.residuals,'730 daily owner guards and 730 balanced conservation closes')
check(len(alive['h:0'])==2 and len(alive['h:1'])==1 and len(alive['h:3'])==1 and len(alive['h:2'])==0,'producing households survive two years; the non-producing household does not')
for owner,st in guard.grown.items():
    check(owner_stock(e,owner)<=guard.setup[owner]+st,f'{owner} never holds more than its own setup plus its own harvests')
harv=[x for x in e.ledger.events if x.type=='harvest']; farms={h['farm']:h['owner'] for h in hs if 'farm' in h}
by_owner={}
for x in harv: by_owner.setdefault(farms[x.place],[]).append(x.day)
check(all(len(v)==2 for v in by_owner.values()) and set(by_owner)=={'h:0','h:1','h:3'},'each producing household harvests in both seasons')
check(by_owner['h:1'][0]>by_owner['h:0'][0] and by_owner['h:1'][1]>by_owner['h:0'][1],'the commuting household harvests later than the local one each year')
for pid in hs[0]['workers']: check(any(x['person']==pid for x in labor),f'both household-0 workers contribute field labor (person {pid})')
daily={}
for x in labor: daily[(x['day'],x['person'])]=daily.get((x['day'],x['person']),0.)+x['hours']
check(max(daily.values())<=e.cfg['agriculture']['activity_hours_per_day']+1e-7,'no worker exceeds the daily activity budget on any day')
deaths=e.deprivation['deaths']
check(set(deaths)==set(hs[2]['people']) and all(v['last_cause']=='no_owned_food' for v in deaths.values()),'only the non-producing household dies, with a complete no-owned-food trace')
w1=hs[1]['workers'][0]; transit=[x for x in e.deprivation['recent'] if x['person']==w1]
hist1=e.deprivation['by_person'].get(w1,{'by_cause':{}})
check(set(hist1['by_cause'])=={'owned_food_out_of_reach'} and transit and all(x['in_transit'] and x['place']==-1 for x in transit),
      'the commuting worker skips meals only while overnighting in open air mid-haul with no ration packed: a logged routine limitation, not missing food')
sources=sum(v.get('grain',0) for k,v in e.ledger.bucket_totals.items() if k.startswith('src:'))
sinks=sum(v.get('grain',0) for k,v in e.ledger.bucket_totals.items() if k.startswith('sink:'))
stock=e.summary()['stocks'].get('grain',0)
check(stock==sources-sinks and sum(guard.setup.values())==e.ledger.bucket_totals['src:setup']['grain'],'multi-household food budget reconciles setup + growth - sinks')
by_cause=e.deprivation['by_cause']
check(e.deprivation['deficit_days']==sum(by_cause.values()),'every deficit day carries exactly one physical cause')

b,*_=run(7); c,*_=run(30)
check(e.digest()==b.digest()==c.digest(),'multi-household outcome is deterministic and reporting-cadence invariant')
with tempfile.TemporaryDirectory() as td:
    r,_,g2,_,reloaded,_=run(checkpoint_at=366,tmpdir=td)
check(reloaded and r.digest()==e.digest() and state_fingerprint(r)==state_fingerprint(e) and r.deprivation==e.deprivation,'checkpoint/reload at day 366 reproduces the uninterrupted two-year multi-household trajectory including the cause trace')
other,*_=run(seed=7,days=120)
check(other.digest()!=e.digest(),'a different seed yields a different world/trajectory')

report={'suite':'multi-household','checks':len(checks),'failures':[],'seconds':round(time.perf_counter()-t0,3),
        'households':[{k:v for k,v in h.items()} for h in hs],
        'final':{'alive':alive,'stock':{h['owner']:owner_stock(e,h['owner']) for h in hs},'setup':guard.setup,'grown':guard.grown},
        'harvest_days':by_owner,'deprivation':{'deficit_days':e.deprivation['deficit_days'],'by_cause':by_cause,
            'by_person':{str(k):{kk:vv for kk,vv in v.items() if kk!='last'} for k,v in e.deprivation['by_person'].items()},'deaths':{str(k):v for k,v in deaths.items()}},
        'food_budget':{'sources':sources,'sinks':sinks,'stock':stock},'timeline':timeline,
        'scope':'Four explicitly seeded households; rain/evaporation/raids/lightning disabled by config overrides. Unequal access, commuting, pantry depletion, overflow and death with physical cause traces. Not calibration, markets, demography or a society model.'}
(ROOT/'test-results/multi-household.json').write_text(json.dumps(report,indent=2))
print(json.dumps({k:v for k,v in report.items() if k not in ('timeline','households')},indent=2))
