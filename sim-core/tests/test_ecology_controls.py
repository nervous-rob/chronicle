import sys,json,copy,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.cells import Cells
from chronicle.rng import Streams
from chronicle.world import World,T
from chronicle.ledger import Ledger
checks=[]
def check(ok,msg):
    assert ok,msg
    checks.append(msg);print('ok',msg)
base={'world.width_km':12,'world.height_km':8,'world.region_count':2,
      'weather.lightning_p':{s:0. for s in ('spring','summer','autumn','winter')}}
def fixture(extra=None):
    cfg=load(overrides={**base,**(extra or {})});w=World.generate(cfg)
    return cfg,w,Cells(cfg,w,Streams(cfg.seed))
# Dispersal-only controlled host population: no growth, predators or disease transition.
cfg,w,c=fixture({'catalog.wildlife.game.r':0.})
g=c.wl_ids.index('game');c.wildlife[:]=0
cell=int(np.flatnonzero(w.mainland())[0]);c.wildlife[cell,g]=100.
c.infect('murrain','game',[cell],.4)
for d in range(20):c.step_wildlife(d)
a=c.seir['murrain','game'];N=c.wildlife[:,g]
check(np.isclose(N.sum(),100.,atol=.001),'dispersal conserves host abundance')
check(np.isclose((N*a[:,2]).sum(),40.,atol=.001),'dispersal conserves infected host count')
check(np.count_nonzero(a[:,2]*N>1e-5)>1,'infection travels to neighboring cells with hosts')
check(np.allclose(a.sum(1),1.,atol=1e-5) and np.all(a>=0),'SEIR stays normalized and nonnegative')
# Pure disease exposure comparison holds habitat/predation/births fixed.
cfg,w,control=fixture({'catalog.wildlife.game.r':0.,'wildlife.dispersal':0.})
control.wildlife[:]=0.;control.wildlife[control.land,g]=100.
infected=copy.deepcopy(control);ids=np.flatnonzero(control.land);infected.infect('murrain','game',ids,.2)
initial=float(control.wildlife[:,g].sum());curve=[]
for day in range(180):
    control.step_disease(day);infected.step_disease(day)
    if day%10==0:curve.append({'day':day,'control':float(control.wildlife[:,g].sum()),'infected':float(infected.wildlife[:,g].sum())})
check(np.isclose(control.wildlife[:,g].sum(),initial),'uninfected control has no disease-only mortality')
check(0<float(infected.wildlife[:,g].sum())<initial,'matched infection causes additional mortality')
check(float(infected.seir['murrain','game'][:,3].max())>.1,'disease survivors accumulate immunity')
disease_final = float(infected.wildlife[:,g].sum())
# Full-ecology paired comparison: all other forcing/randomness identical.
cfg,w,control=fixture();infected=copy.deepcopy(control)
infected.infect('murrain','game',np.flatnonzero(control.land)[:10],.2)
g0=float(control.wildlife[:,g].sum())
for day in range(180):
    control.step(day);infected.step(day)
gc=float(control.wildlife[:,g].sum());gi=float(infected.wildlife[:,g].sum())
check(gi<gc,'disease effect measured against identical uninfected ecology')
check(gc<g0,'uninfected world also loses game: do not blame all decline on disease')
# Moisture dose response and raider protection, isolated from weather/fire.
def crop(moisture,protect,raiders):
    cfg,w,c=fixture({'wildlife.dispersal':0.});i=int(np.flatnonzero(w.terrain==T['plains'])[0]);c.fields[i]=1
    c.plant(0,np.array([i]));c.moisture[:]=moisture;c.protection[:]=protect;c.wildlife[:]=0
    if raiders:
        j=c.wl_ids.index('raider');c.wildlife[:,j]=c.wl_k[j]*c.vegetation
    for day in range(160):c.step_crops(day);c.step_wildlife(day)
    return c.units_per_field*min(1.,c.crop_progress[i])*c.crop_health[i]
optimal=crop(.5,0,False);dry=crop(.05,0,False);guarded=crop(.5,.9,True);raided=crop(.5,0,True)
check(optimal>dry,'moisture stress lowers crop output in isolated pair')
check(optimal>guarded>raided,'protection reduces raids but is not a magical yield source')
# Former fire probes (slow edge, external ignition hour) are now assertions in tests/test_fire.py.
cfg,w,c=fixture({'fire.base_hours':40.})
c.moisture[:]=0;c.fuel[:]=np.where(c.land,.9,0);c.wind_kph[:]=0
source=int(np.flatnonzero(w.mainland())[0]);c.ignite(0,[source],'test')
for d in range(4):c.step_fire(d)
slow_front_reached=int((c.burn_scar_day>=0).sum())
check(slow_front_reached>1,'slow (44 h) crossings persist across days and reach neighbours')
cfg,w,c=fixture();c.fuel[:]=np.where(c.land,.9,0);c.moisture[:]=0;c.wind_kph[:]=0
c.ignite(0,[source],'test',hour=20.);c.step_fire(0)
retained_hour=abs(float(c.burn_hour[source])-20.)<1e-6
check(retained_hour,'external ignition hour survives the daily fire pass')
report={'suite':'ecology-controls','checks':len(checks),'failures':[],
 'disease_only':{'initial_hosts':initial,'uninfected_final':initial,
                 'infected_final':disease_final,'trajectory':curve},
 'full_ecology_pair':{'initial_game':g0,'uninfected_final':gc,'infected_final':gi,'incremental_loss':gc-gi},
 'isolated_crop_units':{'optimal':float(optimal),'dry':float(dry),'guarded':float(guarded),'raided':float(raided)},
 'fire_front_regressions':{'slow_edge_burned_cells':slow_front_reached,'external_ignition_hour_preserved':retained_hour,
   'meaning':'Asserted; full semantics and edge cases in tests/test_fire.py and test-results/fire.json. Fire remains uncalibrated.'},
 'scope':'Synthetic controls of implemented equations; not real-world calibration. Disease advection counts now conserved; human/herd transmission still absent.'}
(ROOT/'test-results/ecology-controls.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='disease_only'},indent=2))
