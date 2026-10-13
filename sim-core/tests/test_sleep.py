"""sleep-v1a: nightly rest resolution driven by [sleep.access] / [sleep.consequences] tables.
The engine walks config rows and reads building FIELDS (affordances, shelter, lodging_rate);
no building kind or row id is named in Python. Verified here: rule priority, rent as a ledgered
coin transfer, refusal fall-through, witness memory, open-air season/wet scaling, consequence
rows (hours multiplier, exposure drain + death cause), the ration check, checkpoint round trip,
and a theme overlay that adds a new sleeping place + consequence with zero Python changes.
"""
import sys, tempfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chronicle.config import load, ConfigError
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.cells import season_of
from chronicle.agency import ScriptedPolicy

checks=[]
def check(x,msg):
    assert x,msg
    checks.append(msg); print('ok',msg,flush=True)
def raises(fn,exc):
    try: fn()
    except exc: return True
    return False

SEASONS=('spring','summer','autumn','winter')
DRY={'weather.rain_mm_mean':{s:0. for s in SEASONS},'weather.evaporation_mm':{s:0. for s in SEASONS}}
BASE={'world.width_km':12,'world.height_km':8,'world.region_count':2,'wildlife.raid_rate':0.0,
      'weather.lightning_p':{s:0. for s in SEASONS},**DRY}

def world(extra=None, path=None):
    return Engine(load(path, overrides={**BASE,**(extra or {})}))
def plains(e):
    return [int(c) for c in np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))]
def person(e, cell, place, hid, name, coin=0, grain=0):
    p=e.add_person(-30*365, cell, place=place, household_id=hid, name=name)
    if coin: e.inventory.create(0,'coin',coin,f'p:{p.id}',f'p:{p.id}',source='setup')
    if grain: e.inventory.create(0,'grain',grain,f'p:{p.id}',f'p:{p.id}',source='setup')
    return p.id
def expected_open(e, pid, season):
    """Open-air quality the engine should have produced tonight: season mult x wet mult (regime flag, not mm)."""
    sl=e.cfg['sleep']; wet=bool(e.cells.wet[e.world.region[e.people.rows[pid].cell]])
    return sl['open_air_mult'][season]*(sl['wet_mult'] if wet else 1.0)
def events(e, *types):
    return [ev for ev in e.ledger.events if ev.type in types]
def sleep_rule(e, pid):
    """Last sleep rule a person resolved under, from the ledger (lodging shows as a transfer)."""
    for ev in reversed(e.ledger.events):   # actors[0] is the sleeper; later actors are witnesses
        if ev.actors and ev.actors[0]==pid and ev.type in ('found_asleep','slept_rough'): return ev.payload['rule']
        if ev.actors and ev.actors[0]==pid and ev.type=='transfer' and ev.payload.get('reason')=='lodging': return 'lodging'
    return None

# ---------------------------------------------------------------- 1. rule walk on day 0 (spring, dry)
e=world(); c=plains(e)[0]
home=e.seed_place('house',c,'h:1'); inn=e.seed_place('tavern',c,'h:9'); barn=e.seed_place('granary',c,'h:1')
A=person(e,c,home,1,'A',grain=200); K=person(e,c,home,1,'K',grain=200)
G=person(e,c,inn,2,'G',coin=5,grain=200); B=person(e,c,inn,3,'B',grain=200)
R=person(e,c,-1,4,'R',grain=200); S=person(e,c,barn,1,'S',grain=200)
keeper=person(e,c,inn,9,'Keeper',grain=200)
rate=e.cfg.building('tavern')['lodging_rate']
e.step()
rows=e.people.rows
check(rows[A].last_sleep_quality==1.0 and rows[K].last_sleep_quality==1.0 and sleep_rule(e,A) is None,'own household home: full quality, no event')
check(abs(rows[S].last_sleep_quality-0.6*min(1,e.cfg.building('granary')['shelter']/e.cfg['sleep']['reference_shelter']))<1e-9 and sleep_rule(e,S) is None,'own outbuilding: store affordance rule, quality scaled by shelter')
check(rows[keeper].last_sleep_quality==1.0 and sleep_rule(e,keeper) is None,'the innkeeper sleeps at home in their own tavern; no rent, no event')
lodg=[ev for ev in events(e,'transfer') if ev.payload.get('reason')=='lodging']
check(len(lodg)==1 and lodg[0].actors==(G,) and lodg[0].payload['legs'][0]['to']==f'b:{inn}' and lodg[0].payload['legs'][0]['owner']=='h:9','a paying guest rents: one ledgered coin transfer to the building, owned by the keeper household')
check(e.inventory.holdings()[f'p:{G}']['coin']==5-rate and e.inventory.holdings()[f'b:{inn}']['coin']==rate and rows[G].last_sleep_quality==0.9,'coin moved guest -> inn at the catalog lodging_rate; lodging quality 0.9')
ref=events(e,'lodging_refused')
check(len(ref)==1 and ref[0].actors==(B,) and ref[0].payload=={'rate':rate,'held':0} and sleep_rule(e,B)=='trespass' and rows[B].last_sleep_quality==0.5,'a broke guest is refused lodging and falls through to trespass, not outdoors')
fa=events(e,'found_asleep')
check(len(fa)==1 and set(fa[0].actors)=={B,keeper} and fa[0].payload['rule']=='trespass','found_asleep names the sleeper plus the owner household residents')
check(fa[0].id in rows[keeper].memory and fa[0].id in rows[B].memory and fa[0].id in rows[G].memory and fa[0].id not in rows[A].memory,'keeper and trespasser remember the finding; the co-present guest witnesses it, the household next door does not')
sr=events(e,'slept_rough')
check(len(sr)==1 and sr[0].actors==(R,) and abs(rows[R].last_sleep_quality-expected_open(e,R,'spring'))<1e-9,'no place: slept_rough at open-air spring quality (x wet_mult if the regime is wet)')
check(all(rows[p].needs['shelter']==min(1.,rows[p].last_sleep_quality*e.cfg['sleep']['shelter_need_per_quality']) for p in (A,G,B,R,S)),'shelter need follows sleep quality')
check(e.ledger.residuals==[] and e.ledger.checks>=0,'rent did not break conservation')

# a room_rate override on the Place beats the catalog rate; unaffordable override refuses
e=world(); c=plains(e)[0]; inn=e.seed_place('tavern',c,'h:9'); e.places[inn].room_rate=3.0
G=person(e,c,inn,2,'G',coin=4,grain=200)
e.step(); check(e.inventory.holdings()[f'p:{G}']['coin']==1 and e.inventory.holdings()[f'b:{inn}']['coin']==3,'Place.room_rate overrides the catalog lodging_rate')
e.step(); check(len(events(e,'lodging_refused'))==1 and sleep_rule(e,G)=='trespass','the next night the guest cannot pay and is found asleep instead')

# ---------------------------------------------------------------- 2. open air: seasons, wet nights
def rough_quality(day, wet_mean):
    ov={'weather.rain_mm_mean':{s:wet_mean for s in SEASONS}}
    e=Engine(load(overrides={**BASE,**ov})); c=plains(e)[0]
    R=person(e,c,-1,4,'R',grain=3000//10)
    e.day=day; e.cells.day=day if hasattr(e.cells,'day') else None
    return e
e=world(); c=plains(e)[0]; R=person(e,c,-1,4,'R',grain=200); h=e.seed_place('house',c,'h:4')
qs={}; exp={}
for d in range(0,365,91):
    e.day=d; e.step(); s=season_of(d)
    qs[s]=e.people.rows[R].last_sleep_quality; exp[s]=expected_open(e,R,s)
    e.people.rows[R].needs['health']=1.0; e.people.rows[R].sleep_streaks.clear()   # keep the subject alive for the next season
check(all(abs(qs[s]-exp[s])<1e-9 for s in qs) and len(set(qs.values()))>1,f'open-air quality follows open_air_mult per season (x wet regime): {qs}')
# wet vs dry regime: same world, same day, only the regime flag differs
def rough(wet):   # read the flag the step actually produced, then compare against the engine's own quality
    e=world(); c=plains(e)[0]; R=person(e,c,-1,4,'R',grain=200)
    e.cells.wet[:]=wet; e.step()
    return e.people.rows[R].last_sleep_quality, bool(e.cells.wet[e.world.region[c]])
pairs=[rough(w) for w in (True,False)]
sl=e.cfg['sleep']
check(all(abs(q-sl['open_air_mult']['spring']*(sl['wet_mult'] if w else 1.0))<1e-9 for q,w in pairs) and sl['wet_mult']<1.0,f'open-air quality is multiplied by wet_mult exactly when the regime flag is wet: {pairs}')

# ---------------------------------------------------------------- 3. consequences: hours, exposure, death cause
e=world(); c=plains(e)[0]; home=e.seed_place('house',c,'h:1'); A=person(e,c,home,1,'A',grain=200)
full=e.cfg['activity_hours_per_day'] if 'activity_hours_per_day' in e.cfg.raw else e.farming.cfg['activity_hours_per_day']
rows=e.people.rows; rows[A].needs['rest']=0.3; e.farming.begin(0)
check(abs(e.farming.budgets[A]-full*0.75)<1e-9,'tired band (rest<0.4): activity budget x0.75 from the consequence row')
rows[A].needs['rest']=0.1; e.farming.begin(1)
check(abs(e.farming.budgets[A]-full*0.5)<1e-9,'exhausted band (rest<0.15): x0.5')
rows[A].needs['rest']=1.0; e.farming.begin(2)
check(abs(e.farming.budgets[A]-full)<1e-9,'rested: full budget')
# exposure: winter, outdoors, no shelter -> drain starts after consecutive_nights, death cause = exposure
WIN=91*3
e=world(); c=plains(e)[0]; R=person(e,c,-1,4,'R'); e.inventory.create(0,'grain',240,f'p:{R}',f'p:{R}',source='setup')
e.day=WIN  # setup at a winter day boundary (no steps yet)
hs=[]
for _ in range(14):
    if not e.people.rows[R].alive: break
    e.step(); hs.append(round(e.people.rows[R].needs['health'],2))
row=e.cfg['sleep']['consequences']['exposure']
first_drain=next(i for i in range(1,len(hs)) if hs[i-1]-hs[i]>=row['health_drain']-1e-9)
check(first_drain+1==row['consecutive_nights'] and e.people.rows[R].sleep_streaks['exposure']>=row['consecutive_nights'],f'exposure drain begins on night {row["consecutive_nights"]} outdoors in winter: {hs}')
died=events(e,'died')
check(died and died[0].actors==(R,) and died[0].payload['cause']=='exposure' and e.cause_of_death(R) is not None,'death from exposure is recorded with cause=exposure (not starvation)')
# summer outdoors: no exposure row matches
e=world(); c=plains(e)[0]; R=person(e,c,-1,4,'R',grain=900); e.day=91
for _ in range(10): e.step()
check(e.people.rows[R].alive and e.people.rows[R].needs['health']==1.0 and e.people.rows[R].sleep_streaks.get('exposure',0)==0,'summer nights outdoors: no exposure drain, streak stays 0')
# one sheltered night resets the streak
e=world(); c=plains(e)[0]; h=e.seed_place('house',c,'h:4'); R=person(e,c,-1,4,'R',grain=900); e.day=WIN
e.step(); e.step(); check(e.people.rows[R].sleep_streaks['exposure']==2,'two cold nights counted')
e.people.rows[R].place=h; e.step(); check(e.people.rows[R].sleep_streaks['exposure']==0,'one sheltered night resets the exposure streak')

# ---------------------------------------------------------------- 4. ration check (opt-in)
e=world({'sleep.ration_refuse':True}); pl=plains(e); c0=pl[0]; far=max(pl,key=lambda x:e.world.hex_distance(x,c0))
h=e.seed_place('house',c0,'h:1'); h2=e.seed_place('house',far,'h:1'); A=person(e,c0,h,1,'A')
e.farming.begin(0); hours,_,_=e.farming._route(A,h2)
check(hours>e.farming.budgets[A],'fixture: the far house is more than a day away')
check(e.farming.move(A,h2) is False and events(e,'journey_refused')[-1].payload['reason']=='no_rations' and A not in e.farming.journeys,'a multi-day journey without carried food is refused')
e.inventory.create(0,'grain',20,f'p:{A}',f'p:{A}',source='setup')
check(e.farming.move(A,h2) is False and A in e.farming.journeys and events(e,'departed')[-1].actors==(A,),'with rations carried the same journey departs')
e2=world(); pl=plains(e2); c0=pl[0]; far=max(pl,key=lambda x:e2.world.hex_distance(x,c0))
h=e2.seed_place('house',c0,'h:1'); h2=e2.seed_place('house',far,'h:1'); A=person(e2,c0,h,1,'A'); e2.farming.begin(0)
check(e2.farming.move(A,h2) is False and A in e2.farming.journeys and not events(e2,'journey_refused'),'ration_refuse=false (default): routine fetches are not refused')

# ---------------------------------------------------------------- 5. checkpoint round trip carries the new state
e=world(); c=plains(e)[0]; inn=e.seed_place('tavern',c,'h:9'); e.places[inn].room_rate=2.0; e.places[inn].condition=0.8
G=person(e,c,inn,2,'G',coin=20,grain=400); R=person(e,c,-1,4,'R',grain=400); e.day=WIN
for _ in range(4): e.step()
with tempfile.TemporaryDirectory() as td:
    p=Path(td)/'s.zip'; e.save_checkpoint(p); e2=Engine.load_checkpoint(p)
check(e2.digest()==e.digest() and e2.places[inn].room_rate==2.0 and e2.places[inn].condition==0.8 and e2.people.rows[R].sleep_streaks==e.people.rows[R].sleep_streaks and e2.people.rows[G].last_sleep_quality==e.people.rows[G].last_sleep_quality,'room_rate, condition, sleep quality and streaks survive save/load')
for _ in range(3): e.step(); e2.step()
check(e.digest()==e2.digest(),'resumed run stays bit-identical through rent, exposure and streak updates')
check(abs(e.people.rows[G].last_sleep_quality-0.9*(1-e.cfg['sleep']['condition_weight']*0.2))<1e-9,'building condition scales sleep quality by condition_weight')

# ---------------------------------------------------------------- 6. config validation
bad=lambda ov: raises(lambda: load(overrides={**BASE,**ov}), ConfigError)
check(bad({'sleep.access.home.relation':'cousin'}),'unknown relation rejected')
check(bad({'sleep.access.home.priority':20}),'duplicate priority rejected')
check(bad({'sleep.access.open.open_air':False}),'removing the only open_air fallback is rejected')
check(bad({'sleep.consequences.exposure.cause':None}) or bad({'sleep.consequences.tired.health_drain':0.1}),'a draining row must name a cause')
check(bad({'sleep.access.home.bogus':1}),'unknown rule field rejected')

# ---------------------------------------------------------------- 7. theme overlay: new sleeping place + consequence, zero Python
over=str(ROOT/'tests'/'fixtures'/'sleep_overlay.toml')
e=world(path=over); c=plains(e)[0]
check('hayloft' in e.cfg['sleep']['access'] and 'stiff' in e.cfg['sleep']['consequences'] and 'home' in e.cfg['sleep']['access'],'overlay ADDS a rule and a row next to the defaults')
barn=e.seed_place('barn',c,'h:9'); W=person(e,c,barn,5,'W',grain=200)
e.step(); q=e.people.rows[W].last_sleep_quality
spec=e.cfg.building('barn')
check(abs(q-0.7*min(1,spec['shelter']/e.cfg['sleep']['reference_shelter']))<1e-9 and sleep_rule(e,W) is None,'a stranger in the barn sleeps under the hayloft rule (priority 35 beats trespass 50), scaled by the barn shelter')
e.farming.begin(1)
full=e.farming.cfg['activity_hours_per_day']
check(abs(e.farming.budgets[W]-full*0.9)<1e-9,'the overlay "stiff" consequence (quality<0.75) trims the next day to x0.9')
src=(ROOT/'chronicle').glob('*.py')
check(not any(('hayloft' in f.read_text() or '"tavern"' in f.read_text() or "'tavern'" in f.read_text()) for f in src),'no engine source names hayloft or tavern')

import json
report={'suite':'sleep','checks':len(checks),'failures':[],
        'scope':'sleep-v1a: nightly access rules, rent transfer, consequence rows, ration check, overlay extensibility. No building condition writer, no eating/social quality consumers, no exposure calibration.'}
(ROOT/'test-results/sleep.json').write_text(json.dumps(report,indent=2))
print(f'\n{len(checks)} checks passed')
