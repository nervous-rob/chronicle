import sys, json, math, copy, time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chronicle.config import load, ConfigError
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.inventory import Leg

checks=[]
def check(x,msg):
    assert x,msg
    checks.append(msg); print('ok',msg)
def rejects(fn):
    try:fn()
    except (ValueError,ConfigError):return True
    return False
base={'world.width_km':12,'world.height_km':8,'world.region_count':2,
      'weather.lightning_p':{s:0. for s in ('spring','summer','autumn','winter')}}
def fixture(cadence=30, favorable=False, assign=True, extra=None):
    e=Engine(load(overrides={**base,'run.days_per_tick':cadence, **({'wildlife.raid_rate':0.0} if favorable else {}), **(extra or {})}))
    land=np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))
    cell=int(land[0]);home=e.seed_place('house',cell,'h:0');store=e.seed_place('granary',cell,'h:0');farm=e.seed_place('farm',cell,'h:0')
    p=e.add_person(-30*365,cell,home,household_id=0,role='farmer',name='Grower')
    e.farming.register(farm,home,store)
    if assign:e.farming.assign(p.id,farm)
    if favorable:
        # EXPLICIT experimental intervention: optimal moisture, no rain or wind change.
        def weather(day):e.cells.moisture[:]=np.where(e.cells.land,e.cells.crop['moisture_optimal'],1.)
        e.cells.step_weather=weather
    return e,p,home,store,farm

def close(e):e.inventory.validate();check(e.ledger.close_day(e.day,e.inventory.holdings())==[],'manual operation conserves food')

for key,v in [('activity_hours_per_day',0),('activity_hours_per_day',25),('handling_kg_per_hour',float('nan')),('pantry_days',0)]:
    check(rejects(lambda key=key,v=v:load(overrides={f'agriculture.{key}':v})),f'config rejects invalid {key}={v}')
e,p,h,s,f=fixture(); cell=p.cell
check(rejects(lambda:e.farming.register(f,h,s)), 'reject duplicate cell cohort')
check(rejects(lambda:e.farming.register(f,h,s,999)), 'reject excessive field area')
child=e.add_person(-10*365,cell,h,household_id=0)
check(rejects(lambda:e.farming.assign(child.id,f)), 'children cannot be assigned crop work')
e.farming.begin(0)
check(rejects(lambda:e.farming.field_work(p.id,'plant')), 'no remote field labor')
check(e.farming.move(p.id,f),'same-cell distinct place reached')
check(e.farming.budgets[p.id]<8 and e.farming.budgets[p.id]>0,'within-cell movement costs time')
e.farming.field_work(p.id,'plant')
check(not e.cells.planted[cell] and 0<e.farming.farms[f].plant_hours<8,'partial planting does not create a crop')
check(e.farming.field_work(p.id,'plant')==0,'daily labor cannot be spent twice')
check(sum(x['hours'] for x in e.farming.labor)<=8,'all field labor within daily budget')
close(e)

# Exact-place harvest, provenance, capacity-limited custody, and no free remote meals.
e,p,h,s,f=fixture();cell=p.cell;e.cells.plant(0,np.array([cell]));e.cells.crop_progress[cell]=1.
out=e.inventory.receive_harvest(0,e.cells,[cell],owner='h:0',place=f,actors=[p.id]); q=out[cell]
lot=next(l for l in e.inventory.lots.values() if l.holder==f'c:{cell}')
check(q==320000 and lot.owner=='h:0' and lot.ground_place==f and lot.origin_event is not None,'harvest ground lot has owner, exact field and event provenance')
e.farming.begin(0)
check(rejects(lambda:e.farming.move_food(p.id,f'c:{cell}',f'p:{p.id}','h:0')), 'cannot pick up field grain from a house in the same cell')
e.farming.move(p.id,f)
check(e.farming.move_food(p.id,f'c:{cell}',f'p:{p.id}','h:0')==3000,'pickup respects 30 kg carry limit')
check(e.inventory.holdings()[f'c:{cell}']['grain']==q-3000,'pickup subtracts same amount from field')
check(rejects(lambda:e.farming.move_food(p.id,f'p:{p.id}',f'b:{s}','h:0')), 'cannot remotely deposit into granary')
check(rejects(lambda:e.farming.move_food(p.id,f'c:{cell}',f'p:{p.id}','h:9')), 'cannot haul another owner food')
e.farming.move(p.id,s);e.farming.move_food(p.id,f'p:{p.id}',f'b:{s}','h:0')
check(e.inventory.holdings()[f'b:{s}']['grain']==3000,'grain reaches granary only after arrival and deposit')
moved=next(l for l in e.inventory.lots.values() if l.holder==f'b:{s}')
check(moved.origin_event==lot.origin_event and moved.owner==lot.owner,'split and delivery preserve harvest provenance and ownership')
close(e)

# Slow journeys retain remaining edge time overnight; no one can work en route.
e,p,h,s,f=fixture(extra={'transit.season_mult.spring':0.02});land=np.flatnonzero(e.world.mainland() & (e.world.terrain==T['plains']))
other=int(max(land,key=lambda c:e.world.hex_distance(c,p.cell)))
remote=e.seed_place('granary',other,'h:0')
# Slow by explicit config in a fresh generated fixture, not production defaults.
e.farming.begin(0);arrived=e.farming.move(p.id,remote)
check(not arrived and p.place==-1 and p.id in e.farming.journeys,'long journey does not teleport to destination')
left=e.farming.journeys[p.id].remaining
check(rejects(lambda:e.farming.field_work(p.id,'plant')),'cannot perform field work while traveling')
e.day=1;e.farming.begin(1);e.farming.move(p.id,remote)
check(e.farming.journeys[p.id].remaining<left,'unfinished slow edge advances next day')

# Journey must ultimately arrive and generate one arrival, not restart each day.
for day in range(2,150):
    e.day=day;e.farming.begin(day)
    if e.farming.move(p.id,remote):break
check(p.place==remote and p.cell==other and p.id not in e.farming.journeys,'multi-day journey ultimately arrives at physical destination')
check(len([x for x in e.ledger.events if x.type=='arrived' and x.place==remote])==1,'multi-day journey emits exactly one arrival')

# Observation samples exact event-time presence, not evening location.
e,p,h,s,f=fixture();e.farming.begin(0)
a=e.ledger.event(0,'say',[],cell=p.cell,place=s)
check(a not in p.memory,'cannot remember speech in another building')
e.farming.move(p.id,s)
b=e.ledger.event(0,'say',[],cell=p.cell,place=s)
check(b in p.memory and a not in p.memory,'arriving later does not retroactively witness an event')
e.farming.move(p.id,h)
c=e.ledger.event(0,'say',[],cell=p.cell,place=s)
check(c not in p.memory and b in p.memory,'leaving preserves witnessed event but excludes later speech')

# Packed storage must not lose overflow, and a role change removes work authority.
e,p,h,s,f=fixture();e.farming.begin(0)
e.inventory.create(0,'grain',200000,f'b:{s}','h:0',source='setup')
e.inventory.create(0,'grain',100,f'p:{p.id}','h:0',source='setup')
e.farming.move(p.id,s);stock=copy.deepcopy(e.inventory.lots);flows=e.ledger.n_flows
check(e.farming.move_food(p.id,f'p:{p.id}',f'b:{s}','h:0')==0 and e.inventory.lots==stock and e.ledger.n_flows==flows,'full store leaves undelivered cargo and ledger unchanged')
e.farming.move(p.id,f);p.role='merchant'
check(rejects(lambda:e.farming.field_work(p.id,'plant')),'changed occupation cannot continue unauthorized farm labor')

# Ground harvest is physical combustible stock, not fireproof cell food.
e,p,h,s,f=fixture();cell=p.cell;e.cells.plant(0,np.array([cell]));e.cells.crop_progress[cell]=1.
e.inventory.receive_harvest(0,e.cells,[cell],owner='h:0',place=f,actors=[p.id])
e.cells.fuel[cell]=.9;e.cells.moisture[cell]=0;e.cells.ignite(0,[cell],'test',e.ledger)
e.step()
check(not any(l.holder==f'c:{cell}' for l in e.inventory.lots.values()),'fire destroys harvested food left on exposed ground')
check(e.ledger.bucket_totals['sink:burn']['grain']==320000 and not e.ledger.residuals,'ground fire loss is ledgered exactly once')

# Paired controlled integration. No post-setup inventory injection.
def run(cadence=30,days=730,favorable=True,assign=True):
    e,p,h,s,f=fixture(cadence,favorable,assign)
    e.inventory.create(0,'grain',15000,f'b:{s}','h:0',source='setup',process='initial-buffer')
    e.inventory.create(0,'grain',420,f'b:{h}','h:0',source='setup',process='initial-pantry')
    timeline=[]
    for d in range(days):
        e.step()
        if d%30==29 or d==days-1:
            timeline.append({'day':e.day,'alive':e.summary()['alive'],'stock':e.summary()['stocks'].get('grain',0),
                'grown':e.ledger.bucket_totals.get('src:growth',{}).get('grain',0),
                'consumed':e.ledger.bucket_totals.get('sink:consume',{}).get('grain',0),
                'spoiled':e.ledger.bucket_totals.get('sink:spoil',{}).get('grain',0)})
    return e,timeline

t0=time.perf_counter();a,series=run();b,_=run(7);c,_=run(30)
check(a.summary()['alive']==1 and a.day==730,'controlled household survives two years with actual production')
check(a.ledger.bucket_totals['src:setup']['grain']==15420,'initial buffer never silently replenished')
check(a.ledger.bucket_totals['src:growth']['grain']>a.ledger.bucket_totals['src:setup']['grain'],'harvest rather than setup supplies most food')
check(len([x for x in a.ledger.events if x.type=='planted'])==2 and len([x for x in a.ledger.events if x.type=='harvest'])==2,'full granary does not prevent a second plant/harvest season')
check(a.ledger.checks==730 and not a.ledger.residuals,'730 daily conservation checks balance')
check(a.digest()==b.digest()==c.digest(),'food-loop deterministic and independent of attention cadence')
stock=a.summary()['stocks'].get('grain',0)
sources=sum(v.get('grain',0) for k,v in a.ledger.bucket_totals.items() if k.startswith('src:'))
sinks=sum(v.get('grain',0) for k,v in a.ledger.bucket_totals.items() if k.startswith('sink:'))
check(stock==sources-sinks,'full food budget reconciles setup + growth - consumption - losses')
unworked,_=run(days=100,assign=False)
check(not unworked.cells.planted.any() and not unworked.ledger.bucket_totals.get('src:growth',{}),'no autonomous crop creation without a worker')
raw,rawseries=run(days=365,favorable=False)
report={'suite':'food-loop','checks':len(checks),'failures':[], 'seconds':round(time.perf_counter()-t0,3),
    'controlled_730_days':a.summary(),'controlled_food_budget':{'sources':sources,'sinks':sinks,'stock':stock},
    'controlled_timeline':series, 'baseline_ecology_no_lightning_365_days':raw.summary(),
    'baseline_key_events':[x.to_json() for x in raw.ledger.events if x.type in ('planted','harvest','died')],'baseline_ecology_no_lightning_timeline':rawseries,
    'scope':'One assigned household, initial finite food buffer. Favorable fixture clamps moisture optimal and disables raids/lightning. Not historical yield calibration, markets, seed grain, demography or a complete society.'}
(ROOT/'test-results/food-loop.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if 'timeline' not in k},indent=2))
