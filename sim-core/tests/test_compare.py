"""compare.py: the scripted-vs-LLM comparison harness. Uses a fake Ollama server (never a real model), so it checks the plumbing and the
arithmetic, not whether any model is good. Checks: identical runs report no divergence; a changed world is dated to the right day;
decision statistics match the log; --run produces a scripted/llm pair from one config and seed; the harness never writes into the runs it reads."""
import sys, os, json, threading, tempfile, http.server, hashlib, io, contextlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import run as runner, compare

checks=[]
def check(x,msg):
    assert x,msg
    checks.append(msg); print('ok',msg,flush=True)

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _send(self,code,obj):
        b=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): self._send(200,{'models':[{'name':'qwen3.5:9b','model':'qwen3.5:9b'}]})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        menu=json.loads(body['messages'][0]['content'].split('OBSERVATION:\n')[1].split('\n\nReply with')[0])['menu']
        kinds=[m['kind'] for m in menu]
        pick='rest' if 'rest' in kinds and len(kinds)%2==0 else ('work' if 'work' in kinds else kinds[0])
        self._send(200,{'message':{'role':'assistant','content':json.dumps({'kind':pick})},'prompt_eval_count':900,'eval_count':6})
srv=http.server.ThreadingHTTPServer(('127.0.0.1',0),H); threading.Thread(target=srv.serve_forever,daemon=True).start()
URL=f'http://127.0.0.1:{srv.server_address[1]}'
tmp=tempfile.mkdtemp(); POP=['--set','population.initial=40','--quiet']; LL=POP+['--set',f'llm.url="{URL}"']
rd=lambda p:[json.loads(l) for l in open(p)]
sha=lambda d:{f:hashlib.sha256(open(f'{d}/{f}','rb').read()).hexdigest() for f in sorted(os.listdir(d))}

runner.main(['--days','20','--out',f'{tmp}/a']+POP)
runner.main(['--days','20','--out',f'{tmp}/a2']+POP)
runner.main(['--days','20','--out',f'{tmp}/s','--set','scenario.enabled=true','--set','scenario.stranger={"day":8,"count":1,"ration_kg":0.5}','--set','scenario.drought={"start_day":-1,"days":1,"evaporation_mult":1.0,"rain_mult":1.0}']+POP)
before=sha(f'{tmp}/a')
same=compare.compare(f'{tmp}/a',f'{tmp}/a2',labels=('A','B'))
check(same['first_divergence_day'] is None,'two identical scripted runs: no divergence')
check(all(same['A'][k]==same['B'][k] for k in same['A']),'two identical scripted runs: every outcome number matches')
check(same['decisions']['A'] is None and same['decisions']['B'] is None,'scripted runs have no decision stats')
diff=compare.compare(f'{tmp}/a',f'{tmp}/s',labels=('A','B'))
check(diff['first_divergence_day']==8,'a stranger arriving on day 8 makes the worlds first differ on day 8 (got %s)'%diff['first_divergence_day'])
check(diff['B']['days']==20 and diff['A']['days']==20,'outcomes report the number of days compared')
check(diff['B']['alive_final']==diff['A']['alive_final']+1,'the stranger is counted in the alive total')

# llm run vs scripted, same seed
runner.main(['--llm','--days','20','--out',f'{tmp}/l']+LL)
rep=compare.compare(f'{tmp}/a',f'{tmp}/l',labels=('scripted','llm'))
ds=rep['decisions']['llm']; rows=rd(f'{tmp}/l/decisions.jsonl')
check(ds['decisions']==len(rows) and sum(ds['kinds'].values())==len(rows),'decision stats: kinds sum to the number of logged decisions')
check(ds['invalid']==sum(1 for r in rows if 'invalid_reply' in r['action']),'decision stats: invalid count matches the log')
check(ds['people_consulted']==len({r['person'] for r in rows}),'decision stats: people consulted matches the log')
check(set(ds['pressure_verb_uses'])=={'give','hunt','firebreak','tell'},'decision stats list the pressure verbs and tell')
check(ds['non_work_share']==round(1-ds['kinds'].get('work',0)/len(rows),3),'non-work share is computed from the kinds')
txt=compare.render(rep)
check('| metric |' in txt and 'model decisions:' in txt and ('worlds first differ' in txt or 'identical' in txt),'render produces a table, divergence line and decision summary')

# --run mode: scripted + llm pair from one config; refuses to overwrite; writes only under --out
buf=io.StringIO()
with contextlib.redirect_stdout(buf):
    reps=compare.main(['--run','--days','10','--out',f'{tmp}/cmp','--set','population.initial=40','--set',f'llm.url="{URL}"','--json',f'{tmp}/cmp.json'])
check(os.path.exists(f'{tmp}/cmp/scripted/digest.jsonl') and os.path.exists(f'{tmp}/cmp/llm/decisions.jsonl'),'--run writes scripted/ and llm/ run directories')
check(not os.path.exists(f'{tmp}/cmp/scripted/decisions.jsonl'),'the scripted half has no decisions.jsonl')
check(len(reps)==1 and json.load(open(f'{tmp}/cmp.json'))[0]['labels']==['scripted','llm'],'--json writes the report')
try: compare.main(['--run','--days','2','--out',f'{tmp}/cmp','--set',f'llm.url="{URL}"']); refused=False
except SystemExit: refused=True
check(refused,'--run refuses to append to an existing run directory')
with contextlib.redirect_stdout(buf):
    reps2=compare.main(['--run','--days','6','--out',f'{tmp}/multi','--seeds','1,2','--set','population.initial=40','--set',f'llm.url="{URL}"'])
check(len(reps2)==2 and os.path.exists(f'{tmp}/multi/seed-1/llm/digest.jsonl') and os.path.exists(f'{tmp}/multi/seed-2/scripted/digest.jsonl'),'--seeds runs one pair per seed')
check(reps2[0]['scripted']['digest_sha']!=reps2[1]['scripted']['digest_sha'],'different seeds give different worlds')
# read-only on existing runs
after=sha(f'{tmp}/a'); check(before==after,'comparing a run does not modify it')
print(f'ALL OK {len(checks)} checks'); 
