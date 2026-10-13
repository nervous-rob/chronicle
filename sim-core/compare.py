"""Scripted-vs-LLM comparison harness (compare-v1).

  python compare.py --run --days 100 --scenario --out runs/cmp        # runs scripted, then --llm, same seed and config, then compares
  python compare.py runs/cmp/scripted runs/cmp/llm                      # compare two finished runs (any two run dirs)
  python compare.py runs/cmp/scripted runs/cmp/llm --json report.json   # also write the numbers

Both runs start from the same (config, seed), so they are identical until the first model decision changes something. The report gives
outcomes (survival, hunger, food, fire, ecology), the first day the two worlds differ, and what the model actually chose.
Read-only: it only reads digest.jsonl / final.json / decisions.jsonl. A single seed is an anecdote, not a result; use --seeds for more.
"""
from __future__ import annotations
import argparse, json, os, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PRESSURE_VERBS = ('give', 'hunt', 'firebreak', 'tell')


def _rd(path):
    with open(path) as f: return [json.loads(l) for l in f if l.strip()]


def outcomes(run_dir):
    """Numbers computed from digest.jsonl alone, so any run (scripted, llm or replay) can be compared."""
    dg = _rd(os.path.join(run_dir, 'digest.jsonl'))
    ev = Counter()
    for r in dg:
        for k, v in r['events'].items(): ev[k] += v
    died_days = [r['day'] for r in dg for _ in range(r['events'].get('died', 0))]
    burning = [r['ecology']['burning'] for r in dg]
    final = json.load(open(os.path.join(run_dir, 'final.json'))) if os.path.exists(os.path.join(run_dir, 'final.json')) else {}
    return {'days': len(dg), 'alive_final': dg[-1]['alive'], 'deaths': ev['died'], 'first_death_day': died_days[0] if died_days else None,
            'hungry_person_days': sum(r['hungry'] for r in dg), 'peak_hungry': max(r['hungry'] for r in dg),
            'food_units_final': dg[-1]['food_units'], 'food_units_min': min(r['food_units'] for r in dg),
            'peak_burning_cells': max(burning), 'burning_days': sum(1 for b in burning if b), 'ignitions': ev['fire_ignited'], 'buildings_burned': ev['burned'],
            'moisture_final': round(dg[-1]['ecology']['moisture_mean'], 3), 'planted_final': dg[-1]['ecology']['planted'],
            'tells': ev['tell'], 'digest_sha': final.get('digest_sha')}


def decision_stats(run_dir):
    p = os.path.join(run_dir, 'decisions.jsonl')
    if not os.path.exists(p): return None
    rows = _rd(p)
    kinds = Counter(); invalid = fallback = not_ok = 0; per_person = Counter()
    for r in rows:
        a = r['action']
        if 'invalid_reply' in a: invalid += 1
        kinds[a.get('kind', '?')] += 1; per_person[r['person']] += 1
        if not r.get('ok', True): not_ok += 1
    n = len(rows)
    return {'decisions': n, 'kinds': dict(kinds.most_common()), 'invalid': invalid, 'not_ok': not_ok,
            'non_work_share': round(1 - kinds['work'] / n, 3) if n else None,
            'pressure_verb_uses': {k: kinds[k] for k in PRESSURE_VERBS}, 'people_consulted': len(per_person)}


def first_divergence(a_dir, b_dir):
    """First day the two digests disagree on anything physical (alive/dead/hungry/food/ecology/event counts); None if identical."""
    A, B = _rd(os.path.join(a_dir, 'digest.jsonl')), _rd(os.path.join(b_dir, 'digest.jsonl'))
    keys = ('alive', 'dead', 'hungry', 'food_units', 'ecology', 'events')
    for x, y in zip(A, B):
        if any(x[k] != y[k] for k in keys): return x['day']
    return None if len(A) == len(B) else min(len(A), len(B))


def compare(a_dir, b_dir, labels=('scripted', 'llm')):
    oa, ob = outcomes(a_dir), outcomes(b_dir)
    return {'labels': list(labels), labels[0]: oa, labels[1]: ob, 'first_divergence_day': first_divergence(a_dir, b_dir),
            'decisions': {labels[0]: decision_stats(a_dir), labels[1]: decision_stats(b_dir)}}


def render(rep):
    la, lb = rep['labels']; a, b = rep[la], rep[lb]
    out = [f'| metric | {la} | {lb} | diff |', '|---|---:|---:|---:|']
    for k in a:
        if k == 'digest_sha': continue
        d = (b[k] - a[k]) if isinstance(a[k], (int, float)) and isinstance(b[k], (int, float)) else ''
        if isinstance(d, float): d = round(d, 3)
        out.append(f'| {k} | {a[k]} | {b[k]} | {d} |')
    fd = rep['first_divergence_day']
    out.append(''); out.append('worlds identical for the whole run' if fd is None else f'worlds first differ on day {fd}')
    ds = rep['decisions'][lb]
    if ds:
        out.append(f"model decisions: {ds['decisions']} over {ds['people_consulted']} people; invalid {ds['invalid']}; non-work share {ds['non_work_share']}")
        out.append('kinds: ' + ', '.join(f'{k} {v}' for k, v in ds['kinds'].items()))
        out.append('pressure verbs used: ' + ', '.join(f'{k} {v}' for k, v in ds['pressure_verb_uses'].items()))
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dirs', nargs='*', help='two run directories (A then B) to compare')
    ap.add_argument('--run', action='store_true', help='run scripted and --llm into <out>/scripted and <out>/llm first')
    ap.add_argument('--out', default='runs/cmp'); ap.add_argument('--days', type=int, default=100)
    ap.add_argument('--scenario', action='store_true', help='set scenario.enabled=true (drought, ignition, blight, stranger)')
    ap.add_argument('--config'); ap.add_argument('--set', action='append', default=[], metavar='KEY=JSON')
    ap.add_argument('--seeds', default='', help='comma-separated run.seed values; one scripted+llm pair per seed under <out>/seed-N')
    ap.add_argument('--frames', action='store_true'); ap.add_argument('--json', help='write the report here')
    a = ap.parse_args(argv)
    reports = []
    if a.run:
        import run as runner
        base = ['--days', str(a.days), '--quiet'] + (['--config', a.config] if a.config else [])
        for kv in a.set: base += ['--set', kv]
        if a.scenario: base += ['--set', 'scenario.enabled=true']
        if a.frames: base += ['--frames']
        seeds = [s for s in a.seeds.split(',') if s] or [None]
        for s in seeds:
            root = os.path.join(a.out, f'seed-{s}') if s else a.out
            extra = ['--set', f'run.seed={s}'] if s else []
            for name, flag in (('scripted', []), ('llm', ['--llm'])):
                d = os.path.join(root, name)
                if os.path.exists(os.path.join(d, 'digest.jsonl')): sys.exit(f'{d} already has a run; pick a fresh --out')
                runner.main(base + extra + flag + ['--out', d])
            reports.append((root, compare(os.path.join(root, 'scripted'), os.path.join(root, 'llm'))))
    else:
        if len(a.dirs) != 2: ap.error('give two run directories, or use --run')
        reports.append((a.dirs[1], compare(a.dirs[0], a.dirs[1], labels=('A', 'B'))))
    for root, rep in reports:
        print(f'\n== {root} ==\n' + render(rep))
    if len(reports) > 1:
        lb = reports[0][1]['labels'][1]
        print('\n== across seeds (llm minus scripted) ==')
        for k in ('alive_final', 'deaths', 'hungry_person_days', 'food_units_final', 'buildings_burned'):
            ds = [r[lb][k] - r[r['labels'][0]][k] for _, r in reports]
            print(f'{k}: ' + ', '.join(f'{x:+}' for x in ds))
    if a.json:
        with open(a.json, 'w') as f: json.dump([{'root': r, **rep} for r, rep in reports], f, indent=1, sort_keys=True)
    return [rep for _, rep in reports]


if __name__ == '__main__':
    main()
