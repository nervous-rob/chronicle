"""Daily integration harness. No network, LLM, or autonomous background loop.

Callers explicitly seed places, people, farms and inventories. This is NOT the
full setup generator. The core steps ecology -> physical inventories -> daily
people -> observations -> conservation. Tick cadence adds reporting only.

Assigned-household farming and budgeted journeys are implemented in farming.py.
Food deficits/deaths get a physical cause trace (engine.deprivation); it is diagnostic only.
Policy-controlled people (agency.py) choose one menu action per day; the engine resolves it.
Nightly sleep is resolved by walking the [sleep.access] rules in config (sleep-v1a): the engine
never names a building kind; quality flows into people.step_person and the morning consequence rows.
Still pending: non-rest leisure, other production, trade,
family demography, succession, diseases in people/herds, building damage model,
chroniclers/attention. Versioned day-boundary checkpoints are in checkpoint.py. Do not use a long run
of this harness as evidence for a calibrated self-sustaining society.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import math
from .world import World
from .cells import Cells
from .rng import Streams
from .ledger import Ledger
from .inventory import Inventory, Leg
from .people import People
from .farming import Farming
from .agency import Agency
from .projects import Projects


@dataclass
class Place:
    id: int
    kind: str
    cell: int
    owner: str | None
    destroyed_day: int | None = None
    condition: float = 1.0            # structural condition 0..1; scales sleep quality (no damage model writes it yet)
    room_rate: float | None = None    # per-night lodging price override; None = catalog lodging_rate


class Engine:
    def __init__(self, cfg):
        # Capture source provenance before this world is constructed.
        from . import checkpoint as _checkpoint
        self.cfg = cfg; self.rng = Streams(cfg.seed)
        self.world = World.generate(cfg); self.cells = Cells(cfg, self.world, self.rng)
        self.ledger = Ledger(cfg); self.inventory = Inventory(cfg, self.ledger)
        self.people = People(cfg, self.inventory, self.ledger, self.rng)
        self.places: dict[int, Place] = {}; self.day = 0; self.reports = []
        self.last_check_day = None
        self.failed = False
        self._in_step = False
        self._completed_day = -1
        self.farming = Farming(self)
        self.agency = Agency(self)
        self.projects = Projects(self)
        # Daily cause trace for food deficits/deaths. Plain JSON-able state, bounded
        # recent list; diagnostic only, never used by resolvers or agents.
        self.deprivation = {'deficit_days': 0, 'by_cause': {}, 'by_person': {}, 'recent': [], 'deaths': {}}
        self.ledger.on_event = self._witness_event

    def seed_place(self, kind, cell, owner=None):
        """Setup-only building creation."""
        if self.day != 0: raise ValueError('seed_place is setup-only')
        return self.create_place(kind, cell, owner)

    def create_place(self, kind, cell, owner=None):
        """Trusted resolver primitive for a completed world project; never policy-facing."""
        spec = self.cfg.building(kind)
        if not 0 <= cell < self.world.n: raise ValueError('invalid cell')
        from .world import T
        if self.world.terrain[cell] not in [T[t] for t in spec['terrain_allow']]:
            raise ValueError('terrain not allowed for building')
        # Avoid silently claiming all catalog siting constraints are supported.
        if spec['requires']: raise NotImplementedError('special siting prerequisites are not yet implemented')
        bid = len(self.places)
        self.places[bid] = Place(bid, kind, int(cell), owner)
        self.inventory.register(f'b:{bid}', spec['store'], 'store' in spec['affordances'], spec['spoil_mult'])
        self.cells.clear([cell], self.cfg['fire']['building_clearing'])
        return bid

    def add_person(self, born_day, cell, place=-1, **kw):
        if place >= 0 and (place not in self.places or self.places[place].cell != cell):
            raise ValueError('place/cell mismatch')
        return self.people.add(born_day, int(cell), place, day=self.day, **kw)

    def household_stores(self, p):
        # Access to a household store is same-place in this harness. It does not
        # imply every resident in the 1-km cell can eat from someone else's barn.
        place = self.places.get(p.place)
        if place and place.destroyed_day is None and place.cell == p.cell and place.owner in self.people.owners(p):
            return [f'b:{place.id}']
        return []

    def _burn_stores(self):
        # Conservative interim coupling: contents burn once their catalog place
        # accumulates a lethal day's exposure. Coin/fireproof media survive.
        # Partial structural damage and evacuation are deliberately still absent.
        for place in self.places.values():
            if place.destroyed_day is not None: continue
            hour = float(self.cells.burn_hour[place.cell])
            duration = max(0., self.cfg['units']['day_hours'] - hour)
            if duration * self.cfg['fire']['building_damage_per_hour'] < 1.: continue
            self.inventory.burn(self.day, f'b:{place.id}')
            place.destroyed_day = self.day
            self.ledger.event(self.day, 'burned', cell=place.cell, place=place.id, building=place.id)
            # Surviving goods remain salvageable at their original physical holder.
            self.inventory.holders[f'b:{place.id}'].store = False

    def _resolve_scarcity(self, d):
        """Cheap, physical critical-hunger outcomes before discretionary agency.

        A person marked critical after yesterday's meal first tries to travel to an
        intact building holding food they already own (or their household owns).
        This is migration in the bounded harness: it changes their actual location,
        consumes ordinary route time, and never teleports stock or grants rights.
        If no such move is available, they may take one day's ration from the
        foreign store at their exact current place.  The taking is concealed;
        inventory conservation and the transfer event remain authoritative.
        """
        if not self.cfg['scarcity']['enabled']:
            return
        demand = self.cfg.derived['consumption_units_per_day']
        for pid, p in sorted(self.people.rows.items()):
            if not p.alive or not p.migrate:
                continue
            # Assigned farm workers and household keepers already have a physical,
            # durable food-logistics routine.  In particular, a worker may have a
            # temporary meal deficit while travelling to that routine's remote
            # store; treating that ordinary commute as emigration hijacks their
            # assignment and can prevent the household from producing.  Scarcity
            # resolution is the fallback for people without such a routine.
            if pid in self.farming.assignments or pid in self.farming.keepers:
                continue
            owners = self.people.owners(p)
            # Migration only targets food the person may legitimately eat once
            # physically present.  A route can continue across days via Farming.
            targets = set()
            for lot in self.inventory.lots.values():
                if (self.cfg.item(lot.item)['kind'] == 'food' and lot.owner in owners
                        and lot.holder.startswith('b:')):
                    bid = int(lot.holder[2:]); place = self.places.get(bid)
                    if place is not None and place.destroyed_day is None and place.cell != p.cell:
                        targets.add(bid)
            if targets and pid not in self.farming.journeys:
                ranked = []
                for bid in sorted(targets):
                    hours, _path, _edges = self.farming._route(pid, bid)
                    if math.isfinite(hours): ranked.append((hours, bid))
                if ranked:
                    _hours, bid = min(ranked, key=lambda row: (row[0], row[1]))
                    before = (p.cell, p.place)
                    arrived = self.farming.move(pid, bid)
                    launched = arrived or pid in self.farming.journeys
                    if launched:
                        # Record migration at the committed departure, even when a
                        # physical route takes several days to complete.
                        self.ledger.event(d, 'migrated', [pid], cell=p.cell, place=p.place,
                                          from_cell=before[0], from_place=before[1], target=bid,
                                          arrived=arrived, reason='critical_food')
                    # Movement is the priority response; do not also steal today.
                    if launched:
                        continue
            if not self.cfg['scarcity']['theft_enabled'] or pid in self.farming.journeys:
                continue
            hours = float(self.cfg['scarcity']['theft_hours'])
            if self.farming.budgets.get(pid, 0.) + 1e-8 < hours or p.place < 0:
                continue
            holder = f'b:{p.place}'
            place = self.places.get(p.place)
            if place is None or place.destroyed_day is not None or place.cell != p.cell:
                continue
            foreign = [lot for lot in self.inventory.lots.values()
                       if lot.holder == holder and lot.owner not in owners
                       and self.cfg.item(lot.item)['kind'] == 'food']
            if not foreign:
                continue
            capacity = max(0., self.cfg['people']['carry_cap'] - self.inventory.weight(f'p:{pid}'))
            legs, remaining = [], min(demand, int(capacity / max(1e-12, self.cfg.item(foreign[0].item)['weight'])))
            thief_owner = f'h:{p.household_id}' if p.household_id is not None else f'p:{pid}'
            for lot in sorted(foreign, key=lambda x: (-self.cfg.item(x.item)['spoil_per_day'], x.id)):
                q = min(remaining, lot.qty)
                if q:
                    legs.append(Leg(lot.id, q, f'p:{pid}', thief_owner)); remaining -= q
                if not remaining:
                    break
            if not legs:
                continue
            eid, moved = self.inventory.transfer(d, legs, actors=(pid,), cell=p.cell, place=p.place,
                                                 visibility='concealed', reason='steal')
            qty = sum(self.inventory.lots[lid].qty for lid in moved)
            self.ledger.event(d, 'steal', [pid], cell=p.cell, place=p.place, visibility='concealed',
                              transfer=eid, quantity=qty, reason='critical_food')
            self.people.witness(d, pid, eid)
            self.people.witness(d, pid, len(self.ledger.events) - 1)
            self.farming._spend(pid, hours, 'steal')

    def _record_deficit(self, d, pid, ate, demand):
        """Classify why a person ate less than demand today, from physical facts only:
        what their household owns and where it physically is relative to their reach."""
        import copy
        p = self.people.rows[pid]; owners = self.people.owners(p)
        reach = {f'p:{pid}', *self.household_stores(p)}
        out = {'store': 0, 'ground': 0, 'carried_by_other': 0}
        for lot in self.inventory.lots.values():
            if lot.owner in owners and lot.holder not in reach and self.cfg.item(lot.item)['kind'] == 'food':
                out['store' if lot.holder[0] == 'b' else 'ground' if lot.holder[0] == 'c' else 'carried_by_other'] += lot.qty
        cause = 'owned_food_out_of_reach' if sum(out.values()) else 'no_owned_food'
        rec = {'day': d, 'person': pid, 'ate': ate, 'demand': demand, 'cause': cause, 'out_of_reach': out,
               'cell': p.cell, 'place': p.place, 'in_transit': pid in self.farming.journeys, 'action': p.last_action}
        t = self.deprivation
        t['deficit_days'] += 1; t['by_cause'][cause] = t['by_cause'].get(cause, 0) + 1
        s = t['by_person'].setdefault(pid, {'deficit_days': 0, 'by_cause': {}, 'first_day': d, 'last': None})
        s['deficit_days'] += 1; s['by_cause'][cause] = s['by_cause'].get(cause, 0) + 1; s['last'] = rec
        t['recent'].append(copy.deepcopy(rec))
        if len(t['recent']) > 365: del t['recent'][0]
        return rec

    def cause_of_death(self, pid):
        """Deficit history behind a recorded death, or None. Diagnostic view, not a belief."""
        return self.deprivation['deaths'].get(pid)

    def _witness_event(self, event):
        # Resolve presence when the event happens, never from end-of-day
        # positions after a worker has visited three buildings.
        if self.people.witness_scope(event) == 'none': return   # [events.witness] "none": the emitter writes memory itself
        for pid, person in sorted(self.people.rows.items()):
            if not person.alive or person.name is None: continue
            if pid in event.actors or (event.visibility == 'public' and self.people.is_local(person, event)):
                self.people.witness(event.day, pid, event.id)

    # ---- contact (contact-v1): intraday hearsay between co-present people, after decisions and before sleep.
    # Co-presence = same place; open ground is one commons per cell with a lower weight. Every roll comes from a
    # generator keyed (day, initiator, slot), so outcomes depend on who stood where, not on draw order. Caps,
    # rates and hours are [contact] data; what a telling is worth is [gossip] data. No tier is read anywhere.
    def _contact_phase(self, d):
        from collections import defaultdict
        import numpy as np
        ct = self.cfg['contact']; g = self.cfg['gossip']; f = self.farming; P = self.people
        hours = ct['hours_per_exchange']
        groups = {}
        for pid, p in sorted(P.rows.items()):
            if not p.alive or p.busy or pid in f.journeys or not P.can_remember(p, d): continue
            groups.setdefault((p.cell, p.place), []).append(pid)
        sent = defaultdict(int); recv = defaultdict(int)
        def free(pid): return f.budgets.get(pid, 0.) + 1e-9 >= hours
        for s in range(ct['slots']):
            for key, members in sorted(groups.items()):
                if len(members) < 2: continue
                for pid in members:
                    p = P.rows[pid]
                    if sent[pid] >= ct['send_cap'] or not free(pid): continue
                    others = [q for q in members if q != pid and recv[q] < ct['receive_cap'] and free(q)]
                    if not others: continue
                    gen = self.rng.one('contact', d, (pid << 16) | s)
                    if gen.random() >= ct['p_base'] * (ct['commons_mult'] if p.place < 0 else 1.0): continue
                    w = np.array([ct['same_household_mult'] if (p.household_id is not None and P.rows[q].household_id == p.household_id) else 1.0 for q in others])
                    q = others[int(gen.choice(len(others), p=w / w.sum()))]
                    eid = P.tellable(p, P.rows[q], gen)
                    if eid is None: continue
                    P.tell(d, pid, q, eid)
                    f._spend(pid, hours, 'talk', label=False); f._spend(q, hours, 'talk', label=False)
                    sent[pid] += 1; recv[q] += 1
                    for r in members:
                        if r in (pid, q) or recv[r] >= ct['receive_cap']: continue
                        if gen.random() < g['overhear_p']:
                            P.tell(d, pid, r, eid, overheard=True); recv[r] += 1

    # ---- sleep (sleep-v1a): data-driven access rules; first matching rule wins, a refused rent falls through
    def _sleep_relation(self, p, place, rel):
        if rel == 'any': return True
        owners = self.people.owners(p)
        if rel == 'household': return place.owner in owners
        return place.owner == f'p:{p.id}' or (place.id == p.home_place and place.owner in owners)

    def _owner_residents(self, owner):
        if owner is None: return []
        if owner.startswith('p:'): return [int(owner[2:])] if self.people.rows[int(owner[2:])].alive else []
        hid = int(owner[2:])
        return [pid for pid, q in sorted(self.people.rows.items()) if q.alive and q.household_id == hid]

    def _pay_lodging(self, d, p, place, rate):
        need = math.ceil(rate)
        if need <= 0: return True
        coin = [l for l in self.inventory.lots.values() if l.holder == f'p:{p.id}' and l.owner in self.people.owners(p)
                and self.cfg.item(l.item)['kind'] == 'coin']
        have = sum(l.qty for l in coin)
        if have < need:
            self.ledger.event(d, 'lodging_refused', [p.id], cell=p.cell, place=place.id, rate=need, held=have)
            return False
        legs, left = [], need
        for l in sorted(coin, key=lambda l: l.id):
            q = min(left, l.qty); legs.append(Leg(l.id, q, f'b:{place.id}', place.owner)); left -= q
            if left == 0: break
        self.inventory.transfer(d, legs, actors=(p.id,), cell=p.cell, place=place.id, reason='lodging')
        return True

    def _sleep_resolve(self, d, p):
        """Returns (quality 0..1, rule id). Walks sleep.access by priority; reads building FIELDS only."""
        from .cells import season_of
        sl = self.cfg['sleep']; season = season_of(d)
        place = self.places.get(p.place)
        if place is not None and (place.cell != p.cell or place.destroyed_day is not None): place = None
        spec = self.cfg.building(place.kind) if place else None
        wet = bool(self.cells.wet[self.world.region[p.cell]])
        for rid, rule in sorted(sl['access'].items(), key=lambda kv: kv[1]['priority']):
            if rule.get('open_air'):
                q = rule['quality'] * sl['open_air_mult'][season] * (sl['wet_mult'] if wet else 1.0)
                actors = [p.id]
            else:
                if spec is None or not self._sleep_relation(p, place, rule.get('relation', 'any')): continue
                aff = rule.get('affordance', '')
                if aff and aff not in spec['affordances']: continue
                if spec['shelter'] < rule.get('min_shelter', 0.0): continue
                if rule.get('rent'):
                    rate = place.room_rate if place.room_rate is not None else spec.get('lodging_rate')
                    if rate is None or place.owner is None or place.owner in self.people.owners(p): continue
                    if not self._pay_lodging(d, p, place, rate): continue   # refused: fall through to the next rule
                q = rule['quality'] * min(1.0, spec['shelter'] / sl['reference_shelter']) * (1.0 - sl['condition_weight'] * (1.0 - place.condition))
                actors = [p.id] + ([pid for pid in self._owner_residents(place.owner) if pid != p.id] if rule.get('witness_owner') else [])
            ev = rule.get('event', '')
            if ev and not rule.get('rent'):   # a rented night is already ledgered by the coin transfer
                self.ledger.event(d, ev, actors, cell=p.cell, place=place.id if place else -1, rule=rid)
            return max(0.0, min(1.0, float(q))), rid
        raise RuntimeError('sleep.access has no matching rule; config validation requires an open_air fallback')

    # ---- pressure-v1 scenarios: stateless functions of the day, so no checkpoint field and replay needs no extra log.
    def _scenario(self, d):
        import numpy as np
        sc = self.cfg['scenario']; ev = self.ledger.event
        dr = sc['drought']
        if dr['start_day'] >= 0 and d in (dr['start_day'], dr['start_day'] + dr['days']):
            ev(d, 'scenario', visibility='private', kind='drought_start' if d == dr['start_day'] else 'drought_end',
               evaporation_mult=dr['evaporation_mult'], rain_mult=dr['rain_mult'])
        ig = sc['ignition']
        if ig['day'] == d:
            live = [pl for pl in self.places.values() if pl.destroyed_day is None]
            near = np.zeros(self.world.n, bool)
            for pl in live:
                near |= self.world.hex_distance(np.arange(self.world.n), np.array([pl.cell])) <= ig['distance_cells']
            cand = np.flatnonzero(near & self.cells.land)
            fuel = self.cells._eff_fuel()
            order = sorted((int(c) for c in cand), key=lambda c: (-float(fuel[c]), c))[:int(ig['count'])]
            hour = float(self.rng.one('scenario', d, 1).uniform(0.0, self.cfg['units']['day_hours'] / 2))
            lit = self.cells.ignite(d, np.array(order, dtype=np.int64), 'scenario', self.ledger, hour=hour) if order else []
            ev(d, 'scenario', visibility='private', kind='ignition', tried=len(order), ignited=len(lit))
        bl = sc['blight']
        if bl['day'] == d:
            idx = np.flatnonzero(self.cells.planted)
            self.cells.crop_health[idx] *= np.float32(1.0 - bl['loss'])
            ev(d, 'scenario', visibility='private', kind='blight', cells=int(idx.size), loss=bl['loss'])
        st = sc['stranger']
        if st['day'] == d:
            lodging = [pl for pl in sorted(self.places.values(), key=lambda q: q.id)
                       if pl.destroyed_day is None and 'lodging' in self.cfg.building(pl.kind)['affordances']]
            anchor = lodging[0] if lodging else next((pl for pl in sorted(self.places.values(), key=lambda q: q.id) if pl.destroyed_day is None), None)
            if anchor is None:
                ev(d, 'scenario', visibility='private', kind='stranger', arrived=0)
                return
            units = int(round(st['ration_kg'] * 1000 / self.cfg['units']['food_unit_g']))
            for _ in range(int(st['count'])):
                p = self.add_person(d - 30 * 365, anchor.cell, anchor.id if lodging else -1, name=f'Wanderer{len(self.people.rows)}')
                if units > 0: self.inventory.create(d, 'grain', units, f'p:{p.id}', f'p:{p.id}', source='production', process='scenario')
                self.ledger.event(d, 'arrived', [p.id], cell=anchor.cell, place=p.place, source='scenario')
            ev(d, 'scenario', visibility='private', kind='stranger', arrived=int(st['count']))

    def _heir(self, day, dead):
        """Deterministic personal succession: named heir, spouse, eldest child, then kin.
        This deliberately chooses a living person rather than silently retaining a dead owner key.
        """
        if self.cfg['succession']['rule'] == 'none': return None
        people = self.people.rows
        def alive(pid):
            q = people.get(pid)
            # A dependent can be named or related, but cannot receive an estate.
            # This keeps legacy meaningful: a child becomes an eligible heir only
            # after their public adult-stage transition.
            return q if q is not None and q.alive and self.people.at_least(q, day, 'adult') else None
        named = alive(dead.designated_heir) if dead.designated_heir is not None else None
        if named is not None: return named
        spouse = alive(dead.spouse) if dead.spouse is not None else None
        if spouse is not None: return spouse
        children = [people[pid] for pid in dead.children if alive(pid) is not None]
        if children: return min(children, key=lambda q: (q.born_day, q.id))
        kin_ids = set(dead.parents)
        for parent_id in dead.parents:
            parent = people.get(parent_id)
            if parent: kin_ids.update(parent.children)
        kin_ids.discard(dead.id)
        kin = [people[pid] for pid in kin_ids if alive(pid) is not None]
        return min(kin, key=lambda q: (q.born_day, q.id)) if kin else None

    def _transfer_estate(self, day, dead):
        """Transfer a dead person's own estate, and a seeded household estate when they head it.
        Ownership changes but custody does not: stock stays in its real physical holder.
        """
        heir = self._heir(day, dead)
        target = f'p:{heir.id}' if heir is not None else None
        owner_keys = {f'p:{dead.id}'}
        if dead.estate_owner and dead.household_id is not None: owner_keys.add(f'h:{dead.household_id}')
        lots = [lot for lot in self.inventory.lots.values() if lot.owner in owner_keys]
        places = [place for place in self.places.values() if place.owner in owner_keys]
        if not lots and not places: return heir
        # Inventory.transfer is the single ownership primitive. Same-holder legs preserve
        # physical custody while ledgering each inherited (or abandoned) lot atomically.
        from .inventory import Leg
        if lots:
            self.inventory.transfer(day, [Leg(lot.id, lot.qty, lot.holder, target) for lot in lots],
                                    actors=[dead.id] + ([heir.id] if heir else []), cell=dead.cell, place=dead.place,
                                    reason='inheritance' if heir else 'estate_abandoned')
        for place in places: place.owner = target
        for farm in self.farming.farms.values():
            if farm.owner in owner_keys: farm.owner = target
        for household in self.farming.households.values():
            if household.owner in owner_keys: household.owner = target
        self.ledger.event(day, 'transfer', [dead.id] + ([heir.id] if heir else []), cell=dead.cell, place=dead.place,
                          reason='inheritance' if heir else 'estate_abandoned', estate=True,
                          owner_from=sorted(owner_keys), owner_to=target, places=[place.id for place in places])
        return heir

    def _succession(self, day, dead_ids):
        """Settle ownership before replacing farm/pantry routines after a death."""
        for pid in dead_ids:
            p = self.people.rows[pid]
            heir = self._transfer_estate(day, p)
            farm = self.farming.assignments.pop(pid, None)
            home = self.farming.keepers.pop(pid, None)
            if farm is not None:
                candidates = [q for q in self.people.rows.values() if q.alive and q.household_id == p.household_id and q.home_place == p.home_place
                              and q.role and self.people.can_use_role(q, q.role, day)]
                f = self.farming.farms[farm]
                candidates = [q for q in candidates if f.owner in self.people.owners(q)
                              and self.cfg.role(q.role)['workplace'] == self.places[farm].kind
                              and self.cells.crop['item'] in self.cfg.role(q.role)['produces']]
                if candidates:
                    successor = heir if heir in candidates else min(candidates, key=lambda q:q.id)
                    self.farming.assignments[successor.id] = farm
                    self.ledger.event(day, 'role_changed', [successor.id, pid], cell=successor.cell, place=successor.place, role=successor.role, succession=True)
            if home is not None and home in self.farming.households:
                candidates = [q for q in self.people.rows.values() if q.alive and q.household_id == p.household_id and q.home_place == p.home_place
                              and self.people.at_least(q, day, 'youth') and self.farming.households[home].owner in self.people.owners(q)]
                if candidates: self.farming.keepers[min(candidates,key=lambda q:q.id).id] = home

    def _step(self):
        from .cells import season_of
        d = self.day
        if self.cfg['scenario']['enabled']: self._scenario(d)
        dead = self.people.lifecycle(d)
        if dead: self._succession(d, dead)
        self.cells.step(d, self.ledger)
        self._burn_stores()
        # Physical harvested lots on burned ground are not sheltered stores.
        # This inherits the existing coarse daily fire exposure model.
        import numpy as np
        for cell in np.flatnonzero(np.isfinite(self.cells.burn_hour)):
            self.inventory.burn(d, f'c:{int(cell)}')
        self.inventory.decay(d, self.rng)
        self.farming.step(d)
        self._resolve_scarcity(d)
        # Policy-controlled people decide once, after routine households, inside the
        # same activity budget. Rejected actions cost nothing and never fail the day.
        for pid in sorted(self.agency.agents):
            p = self.people.rows[pid]
            if p.alive and not p.busy: self.agency.act(pid)
        self._contact_phase(d)
        physiology_dead = []
        for pid, p in sorted(self.people.rows.items()):
            if not p.alive: continue
            quality, _rule = self._sleep_resolve(d, p)
            demand = self.cfg.derived['consumption_units_per_day']
            ate = self.people.step_person(d, pid, self.household_stores(p), None, resolved_action=self.farming.actions.get(pid),
                                          sleep_quality=quality, season=season_of(d))
            if ate < demand: self._record_deficit(d, pid, ate, demand)
            if p.died_day == d:
                physiology_dead.append(pid)
                hist = self.deprivation['by_person'].get(pid)
                self.deprivation['deaths'][pid] = {'day': d, 'cell': p.cell, 'place': p.place,
                    'deficit_days': hist['deficit_days'] if hist else 0, 'by_cause': dict(hist['by_cause']) if hist else {},
                    'first_deficit_day': hist['first_day'] if hist else None, 'last_cause': hist['last']['cause'] if hist else None}
        # Background deaths are succeeded before the day's work. Deaths from ordinary
        # physiology happen at day's end, but must still hand their household routine
        # on before tomorrow rather than leaving a farm permanently unassigned.
        if physiology_dead: self._succession(d, physiology_dead)
        # Only action/location-grounded observations. Anonymous people update
        # expectations but get no event-memory backfill if subsequently named.
        for pid, p in sorted(self.people.rows.items()):
            if not p.alive: continue
            view = self.cells.visible(p.cell)
            self.people.observe(d, pid, 'smoke', float(view['smoke']))
            self.people.observe(d, pid, 'dry', float(view['dry']))
            self.people.observe(d, pid, 'meal_fraction', p.needs['food'])
        self.inventory.compact_food()
        self.inventory.validate()
        check = self.rng.one('conservation-sample', d).random() < self.cfg['validator']['conservation_sample']
        residuals = self.ledger.close_day(d, self.inventory.holdings(), check=check)
        if check: self.last_check_day = d
        self.day += 1
        if self.day % self.cfg['run']['days_per_tick'] == 0:
            self.reports.append(self.summary())
        return residuals

    def step(self):
        if self.failed: raise RuntimeError('engine stopped after failure; reload a known state, do not retry a half-day')
        if self._in_step: raise RuntimeError('recursive engine step')
        self._in_step = True
        try:
            result = self._step()
            self._completed_day = self.day - 1
            return result
        except BaseException:
            self.failed = True
            raise
        finally:
            self._in_step = False

    def save_checkpoint(self, path):
        """Atomic, versioned state at a successfully completed day boundary."""
        from .checkpoint import save
        return save(self, path)

    @classmethod
    def load_checkpoint(cls, path, *, expected_config=None):
        """Strict same-source/runtime resume; never unpickle code or replay setup."""
        from .checkpoint import load
        return load(path, expected_config=expected_config)

    def run(self, days):
        if type(days) is not int or days < 0: raise ValueError('days must be a nonnegative integer')
        for _ in range(days): self.step()
        return self.summary()

    def summary(self):
        alive = [p for p in self.people.rows.values() if p.alive]
        totals = {}
        for stock in self.inventory.holdings().values():
            for item, q in stock.items(): totals[item] = totals.get(item, 0) + q
        return {'day': self.day, 'alive': len(alive), 'dead': len(self.people.rows) - len(alive),
                'capabilities': {c: sum(c in p.capabilities for p in alive) for c in sorted(set(self.cfg['capabilities']) - {'teach_weight'})},
                'stocks': totals, 'events': len(self.ledger.events), 'flows': self.ledger.n_flows,
                'conservation_checks': self.ledger.checks, 'residuals': len(self.ledger.residuals),
                'ecology': self.cells.summary()}

    def digest(self):
        """Regression comparison, NOT a resumable checkpoint."""
        from dataclasses import asdict
        def normalize(x):
            if isinstance(x, dict): return {str(k): normalize(v) for k, v in sorted(x.items(), key=lambda p: str(p[0]))}
            if isinstance(x, (set, tuple, list)): return [normalize(v) for v in (sorted(x) if isinstance(x, set) else x)]
            return x
        payload = normalize({'day': self.day, 'lots': [asdict(l) for l in self.inventory.lots.values()],
            'people': [asdict(p) for p in self.people.rows.values()],
            'places': [asdict(p) for p in self.places.values()],
            'events': [e.to_json() for e in self.ledger.events], 'expected': self.ledger.expected,
            'buckets': self.ledger.bucket_totals, 'farming': self.farming.state(), 'deprivation': self.deprivation, 'agency': self.agency.state()})
        h = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode())
        for name in ('moisture', 'vegetation', 'fuel', 'wildlife', 'planted', 'fields',
                     'crop_progress', 'crop_health', 'burning', 'burn_hour', 'burn_scar_day', 'burn_since_day', 'edge_progress',
                     'rain_mm', 'wet', 'wind_dir', 'wind_kph', 'protection', 'cleared'):
            h.update(getattr(self.cells, name).tobytes())
        for key in sorted(self.cells.seir): h.update(self.cells.seir[key].tobytes())
        h.update(self.cells.wl_initial.tobytes())
        for key in sorted(self.cells.budget): h.update(self.cells.budget[key].tobytes())
        return h.hexdigest()
