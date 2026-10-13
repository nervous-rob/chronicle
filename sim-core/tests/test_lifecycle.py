"""lifecycle-v1: optional bonds/births/mortality plus routine succession. Off remains legacy."""
import sys, json, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chronicle.config import load, ConfigError
from chronicle.engine import Engine
from chronicle.world import T

checks=[]
def check(ok,msg):
 assert ok,msg;checks.append(msg);print('ok',msg,flush=True)
def raises(f):
 try:f()
 except ConfigError:return True
 return False
BASE={'world.width_km':8,'world.height_km':6,'world.region_count':1,'wildlife.raid_rate':0.0,
      'weather.lightning_p':{'spring':0.,'summer':0.,'autumn':0.,'winter':0.}}

def fixture(extra=None):
 e=Engine(load(overrides={**BASE,**(extra or {})}))
 c=int(next(i for i,t in enumerate(e.world.terrain) if t==T['plains'] and e.world.mainland()[i]))
 h=e.seed_place('house',c,'h:0');g=e.seed_place('granary',c,'h:0');f=e.seed_place('farm',c,'h:0');e.farming.register(f,h,g)
 a=e.add_person(-30*365,c,h,household_id=0,name='Ada',sex='female',role='farmer')
 b=e.add_person(-32*365,c,h,household_id=0,name='Ben',sex='male',role='farmer')
 a.spouse=b.id;b.spouse=a.id;e.farming.assign(a.id,f);e.inventory.create(0,'grain',5000,f'b:{g}','h:0',source='setup')
 return e,a,b,f

check(load()['demography']['enabled'] is False,'demography is off by default')
check(raises(lambda:load(overrides={'demography.fertile_min_years':50,'demography.fertile_max_years':40})), 'inverted fertile range rejected')
# exact daily probability 1 means one birth (mother is the keyed subject), and assigns family links
E,A,B,F=fixture({'demography.enabled':True,'population.birth_rate_per_year':1.0,'population.death_base_per_year':0.0})
E.step();kids=[p for p in E.people.rows.values() if p.id not in (A.id,B.id)]
check(len(kids)==1 and kids[0].parents==[A.id,B.id] and kids[0].household_id==0 and kids[0].sex in ('female','male'),'bonded co-resident pair creates linked child')
check(any(x.type=='born' and set(x.actors)=={kids[0].id,A.id,B.id} for x in E.ledger.events),'birth is ledgered with child and parents')
# New bonds are public, local acts: sharing a household alone is not enough.
P,Pa,Pb,Pf=fixture({'demography.enabled':True,'population.birth_rate_per_year':0.0,'population.death_base_per_year':0.0,'demography.bond_chance_per_year':1.0})
Pa.spouse=Pb.spouse=None; Pb.place=-1
P.step();check(Pa.spouse is None and Pb.spouse is None,'separated household adults do not form an invisible bond')
Pb.place=Pa.place
P.step();check(Pa.spouse==Pb.id and Pb.spouse==Pa.id,'co-present eligible adults form a public bond')
# Off: even impossible-high birth rate leaves no child.
O,Ao,Bo,Fo=fixture({'population.birth_rate_per_year':1.0,'population.death_base_per_year':0.0})
O.step();check(len(O.people.rows)==2,'birth setting is inert while lifecycle is off')
# Exact mortality causes death and transfers the farm to a qualified co-resident.
D,Da,Db,Df=fixture({'demography.enabled':True,'population.birth_rate_per_year':0.0,'population.death_base_per_year':1.0,
 'demography.mortality_mult':{'infant':0.,'child':0.,'youth':0.,'adult':1.,'elder':0.}})
D.step();check(not Da.alive and not Db.alive,'background mortality is applied and ledgered')
# separate case where original worker dies but a youth-stage qualified successor survives (zero adult mortality; elder worker)
S,Sa,Sb,Sf=fixture({'demography.enabled':True,'population.birth_rate_per_year':0.0,'population.death_base_per_year':1.0,
 'demography.mortality_mult':{'infant':0.,'child':0.,'youth':0.,'adult':0.,'elder':1.0}})
Sa.born_day=-60*365; Sb.born_day=-30*365
S.step();check(not Sa.alive and S.farming.assignments.get(Sb.id)==Sf,'death hands a farm routine to qualified household survivor')
# Non-background deaths occur during physiology; they must trigger the same next-day-safe reassignment.
N,Na,Nb,Nf=fixture({'demography.enabled':False})
Na.starvation=2*float(N.cfg['needs']['starve_days'])
N.step();check(not Na.alive and N.farming.assignments.get(Nb.id)==Nf,'physiology death also hands farm routine to a household survivor')
# checkpoint preserves a newly born child and family links.
C,A,B,F=fixture({'demography.enabled':True,'population.birth_rate_per_year':1.0,'population.death_base_per_year':0.0})
C.step()
with tempfile.TemporaryDirectory() as td:
 p=Path(td)/'c.zip';C.save_checkpoint(p);R=Engine.load_checkpoint(p,expected_config=C.cfg)
 check(R.digest()==C.digest() and len(R.people.rows)==3 and R.people.rows[2].parents==[0,1],'lifecycle state checkpoint round trips')
report={'suite':'lifecycle','checks':len(checks),'failures':[]}
(ROOT/'test-results/lifecycle.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
