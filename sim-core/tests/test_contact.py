"""contact-v1: an engine-side intraday contact phase (after decisions, before sleep) in which co-present
people pass MEMORY to each other through the people.tell primitive. Hearsay = reduced fidelity, hops+1,
from = teller, source 'hearsay'; overhearing = crowd fidelity. Rates, caps and hours are [contact] data;
what a telling is worth is [gossip] data. Rolls are keyed (day, initiator, slot). Verified here: config
contract, no transfer without co-presence, fidelity arithmetic, caps and budget charges, determinism,
same-seed/tier independence (no tier exists to read), overhearing, hop decay in choice, memory record
shape in checkpoints, resume bit-identity, the agency packet fields, and a plan broken by hearsay.
"""
import sys, time, json, tempfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import load, ConfigError
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.people import PeopleError
from chronicle.agency import ScriptedPolicy, ReplayPolicy
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
CHATTY={'contact.p_base':1.0,'gossip.overhear_p':1.0}
QUIET={'contact.p_base':0.0}

def build(extra=None, far_dee=True):
    """Ada (literate, h:0), Bo (h:1), Cy (h:2) in one house; Dee (h:3) next door. Only Ada's own record is a
    day-0 memory nobody else can get except by being told (Bo/Cy witness it in the same room on day 0)."""
    e=Engine(load(overrides={**BASE,**(extra or {})}))
    land=np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains'])); A=int(land[0])
    home=e.seed_place('house',A,'h:0'); far=e.seed_place('house',A,'h:1')
    ids={'cell':A,'home':home,'far':far}
    for n,h,pl in [('ada',0,home),('bo',1,home),('cy',2,home),('dee',3,far if far_dee else home)]:
        ids[n]=e.add_person(-30*365,A,pl,household_id=h,name=n.title(),capabilities=['literate'] if n=='ada' else ()).id
    e.inventory.create(0,'grain',2000,f'b:{home}','h:0',source='setup')
    for h,pl in [(1,home),(2,home),(3,far if far_dee else home)]: e.inventory.create(0,'grain',600,f'p:{ids[["ada","bo","cy","dee"][h]]}',f'h:{h}',source='setup')
    sheet=e.inventory.create(0,'sheet',1,f'p:{ids["ada"]}','h:0',source='setup')[0]
    ids['rec']=e.people.record(0,ids['ada'],sheet,[{'type':'note','text':'hello'}])
    return e,ids
def tells(e): return [ev for ev in e.ledger.events if ev.type=='tell']
def mem(e,pid): return e.people.rows[pid].memory

t0=time.perf_counter()
# ---------------------------------------------------------------- configuration contract
for k,v,label in [('contact.p_base',2.0,'p_base above 1'),('contact.bogus',1,'unknown contact key'),('gossip.hop_decay',-1.0,'negative hop decay'),
                  ('events.witness.tell','nowhere','unknown witness scope'),('contact.hours_per_exchange',99.0,'exchange longer than the day'),('gossip.salience.default',-1.0,'negative salience')]:
    check(raises(lambda k=k,v=v:load(overrides={**BASE,k:v}),ConfigError),f'config rejects {label}')
c=load(overrides=BASE)
check(c['events']['witness']['tell']=='none','tell events have witness scope "none": the primitive writes memory itself')
src=''.join(f.read_text() for f in (ROOT/'chronicle').glob('*.py'))
import re
check(not re.search(r"\.tier\b|\[['\"]tier['\"]\]|\btier\s*=", src),'no engine code reads or sets a person tier (contact rates come from role/ties/place only)')

# ---------------------------------------------------------------- the primitive
e,ids=build(QUIET); ada,bo,cy,dee=ids['ada'],ids['bo'],ids['cy'],ids['dee']; rec=ids['rec']
check(rec in mem(e,ada) and rec in mem(e,bo) and rec not in mem(e,dee),'day 0: the record is witnessed in the room, not next door')
check(raises(lambda:e.people.tell(0,dee,ada,rec),PeopleError),'a person cannot tell what they do not remember')
check(raises(lambda:e.people.tell(0,ada,ada,rec),PeopleError),'a person cannot tell themself')
n0=len(e.ledger.events)
check(e.people.tell(0,ada,dee,rec) is True,'telling moves the memory')
m=mem(e,dee)[rec]; g=c['gossip']
check(abs(m['fidelity']-1.0*g['tell_fidelity_mult'])<1e-12 and m['source']=='hearsay' and m['hops']==1 and m['from']==ada and m['day']==0,'hearsay record: fidelity x tell_fidelity_mult, source hearsay, hops 1, from the teller, learned today')
ev=e.ledger.events[n0]
check(len(e.ledger.events)==n0+1 and ev.type=='tell' and ev.actors==(ada,dee) and ev.visibility=='private' and ev.payload=={'event':rec,'fidelity':m['fidelity'],'hops':1,'overheard':False},'one private tell event with the exact payload')
check(rec in mem(e,dee) and mem(e,ada)[rec]['fidelity']==1.0 and all(v['hops']==0 and v['from'] is None for v in mem(e,ada).values()),'the tell event itself is remembered by nobody automatically (witness scope none); the teller keeps the original')
check(e.people.tell(0,ada,dee,rec) is False,'telling again at the same or lower fidelity changes nothing')
check(e.people.tell(0,dee,cy,rec) is False and mem(e,cy)[rec]['fidelity']==1.0,'a witness is not downgraded by hearsay')
e.people.tell(0,dee,ids['dee'] if False else bo,rec) if False else None
e2,ids2=build(QUIET); e2.people.tell(0,ids2['ada'],ids2['dee'],ids2['rec'])
# second hop from Dee to a fresh listener: fidelity multiplies, hops count
fresh=e2.add_person(-30*365,ids2['cell'],ids2['far'],household_id=4,name='Eve').id
e2.people.tell(0,ids2['dee'],fresh,ids2['rec'])
m2=mem(e2,fresh)[ids2['rec']]
check(abs(m2['fidelity']-g['tell_fidelity_mult']**2)<1e-12 and m2['hops']==2 and m2['from']==ids2['dee'],'second-hand hearsay: fidelity multiplies per hop, hops 2, from the last teller')
e2.people.tell(0,ids2['ada'],fresh,ids2['rec'])
check(abs(mem(e2,fresh)[ids2['rec']]['fidelity']-g['tell_fidelity_mult'])<1e-12 and mem(e2,fresh)[ids2['rec']]['hops']==1,'a better source upgrades the record and resets hops to its own')
e3,ids3=build(QUIET); e3.people.tell(0,ids3['ada'],ids3['dee'],ids3['rec'],overheard=True)
check(abs(mem(e3,ids3['dee'])[ids3['rec']]['fidelity']-g['tell_fidelity_mult']*g['crowd_fidelity'])<1e-12,'overhearing multiplies by crowd_fidelity')

# ---------------------------------------------------------------- the phase: nothing without co-presence
e,ids=build(CHATTY); e.step()
check(not tells(e),'day 0: everyone in the room witnessed the same things, so there is nothing to tell yet')
e.step()
tl=tells(e)
check(tl and all(set(ev.actors)<= {ids['ada'],ids['bo'],ids['cy']} for ev in tl),'exchanges happen only among people at the same place; Dee next door is never a party')
check(ids['rec'] not in mem(e,ids['dee']),'nothing crosses a wall: Dee never learns of the record')
e,ids=build(QUIET); e.step()
check(not tells(e),'p_base 0 means no exchanges')
e,ids=build({'contact.slots':0,**CHATTY}); e.step()
check(not tells(e),'zero slots means no exchanges')
e,ids=build({**CHATTY,'contact.hours_per_exchange':0.5}); b0=e.cfg['agriculture']['activity_hours_per_day']
e.step(); b=e.farming.budgets
parties=set(); 
for ev in tells(e):
    if not ev.payload['overheard']: parties|=set(ev.actors)
n_by={pid:sum(1 for ev in tells(e) if not ev.payload['overheard'] and pid in ev.actors) for pid in parties}
check(all(abs(b[pid]-(b0-0.5*n_by[pid]))<1e-9 for pid in parties) and b[ids['dee']]==b0,'each non-overheard exchange charges hours_per_exchange to both parties; overhearing is free; Dee is uncharged')
check(e.farming.actions=={} ,'talking does not relabel the day\'s action (rest/work bookkeeping untouched)')

# ---------------------------------------------------------------- caps
e,ids=build({**CHATTY,'contact.send_cap':1,'contact.slots':5,'gossip.overhear_p':0.0}); e.step()
by_sender={}
for ev in tells(e): by_sender[ev.actors[0]]=by_sender.get(ev.actors[0],0)+1
check(all(v<=1 for v in by_sender.values()),'send_cap bounds how many exchanges a person initiates per day')
e,ids=build({**CHATTY,'contact.receive_cap':1,'contact.slots':5}); e.step()
by_recv={}
for ev in tells(e): by_recv[ev.actors[1]]=by_recv.get(ev.actors[1],0)+1
check(all(v<=1 for v in by_recv.values()),'receive_cap bounds how many exchanges (incl. overheard) a person receives per day')
e,ids=build({**CHATTY,'contact.hours_per_exchange':8.0}); e.step()
check(len([ev for ev in tells(e) if not ev.payload['overheard']])<=1 and all(v>=-1e-9 for v in e.farming.budgets.values()),'exchanges never exceed the activity budget')

# ---------------------------------------------------------------- what gets told: salience and hop decay
e,ids=build({**CHATTY,'gossip.salience.record':0.0,'gossip.salience.default':1.0}); e.step()
check(all(e.ledger.events[ev.payload['event']].type!='record' for ev in tells(e)),'an event with zero salience is never chosen for telling')
e,ids=build({**CHATTY,'gossip.salience.found_asleep':0.0,'gossip.salience.default':1.0}); e.step(); e.step()
told=[e.ledger.events[ev.payload['event']].type for ev in tells(e)]
d_,_i=build(CHATTY); d_.step(); d_.step()
check('found_asleep' in [d_.ledger.events[ev.payload['event']].type for ev in tells(d_)] and 'found_asleep' not in told,'salience is a per-type table: found_asleep travels at default salience and never at zero (the record, witnessed by all three, has no listener left)')
e,ids=build({**CHATTY,'gossip.hop_decay':0.0}); e.step(); e.step(); e.step()
check(all(ev.payload['hops']==1 for ev in tells(e)),'hop_decay 0: second-hand memories are never passed on')

# ---------------------------------------------------------------- determinism and keying
a,_=build(CHATTY); b_,_=build(CHATTY)
for _ in range(5): a.step(); b_.step()
check(a.digest()==b_.digest() and [ev.payload for ev in tells(a)]==[ev.payload for ev in tells(b_)],'same seed, same exchanges')
a,ida=build(CHATTY); b_,idb=build(CHATTY)
b_.people.rows[idb['dee']].traits=('gregarious',)   # a change that no rule reads
for _ in range(3): a.step(); b_.step()
check([(ev.actors,ev.payload) for ev in tells(a)]==[(ev.actors,ev.payload) for ev in tells(b_)],'an irrelevant change elsewhere leaves every exchange identical (rolls are keyed by day/person/slot, not draw order)')
a,ida=build({**CHATTY,'gossip.overhear_p':0.0}); 
for _ in range(3): a.step()
days=[ev.day for ev in tells(a)]
check(days==sorted(days) and all(sum(1 for ev in tells(a) if ev.day==d and ev.actors[0]==pid)<=a.cfg['contact']['slots'] for d in set(days) for pid in a.people.rows),'exchanges are resolved day by day, slot by slot; one initiation per person per slot')

# ---------------------------------------------------------------- agency packet
e,ids=build(CHATTY); seen={}
def pol(pk): seen[pk['day']]=pk; return {'kind':'rest'}
e.agency.bind(ids['dee'],ScriptedPolicy(pol,'p'))
e.people.tell(0,ids['ada'],ids['dee'],ids['rec'])
e.step()
m=next(x for x in seen[0]['memory'] if x['event']==ids['rec'])
check(m['source']=='hearsay' and m['hops']==1 and m['from']==ids['ada'] and m['learned']==0 and m['fidelity']<1.0,'the packet memory section carries source/hops/from/learned for hearsay')
check(all(set(x)=={'event','type','day','actors','cell','place','fidelity','source','learned','hops','from','who','detail'} for x in seen[0]['memory']),'packet memory record shape is fixed')

# a plan broken by hearsay of an old event
e,ids=build(QUIET); 
e.agency.bind(ids['dee'],ScriptedPolicy(lambda pk:{'kind':'plan','goal':{'kind':'other','why':'t'},'steps':[{'action':{'kind':'rest'}}]*2,'assumptions':[]} if pk['day']==0 else {'kind':'rest'},'p'))
e.step()                                              # day 0: adopt, step 1 runs
old_ev=e.ledger.event(0,'died',[ids['bo']],cell=ids['cell'],place=ids['home']) if False else None
e_,ids_=build(QUIET)
# Build a surprise-type event that Dee did not witness: Bo is found asleep in Ada's house on night 0 (Dee is next door).
fa=[ev for ev in e.ledger.events if ev.type=='found_asleep' and ev.actors[0]==ids['bo']]
check(bool(fa) and fa[0].id not in mem(e,ids['dee']),'fixture: a surprise-type event exists that Dee has not heard of')
e.people.tell(1,ids['ada'],ids['dee'],fa[0].id)      # told on day 1, before the day-1 decision
e.step()
r=[x for x in e.agency.log if x['person']==ids['dee']]
check(r[-1]['plan'] and r[-1]['plan']['break']=='surprise','hearsay of an older surprise-type event (learned after adoption) breaks a plan')

# ---------------------------------------------------------------- checkpoint
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'contact.chk'
    full,ids=build(CHATTY); full.agency.bind(ids['dee'],ScriptedPolicy(lambda pk:{'kind':'rest'},'p'))
    for _ in range(12): full.step()
    e,ids2=build(CHATTY); e.agency.bind(ids2['dee'],ScriptedPolicy(lambda pk:{'kind':'rest'},'p'))
    for _ in range(6): e.step()
    e.save_checkpoint(path); r=Engine.load_checkpoint(path)
    check(all(mem(r,pid)==mem(e,pid) for pid in r.people.rows) and any(v['source']=='hearsay' for pid in r.people.rows for v in mem(r,pid).values()),'memory records with hops/from round-trip through a checkpoint')
    r.agency.bind(ids2['dee'],ReplayPolicy(full.agency.log))
    for _ in range(6): r.step()
    check(state_fingerprint(r)==state_fingerprint(full),'resuming through the contact phase is bit-identical')
    bad=Engine.load_checkpoint(path); bad.people.rows[ids2['ada']].memory[ids2['rec']]={'day':0,'fidelity':1.0,'source':'witnessed'}
    from chronicle.checkpoint import validate
    check(raises(lambda:validate(bad),CheckpointError),'a memory record without hops/from is refused by checkpoint validation')
    bad=Engine.load_checkpoint(path); bad.people.rows[ids2['ada']].memory[ids2['rec']]={'day':0,'fidelity':1.0,'source':'witnessed','hops':0,'from':ids2['bo']}
    check(raises(lambda:validate(bad),CheckpointError),'hops 0 with a from-person is inconsistent and refused')

report={'suite':'contact','checks':len(checks),'failures':[],'seconds':round(time.perf_counter()-t0,3),
        'scope':'contact-v1: tell primitive, intraday contact phase, hearsay fidelity/hops, caps/budget, salience and hop decay in choice, keyed determinism, packet fields, plan surprise via hearsay, checkpoint. No lit/model-authored words, no stance/belief update from hearsay, no commitments.'}
(ROOT/'test-results').mkdir(exist_ok=True); (ROOT/'test-results/contact.json').write_text(json.dumps(report,indent=2))
print(f'\ncontact: {len(checks)} checks passed in {report["seconds"]}s')
