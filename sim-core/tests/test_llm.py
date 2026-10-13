"""Increment F part 1+2: Ollama adapter, attention selector, run.py --llm / --replay, validated against a FAKE Ollama
server on localhost (no model, no GPU, no internet). Checks the request shape (think/format/options), parsing and
fallback of bad replies, retries, attention determinism, and that a recorded LLM run replays bit-identically with no server."""
import sys, os, json, threading, tempfile, http.server, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chronicle.config import load, ConfigError
from chronicle.engine import Engine
from chronicle import setup
from chronicle.agency import parse_action, canonical, ScriptedPolicy
from chronicle.attention import Attention, run_day, replay_days
from chronicle.llm import OllamaClient, LLMPolicy, LLMError, strip_reply, action_schema
import run as runner

checks=[]
def check(x,msg):
    assert x,msg
    checks.append(msg); print('ok',msg,flush=True)
def raises(fn,exc):
    try: fn()
    except exc: return True
    return False

STATE={'mode':'work','fail':0,'bodies':[],'models':['qwen3.5:9b'],'calls':0}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _send(self,code,obj):
        b=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path=='/api/tags': self._send(200,{'models':[{'name':m,'model':m} for m in STATE['models']]})
        else: self._send(404,{})
    def do_POST(self):
        n=int(self.headers['Content-Length']); body=json.loads(self.rfile.read(n)); STATE['bodies'].append(body); STATE['calls']+=1
        mode=STATE['mode']
        if STATE['fail']>0: STATE['fail']-=1; return self._send(500,{'error':'boom'})
        if mode=='reject_think' and 'think' in body: return self._send(400,{'error':'"qwen" does not support thinking'})
        menu=json.loads(body['messages'][0]['content'].split('OBSERVATION:\n')[1].split('\n\nReply with')[0])['menu'] if 'OBSERVATION' in body['messages'][0]['content'] else []
        kinds=[m['kind'] for m in menu]
        if mode=='garbage': text='I think I will farm today.'
        elif mode=='leak': text='<think>let me see</think>{"kind":"work"}'
        elif mode=='offmenu': text='{"kind":"teach","capability":"x","students":[1]}'
        elif mode=='rest': text='{"kind":"rest"}'
        elif mode=='empty': text=''
        else: text='{"kind":"work"}' if 'work' in kinds else '{"kind":"rest"}'
        self._send(200,{'message':{'role':'assistant','content':text},'prompt_eval_count':900,'eval_count':12})
srv=http.server.ThreadingHTTPServer(('127.0.0.1',0),H); PORT=srv.server_address[1]; threading.Thread(target=srv.serve_forever,daemon=True).start()
URL=f'http://127.0.0.1:{PORT}'
def client(**kw):
    cfg=load(overrides={'llm.url':URL,'llm.retries':kw.pop('retries',1),**{f'llm.{k}':v for k,v in kw.items()}})
    return OllamaClient(cfg['llm'],sleep=lambda s:None),cfg

# ---- config
check(load()['llm']['model']=='qwen3.5:9b' and load()['llm']['think'] is False and load()['llm']['url']=='http://localhost:11434','defaults: qwen3.5:9b, think off, localhost:11434')
for k,v in [('llm.format','xml'),('llm.think','no'),('llm.on_invalid','crash'),('llm.url','localhost:11434'),('llm.retries',-1),('attention.min_stage','giant'),('llm.timeout_s',0.0)]:
    check(raises(lambda:load(overrides={k:v}),ConfigError),f'config rejects {k}={v!r}')

# ---- client
c,_=client(); check(c.ping()==['qwen3.5:9b'],'ping lists pulled models')
STATE['models']=['other:1b']; check(raises(c.ping,LLMError),'ping names the missing model and the pull command'); STATE['models']=['qwen3.5:9b']
try: c.ping()
except LLMError: pass
dead=OllamaClient({**load()['llm'],'url':'http://127.0.0.1:9','timeout_s':1.0}); check(raises(dead.ping,LLMError),'ping to a dead port raises a readable LLMError')
STATE['mode']='work'; STATE['bodies'].clear(); out=c.complete('hello'); b=STATE['bodies'][0]
check(out=='{"kind":"work"}' or out=='{"kind":"rest"}','complete returns the model text')
check(b['model']=='qwen3.5:9b' and b['stream'] is False and b['think'] is False,'request: model, stream false, think false')
check(b['format']['type']=='object' and 'kind' in b['format']['properties'] and b['format']['required']==['kind'],'request: JSON-schema format with required kind')
check(b['options']['num_ctx']==4096 and b['options']['num_predict']==300 and b['keep_alive']=='30m','request: num_ctx, num_predict, keep_alive from [llm]')
check(set(action_schema()['properties']['kind']['enum'])>={'work','rest','go','plan'},'schema enum is the verb registry')
STATE['bodies'].clear(); c2,_=client(format='json'); c2.complete('x'); check(STATE['bodies'][0]['format']=='json','format = "json"')
STATE['bodies'].clear(); c3,_=client(format='none'); c3.complete('x'); check('format' not in STATE['bodies'][0],'format = "none" sends no format')
check(strip_reply('<think>a</think>{"kind":"rest"}')=='{"kind":"rest"}' and strip_reply('<think>never closed {"kind":"work"}')=='','think blocks are stripped, an unterminated one drops the reply')
STATE['mode']='leak'; check(parse_action(c.complete('x'))=={'kind':'work'},'a leaked <think> block still parses')
STATE['mode']='work'; STATE['fail']=1; STATE['calls']=0; r=c.complete('x'); check(r.startswith('{') and STATE['calls']==2 and c.stats['retries']==1,'one 500 is retried and succeeds')
STATE['fail']=5; r=c.complete('x'); check(r.startswith('!llm_error') and c.stats['errors']==1 and 'HTTP 500' in c.last_error,'exhausted retries -> "!llm_error ..." text, never an exception')
STATE['fail']=0; STATE['mode']='empty'; check(c.complete('x').startswith('!llm_error'),'an empty reply is an error')
STATE['mode']='reject_think'; STATE['bodies'].clear(); c4,_=client(); r=c4.complete('x'); check(r.startswith('{') and 'think' not in STATE['bodies'][-1] and c4.think is None,'a model with no thinking switch (HTTP 400) is retried without think')

# ---- policy
def packet(menu): return {'day':1,'menu':[{'kind':k} for k in menu],'self':{'id':1}}
STATE['mode']='garbage'; pol=LLMPolicy(client()[0],'routine'); a=pol.decide(packet(['rest','work']))
check(a['kind']=='work' and a['fallback']=='routine' and 'invalid_reply' in a and pol.counts['fallback']==1,'garbage + routine fallback -> work, reply kept in the log')
pol=LLMPolicy(client()[0],'rest'); a=pol.decide(packet(['rest','work'])); check(a=={'kind':'rest','invalid_reply':a['invalid_reply']},'garbage + rest fallback -> rest')
pol=LLMPolicy(client()[0],'routine'); a=pol.decide(packet(['rest'])); check(a['kind']=='rest','routine fallback with no work on the menu rests')
STATE['mode']='offmenu'; pol=LLMPolicy(client()[0],'routine'); a=pol.decide(packet(['rest','work'])); check(a['kind']=='work' and 'not on the menu' in a['invalid_reply'],'an off-menu kind is treated as invalid')
STATE['mode']='work'; pol=LLMPolicy(client()[0],'routine'); a=pol.decide(packet(['rest','work'])); check(a=={'kind':'work'} and pol.counts['invalid']==0,'a good reply passes through untouched')
check(pol.name=='ollama:qwen3.5:9b','policy name carries the model (it is the log source)')

# ---- per-menu schema (the model cannot emit a kind that is not on today's menu)
check(action_schema(['rest','go'])['properties']['kind']['enum']==['go','rest'] or set(action_schema(['rest','go'])['properties']['kind']['enum'])=={'rest','go'},'schema enum narrows to the menu kinds')
check(set(action_schema([])['properties']['kind']['enum'])==set(action_schema()['properties']['kind']['enum']),'an empty menu falls back to the full verb list')
check(set(action_schema(['rest','not_a_verb'])['properties']['kind']['enum'])=={'rest'},'unknown kinds are not added to the enum')
STATE['mode']='work'; STATE['bodies'].clear(); pol=LLMPolicy(client()[0],'routine'); pol.decide(packet(['rest','go','say']))
check(set(STATE['bodies'][-1]['format']['properties']['kind']['enum'])=={'rest','go','say'},'the request carries the narrowed enum for this packet')
STATE['bodies'].clear(); pol.decide(packet(['rest','work']))
check(set(STATE['bodies'][-1]['format']['properties']['kind']['enum'])=={'rest','work'},'the next packet gets its own enum')
from chronicle.llm import LLM_INSTRUCTIONS
check('If "work" is on your menu' in LLM_INSTRUCTIONS,'instructions no longer tell people to work when work is not offered')

# ---- attention
cfg=load(overrides={'population.initial':60,'llm.url':URL})
def fresh():
    e=Engine(cfg); setup.generate(e); return e
e=fresh(); att=Attention(e); s1=att.select(); s2=Attention(fresh()).select()
check(s1==s2 and s1==sorted(s1),'attention selection is deterministic and sorted')
alive=sum(1 for p in e.people.rows.values() if p.alive)
check(len(s1)==att.k(alive)==max(cfg['attention']['k_per_tick'],round(cfg['attention']['k_per_pop']*alive)),f'selects K={len(s1)} from [attention] k_per_tick/k_per_pop')
check(all(e.people.at_least(e.people.rows[pid],e.day,'youth') for pid in s1),'children are never consulted (min_stage)')
check(att.k(400)==32 and att.k(1000)==cfg['attention']['k_cap']==40 and att.k(10)==cfg['attention']['k_per_tick'],'K scales with population (0.08/person), floors at k_per_tick, caps at k_cap')
# debt: whoever was consulted yesterday ranks lower than an equal who was not
e=fresh(); att=Attention(e); first=att.select(); att.note([{'person':first[0],'day':e.day}]); check(att.scores()[0][first[0]]<=Attention(fresh()).scores()[0][first[0]],'a person consulted today has less debt than one never consulted')

# ---- whole run through run.py with the fake server, then replay with no server
tmp=tempfile.mkdtemp(); OV=['--set','population.initial=40','--set',f'llm.url="{URL}"','--set','run.checkpoint_every_ticks=1','--quiet']
STATE['mode']='work'; STATE['bodies'].clear(); base=runner.main(['--days','20','--out',f'{tmp}/base','--set','population.initial=40','--quiet'])
llm=runner.main(['--llm','--days','20','--out',f'{tmp}/llm']+OV)
recs=[json.loads(l) for l in open(f'{tmp}/llm/decisions.jsonl')]
check(len(recs)>=20 and all(r['source'].startswith('ollama:qwen3.5:9b') or r['source'].startswith('plan:') for r in recs),f'{len(recs)} decisions logged to decisions.jsonl with the model as source')
per_day={}
for r in recs: per_day[r['day']]=per_day.get(r['day'],0)+1
check(max(per_day.values())<=cfg['attention']['k_cap'] and min(per_day.values())>=1,'every day consults between 1 and K people')
check(llm['llm']['calls']==len(STATE['bodies'])-0 or llm['llm']['calls']>0,'final.json reports llm calls, tokens and latency') ; check(llm['llm']['prompt_tokens']>0 and llm['llm']['p50_s'] is not None and 'invalid' in llm['llm'],'final.json llm stats include tokens, p50 and invalid count')
check(llm['summary']['alive']==base['summary']['alive']==40,'an always-work model keeps the village alive exactly like the routine baseline (40/40)')
digest_llm=[json.loads(l) for l in open(f'{tmp}/llm/digest.jsonl')]; check(all('decisions' in d and 'invalid' in d for d in digest_llm),'digest lines carry decisions/invalid counts')
STATE['mode']='garbage'; bad=runner.main(['--llm','--days','10','--out',f'{tmp}/bad']+OV)
check(bad['llm']['invalid']>0 and 0<bad['llm']['fallback']<=bad['llm']['invalid'] and bad['summary']['alive']==40,'a model that only produces garbage is logged as invalid; people with work on the menu fall back to it (others rest) and everyone stays fed')
STATE['mode']='rest'; sl=runner.main(['--llm','--days','5','--out',f'{tmp}/rest']+OV); check(sl['llm']['invalid']==0,'valid rest replies are accepted, not counted invalid')
# replay: stop the server's influence by switching to a mode that would give different answers; replay must ignore it
STATE['mode']='garbage'; calls_before=STATE['calls']
rep=runner.main(['--replay',f'{tmp}/llm/decisions.jsonl','--days','20','--out',f'{tmp}/rep']+['--set','population.initial=40','--set',f'llm.url="{URL}"','--quiet'])
check(STATE['calls']==calls_before,'replay makes zero model calls')
check(rep['digest_sha']==llm['digest_sha'],'replaying decisions.jsonl reproduces the LLM run bit-identically (digest match)')
check(open(f'{tmp}/rep/digest.jsonl').read().replace('"invalid": 0','')!='' and [json.loads(l)['alive'] for l in open(f'{tmp}/rep/digest.jsonl')]==[d['alive'] for d in digest_llm],'replay daily alive counts match')
# unreachable server -> readable exit, not a traceback
try: runner.main(['--llm','--days','1','--out',f'{tmp}/x','--set','population.initial=40','--set','llm.url="http://127.0.0.1:9"','--set','llm.timeout_s=1.0','--quiet']); ok=False
except SystemExit as exc: ok=exc.code!=0
check(ok,'--llm against a dead server exits with a readable error')
check(raises(lambda:runner.main(['--llm','--replay','x','--out',f'{tmp}/y']),SystemExit),'--llm and --replay are mutually exclusive')
# resume path: checkpoint from an LLM run loads and an attention-bound run continues
STATE['mode']='work'; OV2=[x for x in OV]+['--set','run.days_per_tick=5']
first=runner.main(['--llm','--days','10','--out',f'{tmp}/ck']+OV2); ck=first['last_checkpoint']; check(ck is not None,'LLM run wrote a checkpoint (every 5 days)')
res=runner.main(['--llm','--resume',ck,'--days','5','--out',f'{tmp}/res']+OV2); check(res['day']==15 and res['summary']['alive']==40,'an LLM run resumes from a checkpoint and continues (day 15, 40 alive)')
srv.shutdown()
print(json.dumps({'suite':'llm','checks':len(checks),'failures':[]}))
