"""Viewer export (chronicle/frames.py, run.py --frames): read-only by construction. Checks that frames on/off give the same
digest for scripted, --llm (fake Ollama) and --replay runs, that the files have the documented shape, that every
decision has a context record, and that a resumed run appends consistent frames."""
import sys, os, json, threading, tempfile, http.server
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import run as runner
from chronicle.frames import FrameWriter, ContextTap, QUIET

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
        self._send(200,{'message':{'role':'assistant','content':'{"kind":"work"}' if 'work' in kinds else '{"kind":"rest"}'},'prompt_eval_count':900,'eval_count':6})
srv=http.server.ThreadingHTTPServer(('127.0.0.1',0),H); threading.Thread(target=srv.serve_forever,daemon=True).start()
URL=f'http://127.0.0.1:{srv.server_address[1]}'
tmp=tempfile.mkdtemp(); POP=['--set','population.initial=40','--quiet']; LL=POP+['--set',f'llm.url="{URL}"']
rd=lambda p:[json.loads(l) for l in open(p)]

# scripted run
off=runner.main(['--days','15','--out',f'{tmp}/off']+POP); on=runner.main(['--days','15','--frames','--out',f'{tmp}/on']+POP)
check(off['digest_sha']==on['digest_sha'],'scripted run: digest identical with frames on and off')
check(not os.path.exists(f'{tmp}/off/world.json') and not os.path.exists(f'{tmp}/off/frames.jsonl'),'no frame files are written unless --frames')
check(not os.path.exists(f'{tmp}/on/context.jsonl'),'a scripted run (no policy) writes no context.jsonl')
w=json.load(open(f'{tmp}/on/world.json')); fr=rd(f'{tmp}/on/frames.jsonl')
check(w['format']=='chronicle-world-1' and len(w['terrain'])==w['w']*w['h']==len(w['elevation'])==len(w['region']),'world.json: one terrain char, elevation and region per cell')
check(all(0<=int(c)<len(w['terrains']) for c in set(w['terrain'])) and all(0<=r<w['w']*w['h'] for r in w['rivers']),'world.json: terrain indexes and river cells are in range')
check(all(0<=b['cell']<w['w']*w['h'] for b in w['places']) and [b['id'] for b in w['places']]==list(range(len(w['places']))),'world.json: buildings are in order and on the grid')
check([f['day'] for f in fr]==list(range(15)),'frames.jsonl: one line per day, in order')
check(len(fr[0]['born'])==40 and all(len(r)==6 for r in fr[0]['born']) and all(len(f['born'])==0 for f in fr[1:]),'every person is announced once in "born", on the first frame that has them')
check(all(len(r)==9 and 0<=r[1]<w['w']*w['h'] and -1<=r[3]<len(w['roles']) and r[8] in (0,1) for f in fr for r in f['people']),'person rows have 9 fields, a cell on the grid, a role index and an alive flag')
check(all(0<=r[4]<=1 and 0<=r[5]<=1 and 0<=r[6]<=1 for f in fr for r in f['people']),'needs are 0..1')
check(all(ev[0] not in QUIET for f in fr for ev in f['events']),'bookkeeping events are left out of frames')
check(all(isinstance(i,int) for f in fr for i in f['burning']+f['planted']),'burning/planted are plain integer cell lists')

# --llm with frames: digest unchanged, a context record for every decision
l_off=runner.main(['--llm','--days','12','--out',f'{tmp}/l_off']+LL); l_on=runner.main(['--llm','--frames','--days','12','--out',f'{tmp}/l_on']+LL)
check(l_off['digest_sha']==l_on['digest_sha'],'--llm: digest identical with frames on and off')
dec=rd(f'{tmp}/l_on/decisions.jsonl'); ctx=rd(f'{tmp}/l_on/context.jsonl')
check(rd(f'{tmp}/l_off/decisions.jsonl')==dec,'--llm: decisions.jsonl is identical with frames on and off')
model_dec={(r['day'],r['person']) for r in dec if not r['source'].startswith('plan:')}
check({(c['day'],c['person']) for c in ctx}==model_dec and len(ctx)==len(model_dec),'--llm: exactly one context record per model decision (plan days make no call)')
check(all('work' in c['menu'] or all(d['action']['kind']!='work' for d in dec if (d['day'],d['person'])==(c['day'],c['person'])) for c in ctx),'context menus agree with what the fake model could pick')
check(all(set(c)>={'menu','body','place_kind','company','surroundings','hours_left','last_action'} for c in ctx),'context records carry menu, body, place, company and surroundings')

# --replay with frames: same digest, context rebuilt from the replayed packets
rep=runner.main(['--replay',f'{tmp}/l_on/decisions.jsonl','--frames','--days','12','--out',f'{tmp}/rep']+LL)
check(rep['digest_sha']==l_on['digest_sha'],'--replay with frames reproduces the LLM digest')
check(rd(f'{tmp}/rep/frames.jsonl')==rd(f'{tmp}/l_on/frames.jsonl'),'replayed frames are identical to the live run frames')
check(rd(f'{tmp}/rep/context.jsonl')==ctx,'replayed context.jsonl is identical to the live run context')

# resume: frames continue from the checkpoint day and the "born" list restarts from the checkpoint population
ck=runner.main(['--days','10','--out',f'{tmp}/ck','--set','run.checkpoint_every_ticks=1','--set','run.days_per_tick=5','--frames']+POP)['last_checkpoint']
res=runner.main(['--resume',ck,'--days','5','--frames','--out',f'{tmp}/ck']+['--set','run.checkpoint_every_ticks=1','--set','run.days_per_tick=5']+POP)
rf=rd(f'{tmp}/ck/frames.jsonl'); check([f['day'] for f in rf]==list(range(15)),'a resumed run appends frames for the following days')
check(res['day']==15 and len(rf[10]['born'])==40,'the first frame after a resume re-announces everyone, so a viewer can start from any segment')
srv.shutdown()
print(json.dumps({'suite':'frames','checks':len(checks),'failures':[]}))
