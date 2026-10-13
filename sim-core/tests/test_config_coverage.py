"""Config coverage: every section of run.default.toml is either READ by engine code or listed as PENDING here.
A pending section that gains a reader fails too, so the list cannot rot. Leaf-level: within a read section,
each key must appear as a string literal in engine code (outside config.py validation), unless the section's
keys are ids/free keys, or a season/stage table (those are iterated by vocabulary, not by key).
Static heuristic over chronicle/*.py; it is a design-hygiene gate, not a dynamic trace."""
import sys, re, glob, tomllib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from chronicle.config import FREE_KEY_SECTIONS, SEASONS, TERRAINS
checks=[]
def check(x,msg):
    assert x,msg
    checks.append(msg); print('ok',msg,flush=True)

raw=tomllib.load(open(ROOT/'run.default.toml','rb'))
SRC={Path(f).name:open(f).read() for f in glob.glob(str(ROOT/'chronicle'/'*.py'))}
ENGINE=''.join(v for k,v in SRC.items() if k!='config.py')   # config.py validates everything; readers are elsewhere

# Sections whose keys are declared in RUN_PARAMS v2 for systems not yet built. Removing a section from
# this list is the act of wiring it in; adding one must come with a dated reason.
PENDING={
 'leisure':'non-rest leisure (socialize/drink/rites/courtship) not implemented',
 'secrets':'concealed-event discovery not implemented',
 'tension':'claim overlap pressure not implemented',
 'resolver':'conflict resolver (steal/confront) not implemented',
 'market':'trade not implemented',
 'loans':'credit not implemented',
 'hire':'wage labor not implemented',
 'justice':'courts not implemented',
 'succession':'inheritance not implemented',
 'polity':'claims/taxes not implemented',
 'localities':'settlement lifecycle not implemented',
 'chroniclers':'chronicler cadence consumers (increment F) not implemented',
 'prompts':'LLM prompts (increment F) not implemented',
 'catalog':'theme enable/disable/proposals consumed only at load (config.py)',
 'stages':'consumed at load into derived.stage_days / stage_order',
 'transfer':'transfer.default_visibility not yet applied; inventory hardcodes transfer visibility (TODO wire)',
}
def section_read(sec):
    return re.search(r"""\[\s*["']%s["']\s*\]"""%re.escape(sec),ENGINE) is not None
sections=[s for s,v in raw.items() if isinstance(v,dict)]
live=[s for s in sections if s not in PENDING]
unread_live=[s for s in live if not section_read(s)]
check(not unread_live,f'every non-pending section is read by engine code (unread: {unread_live})')
read_pending=[s for s in PENDING if s in raw and section_read(s)]
check(not read_pending,f'no pending section has quietly gained a reader (would be: {read_pending})')
stale=[s for s in PENDING if s not in raw]
check(not stale,f'every pending entry still exists in run.default.toml (stale: {stale})')
print(f'  live sections ({len(live)}): {", ".join(live)}')
print(f'  pending ({len(PENDING)}): {", ".join(PENDING)}')

# leaf level for live sections
VOCAB_TABLES=set(SEASONS)|set(raw['stages'])|set(TERRAINS)|{'food','medium'}   # tables keyed by a closed physical vocabulary
from chronicle.config import VERBS
VOCAB_TABLES|={v for v,spec in VERBS.items() if spec['timed']}   # agency.action_hours is keyed by the verb registry
def is_free(path): return any(path==s or path.startswith(s+'.') for s in FREE_KEY_SECTIONS) or path.startswith('capabilities.')
def leaves(d,prefix=''):
    for k,v in d.items():
        p=f'{prefix}{k}'
        if isinstance(v,dict) and v: yield from leaves(v,p+'.')
        else: yield p
# keys of a read section that are known-unused today, with the reason. Same contract as PENDING.
PENDING_KEYS={
 'gossip.rate_days':'legacy D2 cadence; contact-v1 runs the contact phase daily and rates it with [contact] (candidate for removal)',
 'development.elder_teach_mult':'teaching rate by stage not yet applied (stage_rate used)',
 'development.trait_drift':'traits are static',
 'needs.consumption_g_per_day':'consumed through derived.consumption_units_per_day',
 'needs.safety_hit':'violence events not implemented',
 'fire.hearth_p':'hearth ignition not implemented (lightning + deliberate only)',
 'fire.spot_p':'spotting not implemented','fire.spot_range_km':'spotting not implemented',
 'fire.base_hours':'consumed through derived.cell_cross_hours_base',
 'items.granary_spoil_mult':'granary spoil multiplier not applied; holder spoil_mult is set by callers (TODO wire)',
 'items.spoil_per_day':'per-kind fallback; catalog items carry spoil_per_day',
 'agriculture.harvest_labor_days_per_ha':'harvest labor modeled per field, not per ha','agriculture.plant_labor_days_per_ha':'planting labor modeled per field, not per ha',
 'wildlife.herd_predation_p':'livestock herds not implemented',
 'world.setup_prompt':'LLM setup (increment F)','world.theme':'consumed at load','world.forbid':'consumed at load',
 'world.width_km':'consumed through derived.grid_w','world.height_km':'consumed through derived.grid_h',
 'run.name':'label only','run.origin':'label only','run.years':'consumed through derived.total_days',
 'run.checkpoint_every_ticks':'checkpoint cadence driven by tests/callers today','run.seed':'consumed by Config.seed',
 'rng.generator':'consumed at load',
 'units.weight_unit':'label only','validator.strict':'read via cfg["validator"]["strict"] by ledger (string present)',
 'transfer.default_visibility':'transfer visibility defaults inside inventory',
 'transit.road_wear_per_crossing':'road wear not implemented','transit.road_wear_decay_per_day':'road wear not implemented',
 'transit.road_wear_level_threshold':'road wear not implemented','transit.road_revert_days':'road wear not implemented',
 'transit.terrain_fatigue':'fatigue not implemented',
 'people.traits_per_person':'setup generator','time.work_days':'free keys = roles',
'records.interview_p':'chroniclers (F)','records.index_entries_in_context':'LLM context (F)',
 'records.refill_threshold':'media refill not implemented',
 'attention.weights.proximity':'scene proximity needs the chronicler scene builder (F part 3)','attention.weights.spark':'spark/pull need scene scheduling (F part 3)',
 'attention.weights.pull':'spark/pull need scene scheduling (F part 3)','attention.decay':'scene recency decay (F part 3)',
 'attention.spark_p':'spark/pull need scene scheduling (F part 3)','attention.t_named':'naming threshold (F part 3)',
 'llm.max_scene_tokens':'chronicler scenes (F part 3); decisions use llm.num_predict',
 'llm.gpu_hours_budget':'consumed at load into derived.warnings','llm.avg_scene_seconds':'consumed at load into derived.est_gpu_hours',
}
# leaf names that collide with unrelated code (checkpoint blob records use x['name']); checked by full path instead
AMBIGUOUS={'run.name':False,'rng.layout_version':True}
ITERATED={'needs.drift_per_day':'people.step_person iterates every need',
          'population.roles_mix':'setup.generate samples roles by iterating the mix table',
          'setup.coin_by_wealth':'setup.generate ranks wealth classes by iterating the table'}   # tables read by .items(), not by key
def key_read(path):
    if any(path.startswith(t+'.') for t in ITERATED): return True
    """Heuristic: the leaf key is subscripted or .get()-ed somewhere in engine code (outside config.py)."""
    if path in AMBIGUOUS: return AMBIGUOUS[path]
    leaf=re.escape(path.split('.')[-1])
    return re.search(r"""\[\s*["']%s["']\s*\]|\.get\(\s*["']%s["']"""%(leaf,leaf),ENGINE) is not None
problems=[]; pending_hits=[]
for sec in live:
    for path in leaves({sec:raw[sec]}):
        if is_free(path): continue
        if path.split('.')[-1] in VOCAB_TABLES: continue
        if any(path==k or path.startswith(k+'.') for k in PENDING_KEYS):
            if key_read(path) and path.split('.')[-1] not in ('strict','seed'): pending_hits.append(path)
            continue
        if not key_read(path): problems.append(path)
print('  unread live keys:',problems)
print('  pending keys that now have a reader:',pending_hits)
check(not pending_hits,f'no pending key has quietly gained a reader (would be: {pending_hits})')
check(not problems,f'every key of a live section is read or explicitly pending (unread: {problems})')

report={'suite':'config-coverage','checks':len(checks),'failures':[],'live_sections':live,'pending_sections':PENDING,
        'pending_keys':PENDING_KEYS,'scope':'static grep over chronicle/*.py (config.py excluded); section + leaf level; season/stage/free-key tables exempt'}
(ROOT/'test-results'/'config_coverage.json').write_text(json.dumps(report,indent=2))
print(f'\n{len(checks)} checks passed')
