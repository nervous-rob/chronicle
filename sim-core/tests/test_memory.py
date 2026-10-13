"""memory-v1: the model-facing memory loop. `tell` is a menu verb backed by people.tell (ledgered hearsay), the packet names who
was involved in each remembered event, and a model-driven run spreads news between people the scripted routine leaves apart.
Everything runs the real engine; the fake Ollama server stands in for a model, so this checks mechanism, not model quality."""
import sys, json, tempfile, threading, http.server
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chronicle.config import load, ConfigError, VERBS
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import ScriptedPolicy, ReplayPolicy
import run as runner

checks=[]
def check(x,msg):
    assert x,msg
    checks.append(msg); print('ok',msg,flush=True)
def raises(fn,exc):
    try: fn()
    except exc: return True
    return False

SEAS=('spring','summer','autumn','winter')
BASE={'world.width_km':12,'world.height_km':8,'world.region_count':2,'wildlife.raid_rate':0.0,
      'weather.lightning_p':{s:0. for s in SEAS},'time.work_days.farmer':1.0}
def fixture():
    e=Engine(load(overrides=BASE))
    land=np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains']))
    A=int(land[0]); ids={'cell':A}
    ids['home']=e.seed_place('house',A,'h:0'); ids['store']=e.seed_place('granary',A,'h:0'); ids['farm']=e.seed_place('farm',A,'h:0')
    e.farming.register(ids['farm'],ids['home'],ids['store'])
    a=e.add_person(-30*365,A,ids['home'],household_id=0,role='farmer',name='Ada'); e.farming.assign(a.id,ids['farm'])
    b=e.add_person(-30*365,A,ids['home'],household_id=0,name='Bram'); c=e.add_person(-30*365,A,ids['home'],household_id=0,name='Cole')
    e.inventory.create(0,'grain',5000,f'b:{ids["home"]}','h:0',source='setup'); e.inventory.create(0,'grain',40000,f'b:{ids["store"]}','h:0',source='setup')
    ids.update(ada=a.id,bram=b.id,cole=c.id); return e,ids

# ---- registry and config
check('tell' in VERBS and VERBS['tell']['timed'],'tell is a registered timed verb')
d=load(); check('tell' in d['agency']['action_hours'],'tell has action hours')
check(raises(lambda:load(overrides={'agency.action_hours.tell':99.0}),ConfigError),'tell hours above the daily budget are rejected')

# ---- the menu offer
e,ids=fixture(); ada,bram,cole=ids['ada'],ids['bram'],ids['cole']
for p in (ada,bram): e.agency.bind(p,ScriptedPolicy(lambda pk:{'kind':'rest'}))
e.step(); e.farming.begin(1)
for words in ('the well is dry','smoke to the east'):   # two real ledgered events Ada witnessed; Bram and Cole were not there
    ev=e.ledger.event(e.day,'say',[ada],cell=e.people.rows[ada].cell,place=e.people.rows[ada].place,words=words)
    e.people.remember(e.people.rows[ada],ev,e.day,1.,'witnessed')
for q in (bram,cole): e.people.rows[q].memory.clear()
pk=e.agency.observe(ada)
tm=[m for m in pk['menu'] if m['kind']=='tell']
check(len(pk['memory'])>=2,'Ada remembers the events she witnessed')
check(len(tm)==1 and {n['recipient'] for n in tm[0]['news']}=={bram,cole} and all(n['events']==[m['event'] for m in pk['memory']] for n in tm[0]['news']),'tell is offered per listener with exactly the events they have not heard')
check(all('who' in m and isinstance(m['who'],list) for m in pk['memory']),'every packet memory names the people involved')
check(all(m['who']==['Ada'] for m in pk['memory'] if m['actors']==[ada]),'"who" gives the names of the actors')
json.loads(json.dumps(pk))

# ---- resolution: hearsay with fidelity loss, a ledgered tell event, time spent
eid=tm[0]['news'][1]['events'][0]; cole=tm[0]['news'][1]['recipient']
src=e.people.rows[ada].memory[eid]
had=eid in e.people.rows[cole].memory
if had: del e.people.rows[cole].memory[eid]
n0=len(e.ledger.events); b0=e.farming.budgets[ada]
ok,why=e.agency._resolve(ada,{'kind':'tell','recipient':cole,'event':eid},pk)
rec=e.people.rows[cole].memory.get(eid)
check(ok and why is None,'a valid tell is accepted')
check(rec is not None and rec['source']=='hearsay' and rec['from']==ada and rec['hops']==src['hops']+1,'the listener holds a hearsay record from the teller with hops+1')
check(abs(rec['fidelity']-src['fidelity']*e.cfg['gossip']['tell_fidelity_mult'])<1e-9,'fidelity is the teller fidelity times tell_fidelity_mult')
check(any(ev.type=='tell' and ev.actors==(ada,cole) and ev.payload['event']==eid for ev in e.ledger.events[n0:]),'a private tell event is ledgered')
check(abs((b0-e.farming.budgets[ada])-e.cfg['agency']['action_hours']['tell'])<1e-9,'telling costs the configured hours')
ok2,why2=e.agency._resolve(ada,{'kind':'tell','recipient':cole,'event':eid},pk)
check(not ok2 and why2=='already_known','telling someone what they already hold at that fidelity is refused with no time spent')
check(not e.agency._resolve(ada,{'kind':'tell','recipient':999,'event':eid},pk)[0],'an off-menu recipient is refused')
check(not e.agency._resolve(ada,{'kind':'tell','recipient':cole,'event':10**6},pk)[0],'an event the teller does not hold is refused')
other=[n for n in tm[0]['news'] if n['recipient']!=cole][0]['recipient']
e.people.rows[other].memory[eid]={'day':e.day,'fidelity':1.0,'source':'witnessed','hops':0,'from':None}
check(not e.agency._resolve(ada,{'kind':'tell','recipient':other,'event':eid},pk)[0],'an event the listener already holds is not on the menu for that listener (menu was built before they learned it, so the backstop refuses it)')
check(not e.agency._resolve(ada,{'kind':'tell','recipient':cole},pk)[0],'a tell without an event is refused')
# people with nobody beside them or nothing remembered get no offer
e.people.rows[ada].memory.clear()
check(not any(m['kind']=='tell' for m in e.agency.observe(ada)['menu']),'no memories, no tell offer')

# ---- a model-driven chain: news crosses a gap only a talker closes
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _send(self,obj):
        b=json.dumps(obj).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): self._send({'models':[{'name':'qwen3.5:9b','model':'qwen3.5:9b'}]})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        pk=json.loads(body['messages'][0]['content'].split('OBSERVATION:\n')[1].split('\n\nReply with')[0])
        t=[m for m in pk['menu'] if m['kind']=='tell']
        if t and pk['self']['id']%2==0: act={'kind':'tell','recipient':t[0]['news'][0]['recipient'],'event':t[0]['news'][0]['events'][-1]}
        else: act={'kind':'work'} if any(m['kind']=='work' for m in pk['menu']) else {'kind':'rest'}
        self._send({'message':{'role':'assistant','content':json.dumps(act)},'prompt_eval_count':900,'eval_count':6})
srv=http.server.ThreadingHTTPServer(('127.0.0.1',0),H); threading.Thread(target=srv.serve_forever,daemon=True).start()
URL=f'http://127.0.0.1:{srv.server_address[1]}'
tmp=tempfile.mkdtemp(); LL=['--set','population.initial=40','--quiet','--set',f'llm.url="{URL}"']
rd=lambda p:[json.loads(l) for l in open(p)]
r1=runner.main(['--llm','--days','12','--out',f'{tmp}/a']+LL)
dec=rd(f'{tmp}/a/decisions.jsonl'); tells=[r for r in dec if r['action']['kind']=='tell']
check(len(tells)>0 and any(r['ok'] for r in tells),'a model that chooses tell actually moves memory in a full run (%d tells, %d ok)'%(len(tells),sum(r['ok'] for r in tells)))
check(all(r['ok'] or r.get('reason') for r in tells),'every refused tell carries a reason')
check(sum(r['ok'] for r in tells)>=0.9*len(tells),'with news-only offers nearly every tell a model makes lands (%d of %d)'%(sum(r['ok'] for r in tells),len(tells)))
tev=[ev for ev in rd(f'{tmp}/a/events.jsonl')] if False else None
r2=runner.main(['--replay',f'{tmp}/a/decisions.jsonl','--days','12','--out',f'{tmp}/b']+LL)
check(r1['digest_sha']==r2['digest_sha'],'replaying a run that used tell reproduces the digest with no model')
check(r1['summary']['events']>0,'the run produced events')
print(f'tell decisions {len(tells)}; ok {sum(r["ok"] for r in tells)}')
print(f'ALL OK {len(checks)} checks')
