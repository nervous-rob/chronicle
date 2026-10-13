"""Chronicle runner (worldgen-v1). Load a run TOML, generate a world, step days, write checkpoints and a digest.

  python run.py --config run.example.toml --days 365 --out runs/iron-coast
  python run.py --resume runs/iron-coast/checkpoint-day-0180.zip --days 100 --out runs/iron-coast

Default: no network, no LLM; every person follows the engine's routine logistics (farm workers and pantry keepers).
With --llm, [attention] picks K people a day and a local Ollama model (see [llm]) chooses their action; every decision
is logged to <out>/decisions.jsonl and `--replay <that file>` re-runs the same world with no model at all.
Writes <out>/setup.json (generator report), <out>/digest.jsonl (one line per day: alive, dead, hungry,
event-type counts, stocks), <out>/checkpoint-day-NNNN.zip every run.checkpoint_every_ticks ticks, and
<out>/final.json. With --frames it also writes world.json, frames.jsonl and (for --llm / --replay) context.jsonl for viewer.html;
the export only reads state, so the digest is unchanged. A world is a pure function of (config, seed): the same inputs give the same digest.
"""
from __future__ import annotations
import argparse, json, os, sys, time
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle import setup
from chronicle.attention import Attention, run_day, replay_days
from chronicle.llm import OllamaClient, LLMPolicy, LLMError, client_from_config
from chronicle.agency import ReplayPolicy
from chronicle.frames import FrameWriter, ContextTap


def day_line(e, day):
    alive = [p for p in e.people.rows.values() if p.alive]
    th = e.cfg['needs']['thresholds']['low']
    return {'day': day, 'alive': len(alive), 'dead': len(e.people.rows) - len(alive),
            'hungry': sum(1 for p in alive if p.needs['food'] < th),
            'events': dict(sorted(Counter(ev.type for ev in e.ledger.events_on(day)).items())),
            'food_units': sum(l.qty for l in e.inventory.lots.values() if e.cfg.item(l.item)['kind'] == 'food'),
            'ecology': {k: v for k, v in e.cells.summary().items() if k in ('moisture_mean', 'burning', 'planted')}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--config', help='run TOML (defaults to run.default.toml when omitted)')
    ap.add_argument('--resume', help='checkpoint zip to continue from')
    ap.add_argument('--days', type=int, help='days to step (default: run.years * 365 minus days already done)')
    ap.add_argument('--out', default='runs/default', help='output directory')
    ap.add_argument('--set', action='append', default=[], metavar='KEY=JSON', help='override, e.g. --set population.initial=60')
    ap.add_argument('--llm', action='store_true', help='let a local Ollama model decide for the K people attention picks each day')
    ap.add_argument('--replay', metavar='DECISIONS_JSONL', help='re-run from a recorded decisions.jsonl; no model is contacted')
    ap.add_argument('--frames', action='store_true', help='also write world.json, frames.jsonl and context.jsonl for viewer.html (read-only; the digest is unchanged)')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)
    overrides = {}
    for kv in a.set:
        k, _, v = kv.partition('=')
        overrides[k] = json.loads(v)
    cfg = load(a.config, overrides=overrides)
    os.makedirs(a.out, exist_ok=True)
    t0 = time.perf_counter()
    if a.resume:
        e = Engine.load_checkpoint(a.resume, expected_config=cfg)
        mode = 'resume'
    else:
        e = Engine(cfg); report = setup.generate(e)
        with open(os.path.join(a.out, 'setup.json'), 'w') as f: json.dump(report, f, indent=1, sort_keys=True)
        mode = 'generate'
    total = cfg.derived['total_days']
    days = a.days if a.days is not None else max(0, total - e.day)
    every = cfg['run']['checkpoint_every_ticks'] * cfg['run']['days_per_tick']
    if not a.quiet:
        print(f'{mode}: run_id {cfg.run_id} seed {cfg.seed} day {e.day} -> {e.day + days} ({time.perf_counter() - t0:.2f}s setup)', flush=True)
    if a.llm and a.replay: ap.error('--llm and --replay are exclusive')
    client = policy_for = att = tapped = None; replay = {}
    if a.llm:
        client, pol_kw = client_from_config(cfg)
        try: have = client.ping()
        except LLMError as exc: ap.error(str(exc))
        shared = LLMPolicy(client, **pol_kw); tapped = ContextTap(shared) if a.frames else shared; policy_for = lambda pid: tapped
        att = Attention(e); print(f"llm: {client.model} at {client.url}, K={att.k(sum(1 for p in e.people.rows.values() if p.alive))}/day, think={client.think}, format={client.fmt}", flush=True)
    elif a.replay:
        with open(a.replay) as f: rlog = [json.loads(l) for l in f if l.strip()]
        rp = ReplayPolicy(rlog); tapped = ContextTap(rp) if a.frames else rp; policy_for = lambda pid: tapped; replay = replay_days(rlog); att = Attention(e)
        print(f'replay: {len(rlog)} recorded decisions over {len(replay)} days, no model', flush=True)
    dec = open(os.path.join(a.out, 'decisions.jsonl'), 'a') if a.llm else None
    digest = open(os.path.join(a.out, 'digest.jsonl'), 'a')
    fw = fr = ctx = None
    if a.frames:
        fw = FrameWriter(e)
        with open(os.path.join(a.out, 'world.json'), 'w') as f: json.dump(fw.world_doc(), f, separators=(',', ':'))
        fr = open(os.path.join(a.out, 'frames.jsonl'), 'a')
        if tapped is not None: ctx = open(os.path.join(a.out, 'context.jsonl'), 'a')
    last = None
    for _ in range(days):
        d = e.day
        if att is not None:
            _d, recs = run_day(e, att, policy_for, chosen=replay.get(d, []) if a.replay else None)
            if dec is not None:
                for r in recs: dec.write(json.dumps(r, sort_keys=True) + '\n')
                dec.flush()
        else: e.step()
        line = day_line(e, d)
        if att is not None: line['decisions'] = len(e.agency.decisions_on(d)); line['invalid'] = sum(1 for r in e.agency.decisions_on(d) if 'invalid_reply' in r['action'])
        digest.write(json.dumps(line, sort_keys=True) + '\n')
        if fr is not None:
            fr.write(json.dumps(fw.frame(d), separators=(',', ':')) + '\n')
            if ctx is not None:
                for r in tapped.drain(): ctx.write(json.dumps(r, sort_keys=True, separators=(',', ':')) + '\n')
        if every and (d + 1) % every == 0:
            last = os.path.join(a.out, f'checkpoint-day-{d + 1:04d}.zip'); e.save_checkpoint(last)
        if not a.quiet and ((d + 1) % 30 == 0 or line['alive'] == 0):
            print(f"day {d + 1:5d} alive {line['alive']:4d} dead {line['dead']:4d} hungry {line['hungry']:4d} food {line['food_units']:9d} events {sum(line['events'].values()):5d}" + (f" decisions {len(e.agency.log)}" if att is not None else ''), flush=True)
        elif not a.quiet and a.llm:
            print(f"day {d + 1:5d} decisions today {line['decisions']} invalid {line['invalid']} ({client.stats['seconds'] / max(1, client.stats['calls']):.1f}s/call)", flush=True)
        if line['alive'] == 0: break
    digest.close()
    for f in (fr, ctx):
        if f is not None: f.close()
    if dec is not None: dec.close()
    final = {'run_id': cfg.run_id, 'seed': cfg.seed, 'mode': mode, 'day': e.day, 'summary': e.summary(), 'digest_sha': e.digest(),
             'last_checkpoint': last, 'seconds': round(time.perf_counter() - t0, 2)}
    if a.llm:
        st = dict(client.stats); lat = sorted(st.pop('latencies'))
        final['llm'] = {**st, 'model': client.model, 'p50_s': lat[len(lat) // 2] if lat else None, 'p95_s': lat[int(len(lat) * .95)] if lat else None, **shared.counts}
    with open(os.path.join(a.out, 'final.json'), 'w') as f: json.dump(final, f, indent=1, sort_keys=True, default=str)
    if not a.quiet: print(f"done: day {e.day} alive {final['summary']['alive']} in {final['seconds']}s -> {a.out}", flush=True)
    return final


if __name__ == '__main__':
    main()
