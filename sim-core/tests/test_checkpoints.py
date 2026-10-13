"""Day-boundary checkpoint regression suite. Python stdlib + NumPy only.

Independent recursive comparison visits every actual object attribute (including
reference identity), rather than trusting Engine.digest or the serializer field list.
"""
import os,sys,json,copy,tempfile,zipfile,hashlib,subprocess,time,io
from pathlib import Path
from dataclasses import is_dataclass
from collections import defaultdict
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.inventory import Leg
from chronicle.checkpoint import CheckpointError, state_fingerprint, Codec, canonical, sha, sources, runtime, VERSION

checks=[]
def check(ok,msg):
    assert ok,msg
    checks.append(msg);print('ok',msg,flush=True)
def rejected(fn,text=''):
    try:fn()
    except CheckpointError as e:
        if text:assert text in str(e),(text,str(e))
        return True
    return False

def equal(a,b,path='root',seen=None):
    if seen is None:seen={}
    assert type(a) is type(b),(path,type(a),type(b))
    if a is None or type(a) in (str,int,float,bool):
        assert a==b,(path,a,b);return
    if callable(a):
        assert getattr(a,'__func__',a)==getattr(b,'__func__',b),path
        if hasattr(a,'__self__'):equal(a.__self__,b.__self__,path+'.bound',seen)
        return
    if id(a) in seen:
        assert seen[id(a)]==id(b),(path,'alias relationship lost');return
    seen[id(a)]=id(b)
    if isinstance(a,np.ndarray):
        assert a.dtype==b.dtype and a.shape==b.shape and a.tobytes()==b.tobytes(),path;return
    if isinstance(a,np.generic):assert a.dtype==b.dtype and a.tobytes()==b.tobytes(),path;return
    if isinstance(a,dict):
        assert list(a)==list(b),(path,'map key/order mismatch')
        if isinstance(a,defaultdict):assert type(a.default_factory()) is type(b.default_factory()),path
        for k in a:equal(a[k],b[k],path+'.'+str(k),seen)
        return
    if isinstance(a,(tuple,list)):
        assert len(a)==len(b),path
        for i,(x,y) in enumerate(zip(a,b)):equal(x,y,path+'.'+str(i),seen)
        return
    if type(a) is set:assert a==b,path;return
    if hasattr(a,'__dict__'):
        assert set(vars(a))==set(vars(b)),(path,'attribute coverage mismatch')
        for k,v in vars(a).items():equal(v,getattr(b,k),path+'.'+k,seen)
        return
    if hasattr(a,'__slots__'):
        for k in a.__slots__:equal(getattr(a,k),getattr(b,k),path+'.'+k,seen)
        return
    raise AssertionError((path,type(a)))

BASE={'world.width_km':12,'world.height_km':8,'world.region_count':2,
      'weather.lightning_p':{s:0. for s in ('spring','summer','autumn','winter')}}
CALM={'wildlife.raid_rate':0.,'weather.rain_mm_mean':{s:0. for s in ('spring','summer','autumn','winter')},
      'weather.evaporation_mm':{s:0. for s in ('spring','summer','autumn','winter')},'time.work_days.farmer':1.0}

def farm_fixture(extra=None,remote=False):
    e=Engine(load(overrides={**BASE,**CALM,**(extra or {})}))
    land=np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains']));cell=int(land[0])
    far=int(max(land,key=lambda c:e.world.hex_distance(c,cell))) if remote else cell
    h=e.seed_place('house',cell,'h:0');s=e.seed_place('granary',far if remote else cell,'h:0');f=e.seed_place('farm',far,'h:0')
    p=e.add_person(-30*365,cell,h,household_id=0,role='farmer',name='Grower')
    e.farming.register(f,h,s);e.farming.assign(p.id,f)
    e.inventory.create(0,'grain',15000,f'b:{s}','h:0',source='setup')
    e.inventory.create(0,'grain',420,f'b:{h}','h:0',source='setup')
    if remote:e.inventory.create(0,'grain',2500,f'p:{p.id}','h:0',source='setup')
    return e,p.id,h,s,f

def rich_fixture():
    e=Engine(load(overrides={**BASE,'records.copy_loss':0.,'people.memory_base':5,'people.memory_per_role_weight':0}))
    cell=int(np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains']))[0])
    h=e.seed_place('manor',cell,'h:0');other=e.seed_place('house',cell,'h:1')
    p=e.add_person(-30*365,cell,h,household_id=0,name='Writer',role='tutor',capabilities=['literate'],traits=['patient','curious'])
    q=e.add_person(-10*365,cell,h,household_id=0,name='Learner')
    distant=e.add_person(-30*365,cell,other,household_id=1,name='Elsewhere',capabilities=['literate'])
    e.inventory.create(0,'grain',20000,f'b:{h}','h:0',source='setup')
    e.inventory.create(0,'grain',1000,f'p:{distant.id}','h:1',source='setup')
    sheet=e.inventory.create(0,'sheet',1,f'b:{h}','h:0',source='setup')[0]
    tablet=e.inventory.create(0,'tablet',1,f'b:{h}','h:0',source='setup')[0]
    herd=e.inventory.create(0,'cattle',7,f'b:{h}','h:0',source='setup')[0]
    e.inventory.lots[herd].health['murrain']={'S':3,'E':1,'I':2,'R':1}
    e.people.teach(0,p.id,[q.id],'literate',sheet)
    ev=e.ledger.event(0,'say',[p.id],cell=cell,place=h,words='First archive')
    e.people.record(0,p.id,sheet,[{'type':'cite','event_id':ev},{'type':'note','text':'Archive'}])
    e.people.copy_record(0,p.id,sheet,tablet)
    e.people.seal(0,p.id,sheet)
    p.parents=[distant.id];p.children=[q.id];p.spouse=distant.id;p.sex='female'
    p.disease_state={'test-state':{'phase':'E','day':0}};p.goals=[{'kind':'protect-record','medium':sheet}];p.attention=.321
    e.world.road_level[cell]=2;e.world.road_wear[cell]=.3;e.world.bridge[cell]=True;e.world.ferry[cell]=True
    e.cells.infect('murrain','game',[cell],.2)
    e.step()
    return e,(p.id,q.id,distant.id,h,other,sheet,tablet,herd)

def rewrite(src,dst,mutate_manifest=None,mutate_state=None,corrupt=False,extra=False):
    with zipfile.ZipFile(src) as z:blobs={n:z.read(n) for n in z.namelist()}
    m=json.loads(blobs.pop('manifest.json'))
    if mutate_state:
        codec=Codec({k:v for k,v in blobs.items() if k!='state.json'})
        tree=codec.decode(json.loads(blobs['state.json']));mutate_state(tree)
        codec=Codec();state=canonical(codec.encode(tree));blobs={'state.json':state,**codec.blobs}
        m['members']={n:{'bytes':len(b),'sha256':sha(b)} for n,b in blobs.items()}
    if mutate_manifest:mutate_manifest(m)
    if corrupt:blobs['state.json']+=b' '
    if extra:blobs['surprise.py']=b'raise RuntimeError("not executed")'
    with zipfile.ZipFile(dst,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json',canonical(m))
        for n,b in blobs.items():z.writestr(n,b)

def main():
    start=time.monotonic();timeline={};restart={}
    with tempfile.TemporaryDirectory() as d:
        d=Path(d);path=d/'world.zip'
        # Rich state: records, marginal herd counts, refs, history, cached weather.
        e,ids=rich_fixture();before=state_fingerprint(e);manifest=e.save_checkpoint(path)
        check(before==state_fingerprint(e),'saving is observational: no counters, events, stocks or RNG changed')
        b=Engine.load_checkpoint(path,expected_config=e.cfg);equal(e,b)
        check(True,'independent full object/array comparison covers every actual instance attribute and runtime alias')
        check(b.cfg is b.cells.cfg is b.people.cfg and b.ledger.on_event.__self__ is b and b.farming.e is b,'all runtime references and witness callback rebound to loaded engine')
        check(b.people.rows[ids[1]].learning and b.people._taught==e.people._taught,'partial learning and same-day teaching guard survive')
        check(b.inventory.lots[ids[5]].sealed_by is not None and b.inventory.lots[ids[6]].copied_from==ids[5],'physical record seals, copies and identity survive')
        check(b.inventory.lots[ids[7]].health==e.inventory.lots[ids[7]].health,'integer herd disease counts survive')
        check(b.cells.seir.keys()==e.cells.seir.keys() and all(type(k) is tuple for k in b.cells.seir),'tuple-key wildlife disease maps survive')
        check(type(b.people.rows[0].capabilities) is set and type(b.people.rows[0].traits) is tuple and all(type(k) is int for k in b.people.rows[0].memory),'sets, tuples and integer memory keys are preserved')
        check(np.array_equal(b.world.road_level,e.world.road_level) and np.array_equal(b.world.bridge,e.world.bridge),'mutable roads/bridges/ferries survive')
        p,q,far,h,other,sheet,tablet,herd=ids
        for engine in (e,b):
            a=engine.ledger.event(engine.day,'say',[],cell=engine.places[h].cell,place=h,words='After reload')
            assert a in engine.people.rows[q].memory and a not in engine.people.rows[far].memory
            private=engine.ledger.event(engine.day,'say',[p],cell=engine.places[h].cell,place=h,visibility='private')
            assert private in engine.people.rows[p].memory and private not in engine.people.rows[q].memory
            engine.people.open_record(engine.day,p,sheet)
            engine.people.read_record(engine.day,p,sheet)
            engine.people.teach(engine.day,p,[q],'literate',tablet)
            engine.inventory.transfer(engine.day,[Leg(herd,2,f'p:{p}','h:0')],actors=[p],cell=engine.places[h].cell,place=h)
        e.step();b.step();equal(e,b)
        check(True,'post-reload public/private event-time witnessing, teaching, reading and herd transfer reproduce continuation')
        check(len(e.people.rows[p].memory)<=5,'memory eviction order exercised after reload')
        for _ in range(35):e.step();b.step();equal(e,b)
        check(True,'35 further days agree in every object field, byte array, report, event and conserved stock')
        check(any(not p.alive for p in e.people.rows.values()),'deceased-person state exercised without losing estate')
        # Full two-year production, multiple reload points incl unfinished work.
        a,_,_,_,f=farm_fixture();b,_,_,_,_=farm_fixture()
        points={1,5,183,190,200,366,550,730}
        partial_plant=False;partial_harvest=False;ground=False
        for day in range(1,731):
            a.step();b.step()
            if day in points:
                partial_plant |= b.farming.farms[f].plant_hours>0
                partial_harvest |= b.farming.farms[f].harvest_hours>0
                ground |= any(l.origin_event is not None for l in b.inventory.lots.values())
                old=state_fingerprint(b);b.save_checkpoint(path);b=Engine.load_checkpoint(path)
                equal(a,b);check(old==state_fingerprint(b),f'day {day}: full round trip and uninterrupted trajectory agree')
                timeline[day]=b.summary()
        check(partial_plant and partial_harvest,'checkpoints actually cover unfinished planting AND harvesting')
        check(ground,'harvest provenance identities survive post-production checkpoints')
        check(b.summary()['alive']==1 and b.ledger.checks==730 and not b.ledger.residuals,'reloaded two-year household survives with 730 exact conservation closes')
        check(len([v for v in b.ledger.events if v.type=='harvest'])==2,'two actual harvests, not duplicated by reload')
        # Same source/cadence controls; config manifests remain distinct.
        c,_,_,_,_=farm_fixture({'run.days_per_tick':7});c.run(40);c.save_checkpoint(path);cr=Engine.load_checkpoint(path);cr.run(20);c.run(20);equal(c,cr)
        ref,_,_,_,_=farm_fixture();ref.run(60)
        check(ref.digest()==cr.digest(),'checkpoint continuation preserves 7/30-day reporting-cadence physics invariance')
        # Slow cross-cell journey with frozen entered-edge time and on-person cargo.
        a,p,_,_,_=farm_fixture({'transit.season_mult.spring':.02},remote=True);a.step()
        check(p in a.farming.journeys and a.farming.journeys[p].remaining>0,'checkpoint fixture has a genuine unfinished multi-day edge')
        check(any(l.holder==f'p:{p}' for l in a.inventory.lots.values()),'journey checkpoint includes physical carried food')
        a.save_checkpoint(path);b=Engine.load_checkpoint(path);equal(a,b)
        check(bool(b.farming._routes),'route cache, edge arrays and infinite unreachable costs round-trip')
        old=a.farming.journeys[p].remaining
        for _ in range(6):a.step();b.step();equal(a,b)
        check(a.farming.journeys[p].remaining!=old,'slow journey makes identical progress after reload')
        # Slow fire, nonzero ignition hour, overlap; all from normal Engine steps.
        a=Engine(load(overrides={**BASE,'fire.base_hours':40.}));cell=int(np.flatnonzero(a.world.mainland())[0]);a.cells.moisture[:]=0;a.cells.fuel[:]=np.where(a.cells.land,.9,0)
        a.cells.ignite(0,[cell],'checkpoint-test',a.ledger,hour=18.);a.step()
        check(a.cells.burning.any() and (a.cells.edge_progress>0).any(),'fire checkpoint contains live unfinished fronts and ignition timing')
        a.save_checkpoint(path);b=Engine.load_checkpoint(path);equal(a,b)
        for _ in range(7):a.step();b.step();equal(a,b)
        check(any(v.type=='fire_spread' for v in b.ledger.events),'continued multi-day front spreads after reload with identical hours/events')
        check(len([v for v in b.ledger.events if v.type in ('fire_ignited','fire_spread')])==len({v.cell for v in b.ledger.events if v.type in ('fire_ignited','fire_spread')}),'reloading never duplicates fire episode events')
        # Must be a real successful Engine boundary; read-only accounting validation.
        e=Engine(load(overrides=BASE));check(rejected(lambda:e.save_checkpoint(path),'completed'),'initial setup cannot masquerade as a validated day')
        e.step();valid=d/'valid.zip';e.save_checkpoint(valid);original=valid.read_bytes()
        e._in_step=True;check(rejected(lambda:e.save_checkpoint(valid),'in-progress'),'in-step checkpoints rejected');e._in_step=False
        e.failed=True;check(rejected(lambda:e.save_checkpoint(valid),'failed'),'failed half-day checkpoints rejected');e.failed=False
        e.day+=1;check(rejected(lambda:e.save_checkpoint(valid),'completed'),'manually advanced day counter rejected');e.day-=1
        e.farming.begin(e.day);check(rejected(lambda:e.save_checkpoint(valid),'bookkeeping'),'manual partial-day farm activity rejected');e=Engine.load_checkpoint(valid)
        e.cells.step_weather=lambda day:None
        check(rejected(lambda:e.save_checkpoint(valid),'unclassified'),'monkeypatched fixture behavior is explicitly rejected, never silently lost');del e.cells.step_weather
        e.new_subsystem_state={'unreviewed':True};check(rejected(lambda:e.save_checkpoint(valid),'unclassified'),'new unclassified subsystem fields fail closed');del e.new_subsystem_state
        e.ledger.on_event=lambda event:None;check(rejected(lambda:e.save_checkpoint(valid),'hook'),'unregistered callback rejected');e.ledger.on_event=e._witness_event
        with (d/'external.jsonl').open('w') as out:
            e.ledger._ev_out=out;check(rejected(lambda:e.save_checkpoint(valid),'output handles'),'external ledger stream handles rejected in v1')
        e.ledger._ev_out=None
        check(valid.read_bytes()==original,'failed saves leave previously published checkpoint untouched')
        e,ids=rich_fixture();e.save_checkpoint(valid);original=valid.read_bytes()
        first=next(l for l in e.inventory.lots.values() if l.item=='grain');first.qty+=1
        check(rejected(lambda:e.save_checkpoint(valid),'conservation'),'untracked inventory mutation cannot become a valid checkpoint');first.qty-=1
        e.ledger.event(e.day,'say',[],cell=e.people.rows[0].cell,place=e.people.rows[0].place)
        check(rejected(lambda:e.save_checkpoint(valid),'event'),'new-day events rejected until engine closes that day')
        e=Engine.load_checkpoint(valid);e.inventory.create(e.day,'coin',1,f'p:0','p:0',source='setup')
        check(rejected(lambda:e.save_checkpoint(valid),'pending'),'new-day flows rejected until close')
        # Atomic rename failure must not overwrite previous file or leak temp files.
        e=Engine.load_checkpoint(valid);before_names=set(d.iterdir())
        with patch('chronicle.checkpoint.os.replace',side_effect=OSError('simulated disk fault')):
            check(rejected(lambda:e.save_checkpoint(valid),'disk fault'),'atomic replace failure is reported')
        check(valid.read_bytes()==original and set(d.iterdir())==before_names,'atomic failure preserves old bytes and cleans temporary file')
        with patch('chronicle.checkpoint.sources',return_value={'changed.py':'not-this-code'}):
            check(rejected(lambda:e.save_checkpoint(valid),'source changed'),'source modified during interpreter lifetime rejected on save')
            check(rejected(lambda:Engine.load_checkpoint(valid),'source changed'),'source modified during interpreter lifetime rejected on load')
        # Damage, provenance mismatch and rechecksummed structural defects.
        bad=d/'bad.zip';bad.write_bytes(valid.read_bytes()[:40]);check(rejected(lambda:Engine.load_checkpoint(bad)),'truncated archive rejected')
        rewrite(valid,bad,corrupt=True);check(rejected(lambda:Engine.load_checkpoint(bad),'checksum'),'content damage rejected by checksum')
        rewrite(valid,bad,extra=True);check(rejected(lambda:Engine.load_checkpoint(bad),'unexpected'),'unexpected archive member rejected without extraction/execution')
        for key,value,label in [('version',999,'schema'),('rng_layout',1,'RNG'),('runtime',{},'runtime'),('source_sha256',{},'source'),('day',999,'day'),('run_id','wrong','config')]:
            rewrite(valid,bad,mutate_manifest=lambda m,k=key,v=value:m.__setitem__(k,v))
            check(rejected(lambda:Engine.load_checkpoint(bad)),label+' incompatibility rejected')
        wrong=load(overrides={**BASE,'run.seed':123456});check(rejected(lambda:Engine.load_checkpoint(valid,expected_config=wrong),'configuration'),'caller expected-config mismatch rejected')
        mutations=[
         ('event index',lambda s:s['objects']['ledger'].__setitem__('_day_start',99999)),
         ('bucket totals',lambda s:s['objects']['ledger']['bucket_totals']['src:setup'].__setitem__('grain',1)),
         ('teaching day',lambda s:s['objects']['people'].__setitem__('_teaching_day',99999)),
         ('missing subsystem',lambda s:s['objects'].pop('farming')),
         ('missing field',lambda s:s['objects']['cells'].pop('edge_progress')),
         ('unknown field',lambda s:s['objects']['engine'].__setitem__('future',1)),
         ('array shape',lambda s:s['objects']['cells'].__setitem__('moisture',np.zeros(1,np.float32))),
         ('array dtype',lambda s:s['objects']['cells'].__setitem__('moisture',s['objects']['cells']['moisture'].astype(np.float64))),
         ('config derived',lambda s:s['config']['derived'].__setitem__('cells',999)),
         ('stored RNG seed',lambda s:s.__setitem__('rng_seed',-1)),
         ('lot identity counter',lambda s:s['objects']['inventory'].__setitem__('next_id',0)),
         ('person location',lambda s:setattr(s['objects']['people']['rows'][0],'cell',99999)),
         ('memory event',lambda s:s['objects']['people']['rows'][0].memory.__setitem__(99999,{'fidelity':1.})),
         ('inventory mismatch',lambda s:setattr(next(iter(s['objects']['inventory']['lots'].values())),'qty',1234)),
         ('unfinished day',lambda s:s['objects']['engine'].__setitem__('_completed_day',-1)),
        ]
        for label,fn in mutations:
            rewrite(valid,bad,mutate_state=fn);check(rejected(lambda:Engine.load_checkpoint(bad)),label+' rejected even with recalculated file checksums')
        # Actual process restart: a new interpreter restores alone and continues.
        e,_,_,_,_=farm_fixture();e.run(5);e.save_checkpoint(path);out=d/'child.zip'
        script="from chronicle.engine import Engine; import sys; e=Engine.load_checkpoint(sys.argv[1]); e.run(20); e.save_checkpoint(sys.argv[2]); print(e.day)"
        env=dict(os.environ,PYTHONPATH=str(ROOT),PYTHONHASHSEED='193')
        child=subprocess.run([sys.executable,'-c',script,str(path),str(out)],env=env,text=True,capture_output=True,timeout=30)
        assert child.returncode==0,child.stderr
        e.run(20);b=Engine.load_checkpoint(out);equal(e,b)
        check(True,'fresh interpreter with different hash seed reproduces 20-day continuation')
        restart={'exit_code':child.returncode,'stdout':child.stdout.strip(),'continuation_days':20}
        # Coverage guard detects fields absent from Engine.digest too.
        before=e.digest();e.world.road_wear[0]+=.123
        check(e.digest()==before,'existing digest omits road wear: completeness test is meaningfully stronger')
        e.save_checkpoint(path);b=Engine.load_checkpoint(path);equal(e,b)
        check(True,'serializer preserves road wear despite digest omission')
        # Preserve aliasing of intentionally shared runtime containers, not just values.
        shared={'reason':['shared-test']};e.people.rows[0].goals=[shared,shared]
        e.save_checkpoint(path);b=Engine.load_checkpoint(path);equal(e,b)
        check(b.people.rows[0].goals[0] is b.people.rows[0].goals[1],'shared mutable data identities survive without duplicate independent copies')
        shared['cycle']=shared
        check(rejected(lambda:e.save_checkpoint(path),'cyclic'),'unsupported cyclic data rejected explicitly')
        shared.pop('cycle')
        # Sample-disabled runs still receive read-only exact checkpoint validation.
        sampled,_,_,_,_=farm_fixture({'validator.conservation_sample':0.0});sampled.step()
        count=sampled.ledger.checks;sampled.save_checkpoint(path);sb=Engine.load_checkpoint(path)
        check(count==0 and sb.ledger.checks==0 and sb.last_check_day is None,'checkpoint accounting validation does not rewrite sampled-day evidence')
        # Baseline food-failure branch also resumes, not just favorable survival.
        raw,_,_,_,_=farm_fixture({'wildlife.raid_rate':.02,'weather.rain_mm_mean':{s:3. for s in ('spring','summer','autumn','winter')}})
        raw.run(300);raw.save_checkpoint(path);rr=Engine.load_checkpoint(path);raw.run(65);rr.run(65);equal(raw,rr)
        check(True,'365-day non-favorable ecology branch also reproduces all continuation state')
        sizes={'rich_checkpoint_bytes':len(original),'latest_checkpoint_bytes':path.stat().st_size}
    report={'suite':'checkpoints','checks':len(checks),'failures':[],'labels':checks,'seconds':round(time.monotonic()-start,3),
            'schema_version':VERSION,'runtime':runtime(),'source_sha256':sources(),'two_year_reload_points':timeline,'fresh_process':restart,'sizes':sizes,
            'scope':'Same-source/runtime, successfully completed day boundaries; data-only ZIP/JSON/NPY. No monkeypatches, external streams, migrations, concurrent writes, LLM inference or full society claims.'}
    (ROOT/'test-results/checkpoints.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('labels','two_year_reload_points','source_sha256')},indent=2))

if __name__=='__main__':main()
