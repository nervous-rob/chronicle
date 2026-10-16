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
# Exact daily probability conceives first; a due birth later assigns durable family links.
E,A,B,F=fixture({'demography.enabled':True,'population.birth_rate_per_year':1.0,'population.death_base_per_year':0.0,'demography.gestation_days':1})
E.step();check(A.pregnancy=={'father':B.id,'due_day':1} and any(x.type=='conceived' and set(x.actors)=={A.id,B.id} for x in E.ledger.events),'co-resident bonded pair conceives publicly before birth')
E.step();kids=[p for p in E.people.rows.values() if p.id not in (A.id,B.id)]
check(len(kids)==1 and kids[0].parents==[A.id,B.id] and kids[0].household_id==0 and kids[0].sex in ('female','male'),'due pregnancy creates linked child')
check(any(x.type=='born' and set(x.actors)=={kids[0].id,A.id,B.id} for x in E.ledger.events),'birth is ledgered with child and parents')
# A born child remains roleless through youth, then crosses adulthood publicly and may
# inherit a parent role only at that point.  The test moves the clock-age directly so
# the transition, rather than twenty simulated years of unrelated physics, is isolated.
G,Ga,Gb,Gf=fixture({'demography.enabled':True,'population.birth_rate_per_year':0.0,'population.death_base_per_year':0.0,
                     'development.role_inherit_p':1.0})
child=G.add_person(G.day,Gb.cell,Gb.place,household_id=0,name='Cora',sex='female',parents=(Ga.id,Gb.id),emit_birth=True)
child.stage_seen='youth'; child.born_day=G.day-20*365
G.step()
check(child.role=='farmer' and child.role_since_day==G.day-1 and any(x.type=='stage' and x.actors==(child.id,) and x.payload.get('stage')=='adult' for x in G.ledger.events),'born child becomes an adult publicly and then inherits a viable parent role')
# The pending adulthood handoff is durable across a day-boundary checkpoint.
H,Ha,Hb,Hf=fixture({'demography.enabled':True,'population.birth_rate_per_year':0.0,'population.death_base_per_year':0.0,
                     'development.role_inherit_p':1.0})
teen=H.add_person(H.day,Hb.cell,Hb.place,household_id=0,name='Eli',sex='male',parents=(Ha.id,Hb.id),emit_birth=True)
teen.stage_seen='youth'; teen.born_day=H.day-20*365+1
# Checkpoint only exists at a completed day boundary. Advance while Eli is still youth;
# the following day is the adulthood transition in both original and reloaded engines.
H.step()
with tempfile.TemporaryDirectory() as td:
 q=Path(td)/'adult.zip';H.save_checkpoint(q);J=Engine.load_checkpoint(q,expected_config=H.cfg)
 H.step();J.step()
 check(H.digest()==J.digest() and J.people.rows[teen.id].role=='farmer','pending adulthood handoff resolves deterministically after reload')
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
# Estate succession follows designated heir before spouse, transfers all household ownership while
# leaving goods physically where they were, and lets the heir retain the farm routine.
I,Ia,Ib,If=fixture({'demography.enabled':True,'population.birth_rate_per_year':0.0,'population.death_base_per_year':1.0,
 'demography.mortality_mult':{'infant':0.,'child':0.,'youth':0.,'adult':0.,'elder':1.0}})
heir=I.add_person(-25*365,Ia.cell,Ia.place,household_id=0,name='Cara',sex='female',role='farmer')
Ia.born_day=-60*365; Ia.designated_heir=heir.id; Ia.estate_owner=True
I.step()
owned_lots=[l for l in I.inventory.lots.values() if l.owner==f'p:{heir.id}']
check(not Ia.alive and I.places[Ia.home_place].owner==f'p:{heir.id}' and I.farming.farms[If].owner==f'p:{heir.id}' and owned_lots and any(l.holder==f'b:{I.farming.farms[If].store}' for l in owned_lots) and all(l.holder in {f'b:{I.farming.farms[If].store}',f'b:{I.farming.farms[If].home}'} for l in owned_lots) and I.farming.assignments.get(heir.id)==If,'designated heir receives estate, preserves custody, and can continue farm work')
check(any(x.type=='transfer' and x.payload.get('estate') and x.payload.get('owner_to')==f'p:{heir.id}' for x in I.ledger.events),'estate succession is publicly ledgered')
# Estate ownership can change while another household member has the active field routine.
# That worker must lose stale authority and the qualified heir must receive the routine.
R,Ra,Rb,Rf=fixture({'demography.enabled':True,'population.birth_rate_per_year':0.0,'population.death_base_per_year':1.0,
 'demography.mortality_mult':{'infant':0.,'child':0.,'youth':0.,'adult':0.,'elder':1.0}})
head=R.add_person(-60*365,Ra.cell,Ra.place,household_id=0,name='Head',sex='female',role='farmer')
heir2=R.add_person(-25*365,Ra.cell,Ra.place,household_id=0,name='Heir',sex='female',role='farmer')
head.estate_owner=True; head.designated_heir=heir2.id
R.step()
check(not head.alive and R.farming.farms[Rf].owner==f'p:{heir2.id}' and R.farming.assignments.get(heir2.id)==Rf and Rb.id not in R.farming.assignments,'estate-owner death reconciles stale farm workers to the qualified heir')
# A dependent may be designated, but cannot take an estate before adulthood; the next
# qualified successor (the spouse here) receives it instead.
M,Ma,Mb,Mf=fixture({'demography.enabled':True,'population.birth_rate_per_year':0.0,'population.death_base_per_year':1.0,
 'demography.mortality_mult':{'infant':0.,'child':0.,'youth':0.,'adult':0.,'elder':1.0}})
minor=M.add_person(M.day,Ma.cell,Ma.place,household_id=0,name='Dara',sex='female',parents=(Ma.id,Mb.id),emit_birth=True)
Ma.born_day=-60*365; Ma.designated_heir=minor.id; Ma.estate_owner=True
M.step()
check(not Ma.alive and M.places[Ma.home_place].owner==f'p:{Mb.id}','dependent designated heir waits; qualified spouse receives the estate')
# Kin succession traverses the full parent/child graph, not only a parent or sibling.
# Here Ada's parent is dead, but that parent has a living sister through Ada's dead grandparent.
K,Ka,Kb,Kf=fixture({'demography.enabled':True,'population.birth_rate_per_year':0.0,'population.death_base_per_year':1.0,
 'demography.mortality_mult':{'infant':0.,'child':0.,'youth':0.,'adult':0.,'elder':1.0}})
parent=K.add_person(-65*365,Ka.cell,Ka.place,household_id=7,name='Parent',sex='male',role='farmer')
grand=K.add_person(-90*365,Ka.cell,Ka.place,household_id=7,name='Grand',sex='female',role='farmer')
aunt=K.add_person(-40*365,Ka.cell,Ka.place,household_id=7,name='Aunt',sex='female',role='farmer')
parent.died_day=-1; grand.died_day=-1
Ka.parents=[parent.id]; parent.children=[Ka.id]; parent.parents=[grand.id]; grand.children=[parent.id,aunt.id]; aunt.parents=[grand.id]
Ka.born_day=-60*365; Ka.spouse=Kb.spouse=None; Ka.estate_owner=True
K.step()
check(not Ka.alive and K.places[Ka.home_place].owner==f'p:{aunt.id}','nearest eligible blood kin inherits beyond parent/sibling fallback')
# Death dissolves a reciprocal bond after succession, leaving the survivor free for a later public bond.
W,Wa,Wb,Wf=fixture({'demography.enabled':True,'population.birth_rate_per_year':0.0,'population.death_base_per_year':1.0,
 'demography.mortality_mult':{'infant':0.,'child':0.,'youth':0.,'adult':0.,'elder':1.0}})
Wa.born_day=-60*365; Wa.estate_owner=True
W.step()
check(not Wa.alive and Wb.spouse is None and any(x.type=='unbond' and x.payload.get('cause')=='death' for x in W.ledger.events),'death dissolves the surviving spouse bond after estate succession')
# With no qualifying relation, the estate is explicitly unowned rather than permanently dead-owned.
U,Ua,Ub,Uf=fixture({'demography.enabled':True,'population.birth_rate_per_year':0.0,'population.death_base_per_year':1.0,
 'demography.mortality_mult':{'infant':0.,'child':0.,'youth':0.,'adult':0.,'elder':1.0}})
Ua.born_day=-60*365; Ua.spouse=Ub.spouse=None; Ua.estate_owner=True
U.step()
check(U.places[Ua.home_place].owner is None and all(l.owner is None for l in U.inventory.lots.values()),'estate without heir becomes unowned')
# Checkpoint preserves an in-progress pregnancy; reload yields the same later birth.
C,A,B,F=fixture({'demography.enabled':True,'population.birth_rate_per_year':1.0,'population.death_base_per_year':0.0,'demography.gestation_days':2})
C.step()
with tempfile.TemporaryDirectory() as td:
 p=Path(td)/'c.zip';C.save_checkpoint(p);R=Engine.load_checkpoint(p,expected_config=C.cfg)
 check(R.digest()==C.digest() and R.people.rows[A.id].pregnancy=={'father':B.id,'due_day':2},'pregnancy state checkpoint round trips')
 C.step();C.step();R.step();R.step()
 check(R.digest()==C.digest() and len(R.people.rows)==3 and R.people.rows[2].parents==[0,1],'pregnancy resolves deterministically after reload')
report={'suite':'lifecycle','checks':len(checks),'failures':[]}
(ROOT/'test-results/lifecycle.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
