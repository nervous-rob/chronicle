"""Per-person decision interface (handoff item E): observation packets, legal action
menus, engine-side resolution and a durable decision log that replays.

Contract: the engine resolves consequences; a policy only proposes ONE action per
person per day, from a packet containing only what that person can physically see
(cell bands), carries, owns at hand, remembers, has read, or expects. Packets are
plain JSON data: no engine objects, no global arrays, no other household's stock,
no counts from remote stores. The menu is advisory; farming/people/inventory
primitives remain authoritative (reach, custody, literacy, consent, budget).

A policy may instead answer with a PLAN (plans-v1): a goal, an ordered list of ordinary menu
actions with optional `expects`, and `assumptions` about the observation that must keep holding.
The plan is persisted as plain JSON in Agency.state(); each following day the engine executes the
next step through the same _resolve path without consulting the policy, and consults it again
only on a BREAK: plan finished, horizon passed, a step refused, an `expects` or assumption that
no longer holds, a surprise event newly remembered, or a critical body band. Step cap and horizon
come from [plans] and the person's planning level; the engine truncates, never judges content.

Rejected actions are logged with a reason, cost no time, and never fail the engine.
Timed social/record actions share the daily activity budget with farming/journeys.
Policies are runtime objects and are never serialized: after a checkpoint reload every
agent must be rebound (normally ReplayPolicy over the saved log) or explicitly unbound.
No LLM, network or GPU code lives here; TextPolicy is the text-in/text-out seam.
"""
from __future__ import annotations
import hashlib, json, math
import numpy as np
from .cells import season_of
from .inventory import InventoryError, Leg
from .people import PeopleError
from .config import VERBS

ACTIONS = tuple(VERBS)   # menu order; each verb has _offer_<verb> (menu entry or None) and _do_<verb> (resolution)
LOG_FIELDS = frozenset('day person packet action ok reason hours events source plan'.split())
PLAN_FIELDS = frozenset('goal steps assumptions made_day horizon_day cursor source last_ok expect ledger_mark'.split())
BREAKS = ('finished', 'expired', 'step_refused', 'expectation', 'assumption', 'surprise', 'critical')


def lookup(packet, path):
    """Dotted path into plain packet data ("here.cell", "at_hand.p:3.grain", "self.in_transit"). Raises KeyError when absent."""
    cur = packet
    for part in str(path).split('.'):
        if isinstance(cur, dict):
            if part not in cur: raise KeyError(path)
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip('-').isdigit() and -len(cur) <= int(part) < len(cur):
            cur = cur[int(part)]
        else: raise KeyError(path)
    return cur


class AgencyError(RuntimeError):
    pass


def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False)


def packet_hash(packet):
    return hashlib.sha256(canonical(packet).encode()).hexdigest()


def plain(x):
    """Coerce to JSON-plain data; anything else is a contract violation at the seam."""
    if isinstance(x, dict): return {str(k): plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)): return [plain(v) for v in (sorted(x) if isinstance(x, set) else x)]
    if isinstance(x, np.generic): return x.item()
    if isinstance(x, float) and not math.isfinite(x): return None
    if x is None or type(x) in (bool, int, float, str): return x
    raise AgencyError(f'non-plain value at the agency seam: {type(x).__name__}')


def parse_action(raw):
    """First JSON object in a text reply, or None. Never raises."""
    if isinstance(raw, dict): text = None; cand = raw
    else:
        text = str(raw); cand = None
        for attempt in (text, text[text.find('{'):text.rfind('}') + 1] if '{' in text and '}' in text else ''):
            try:
                cand = json.loads(attempt); break
            except (ValueError, TypeError):
                cand = None
    if type(cand) is not dict or type(cand.get('kind')) is not str: return None
    try: return plain(cand)
    except AgencyError: return None


class ScriptedPolicy:
    """Deterministic test/driver policy: fn(packet) -> action dict."""
    def __init__(self, fn, name='scripted'):
        self.fn, self.name = fn, name
    def decide(self, packet):
        return self.fn(packet)


class ReplayPolicy:
    """Replays a saved decision log. Refuses to continue when the observed packet differs
    from the one the original decision was made on (replay divergence)."""
    name = 'replay'
    def __init__(self, log):
        self.index = {}; self.replayed = 0; self.last_source = None
        for r in log:
            key = (r['day'], r['person'])
            if key in self.index: raise AgencyError('duplicate decision in log')
            self.index[key] = r
    def decide(self, packet):
        key = (packet['day'], packet['self']['id'])
        r = self.index.get(key)
        if r is None: raise AgencyError(f'no recorded decision for day {key[0]} person {key[1]}')
        if r['packet'] != packet_hash(packet):
            raise AgencyError(f'replay divergence on day {key[0]} person {key[1]}: observed packet differs from the recorded one')
        # The log records who made the decision; replay is a mechanism, not an author. A resumed run
        # must be indistinguishable from the uninterrupted one, so the original source is preserved.
        self.last_source = r['source']
        self.replayed += 1
        return r['action']


DEFAULT_INSTRUCTIONS = (
    'You are one person in a simulated world. You know only what the OBSERVATION says. '
    'Choose exactly one action from "menu". Reply with a single JSON object such as '
    '{"kind":"work"} or {"kind":"go","place":3} or {"kind":"say","words":"..."} or '
    '{"kind":"note","medium":7,"text":"..."} or {"kind":"teach","capability":"<id>","students":[2],"medium":7}. '
    'You may instead reply with a PLAN for the next days: {"kind":"plan","goal":{"kind":"food","why":"..."},'
    '"steps":[{"action":{"kind":"go","place":3},"expects":{"path":"here.place","value":3}},{"action":{"kind":"haul","from":"b:3","to":"p:1"}}],'
    '"assumptions":[{"path":"here.at_own_place","value":true}]}. One step runs per day; you are asked again only when the plan '
    'ends or breaks ("plan.break" tells you why). Anything else is treated as rest.')


class TextPolicy:
    """Text-in/text-out seam for a local language model. complete(prompt) -> str is supplied
    by the caller; nothing here talks to a network. Unparsable replies become rest and keep
    the raw reply (truncated) in the log so model misbehaviour is visible, never fatal."""
    def __init__(self, complete, name='text', instructions=None, max_raw=400):
        self.complete, self.name, self.instructions, self.max_raw = complete, name, instructions or DEFAULT_INSTRUCTIONS, max_raw
    def prompt(self, packet):
        return self.instructions + '\n\nOBSERVATION:\n' + canonical(packet) + '\n\nReply with one JSON object.'
    def decide(self, packet):
        raw = self.complete(self.prompt(packet))
        action = parse_action(raw)
        if action is None: return {'kind': 'rest', 'invalid_reply': str(raw)[:self.max_raw]}
        return action


class Agency:
    def __init__(self, engine):
        self.e = engine
        self.policies = {}     # runtime only; never checkpointed
        self.agents = set()    # pids under policy control; checkpointed
        self.log = []          # every decision, accepted or rejected; checkpointed
        self.plans = {}        # pid -> active plan (plain JSON) ; checkpointed, executed without the policy
        self.day = -1

    def bind(self, pid, policy):
        self.e.people._actor(pid)
        if not callable(getattr(policy, 'decide', None)) or not isinstance(getattr(policy, 'name', None), str):
            raise AgencyError('policy needs decide(packet) and a string name')
        self.policies[pid] = policy; self.agents.add(pid)

    def unbind(self, pid):
        self.policies.pop(pid, None); self.agents.discard(pid); self.plans.pop(pid, None)

    def state(self):
        return {'agents': sorted(self.agents), 'log': self.log, 'day': self.day, 'plans': self.plans}

    # ------------------------------------------------------------ plans (plans-v1)
    def planning_level(self, p):
        return 1 if self.e.people.can(p, 'plan') else 0

    def plan_caps(self, p):
        pl = self.e.cfg['plans']; lvl = self.planning_level(p)
        return pl['base_steps'] + pl['steps_per_level'] * lvl, pl['base_horizon_days'] + pl['horizon_days_per_level'] * lvl

    def _plan_break(self, p, plan, packet):
        """First break condition that holds this morning, or None. Checked BEFORE a step runs."""
        if plan['cursor'] >= len(plan['steps']): return 'finished'
        if packet['day'] > plan['horizon_day']: return 'expired'
        if plan['last_ok'] is False: return 'step_refused'
        if plan['expect'] is not None:
            try: ok = lookup(packet, plan['expect']['path']) == plan['expect']['value']
            except KeyError: ok = False
            if not ok: return 'expectation'
        for a in plan['assumptions']:
            try: ok = lookup(packet, a['path']) == a['value']
            except KeyError: ok = False
            if not ok: return 'assumption'
        surprise = set(self.e.cfg['plans']['surprise_events'])
        # newly learned since adoption: an event created after the plan, or an older one first heard of on a later day
        if any(m['type'] in surprise and (m['event'] >= plan['ledger_mark'] or m['learned'] > plan['made_day']) for m in packet['memory']): return 'surprise'
        if any(packet['body'][k] == 'critical' for k in ('last_meal', 'health', 'rest')): return 'critical'
        return None

    def _adopt(self, p, a, packet, source):
        """Validate a plan reply against [plans] and the current packet; store it. Returns (ok, reason, truncated)."""
        pl = self.e.cfg['plans']
        goal = a.get('goal')
        if type(goal) is not dict or goal.get('kind') not in pl['goal_kinds'] or type(goal.get('why')) is not str or len(goal['why']) > pl['max_chars']:
            return False, 'invalid_goal', 0
        steps = a.get('steps')
        if type(steps) is not list or not steps: return False, 'invalid_steps', 0
        for s in steps:
            if type(s) is not dict or type(s.get('action')) is not dict or s['action'].get('kind') not in VERBS or s['action'].get('kind') == 'plan':
                return False, 'invalid_steps', 0
            ex = s.get('expects')
            if ex is not None and (type(ex) is not dict or set(ex) != {'path', 'value'} or type(ex['path']) is not str): return False, 'invalid_expects', 0
        assumptions = a.get('assumptions', [])
        if type(assumptions) is not list or len(assumptions) > pl['max_assumptions']: return False, 'invalid_assumptions', 0
        for asm in assumptions:
            if type(asm) is not dict or set(asm) != {'path', 'value'} or type(asm['path']) is not str or asm['path'].split('.')[0] in ('plan', 'menu', 'memory'):
                return False, 'invalid_assumptions', 0
            try: held = lookup(packet, asm['path']) == asm['value']
            except KeyError: held = False
            if not held: return False, 'assumption_false', 0
        cap, horizon = self.plan_caps(p)
        truncated = max(0, len(steps) - cap)
        self.plans[p.id] = {'goal': {'kind': goal['kind'], 'why': goal['why']},
                            'steps': [{'action': s['action'], 'expects': s.get('expects')} for s in steps[:cap]],
                            'assumptions': assumptions, 'made_day': packet['day'], 'horizon_day': packet['day'] + horizon,
                            'cursor': 0, 'source': source, 'last_ok': None, 'expect': None, 'ledger_mark': len(self.e.ledger.events)}
        return True, None, truncated

    def _run_step(self, p, packet):
        plan = self.plans[p.id]; step = plan['steps'][plan['cursor']]
        ok, reason = self._resolve(p.id, step['action'], packet)
        plan['cursor'] += 1; plan['last_ok'] = ok; plan['expect'] = step['expects']
        return step['action'], ok, reason

    def decisions_on(self, day):
        return [r for r in self.log if r['day'] == day]

    # ------------------------------------------------------------ observation
    def _band(self, x):
        th = self.e.cfg['needs']['thresholds']
        return 'critical' if x < th['critical'] else 'low' if x < th['low'] else 'ok'

    def observe(self, pid):
        e = self.e; f = e.farming; cfg = e.cfg; p = e.people.rows[pid]; day = e.day
        if f.day != day or pid not in f.budgets: raise AgencyError('observe only inside the daily activity phase')
        if not p.alive: raise AgencyError('dead people do not observe')
        owners = e.people.owners(p)
        here = e.places.get(p.place)
        at_own = here is not None and here.destroyed_day is None and here.cell == p.cell and here.owner in owners
        # Food reach follows eat/move_food: own carry, an own intact place, or own ground lots
        # at this exact place. Media follow people._accessible: any medium in the building one
        # stands in is physically visible; consent/literacy gate reading and writing later.
        reach = {f'p:{pid}'} | ({f'b:{p.place}'} if at_own else set())
        building = f'b:{p.place}' if p.place >= 0 else None
        at_hand, media = {}, []
        for lid in sorted(e.inventory.lots):
            lot = e.inventory.lots[lid]; kind = cfg.item(lot.item)['kind']
            on_ground = lot.holder == f'c:{p.cell}' and p.place >= 0 and lot.ground_place == p.place
            mine = lot.owner in owners
            if kind == 'medium':
                if lot.holder not in reach and lot.holder != building: continue
                sealed = lot.sealed_by is not None and lot.opened_by is None
                media.append({'id': lid, 'item': lot.item, 'holder': lot.holder, 'mine': mine, 'sealed': sealed,
                              'entries': None if sealed else len(lot.entries), 'known': lid in p.known_records})
            elif (lot.holder in reach or on_ground) and (mine or lot.holder == f'p:{pid}'):
                stock = at_hand.setdefault(lot.holder, {}); stock[lot.item] = stock.get(lot.item, 0) + lot.qty
        places = []
        for bid in sorted(e.places):
            b = e.places[bid]; mine = b.owner in owners
            if not (mine or b.cell == p.cell): continue
            dist = int(e.world.hex_distance(np.array([p.cell]), np.array([b.cell]))[0])
            spec = e.cfg.building(b.kind)
            places.append({'id': bid, 'kind': b.kind, 'mine': mine, 'distance_cells': dist, 'here': bid == p.place,
                           'home': bid == p.home_place, 'destroyed': b.destroyed_day is not None,
                           'lodging': 'lodging' in spec['affordances'] and not mine,
                           'rate': (b.room_rate if b.room_rate is not None else spec.get('lodging_rate')) if 'lodging' in spec['affordances'] and not mine else None})
        company = []
        for qid in sorted(e.people.rows):
            q = e.people.rows[qid]
            if qid != pid and e.people.co_present(p, q):
                company.append({'id': qid, 'name': q.name, 'stage': e.people.stage(q, day),
                                'same_household': p.household_id is not None and q.household_id == p.household_id})
        limit = cfg['agency']['packet_memory_limit']
        memory = []
        for eid, m in list(p.memory.items())[-limit:]:
            ev = e.ledger.events[eid]
            memory.append({'event': eid, 'type': ev.type, 'day': ev.day, 'actors': list(ev.actors),
                           'who': [e.people.rows[a].name for a in ev.actors if a in e.people.rows], 'cell': ev.cell,
                           'place': ev.place, 'fidelity': m['fidelity'], 'source': m['source'], 'learned': m['day'],
                           'hops': m['hops'], 'from': m['from'], 'detail': plain(ev.payload)})
        j = f.journeys.get(pid)
        th = cfg['needs']['thresholds']
        packet = {
            'day': day, 'season': season_of(day),
            'self': {'id': pid, 'name': p.name, 'stage': e.people.stage(p, day), 'role': p.role, 'household': p.household_id,
                     'capabilities': sorted(p.capabilities), 'learning': {k: round(v, 4) for k, v in sorted(p.learning.items())},
                     'traits': list(p.traits), 'home': p.home_place, 'hours_left': round(float(f.budgets[pid]), 6),
                     'last_action': p.last_action,
                     'in_transit': None if j is None else {'target': j.target, 'hours_to_next_cell': round(j.remaining, 4)}},
            'body': {'last_meal': self._band(p.needs['food']), 'hungry_days': int(math.ceil(p.starvation - 1e-9)),
                     'health': self._band(p.needs['health']), 'injured': p.needs['health'] < th['low'],
                     'rest': self._band(p.needs['rest']), 'sheltered': p.needs['shelter'] >= 0.5,
                     'slept': self._band(p.last_sleep_quality)},
            'here': {'cell': p.cell, 'place': p.place, 'place_kind': None if here is None else here.kind, 'at_own_place': at_own,
                     'surroundings': plain(e.cells.visible(p.cell)), 'company': company, 'places': places},
            'at_hand': at_hand, 'media': media, 'memory': memory,
            'records_known': [{'medium': lid, 'last_seen_cell': c} for lid, c in sorted(p.known_records.items())],
            'map': {'known_cells': sorted(p.known_cells), 'knowledge': plain(p.known_cells)},
            'beliefs': {k: {'mean': round(v[0], 4), 'samples': v[2]} for k, v in sorted(p.expectations.items())},
        }
        plan = self.plans.get(pid)
        if plan is None: packet['plan'] = None
        else:
            packet['plan'] = {'goal': plan['goal'], 'made_day': plan['made_day'], 'horizon_day': plan['horizon_day'],
                              'step': plan['cursor'], 'of': len(plan['steps']), 'break': self._plan_break(p, plan, packet)}
        packet['menu'] = self._menu(p, packet, media, company, at_own)
        packet = plain(packet)
        json.loads(canonical(packet))
        return packet

    def _menu(self, p, packet, media, company, at_own):
        """Walk the verb registry: skip verbs whose required primitive no held capability enables, then ask each
        verb's _offer_ method whether it is physically/legally available right now. No verb is named here."""
        ctx = dict(p=p, packet=packet, media=media, company=company, at_own=at_own)
        menu = []
        for verb, spec in VERBS.items():
            if spec['requires'] and not self.e.people.can(p, spec['requires']): continue
            entry = getattr(self, f'_offer_{verb}')(**ctx)
            if entry is not None: menu.append(entry)
        return menu

    # ------------------------------------------------------------ menu entries, one per verb
    def _offer_rest(self, **_): return {'kind': 'rest'}
    def _offer_work(self, p, **_):
        f = self.e.farming
        if p.id in f.assignments: return {'kind': 'work', 'farm': f.assignments[p.id]}
        return {'kind': 'work', 'household': f.keepers[p.id]} if p.id in f.keepers else None   # keepers: pantry routine
    def _offer_go(self, p, packet, **_):
        j = self.e.farming.journeys.get(p.id)
        go = [j.target] if j is not None else [b['id'] for b in packet['here']['places'] if not b['destroyed'] and not b['here']]
        return {'kind': 'go', 'places': go} if go else None
    def _offer_haul(self, p, packet, at_own, **_):
        e = self.e; f = e.farming; cfg = e.cfg; pid = p.id
        if pid in f.journeys: return None
        sources = [h for h, stock in packet['at_hand'].items() if any(cfg.item(i)['kind'] in ('food','material') for i in stock)]
        if not sources: return None
        return {'kind': 'haul', 'sources': sources, 'destinations': [f'p:{pid}'] + ([f'b:{p.place}'] if at_own else []),
                'owners': sorted(e.people.owners(p)), 'kg_per_hour': f.cfg['handling_kg_per_hour']}
    def _carried_food(self, p):
        e = self.e; owners = e.people.owners(p)
        return sorted((l for l in e.inventory.lots.values() if l.holder == f'p:{p.id}' and l.owner in owners and e.cfg.item(l.item)['kind'] == 'food'), key=lambda l: l.id)
    def _offer_give(self, p, company, **_):
        if p.id in self.e.farming.journeys or not company: return None
        kg = int(sum(l.qty for l in self._carried_food(p)) * self.e.cfg['units']['food_unit_g'] / 1000)
        return {'kind': 'give', 'recipients': [c['id'] for c in company], 'carried_kg': kg} if kg >= 1 else None
    def _offer_hunt(self, p, packet, **_):
        e = self.e
        if p.id in e.farming.journeys or e.cfg['people']['carry_cap'] - e.inventory.weight(f'p:{p.id}') < 1.0: return None   # nothing to carry a kill home in
        targets = [w for w, band in packet['here']['surroundings']['forage'].items() if band != 'scarce' and e.cells.wl_cfg[w]['hunt_yield_kg'] > 0]
        return {'kind': 'hunt', 'targets': targets} if targets else None
    def _offer_project(self, p, **_):
        if p.id in self.e.farming.journeys: return None
        offers=self.e.projects.offers(p)
        return {'kind':'project','offers':offers} if offers else None
    def _offer_explore(self, p, **_):
        e = self.e
        if p.id in e.farming.journeys: return None
        survey = float(e.cfg['exploration']['survey_hours'])
        edges = e.world.edge_hours(season_of(e.day), injured=p.needs['health'] < e.cfg['needs']['thresholds']['low'])
        cells = []
        for k, cell in enumerate(e.world.neighbors(p.cell)):
            cell = int(cell)
            if cell < 0 or cell in p.known_cells: continue
            travel = float(edges[p.cell, k])
            if math.isfinite(travel) and travel + survey <= e.farming.budgets[p.id] + 1e-7: cells.append(cell)
        return {'kind': 'explore', 'cells': cells, 'survey_hours': survey} if cells else None
    def _offer_firebreak(self, p, **_):
        c = self.e.cells
        if p.id in self.e.farming.journeys or bool(c.burning[p.cell]) or float(c.cleared[p.cell]) >= 0.999: return None
        return {'kind': 'firebreak'}
    def _offer_tell(self, p, packet, company, **_):
        e = self.e
        if p.id in e.farming.journeys or not packet['memory']: return None
        # Simplification (memory-v1): the offer lists, per listener, only events that listener would be hearing for the first
        # time (or at lower fidelity). A real teller sees a blank face or a nod; here that is read straight from their memory.
        mult = e.cfg['gossip']['tell_fidelity_mult']; news = []
        for c in company:
            q = e.people.rows[c['id']]
            if not e.people.can_remember(q, e.day): continue
            fresh = [m['event'] for m in packet['memory'] if (q.memory.get(m['event']) is None or q.memory[m['event']]['fidelity'] < m['fidelity'] * mult)]
            if fresh: news.append({'recipient': c['id'], 'events': fresh})
        return {'kind': 'tell', 'news': news} if news else None
    def _offer_say(self, **_): return {'kind': 'say', 'max_chars': self.e.cfg['stance']['max_chars']}
    def _offer_note(self, media, **_):
        writable = [m['id'] for m in media if m['mine'] and not m['sealed']]
        return {'kind': 'note', 'media': writable, 'max_chars': self.e.cfg['records']['max_chars']} if writable else None
    def _offer_cite(self, packet, media, **_):
        writable = [m['id'] for m in media if m['mine'] and not m['sealed']]
        return {'kind': 'cite', 'media': writable, 'events': [m['event'] for m in packet['memory']]} if writable and packet['memory'] else None
    def _offer_read(self, media, **_):
        readable = [m['id'] for m in media if not m['sealed']]
        return {'kind': 'read', 'media': readable} if readable else None
    def _offer_plan(self, p, **_):
        cap, horizon = self.plan_caps(p); pl = self.e.cfg['plans']
        return {'kind': 'plan', 'max_steps': cap, 'horizon_days': horizon, 'goal_kinds': list(pl['goal_kinds']), 'max_assumptions': pl['max_assumptions']}
    def _offer_teach(self, p, media, company, **_):
        cfg = self.e.cfg
        teachable = sorted(c for c in p.capabilities if cfg['capabilities'][c]['teachers'] == 'any_holder' or p.role in cfg['capabilities'][c]['teachers'])
        if not (teachable and company): return None
        return {'kind': 'teach', 'capabilities': teachable, 'students': [c['id'] for c in company],
                'media': [m['id'] for m in media if not m['sealed']]}

    # ------------------------------------------------------------ decision + resolution
    def act(self, pid):
        e = self.e; f = e.farming
        if f.day != e.day: raise AgencyError('agency acts inside the daily activity phase')
        policy = self.policies.get(pid)
        if policy is None: raise AgencyError(f'agent {pid} has no bound policy: rebind (e.g. ReplayPolicy) or unbind after reload')
        packet = self.observe(pid); h = packet_hash(packet); p = e.people.rows[pid]
        before = f.budgets[pid]; start = len(e.ledger.events)
        plan = self.plans.get(pid); brk = packet['plan']['break'] if plan is not None else None
        if plan is not None and brk is None:
            # Plan day: no policy call. The step is an ordinary action through the ordinary resolver.
            info = {'made_day': plan['made_day'], 'step': plan['cursor'], 'of': len(plan['steps']), 'break': None, 'truncated': 0}
            logged, ok, reason = self._run_step(p, packet); source = f"plan:{plan['source']}"
        else:
            if plan is not None: del self.plans[pid]   # broken or finished: the policy decides afresh (it may plan again)
            action = policy.decide(packet)
            source = getattr(policy, 'last_source', None) or policy.name
            try: logged = plain(action) if type(action) is dict else {'kind': 'invalid', 'repr': str(action)[:200]}
            except AgencyError: logged = {'kind': 'invalid', 'repr': str(action)[:200]}
            info = None if brk is None else {'made_day': plan['made_day'], 'step': plan['cursor'], 'of': len(plan['steps']), 'break': brk, 'truncated': 0}
            if logged.get('kind') == 'plan':
                f.actions[pid] = 'rest'
                ok, reason, truncated = self._adopt(p, logged, packet, source)
                info = {'made_day': e.day, 'step': 0, 'of': len(self.plans[pid]['steps']) if ok else 0, 'break': brk, 'truncated': truncated}
                if ok: _step_action, ok, reason = self._run_step(p, packet)   # the first step runs today; adopting costs no time
            else:
                ok, reason = self._resolve(pid, logged, packet)
        rec = {'day': e.day, 'person': pid, 'packet': h, 'action': logged, 'ok': ok, 'reason': reason,
               'hours': [round(float(before), 6), round(float(f.budgets[pid]), 6)],
               'events': [start, len(e.ledger.events)], 'source': source, 'plan': info}
        self.log.append(rec); self.day = e.day
        return rec

    def _timed(self, pid, kind):
        f = self.e.farming; hours = float(self.e.cfg['agency']['action_hours'][kind])
        if f.budgets[pid] + 1e-7 < hours: raise ValueError('no_time')
        return hours

    def _resolve(self, pid, a, packet):
        e = self.e; f = e.farming; p = e.people.rows[pid]
        f.actions[pid] = 'rest'
        kind = a.get('kind')
        if kind not in VERBS: return False, 'invalid_action'
        entry = next((m for m in packet['menu'] if m['kind'] == kind), None)
        if entry is None: return False, 'not_on_menu'
        try:
            return getattr(self, f'_do_{kind}')(p, a, entry)
        except (PeopleError, InventoryError, ValueError, KeyError, TypeError) as exc:
            return False, f'{type(exc).__name__}:{str(exc)[:120]}'

    # ------------------------------------------------------------ resolution, one per verb.
    # Arguments must come from the offered menu entry: a legal kind with an off-menu medium/event/student
    # is rejected before any primitive runs. Timed verbs check the budget (_timed) after argument
    # validation and spend after the primitive succeeded.
    def _do_rest(self, p, a, entry): return True, None
    def _do_work(self, p, a, entry):
        f = self.e.farming
        (f.automatic if p.id in f.assignments else f.keep)(p.id); return True, None
    def _do_go(self, p, a, entry):
        f = self.e.farming; target = a.get('place')
        if type(target) is not int or target not in entry['places']: return False, 'unknown_place'
        if f.move(p.id, target): return True, 'arrived'
        return (True, 'en_route') if p.id in f.journeys else (False, 'unreachable')
    def _do_haul(self, p, a, entry):
        f = self.e.farming; pid = p.id
        src, dst, limit = a.get('from'), a.get('to'), a.get('limit')
        if type(src) is not str or type(dst) is not str or (limit is not None and (type(limit) is not int or limit < 0)):
            return False, 'invalid_args'
        owner = a.get('owner', f'h:{p.household_id}' if p.household_id is not None else f'p:{pid}')
        moved = f.move_food(pid, src, dst, owner, limit)
        return (moved > 0), (None if moved else 'nothing_moved')
    def _do_give(self, p, a, entry):
        e = self.e; rid, kg = a.get('recipient'), a.get('kg')
        if type(rid) is not int or rid not in entry['recipients']: return False, 'not_on_menu'
        if type(kg) is not int or not 1 <= kg <= entry['carried_kg']: return False, 'invalid_args'
        hours = self._timed(p.id, 'give')
        left = int(round(kg * 1000 / e.cfg['units']['food_unit_g'])); legs = []
        for l in self._carried_food(p):
            q = min(left, l.qty)
            if q > 0: legs.append(Leg(l.id, q, f'p:{rid}', f'p:{rid}')); left -= q
            if left == 0: break
        e.inventory.transfer(e.day, legs, actors=(p.id, rid), cell=p.cell, place=p.place, reason='give')
        e.farming._spend(p.id, hours, 'give'); return True, None
    def _do_hunt(self, p, a, entry):
        e = self.e; target = a.get('target')
        if target not in entry['targets']: return False, 'not_on_menu'
        hours = self._timed(p.id, 'hunt')
        killed = int(e.cells.sample_hunt(e.day, p.id, target, p.cell))
        e.farming._spend(p.id, hours, 'hunt')
        room = int(e.cfg['people']['carry_cap'] - e.inventory.weight(f'p:{p.id}'))   # kg the hunter can physically carry home
        carried = max(0, min(killed, room))
        if carried > 0:
            e.inventory.create(e.day, e.cfg['agency']['hunt_item'], int(round(carried * 1000 / e.cfg['units']['food_unit_g'])),
                               f'p:{p.id}', f'p:{p.id}', source='production', process='hunt')
        e.ledger.event(e.day, 'hunt', [p.id], cell=p.cell, place=p.place, visibility='public' if killed else 'private',
                       target=target, killed_kg=killed, carried_kg=carried)
        return True, (None if killed else 'no_kill')
    def _do_project(self, p, a, entry):
        qid=a.get('id')
        if qid is not None:
            if type(qid) is not int or not any(x.get('id')==qid for x in entry['offers']): return False, 'not_on_menu'
        else:
            kind=a.get('project'); building=a.get('building'); target=a.get('target_place')
            ok=any(x.get('kind')==kind and (kind not in ('build','repair') or x.get('building')==building) and (kind!='repair' or x.get('target_place')==target) for x in entry['offers'])
            if not ok: return False, 'not_on_menu'
        self.e.projects.work(p,a); return True, None
    def _do_explore(self, p, a, entry):
        e = self.e; target = a.get('cell')
        if type(target) is not int or target not in entry['cells']: return False, 'not_on_menu'
        origin, origin_place = p.cell, p.place; neighbors = list(e.world.neighbors(origin)); k = neighbors.index(target)
        edges = e.world.edge_hours(season_of(e.day), injured=p.needs['health'] < e.cfg['needs']['thresholds']['low'])
        travel = float(edges[origin, k]); total = travel + float(e.cfg['exploration']['survey_hours'])
        if not math.isfinite(travel) or e.farming.budgets[p.id] + 1e-7 < total: return False, 'unreachable'
        e.ledger.event(e.day, 'departed', [p.id], cell=origin, place=origin_place, target_cell=target, purpose='explore')
        e.farming._spend(p.id, total, 'explore'); p.cell = target; p.place = -1
        e.ledger.event(e.day, 'passed_through', [p.id], cell=target, target_cell=target, purpose='explore')
        from .world import T
        terrain = next(name for name, code in T.items() if code == int(e.world.terrain[target]))
        p.known_cells[target] = {'day': e.day, 'source': 'survey', 'terrain': terrain, 'visible': plain(e.cells.visible(target))}
        e.ledger.event(e.day, 'discovered', [p.id], cell=target, place=-1, discovery='cell', terrain=terrain)
        e.ledger.event(e.day, 'arrived', [p.id], cell=target, place=-1, travel_hours=travel, purpose='explore')
        return True, None
    def _do_firebreak(self, p, a, entry):
        e = self.e; hours = self._timed(p.id, 'firebreak'); frac = e.cfg['agency']['firebreak_clear']
        e.cells.clear([p.cell], frac)
        e.ledger.event(e.day, 'firebreak', [p.id], cell=p.cell, place=p.place, fraction=frac)
        e.farming._spend(p.id, hours, 'firebreak'); return True, None
    def _do_tell(self, p, a, entry):
        e = self.e; rid, eid = a.get('recipient'), a.get('event')
        row = next((n for n in entry['news'] if n['recipient'] == rid), None) if type(rid) is int else None
        if row is None or type(eid) is not int or eid not in row['events']: return False, 'not_on_menu'
        m = p.memory.get(eid); l = e.people.rows[rid]; g = e.cfg['gossip']
        if m is None: return False, 'not_remembered'
        old = l.memory.get(eid)
        if old is not None and old['fidelity'] >= m['fidelity'] * g['tell_fidelity_mult']: return False, 'already_known'   # no hours wasted telling what they know
        hours = self._timed(p.id, 'tell')
        e.people.tell(e.day, p.id, rid, eid)
        e.farming._spend(p.id, hours, 'tell'); return True, None
    def _do_say(self, p, a, entry):
        e = self.e; words = a.get('words')
        if type(words) is not str or not words.strip() or len(words) > entry['max_chars']: return False, 'invalid_words'
        hours = self._timed(p.id, 'say')
        e.ledger.event(e.day, 'say', [p.id], cell=p.cell, place=p.place, words=words)
        e.farming._spend(p.id, hours, 'say'); return True, None
    def _do_note(self, p, a, entry):
        e = self.e
        if a.get('medium') not in entry['media']: return False, 'not_on_menu'
        if type(a.get('text')) is not str or not a['text'].strip() or len(a['text']) > entry['max_chars']: return False, 'invalid_text'
        hours = self._timed(p.id, 'note')
        e.people.record(e.day, p.id, a['medium'], [{'type': 'note', 'text': a['text']}])
        e.farming._spend(p.id, hours, 'note'); return True, None
    def _do_cite(self, p, a, entry):
        e = self.e
        if a.get('medium') not in entry['media'] or a.get('event') not in entry['events']: return False, 'not_on_menu'
        hours = self._timed(p.id, 'cite')
        e.people.record(e.day, p.id, a['medium'], [{'type': 'cite', 'event_id': a['event']}])
        e.farming._spend(p.id, hours, 'cite'); return True, None
    def _do_read(self, p, a, entry):
        e = self.e
        if a.get('medium') not in entry['media']: return False, 'not_on_menu'
        hours = self._timed(p.id, 'read')
        e.people.read_record(e.day, p.id, a['medium'])
        e.farming._spend(p.id, hours, 'read'); return True, None
    def _do_plan(self, p, a, entry): return False, 'plan_is_not_a_step'   # act() adopts plans before _resolve; a plan inside a plan is refused
    def _do_teach(self, p, a, entry):
        e = self.e; students = a.get('students'); medium = a.get('medium')
        if type(students) is not list or not students or not all(type(s) is int for s in students): return False, 'invalid_args'
        if a.get('capability') not in entry['capabilities'] or not set(students) <= set(entry['students']) or (medium is not None and medium not in entry['media']):
            return False, 'not_on_menu'
        hours = self._timed(p.id, 'teach')
        e.people.teach(e.day, p.id, students, a['capability'], medium)
        e.farming._spend(p.id, hours, 'teach'); return True, None
