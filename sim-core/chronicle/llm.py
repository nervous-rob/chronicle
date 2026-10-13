"""Local-model adapter (increment F, part 1): an Ollama /api/chat client and the policy that wraps it.

Standard library only (urllib). The client supplies TextPolicy's `complete(prompt) -> str`; it never raises into
the engine: a network failure, timeout or empty reply becomes a text starting '!llm_error', which parse_action
rejects, so the decision is logged as an invalid reply and the person falls back per [llm] on_invalid.
Nothing here is replayed: decisions are logged by agency.py and ReplayPolicy re-feeds them without a model.
"""
from __future__ import annotations
import json, re, time, urllib.request, urllib.error
from .agency import TextPolicy, parse_action
from .config import VERBS

THINK_RE = re.compile(r'<think>.*?</think>', re.S)

LLM_INSTRUCTIONS = (
    'You are one person in a simulated world. You know only what the OBSERVATION says. '
    'Choose exactly one action from "menu" (the "kind" must appear in the menu). Reply with a single JSON object such as '
    '{"kind":"work"} or {"kind":"go","place":3} or {"kind":"say","words":"..."} or {"kind":"rest"}. '
    'If "work" is on your menu, your usual daily labour is {"kind":"work"} (tending your fields or your household pantry); choose something else '
    'only when your situation calls for it, and never choose a kind that is not on your menu. Other kinds, only if on your menu: {"kind":"give","recipient":2,"kg":1} '
    'hands carried food to someone beside you, {"kind":"hunt","target":"game"} hunts, {"kind":"firebreak"} clears fuel from your cell, {"kind":"tell","recipient":2,"event":17} passes on one remembered event to someone beside you who has not heard it (the menu "news" lists, per person, the event numbers they would be hearing for the first time). You may reply with a multi-day PLAN: {"kind":"plan","goal":{"kind":"food","why":"..."},'
    '"steps":[{"action":{"kind":"go","place":3}},{"action":{"kind":"work"}}]}; one step runs per day and you are asked again when it ends '
    'or breaks. Reply with the JSON object only, no commentary.')


class LLMError(RuntimeError):
    pass


def action_schema(kinds=None):
    """JSON schema for Ollama structured output (format="schema"). Loosely typed on purpose: the engine validates arguments.
    kinds, when given, narrows the "kind" enum to what is on this person's menu today, so the model cannot emit an off-menu verb."""
    allowed = [k for k in VERBS if k in set(kinds)] if kinds else list(VERBS)
    if not allowed: allowed = list(VERBS)
    loose = {'place': {'type': 'integer'}, 'from': {'type': 'string'}, 'to': {'type': 'string'}, 'limit': {'type': 'integer'},
             'recipient': {'type': 'integer'}, 'kg': {'type': 'integer'}, 'target': {'type': 'string'}, 'words': {'type': 'string'}, 'medium': {'type': 'integer'}, 'text': {'type': 'string'}, 'event': {'type': 'integer'},
             'capability': {'type': 'string'}, 'students': {'type': 'array', 'items': {'type': 'integer'}},
             'goal': {'type': 'object'}, 'steps': {'type': 'array', 'items': {'type': 'object'}}, 'assumptions': {'type': 'array', 'items': {'type': 'object'}}}
    return {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': allowed}, **loose}, 'required': ['kind']}


def strip_reply(text):
    """Drop <think> blocks (and an unterminated one) a model may emit despite think=false."""
    text = THINK_RE.sub('', text or '')
    if '<think>' in text: text = text.split('<think>')[0]
    return text.strip()


class OllamaClient:
    def __init__(self, llm, sleep=time.sleep):
        self.url = llm['url'].rstrip('/'); self.model = llm['model']; self.temperature = llm['temperature']
        self.think = llm['think']; self.fmt = llm['format']; self.timeout = llm['timeout_s']; self.retries = llm['retries']
        self.num_ctx = llm['num_ctx']; self.num_predict = llm['num_predict']; self.keep_alive = llm['keep_alive']
        self.sleep = sleep; self.last_error = None
        self.stats = {'calls': 0, 'errors': 0, 'retries': 0, 'seconds': 0.0, 'prompt_tokens': 0, 'eval_tokens': 0, 'latencies': []}

    def _request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def ping(self):
        """Raise LLMError with a fix-it message unless the server answers and the model is pulled."""
        try: tags = self._request('/api/tags')
        except Exception as exc:
            raise LLMError(f'cannot reach Ollama at {self.url} ({type(exc).__name__}: {exc}). Is `ollama serve` running?') from exc
        names = {m.get('name') for m in tags.get('models', [])} | {m.get('model') for m in tags.get('models', [])}
        if self.model not in names and f'{self.model}:latest' not in names:
            raise LLMError(f'model {self.model!r} is not pulled on this Ollama server; run: ollama pull {self.model}')
        return sorted(n for n in names if n)

    def body(self, prompt, kinds=None):
        b = {'model': self.model, 'stream': False, 'messages': [{'role': 'user', 'content': prompt}],
             'options': {'temperature': self.temperature, 'num_ctx': self.num_ctx, 'num_predict': self.num_predict},
             'keep_alive': self.keep_alive}
        if self.think is not None: b['think'] = self.think
        if self.fmt == 'json': b['format'] = 'json'
        elif self.fmt == 'schema': b['format'] = action_schema(kinds)
        return b

    def complete(self, prompt, kinds=None):
        body = self.body(prompt, kinds); last = None
        for attempt in range(self.retries + 1):
            t0 = time.perf_counter()
            try:
                data = self._request('/api/chat', body)
                content = strip_reply(data['message']['content'])
                dt = time.perf_counter() - t0
                s = self.stats; s['calls'] += 1; s['seconds'] += dt; s['latencies'].append(dt)
                s['prompt_tokens'] += int(data.get('prompt_eval_count') or 0); s['eval_tokens'] += int(data.get('eval_count') or 0)
                if not content: last = 'empty reply' + (' (model only produced thinking)' if data['message'].get('thinking') else ''); continue
                self.last_error = None; return content
            except urllib.error.HTTPError as exc:
                text = ''
                try: text = exc.read().decode(errors='replace')[:300]
                except Exception: pass
                last = f'HTTP {exc.code}: {text}'
                if exc.code == 400 and 'think' in body and 'think' in text.lower():
                    body = {k: v for k, v in body.items() if k != 'think'}; self.think = None; continue   # model has no thinking switch
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as exc:
                last = f'{type(exc).__name__}: {exc}'
            self.stats['retries'] += 1
            if attempt < self.retries: self.sleep(min(2 ** attempt, 5))
        self.stats['errors'] += 1; self.last_error = last
        return f'!llm_error {last}'


class LLMPolicy(TextPolicy):
    """TextPolicy over an OllamaClient. An unparsable reply, a transport error, or a kind that is not on today's menu
    is kept in the log (invalid_reply) and, with [llm] on_invalid = "routine", replaced by the person's usual work
    so a flaky model does not silently starve a household; with "rest" it becomes a plain rest."""
    def __init__(self, client, on_invalid='routine', instructions=None):
        super().__init__(client.complete, name=f'ollama:{client.model}', instructions=instructions or LLM_INSTRUCTIONS)
        self.client, self.on_invalid = client, on_invalid
        self.counts = {'decisions': 0, 'invalid': 0, 'fallback': 0}
    def decide(self, packet):
        menu = {m['kind'] for m in packet['menu']}
        raw = self.complete(self.prompt(packet), kinds=sorted(menu)); self.counts['decisions'] += 1
        action = parse_action(raw)
        if action is not None and action['kind'] in menu: return action
        self.counts['invalid'] += 1
        why = str(raw)[:self.max_raw] if action is None else f"kind {action['kind']!r} is not on the menu: " + str(raw)[:self.max_raw]
        if self.on_invalid == 'routine' and 'work' in menu:
            self.counts['fallback'] += 1; return {'kind': 'work', 'fallback': 'routine', 'invalid_reply': why}
        return {'kind': 'rest', 'invalid_reply': why}


def client_from_config(cfg, **overrides):
    """The one place the [llm] section is read into a client; run.py and bench.py go through here.
    overrides replace keys of a copy of cfg['llm'] (bench sweeps model/format/url this way)."""
    llm = {**cfg['llm'], **overrides}
    return OllamaClient(llm), LLMPolicy_kwargs(llm)


def LLMPolicy_kwargs(llm):
    return {'on_invalid': llm['on_invalid']}
