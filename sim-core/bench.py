"""Model bench: how well does an Ollama model answer Chronicle's decision prompt?

  python bench.py                                  # [llm] model, 24 prompts, all three reply formats
  python bench.py --model qwen3.5:9b --model gemma4:12b --n 30 --format schema --format json

Builds a small world, collects real observation packets from the first days, then asks each model/format to decide
and scores: parsed (valid JSON action), on_menu (kind offered today), seconds per call, and tokens/s. Nothing here
changes a run: it is a read-only comparison so you can pick a model and format on your own hardware.
"""
from __future__ import annotations
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle import setup
from chronicle.agency import ScriptedPolicy, parse_action
from chronicle.attention import Attention, run_day
from chronicle.llm import OllamaClient, LLMError, LLM_INSTRUCTIONS, client_from_config
from chronicle.agency import canonical


def collect(cfg, n):
    e = Engine(cfg); setup.generate(e); att = Attention(e); packets = []
    def fn(packet):
        if len(packets) < n * 3: packets.append(packet)
        return {'kind': 'work'} if any(m['kind'] == 'work' for m in packet['menu']) else {'kind': 'rest'}
    pol = ScriptedPolicy(fn)
    for _ in range(40):
        run_day(e, att, lambda pid: pol)
        if len(packets) >= n * 3: break
    step = max(1, len(packets) // n)
    return packets[::step][:n]


def pct(xs, q): return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--model', action='append', help='Ollama model tag (repeatable); default is [llm].model')
    ap.add_argument('--format', action='append', choices=['schema', 'json', 'none'], help='reply format (repeatable); default all three')
    ap.add_argument('--n', type=int, default=24, help='prompts per model/format')
    ap.add_argument('--url'); ap.add_argument('--config'); ap.add_argument('--out', default='bench-results.json')
    ap.add_argument('--think', choices=['false', 'true', 'default'], default='false')
    ap.add_argument('--set', action='append', default=[], metavar='KEY=JSON')
    a = ap.parse_args(argv)
    ov = {'population.initial': 40}
    for kv in a.set:
        k, _, v = kv.partition('='); ov[k] = json.loads(v)
    cfg = load(a.config, overrides=ov)
    base = dict(cfg['llm'])
    if a.url: base['url'] = a.url
    base['think'] = {'false': False, 'true': True, 'default': None}[a.think]
    packets = collect(cfg, a.n)
    print(f'{len(packets)} real observation packets, median {sorted(len(canonical(p)) for p in packets)[len(packets) // 2]} chars', flush=True)
    rows = []
    for model in a.model or [base['model']]:
        for fmt in a.format or ['schema', 'json', 'none']:
            llm = {**base, 'model': model, 'format': fmt, 'retries': 0}
            c = OllamaClient(llm)
            try: c.ping()
            except LLMError as exc: print(f'SKIP {model}: {exc}'); break
            c.complete('Reply with {"kind":"rest"}')   # warm-up: load the model into VRAM, not scored
            c.stats.update(calls=0, seconds=0.0, prompt_tokens=0, eval_tokens=0, latencies=[], errors=0)
            parsed = on_menu = work = 0; kinds = {}
            for pk in packets:
                raw = c.complete(LLM_INSTRUCTIONS + '\n\nOBSERVATION:\n' + canonical(pk) + '\n\nReply with one JSON object.', kinds=sorted({m['kind'] for m in pk['menu']}))
                act = parse_action(raw)
                if act is not None:
                    parsed += 1; kinds[act['kind']] = kinds.get(act['kind'], 0) + 1
                    if act['kind'] in {m['kind'] for m in pk['menu']}: on_menu += 1
                    if act['kind'] == 'work': work += 1
            lat = sorted(c.stats['latencies']); n = len(packets)
            row = {'model': model, 'format': fmt, 'think': a.think, 'n': n, 'parsed': parsed / n, 'on_menu': on_menu / n,
                   'errors': c.stats['errors'], 'p50_s': pct(lat, .5), 'p95_s': pct(lat, .95),
                   'tok_per_s': (c.stats['eval_tokens'] / c.stats['seconds']) if c.stats['seconds'] else None,
                   'prompt_tokens_avg': c.stats['prompt_tokens'] / max(1, c.stats['calls']), 'eval_tokens_avg': c.stats['eval_tokens'] / max(1, c.stats['calls']), 'kinds': kinds}
            rows.append(row)
            f = lambda x, p='.2f': 'n/a' if x is None else format(x, p)
            print(f"{model:22s} {fmt:7s} parsed {row['parsed']:.0%} on-menu {row['on_menu']:.0%} err {row['errors']} p50 {f(row['p50_s'])}s p95 {f(row['p95_s'])}s {f(row['tok_per_s'], '.1f')} tok/s  eval {row['eval_tokens_avg']:.0f} tok  kinds {kinds}", flush=True)
    with open(a.out, 'w') as fh: json.dump({'rows': rows}, fh, indent=1)
    if rows:
        best = max(rows, key=lambda r: (r['on_menu'], -(r['p50_s'] or 99)))
        k = cfg['attention']
        print(f"best: {best['model']} / {best['format']}. At K={k['k_per_tick']}/day and p50 {f(best['p50_s'])}s a 365-day year is about {k['k_per_tick'] * 365 * (best['p50_s'] or 0) / 3600:.1f} GPU-hours.")
    return rows


if __name__ == '__main__':
    main()
