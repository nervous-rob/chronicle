"""Version 1 checkpoints: data only, explicit state inventory, strict source/runtime resume.

ZIP contains manifest.json, state.json and allow_pickle=False NPY arrays. The typed
JSON codec preserves dictionary insertion order, tuple keys, sets, NumPy scalars,
and dataclass field types. No import names, callables, file handles or pickle are
accepted. Checksums detect damage, not malicious forgery/authenticity.

Only successfully completed Engine days are accepted (not initial setup/half-days).
All runtime refs are rebound once, without replaying events or generating a world.
External ledger output handles and instance monkeypatches are rejected explicitly.
No schema/source/runtime migration or concurrent mutation support in this version.
"""
from __future__ import annotations
import base64, hashlib, io, json, math, os, platform, sys, tempfile, zipfile
from collections import defaultdict
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from .config import Config, _validate, _derive, EVENT_TYPES, VISIBILITY
from .rng import Streams, LAYOUT_VERSION
from .world import World
from .cells import Cells
from .ledger import Ledger, Event
from .inventory import Inventory, Holder, Lot
from .people import People, Person
from .farming import Farming, Farm, Journey, Household
from .projects import Projects, Project
from .engine import Engine, Place
from .agency import Agency, LOG_FIELDS, ACTIONS, PLAN_FIELDS, BREAKS

VERSION = 10  # exploration-v1: personal durable cell-knowledge snapshots.
# VERSION 4   # contact-v1: memory records carry hops/from; tell events; [contact]/[gossip] wired
MAX_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 20000
class CheckpointError(ValueError): pass

def require(ok, message):
    if not ok: raise CheckpointError(message)

# Every instance attribute is explicitly classified. New engine fields fail closed
# until reviewed here; no silently omitted future subsystem state.
STATE = {
 'engine': 'places day reports last_check_day failed _in_step _completed_day deprivation'.split(),
 'agency': 'agents log day plans'.split(),
 'projects': 'projects next_id'.split(),
 'world': 'w h n cell_km day_hours ys xs x_km y_km nbr terrain elevation slope river region water_adjacent river_adjacent road_level road_wear bridge ferry _hours_per_plains_cell region_seeds'.split(),
 'cells': 'land wl_ids wl_k moisture vegetation fuel rain_mm protection cleared burning burn_hour burn_scar_day burn_since_day edge_progress crop_id units_per_field fields planted crop_progress crop_health wildlife seir regions wet wind_dir wind_kph land_nbr deg last_fire_cells wl_initial budget'.split(),
 'ledger': 'kind_of events by_day _day_start _cur_day _f_res _f_from _f_to _f_qty _f_proc _f_day expected bucket_totals n_flows checks residuals'.split(),
 'inventory': 'holders lots next_id _decay_day'.split(),
 'people': 'rows _taught _teaching_day'.split(),
 'farming': 'farms assignments households keepers journeys day budgets actions labor _routes'.split(),
}
# agency.policies are live callables: never serialized, cleared on load, rebind explicitly.
REFS = {
 'engine': 'cfg rng world cells ledger inventory people farming agency projects'.split(),
 'world': ['cfg'], 'cells': 'cfg world rng wl_cfg crop'.split(),
 'ledger': 'cfg on_event _ev_out _fl_out'.split(),
 'inventory': 'cfg ledger'.split(), 'people': 'cfg inventory ledger rng'.split(), 'farming': ['e'], 'agency': ['e', 'policies'], 'projects': ['e'],
}
CLASSES={'engine':Engine,'world':World,'cells':Cells,'ledger':Ledger,'inventory':Inventory,'people':People,'farming':Farming,'agency':Agency,'projects':Projects}
RECORDS={c.__name__:c for c in (Place,Event,Holder,Lot,Person,Farm,Journey,Household,Project)}

def canonical(x): return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(b): return hashlib.sha256(b).hexdigest()
def runtime():
    return {'python':platform.python_version(),'implementation':platform.python_implementation(),
            'numpy':np.__version__,'byteorder':sys.byteorder,'machine':platform.machine(),'system':platform.system()}
def sources():
    root=Path(__file__).resolve().parent
    return {p.name:sha(p.read_bytes()) for p in sorted(root.glob('*.py'))}

# Refuse source files edited while this interpreter still holds old imported code.
# checkpoint is imported by Engine.__init__, before constructing resumable state.
LOADED_SOURCE_SHA256 = sources()

def dd_int(): return defaultdict(int)

class Codec:
    def __init__(self, blobs=None):
        self.blobs={} if blobs is None else blobs; self.used=set()
        self.encoded={}; self.decoded={}; self.active=set()
        self.owned=[]  # keep transient encoded containers alive; avoid Python id reuse
    def encode(self,x):
        tracked=isinstance(x,(dict,list,tuple,set,np.ndarray)) or type(x).__name__ in RECORDS
        if not tracked:return self._encode(x)
        identity=id(x)
        require(identity not in self.active,'cyclic data containers unsupported')
        if identity in self.encoded:return {'@':'ref','id':self.encoded[identity]}
        number=len(self.encoded);self.encoded[identity]=number;self.owned.append(x);self.active.add(identity)
        node=self._encode(x);self.active.remove(identity);node['$id']=number
        return node
    def _encode(self,x):
        if x is None or type(x) in (bool,int,str): return x
        if type(x) is float:
            require(not math.isnan(x),'NaN scalar state')
            return x if math.isfinite(x) else {'@':'infinity','sign':1 if x>0 else -1}
        if isinstance(x,np.ndarray):
            require(x.dtype.kind in 'biuf' and not x.dtype.hasobject,'unsupported array dtype')
            name=f'arrays/{len(self.blobs):06d}.npy'; b=io.BytesIO(); np.save(b,x,allow_pickle=False)
            self.blobs[name]=b.getvalue();return {'@':'array','name':name}
        if isinstance(x,np.generic):
            a=np.asarray(x);require(a.dtype.kind in 'biuf','unsupported numpy scalar')
            return {'@':'scalar','dtype':a.dtype.str,'hex':a.tobytes().hex()}
        if type(x).__name__ in RECORDS and type(x) is RECORDS[type(x).__name__]:
            names=[f.name for f in fields(x)]
            require(set(vars(x))==set(names),'unclassified dataclass state')
            return {'@':'record','class':type(x).__name__,'fields':self.encode({k:getattr(x,k) for k in names})}
        if type(x) in (dict,defaultdict):
            kind='dict'
            if type(x) is defaultdict:
                # Only the two ledger factory shapes are permitted. Lambdas from
                # Ledger.__init__ are recognized by their empty product, not saved.
                empty=x.default_factory()
                require(type(empty) is defaultdict and empty.default_factory is int,'unknown defaultdict factory')
                kind='ledger-map'
                return {'@':kind,'items':[[self.encode(k),self.encode(dict(v))] for k,v in x.items()]}
            return {'@':kind,'items':[[self.encode(k),self.encode(v)] for k,v in x.items()]}
        if type(x) in (list,tuple,set):
            items=[self.encode(v) for v in x]
            if type(x) is set: items.sort(key=lambda v:canonical(v))
            return {'@':type(x).__name__,'items':items}
        raise CheckpointError(f'unsupported state type {type(x).__name__}')
    def decode(self,x,depth=0):
        if type(x) is dict and x.get('@')=='ref':
            require(set(x)=={'@','id'} and type(x['id']) is int and x['id'] in self.decoded,'invalid/forward alias reference')
            return self.decoded[x['id']]
        if type(x) is dict and '$id' in x:
            number=x['$id'];require(type(number) is int and number>=0 and number not in self.decoded,'duplicate/invalid object identity')
            value=self._decode({k:v for k,v in x.items() if k!='$id'},depth)
            self.decoded[number]=value
            return value
        return self._decode(x,depth)
    def _decode(self,x,depth=0):
        require(depth<160,'state nesting limit')
        if x is None or type(x) in (bool,int,str): return x
        if type(x) is float:
            require(math.isfinite(x),'nonfinite JSON scalar');return x
        require(type(x) is dict and '@' in x,'invalid typed state')
        tag=x['@']; dec=lambda v:self.decode(v,depth+1)
        if tag=='infinity':
            require(set(x)=={'@','sign'} and x['sign'] in (-1,1),'invalid infinity')
            return math.inf*x['sign']
        if tag=='array':
            require(set(x)=={'@','name'} and x['name'] in self.blobs,'missing array')
            self.used.add(x['name'])
            raw=self.blobs[x['name']]; stream=io.BytesIO(raw)
            version=np.lib.format.read_magic(stream)
            require(version in ((1,0),(2,0)),'unsupported NPY version')
            reader=np.lib.format.read_array_header_1_0 if version==(1,0) else np.lib.format.read_array_header_2_0
            shape,order,dtype=reader(stream)
            require(dtype.kind in 'biuf' and not dtype.hasobject and len(shape)<=4 and all(0<=v<=MAX_BYTES for v in shape),'invalid NPY schema')
            require(math.prod(shape)*dtype.itemsize==len(raw)-stream.tell(),'NPY byte length/shape mismatch')
            a=np.load(io.BytesIO(raw),allow_pickle=False)
            require(type(a) is np.ndarray and a.dtype.kind in 'biuf' and not a.dtype.hasobject,'unsupported array')
            return a
        if tag=='scalar':
            require(set(x)=={'@','dtype','hex'},'bad numpy scalar')
            d=np.dtype(x['dtype']);b=bytes.fromhex(x['hex'])
            require(d.kind in 'biuf' and len(b)==d.itemsize,'invalid scalar bytes')
            return np.frombuffer(b,dtype=d)[0]
        if tag=='record':
            require(set(x)=={'@','class','fields'} and x['class'] in RECORDS,'unknown record type')
            c=RECORDS[x['class']];v=dec(x['fields'])
            require(type(v) is dict and set(v)=={f.name for f in fields(c)},'record fields incompatible')
            return c(**v)
        require(set(x)=={'@','items'} and type(x['items']) is list,'invalid container')
        if tag in ('dict','ledger-map'):
            out={}
            for pair in x['items']:
                require(type(pair) is list and len(pair)==2,'invalid map entry')
                k,v=map(dec,pair);require(k not in out,'duplicate decoded map key');out[k]=v
            if tag=='ledger-map':
                require(all(type(v) is dict for v in out.values()),'invalid ledger map')
                return defaultdict(dd_int,{k:defaultdict(int,v) for k,v in out.items()})
            return out
        require(tag in ('list','tuple','set'),'unknown state tag')
        v=[dec(i) for i in x['items']]
        return {'list':list,'tuple':tuple,'set':set}[tag](v)

def config_check(c):
    _validate(c.raw,c.catalog)
    require(c.raw['rng']['layout_version']==LAYOUT_VERSION,'RNG layout mismatch')
    frozen=json.dumps({'run':c.raw,'catalog':c.catalog},sort_keys=True,separators=(',',':')).encode()
    require(c.run_id==sha(frozen)[:12] and c.seed==c.raw['run']['seed'],'configuration hash/seed mismatch')
    require(c.derived==_derive(c.raw,c.catalog),'derived configuration mismatch')

def objects(e): return {'engine':e,**{k:getattr(e,k) for k in STATE if k!='engine'}}

def validate(e):
    """Read-only boundary, field coverage, reference and conserved-state checks."""
    require(sources()==LOADED_SOURCE_SHA256,'source changed in this interpreter; restart before checkpointing')
    config_check(e.cfg)
    for name,o in objects(e).items():
        require(type(o) is CLASSES[name],f'unsupported {name} subclass')
        require(set(vars(o))==set(STATE[name]+REFS[name]),f'unclassified/missing {name} fields: {set(vars(o)) ^ set(STATE[name]+REFS[name])}')
    require(type(e.day) is int and e.day>0 and e._completed_day==e.day-1,'checkpoint requires a completed engine day')
    require(not e.failed and not e._in_step,'failed or in-progress engine cannot checkpoint')
    require(e.farming.day==e.day-1 and e.inventory._decay_day==e.day-1,'daily bookkeeping mismatch')
    require(e.ledger._ev_out is None and e.ledger._fl_out is None,'external ledger output handles are not resumable in v1')
    hook=e.ledger.on_event
    require(getattr(hook,'__self__',None) is e and getattr(hook,'__func__',None) is Engine._witness_event,'event hook must be the engine witness callback')
    require(e.agency.e is e and type(e.agency.policies) is dict and e.projects.e is e,'agency/project runtime references broken')
    require(e.farming.e is e and e.cells.world is e.world and e.inventory.ledger is e.ledger and e.people.ledger is e.ledger and e.people.inventory is e.inventory,'broken runtime references')
    require(all(o.cfg is e.cfg for name,o in objects(e).items() if name not in ('engine','farming','agency','projects')),'configuration references diverge')
    require(type(e.rng) is Streams and e.cells.rng is e.rng and e.people.rng is e.rng and e.rng.seed==(e.cfg.seed & ((1<<64)-1)),'RNG references/seed mismatch')
    require(e.cells.wl_cfg is e.cfg.catalog['wildlife'] and e.cells.crop is e.cfg.catalog['crops'][e.cells.crop_id],'catalog references diverge')
    require(e.cells.wl_ids==list(e.cfg.catalog['wildlife']),'wildlife axis order mismatch')
    require(type(e.people._teaching_day) is int and -1<=e.people._teaching_day<e.day,'teaching bookkeeping outside completed day')
    require(e.last_check_day is None or type(e.last_check_day) is int and 0<=e.last_check_day<e.day,'conservation check marker invalid')
    require(type(e.ledger.checks) is int and e.ledger.checks>=0 and type(e.ledger.n_flows) is int and e.ledger.n_flows>=0,'invalid ledger counters')
    require(not e.ledger.residuals,'engine has unresolved conservation failures')
    require(all(not getattr(e.ledger,f) for f in ('_f_res','_f_from','_f_to','_f_qty','_f_proc','_f_day')),'pending daily flows: not a closed day')
    require(e.ledger.kind_of=={k:v['kind'] for k,v in e.cfg.catalog['items'].items()},'ledger item kinds mismatch')
    e.inventory.validate()
    actual=e.inventory.holdings()
    for h in set(actual)|set(e.ledger.expected):
        for item in set(actual.get(h,{}))|set(e.ledger.expected.get(h,{})):
            q=e.ledger.expected.get(h,{}).get(item,0)
            require(type(q) is int and q>=0 and q==actual.get(h,{}).get(item,0),f'conservation mismatch {h}/{item}')
    bucket_net={}
    for bucket,stock in e.ledger.bucket_totals.items():
        require(bucket.startswith(('src:','sink:')),'invalid accounting bucket')
        for item,q in stock.items():
            require(item in e.ledger.kind_of and type(q) is int and q>=0,'invalid bucket quantity')
            bucket_net[item]=bucket_net.get(item,0)+(q if bucket.startswith('src:') else -q)
    totals=e.ledger.totals()
    require(all(bucket_net.get(k,0)==totals.get(k,0) for k in set(bucket_net)|set(totals)),'cumulative sources/sinks disagree with holdings')
    require(type(e.inventory.next_id) is int and e.inventory.next_id>max(e.inventory.lots,default=-1),'lot identity counter invalid')
    require(set(e.people.rows)==set(range(len(e.people.rows))) and set(e.places)==set(range(len(e.places))),'person/place identity sequence invalid')
    n=e.cfg.derived['cells'];C=len(e.cells.wl_ids)
    require((e.world.n,e.world.w,e.world.h)==(n,e.cfg.derived['grid_w'],e.cfg.derived['grid_h']),'world dimensions mismatch')
    def array(a,shape,kind):
        require(type(a) is np.ndarray and a.shape==shape and a.dtype.kind in kind,'array shape/type mismatch')
        require(not np.isnan(a).any(),'NaN array state')
    dtypes_world={'ys':'int64','xs':'int64','x_km':'float64','y_km':'float64','nbr':'int32','terrain':'int8','elevation':'float32','slope':'float32','river':'bool','region':'int16','water_adjacent':'bool','river_adjacent':'bool','road_level':'int8','road_wear':'float32','bridge':'bool','ferry':'bool','region_seeds':'int32'}
    dtypes_cells={'land':'bool','wl_k':'float32','moisture':'float32','vegetation':'float32','fuel':'float32','rain_mm':'float32','protection':'float32','cleared':'float32','burning':'bool','burn_hour':'float32','burn_scar_day':'int32','burn_since_day':'int32','edge_progress':'float32','fields':'int32','planted':'bool','crop_progress':'float32','crop_health':'float32','wildlife':'float32','regions':'int16','wet':'bool','wind_dir':'int8','wind_kph':'float32','land_nbr':'bool','deg':'int64','wl_initial':'float64'}
    for obj,types in ((e.world,dtypes_world),(e.cells,dtypes_cells)):
        for name,dtype in types.items():
            a=getattr(obj,name);require(type(a) is np.ndarray and a.dtype==np.dtype(dtype),f'array dtype mismatch: {name}')
            require(np.isfinite(a).all() if name!='burn_hour' else (~np.isnan(a)).all() and (a>=0).all(),f'nonfinite array: {name}')
    require(type(e.cells.last_fire_cells) is np.ndarray and e.cells.last_fire_cells.ndim==1 and e.cells.last_fire_cells.dtype in (np.dtype('int32'),np.dtype('int64')) and np.all((e.cells.last_fire_cells>=0)&(e.cells.last_fire_cells<n)),'invalid fire index array')
    for name in ('ys','xs','x_km','y_km','terrain','elevation','slope','river','region','water_adjacent','river_adjacent','road_level','road_wear','bridge','ferry'):
        array(getattr(e.world,name),(n,),'biuf')
    array(e.world.region_seeds,(len(e.cells.regions),),'i');require(np.all((e.world.region_seeds>=0)&(e.world.region_seeds<n)),'region seed outside world')
    array(e.world.nbr,(n,6),'i');require(np.all((e.world.nbr>=-1)&(e.world.nbr<n)),'bad neighbor id')
    for name in ('land','moisture','vegetation','fuel','rain_mm','protection','cleared','burning','burn_hour','burn_scar_day','burn_since_day','fields','planted','crop_progress','crop_health','deg'):
        array(getattr(e.cells,name),(n,),'biuf')
    array(e.cells.edge_progress,(n,6),'f');array(e.cells.land_nbr,(n,6),'b');array(e.cells.wildlife,(n,C),'f');array(e.cells.wl_initial,(C,),'f');array(e.cells.wl_k,(C,),'f')
    require(np.array_equal(e.cells.regions,np.unique(e.world.region)),'region index mismatch')
    R=len(e.cells.regions)
    for name in ('wet','wind_dir','wind_kph'):array(getattr(e.cells,name),(R,),'biuf')
    require(np.all((e.world.region>=0)&(e.world.region<R)),'region index out of range')
    require(np.all(np.isfinite(e.cells.wildlife)) and np.all(e.cells.wildlife>=0),'invalid wildlife abundance')
    require(np.all((e.cells.edge_progress>=0)&(e.cells.edge_progress<=1)),'invalid fire progress')
    keys={(d,h) for d,v in e.cfg.catalog['disease'].items() for h in v['hosts'] if h in e.cells.wl_ids}
    require(set(e.cells.seir)==keys,'disease state keys mismatch')
    for a in e.cells.seir.values():
        array(a,(n,4),'f');require(a.dtype==np.dtype('float32') and np.isfinite(a).all() and (a>=0).all() and np.allclose(a.sum(1),1,atol=2e-5),'invalid disease fractions')
    require(set(e.cells.budget)=={'growth','density_loss','dispersal','predation','disease','fire','hunt'},'wildlife diagnostic fields mismatch')
    for a in e.cells.budget.values():
        array(a,(C,),'f');require(a.dtype==np.dtype('float64') and np.isfinite(a).all(),'budget dtype/nonfinite mismatch')
    for bid,b in e.places.items():
        require(type(b) is Place and b.id==bid and 0<=b.cell<n and b.kind in e.cfg.catalog['buildings'] and f'b:{bid}' in e.inventory.holders,'invalid place')
        require(type(b.condition) is float and 0.<=b.condition<=1. and (b.room_rate is None or (type(b.room_rate) in (int,float) and math.isfinite(b.room_rate) and b.room_rate>=0)),'invalid place sleep fields')
    for pid,p in e.people.rows.items():
        require(type(p) is Person and p.id==pid and 0<=p.cell<n and f'p:{pid}' in e.inventory.holders,'invalid person')
        require(p.place==-1 or (p.place in e.places and e.places[p.place].cell==p.cell),'person location mismatch')
        require(p.home_place==-1 or p.home_place in e.places,'missing home')
        require(type(p.capabilities) is set and type(p.traits) is tuple,'person collection types changed')
        pregnancy_ok = (p.pregnancy is None or (type(p.pregnancy) is dict and set(p.pregnancy)=={'father','due_day'} and p.sex == 'female' and p.alive and p.pregnancy['father'] in e.people.rows and type(p.pregnancy['due_day']) is int and p.pregnancy['due_day'] >= e.day))
        require(p.sex in ('unspecified','female','male') and (p.spouse is None or p.spouse in e.people.rows) and (p.designated_heir is None or p.designated_heir in e.people.rows) and type(p.estate_owner) is bool and all(parent in e.people.rows for parent in p.parents) and all(child in e.people.rows for child in p.children) and pregnancy_ok,'invalid family references')
        require(type(p.known_cells) is dict and all(type(c) is int and 0<=c<n and type(v) is dict and {'day','source'}<=set(v) and type(v['day']) is int and 0<=v['day']<e.day and type(v['source']) is str for c,v in p.known_cells.items()),'invalid personal map knowledge')
        require(p.last_day<e.day and (not p.alive or p.last_day==e.day-1),'person daily marker mismatch')
        require(all(type(k) is int and 0<=k<len(e.ledger.events) and type(m) is dict and set(m)=={'day','fidelity','source','hops','from'} and type(m['hops']) is int and m['hops']>=0 and (m['from'] is None or m['from'] in e.people.rows) and (m['hops']==0)==(m['from'] is None) for k,m in p.memory.items()),'invalid memory record')
        require(type(p.last_sleep_quality) is float and 0.<=p.last_sleep_quality<=1.,'invalid sleep quality')
        require(type(p.sleep_streaks) is dict and all(k in e.cfg['sleep']['consequences'] and type(v) is int and v>=0 for k,v in p.sleep_streaks.items()),'invalid sleep streaks')
    for i,ev in enumerate(e.ledger.events):
        require(type(ev) is Event and ev.id==i and 0<=ev.day<e.day and ev.type in EVENT_TYPES and ev.visibility in VISIBILITY and type(ev.actors) is tuple,'invalid event')
        require(all(pid in e.people.rows for pid in ev.actors),'missing event actor')
    expected_by_day={};cur=-1;start=0
    for i,ev in enumerate(e.ledger.events):
        require(ev.day>=cur,'events are not chronological')
        if ev.day!=cur:
            if cur>=0:expected_by_day[cur]=(start,i)
            cur=ev.day;start=i
    require(e.ledger.by_day==expected_by_day and e.ledger._cur_day==cur and e.ledger._day_start==start,'event day index mismatch')
    for lid,lot in e.inventory.lots.items():
        require(type(lot) is Lot,'invalid lot type')
        require(lot.origin_event is None or 0<=lot.origin_event<len(e.ledger.events),'missing provenance event')
        require(all(entry.get('type')!='cite' or type(entry.get('event_id')) is int and 0<=entry['event_id']<len(e.ledger.events) for entry in lot.entries),'missing record citation')
    for k,h in e.inventory.holders.items():require(type(h) is Holder and h.key==k,'invalid holder identity')
    for key,q in e.projects.projects.items(): require(type(q) is Project and key==q.id and q.owner and q.kind in ('clear','extract','build','repair') and 0<=q.cell<n and q.place in e.places,'invalid project')
    require(type(e.projects.next_id) is int and e.projects.next_id>max(e.projects.projects,default=-1),'invalid project id counter')
    for key,f in e.farming.farms.items():require(type(f) is Farm and key==f.place and all(i in e.places for i in (f.place,f.home,f.store)),'invalid farm')
    for pid,f in e.farming.assignments.items():require(pid in e.people.rows and f in e.farming.farms,'invalid farm assignment')
    for key,h in e.farming.households.items():require(type(h) is Household and key==h.home and all(i in e.places for i in (h.home,h.store)) and e.places[h.home].owner==h.owner,'invalid household')
    for pid,h in e.farming.keepers.items():require(pid in e.people.rows and h in e.farming.households,'invalid keeper assignment')
    t=e.deprivation
    require(type(t) is dict and set(t)=={'deficit_days','by_cause','by_person','recent','deaths'},'deprivation trace fields mismatch')
    require(type(t['deficit_days']) is int and t['deficit_days']>=0 and all(type(k) is str and type(v) is int and v>=0 for k,v in t['by_cause'].items()),'invalid deprivation counts')
    require(sum(t['by_cause'].values())==t['deficit_days']==sum(s['deficit_days'] for s in t['by_person'].values()),'deprivation counts disagree')
    require(all(type(pid) is int and pid in e.people.rows for pid in t['by_person']),'deprivation trace references unknown people')
    require(all(type(pid) is int and pid in e.people.rows and not e.people.rows[pid].alive and 0<=v['day']<e.day for pid,v in t['deaths'].items()),'death trace references unknown/living people')
    require(type(t['recent']) is list and len(t['recent'])<=365 and all(type(r) is dict and type(r.get('day')) is int and 0<=r['day']<e.day and r.get('person') in e.people.rows for r in t['recent']),'invalid recent deprivation records')
    g=e.agency
    require(type(g.agents) is set and all(type(pid) is int and pid in e.people.rows for pid in g.agents),'agency agents reference unknown people')
    require(type(g.day) is int and -1<=g.day<e.day and type(g.log) is list,'agency bookkeeping outside completed day')
    seen=set()
    for r in g.log:
        require(type(r) is dict and set(r)==LOG_FIELDS and type(r['day']) is int and 0<=r['day']<e.day and r['person'] in e.people.rows,'invalid decision record')
        require(type(r['packet']) is str and len(r['packet'])==64 and type(r['action']) is dict and type(r['ok']) is bool and type(r['source']) is str,'invalid decision record fields')
        require(type(r['events']) is list and len(r['events'])==2 and 0<=r['events'][0]<=r['events'][1]<=len(e.ledger.events),'decision event span invalid')
        require((r['day'],r['person']) not in seen,'duplicate decision for a person-day');seen.add((r['day'],r['person']))
        pi=r['plan']
        require(pi is None or (type(pi) is dict and set(pi)=={'made_day','step','of','break','truncated'} and type(pi['made_day']) is int and 0<=pi['made_day']<=r['day']
                and type(pi['step']) is int and type(pi['of']) is int and 0<=pi['step']<=max(pi['of'],0) and (pi['break'] is None or pi['break'] in BREAKS) and type(pi['truncated']) is int and pi['truncated']>=0),'invalid plan info on decision record')
        canonical(r)
    require(type(g.plans) is dict,'agency plans must be a dict')
    for pid,pl in g.plans.items():
        require(type(pid) is int and pid in g.agents and e.people.rows[pid].alive,'plan held by a non-agent or dead person')
        require(type(pl) is dict and set(pl)==PLAN_FIELDS,'invalid plan record')
        require(type(pl['goal']) is dict and set(pl['goal'])=={'kind','why'} and pl['goal']['kind'] in e.cfg['plans']['goal_kinds'] and type(pl['goal']['why']) is str,'invalid plan goal')
        require(type(pl['steps']) is list and pl['steps'] and all(type(s) is dict and set(s)=={'action','expects'} and type(s['action']) is dict and s['action'].get('kind') in ACTIONS for s in pl['steps']),'invalid plan steps')
        require(type(pl['assumptions']) is list and len(pl['assumptions'])<=e.cfg['plans']['max_assumptions'],'invalid plan assumptions')
        require(type(pl['made_day']) is int and type(pl['horizon_day']) is int and 0<=pl['made_day']<e.day and pl['horizon_day']>=pl['made_day'],'plan days outside completed run')
        require(type(pl['cursor']) is int and 0<=pl['cursor']<=len(pl['steps']) and (pl['last_ok'] is None or type(pl['last_ok']) is bool) and type(pl['source']) is str,'invalid plan progress')
        require(type(pl['ledger_mark']) is int and 0<=pl['ledger_mark']<=len(e.ledger.events),'plan ledger mark outside the ledger')
        canonical(pl)
    for pid,j in e.farming.journeys.items():
        require(type(j) is Journey and pid in e.people.rows and j.target in e.places and 0<=j.edge<len(j.path) and all(0<=i<n for i in j.path),'invalid journey')
        require(math.isfinite(j.remaining) and j.remaining>=0 and e.people.rows[pid].place==-1,'invalid journey progress')
    return True

def state_tree(e):
    validate(e)
    return {'config':{'raw':e.cfg.raw,'catalog':e.cfg.catalog,'derived':e.cfg.derived,'run_id':e.cfg.run_id,'seed':e.cfg.seed},
            'rng_seed':e.rng.seed,'objects':{name:{k:getattr(o,k) for k in STATE[name]} for name,o in objects(e).items()}}

def state_fingerprint(e):
    """Complete serialized-state comparator; tests also compare object graphs independently."""
    codec=Codec();payload=canonical(codec.encode(state_tree(e)))
    return sha(payload+b''.join(name.encode()+bytes.fromhex(sha(b)) for name,b in codec.blobs.items()))

def save(e,path):
    try:
        codec=Codec();state=canonical(codec.encode(state_tree(e)))
        blobs={'state.json':state,**codec.blobs}
        require(sum(map(len,blobs.values()))<=MAX_BYTES,'checkpoint exceeds size limit')
        manifest={'format':'chronicle-world','version':VERSION,'created_at_utc':datetime.now(timezone.utc).isoformat(),
                  'day':e.day,'run_id':e.cfg.run_id,'rng_layout':LAYOUT_VERSION,'runtime':runtime(),'source_sha256':sources(),
                  'members':{n:{'sha256':sha(b),'bytes':len(b)} for n,b in blobs.items()}}
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=None
        try:
            fd,temp=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent)
            with os.fdopen(fd,'w+b') as f:
                with zipfile.ZipFile(f,'w',zipfile.ZIP_DEFLATED) as z:
                    z.writestr('manifest.json',canonical(manifest))
                    for n,b in blobs.items():z.writestr(n,b)
                f.flush();os.fsync(f.fileno())
            os.replace(temp,path);temp=None
            # Directory fsync makes the rename durable on platforms supporting it.
            if hasattr(os,'O_DIRECTORY'):
                dfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(dfd)
                finally:os.close(dfd)
        finally:
            if temp is not None:
                try:os.unlink(temp)
                except FileNotFoundError:pass
        return manifest
    except CheckpointError:raise
    except Exception as exc:raise CheckpointError(f'checkpoint save failed: {exc}') from exc

def _json(b):
    def pairs(p):
        d={}
        for k,v in p:
            require(k not in d,'duplicate JSON key');d[k]=v
        return d
    return json.loads(b,object_pairs_hook=pairs,parse_constant=lambda x:(_ for _ in ()).throw(CheckpointError('nonfinite JSON')))

def load(path,*,expected_config=None):
    try:
        require(sources()==LOADED_SOURCE_SHA256,'source changed in this interpreter; restart before loading')
        require(Path(path).stat().st_size<=MAX_BYTES,'checkpoint exceeds archive limit')
        with zipfile.ZipFile(path) as z:
            names=z.namelist();require(len(names)==len(set(names)) and len(names)<=MAX_MEMBERS,'duplicate/excessive ZIP entries')
            require(sum(i.file_size for i in z.infolist())<=MAX_BYTES,'checkpoint expands beyond limit')
            require('manifest.json' in names and z.getinfo('manifest.json').file_size<2*1024*1024,'missing/oversize manifest')
            m=_json(z.read('manifest.json'))
            require(set(m)=={'format','version','created_at_utc','day','run_id','rng_layout','runtime','source_sha256','members'},'invalid manifest fields')
            require(m['format']=='chronicle-world' and m['version']==VERSION,'checkpoint schema incompatible')
            require(m['rng_layout']==LAYOUT_VERSION,'checkpoint RNG incompatible')
            require(m['runtime']==runtime(),'checkpoint runtime incompatible')
            require(m['source_sha256']==sources(),'checkpoint source incompatible; explicit migration required')
            require(set(names)==set(m['members'])|{'manifest.json'} and 'state.json' in m['members'],'missing/unexpected ZIP entries')
            blobs={}
            for name,spec in m['members'].items():
                require(name=='state.json' or (name.startswith('arrays/') and name.endswith('.npy') and name[7:-4].isdigit()),'invalid member name')
                b=z.read(name);require(set(spec)=={'bytes','sha256'} and len(b)==spec['bytes'] and sha(b)==spec['sha256'],f'checksum mismatch: {name}')
                blobs[name]=b
        codec=Codec({k:v for k,v in blobs.items() if k!='state.json'});s=codec.decode(_json(blobs['state.json']))
        require(codec.used==set(codec.blobs),'unreferenced array members')
        require(type(s) is dict and set(s)=={'config','rng_seed','objects'},'state root incompatible')
        require(type(s['config']) is dict and set(s['config'])=={'raw','catalog','derived','run_id','seed'},'config fields incompatible')
        cfg=Config(**s['config']);config_check(cfg)
        require(cfg.run_id==m['run_id'],'manifest config mismatch')
        if expected_config is not None:
            config_check(expected_config)
            require(canonical(Codec().encode(s['config']))==canonical(Codec().encode({'raw':expected_config.raw,'catalog':expected_config.catalog,'derived':expected_config.derived,'run_id':expected_config.run_id,'seed':expected_config.seed})),'expected configuration mismatch')
        require(set(s['objects'])==set(STATE),'subsystem fields incompatible')
        objs={}
        for name,attrs in s['objects'].items():
            require(type(attrs) is dict and set(attrs)==set(STATE[name]),f'{name} fields incompatible')
            obj=CLASSES[name].__new__(CLASSES[name]);obj.__dict__.update(attrs);objs[name]=obj
        e=objs['engine'];e.cfg=cfg;e.rng=Streams(cfg.seed)
        require(s['rng_seed']==e.rng.seed,'stored RNG seed mismatch')
        for name,o in objs.items():
            if name!='engine':setattr(e,name,o)
            if name not in ('farming','agency','projects'):o.cfg=cfg
        e.cells.world=e.world;e.cells.rng=e.rng;e.cells.wl_cfg=cfg.catalog['wildlife'];e.cells.crop=cfg.catalog['crops'][e.cells.crop_id]
        e.inventory.ledger=e.ledger;e.people.inventory=e.inventory;e.people.ledger=e.ledger;e.people.rng=e.rng
        e.farming.e=e;e.agency.e=e;e.projects.e=e;e.agency.policies={};e.ledger.on_event=e._witness_event;e.ledger._ev_out=None;e.ledger._fl_out=None
        require(e.day==m['day'],'manifest day mismatch');validate(e)
        return e
    except CheckpointError:raise
    except Exception as exc:raise CheckpointError(f'checkpoint load failed: {exc}') from exc
